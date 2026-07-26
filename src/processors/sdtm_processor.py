"""
SDTM Recommendation Processor
Handles the core logic for generating SDTM domain recommendations.

The class is composed of four mixins that group related functionality:
  - CatalogMixin        (catalog.py)       – standard catalog loading
  - DomainInferenceMixin (domain_inference.py) – domain hint inference
  - PostprocessMixin     (postprocess.py)    – recommendation normalisation
  - IOHelpersMixin       (io_helpers.py)     – file I/O, checkpointing, logging
"""

import dataclasses
import json
import logging
import os
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, cast

import pandas as pd

from src.clients.base_client import BaseAIClient
from src.config.domain_semantic_map import (
    ANNOTATION_KEYWORD_DOMAIN_MAP,
    DOMAIN_VARIABLES,
    normalize_semantic_map,
)
from src.config.domain_semantic_map import (
    CHINESE_TABLE_DOMAIN_MAP as DEFAULT_CHINESE_TABLE_DOMAIN_MAP,
)
from src.models.sdtm_models import GenerationConfig, RateLimitConfig
from src.processors.catalog import CatalogMixin
from src.processors.domain_inference import DomainInferenceMixin
from src.processors.io_helpers import IOHelpersMixin
from src.processors.postprocess import PostprocessMixin
from src.prompts.sdtm_prompts_simple import SDTMPromptGenerator
from src.utils.rate_limiter import RateLimiter, TokenBucketRateLimiter

ProgressCallback = Callable[[int, int, str | None, str | None], bool | None]

logger = logging.getLogger(__name__)


def _recommendation_source_excel_label(source: str | None) -> str:
    """Map ``domain_rec['source']`` to the user-facing Excel *Source* column.

    The cascade stores ``KB`` / ``RAG`` / ``LLM`` / ``UNMAPPED`` (and postprocess may use
    ``FALLBACK``).  Older code only showed *KB* when source was exactly ``KB``; *RAG*
    rows were mislabeled *LLM*.
    """
    s = (source or "").strip().upper()
    if s in ("KB", "KB_NOT_SUBMITTED"):
        return "KB"
    if s == "RAG":
        return "RAG"
    if s == "UNMAPPED":
        return "UNMAPPED"
    return "LLM"


def _build_standard_variable_sets() -> dict[str, set]:
    """Build a {domain: set(variable_names)} lookup from DOMAIN_VARIABLES.

    The result includes common identifier variables (STUDYID, DOMAIN,
    USUBJID) that are valid for every domain, plus timing variables
    shared across most domains (EPOCH, VISIT, VISITNUM, VISITDY).
    """
    common = {"STUDYID", "DOMAIN", "USUBJID", "EPOCH", "VISIT", "VISITNUM", "VISITDY"}
    result: dict[str, set] = {}
    for domain, info in DOMAIN_VARIABLES.items():
        variables = info.get("variables", [])
        if variables and isinstance(variables[0], dict):
            names = {v["variable"].upper() for v in variables if v.get("variable")}
        else:
            names = {str(v).upper() for v in variables if v}
        result[domain.upper()] = names | common
    return result


_STANDARD_VARS_BY_DOMAIN: dict[str, set] = _build_standard_variable_sets()


def _compute_ig34_check(domain_rec: dict[str, Any]) -> str:
    """Return the IG34_Check label for a single domain recommendation.

    Values:
      "Pass"  — all extracted variable tokens are in the IG 3.4 standard list
      "Fail"  — at least one token is NOT in the standard list, OR variable name is malformed
      "Skip"  — not applicable (NOT_SUBMITTED / supp type / SUPPQUAL variable / no domain info)
      ""      — domain not found in IG 3.4 data (cannot validate)
    """
    from src.processors.deterministic_validator import (
        SUPPQUAL_VARS,
        _extract_qnam_from_expression,
        _extract_qnam_from_supp_variable,
        _extract_variable_names,
    )

    var_type = str(domain_rec.get("sdtm_variable_type", "")).lower()
    sdtm_var = str(domain_rec.get("sdtm_variable", "") or "").strip()
    domain_raw = str(domain_rec.get("domain", "") or "").strip().upper()

    # NOT_SUBMITTED and empty → always Skip
    if sdtm_var.upper() == "NOT SUBMITTED" or not sdtm_var:
        return "Skip"

    # --- SUPP type: sdtm_variable is "QVAL" and supp_variable holds the QNAM ---
    # If the QNAM itself is a standard domain variable, the mapping is wrong —
    # the CRF variable should be mapped directly as a standard variable, not SUPP.
    if var_type == "supp":
        domains_list_supp = [d.strip().upper() for d in domain_raw.split("|") if d.strip()]
        if sdtm_var.upper() == "QVAL" and domains_list_supp:
            supp_var = str(domain_rec.get("supp_variable", "") or "").strip()
            qnam = _extract_qnam_from_supp_variable(supp_var) if supp_var else None
            if qnam:
                for target_domain in domains_list_supp:
                    std_vars = _STANDARD_VARS_BY_DOMAIN.get(target_domain)
                    if std_vars and qnam in std_vars:
                        return "Fail"
        return "Skip"

    # Non-standard types other than "supp" → always Skip
    if var_type != "standard":
        return "Skip"

    # Pre-compute domain list (needed by both the early-return and the main loop)
    domains_list = [d.strip().upper() for d in domain_raw.split("|") if d.strip()]
    if not domains_list:
        return "Skip"

    extracted = _extract_variable_names(sdtm_var)
    if not extracted:
        # Distinguish between recognised non-validatable patterns (conditionals / assignments)
        # and plain malformed variable names (e.g. "PC_PCCYC_PENDING" with underscores).
        sdtm_upper = sdtm_var.upper()
        has_conditional = any(kw in sdtm_upper for kw in (" IF ", " WHEN "))
        has_assignment = "=" in sdtm_var
        if not has_conditional and not has_assignment:
            # Nothing could be extracted from a plain name → malformed variable name
            return "Fail"

        # Even when the main token could not be extracted (e.g. "QVAL when QNAM=EGSTAT=未查
        # IF [RAW]EGPERF=否" where _extract_variable_names gets confused by the compound
        # structure), still check for QNAM=<standard_var> conflicts.
        qnam = _extract_qnam_from_expression(sdtm_var)
        if qnam:
            for target_domain in domains_list:
                standard_vars = _STANDARD_VARS_BY_DOMAIN.get(target_domain)
                if standard_vars and qnam in standard_vars:
                    return "Fail"
        return "Skip"

    for i, var_token in enumerate(extracted):
        # SUPPQUAL variables (QVAL, QNAM, etc.) live in SUPPXX datasets.
        # For plain QVAL mappings this is Skip, but when the expression contains
        # "QNAM=XXX" and XXX is itself a standard domain variable, the mapping
        # is incorrect — the variable should be mapped directly as a standard var.
        if var_token in SUPPQUAL_VARS:
            qnam = _extract_qnam_from_expression(sdtm_var)
            if qnam:
                target_domain = domains_list[i] if i < len(domains_list) else domains_list[0]
                standard_vars = _STANDARD_VARS_BY_DOMAIN.get(target_domain)
                if standard_vars and qnam in standard_vars:
                    # QNAM=<standard_var> — should use the standard variable directly
                    return "Fail"
            return "Skip"

        target_domain = domains_list[i] if i < len(domains_list) else domains_list[0]
        standard_vars = _STANDARD_VARS_BY_DOMAIN.get(target_domain)
        if standard_vars is None:
            # Domain not in IG 3.4 — cannot validate
            return ""
        if var_token not in standard_vars:
            return "Fail"

    return "Pass"


def compute_diff_status(
    ai_domain: str | None,
    ai_variable: str | None,
    ref_domain: str | None,
    ref_variable: str | None,
) -> str:
    """Categorize AI vs reference mapping for a single row."""
    ai_has = bool((ai_domain or "").strip() and (ai_variable or "").strip())
    ref_has = bool((ref_domain or "").strip() and (ref_variable or "").strip())
    if ai_has and not ref_has:
        return "ai_only"
    if ref_has and not ai_has:
        return "ref_only"
    if not ai_has and not ref_has:
        return "ai_only"
    if (ai_domain or "").strip() != (ref_domain or "").strip():
        return "domain_diff"
    if (ai_variable or "").strip() != (ref_variable or "").strip():
        return "var_diff"
    return "match"


def attach_reference_diff(
    excel_rows: list[dict],
    reference_kb: pd.DataFrame | None,
) -> list[dict]:
    """Enrich each row with Reference_Domain/Reference_Variable/Diff_Status.

    If reference_kb is None or produces no overlap, rows are returned unchanged
    (no diff columns added, keeping the workbook lean).
    """
    if reference_kb is None or reference_kb.empty:
        return excel_rows

    key_cols = ["annotation_table", "annotation_variable"]
    missing = [c for c in key_cols if c not in reference_kb.columns]
    if missing:
        return excel_rows

    ref = reference_kb.copy()
    for col in ("SDTM_Domain", "SDTM_Variable"):
        if col not in ref.columns:
            ref[col] = ""
    ref = ref[[*key_cols, "SDTM_Domain", "SDTM_Variable"]]
    lookup = {
        (str(r["annotation_table"]), str(r["annotation_variable"])): (
            str(r["SDTM_Domain"] or ""),
            str(r["SDTM_Variable"] or ""),
        )
        for _, r in ref.iterrows()
    }

    if not lookup:
        return excel_rows

    enriched: list[dict] = []
    any_match = False
    for row in excel_rows:
        new_row = dict(row)
        key = (
            str(new_row.get("annotation_table", "") or ""),
            str(new_row.get("annotation_variable", "") or ""),
        )
        ref_dom, ref_var = lookup.get(key, ("", ""))
        ai_dom = new_row.get("Domain") or new_row.get("SDTM_Domain") or ""
        ai_var = new_row.get("Variable") or new_row.get("SDTM_Variable") or ""
        status = compute_diff_status(ai_dom, ai_var, ref_dom, ref_var)
        new_row["Reference_Domain"] = ref_dom
        new_row["Reference_Variable"] = ref_var
        new_row["Diff_Status"] = status
        if ref_dom or ref_var:
            any_match = True
        enriched.append(new_row)

    if not any_match:
        for row in enriched:
            row.pop("Reference_Domain", None)
            row.pop("Reference_Variable", None)
            row.pop("Diff_Status", None)
    return enriched


@dataclasses.dataclass
class CascadeResult:
    """Result from the Level 1/2/3 cascade shortcircuit attempt."""

    recs: list[dict[str, Any]] | None
    cascade_level: int
    rag_contexts: list[Any] = dataclasses.field(default_factory=list)
    rag_info: dict[str, Any] = dataclasses.field(default_factory=lambda: {"query": "", "top_score": 0.0, "total": 0})


class SDTMProcessor(
    CatalogMixin,
    DomainInferenceMixin,
    PostprocessMixin,
    IOHelpersMixin,
):
    """Processes variable mappings to generate SDTM domain recommendations."""

    CHINESE_TABLE_DOMAIN_MAP: dict[str, str] = DEFAULT_CHINESE_TABLE_DOMAIN_MAP.copy()
    rate_limiter: RateLimiter | TokenBucketRateLimiter
    save_frequency: int | None
    audit_logger: Any
    data_masker: Any
    kb_query: Any
    rag_augmenter: Any

    def __init__(
        self,
        client: BaseAIClient,
        model_name: str,
        generation_config: GenerationConfig | None = None,
        rate_limit_config: RateLimitConfig | None = None,
        debug: bool = False,
        language: str = "en",
        enable_knowledge_base: bool = True,
        rag_config: dict[str, Any] | None = None,
        log_ai_interactions: bool | None = None,
        save_frequency: int | None = None,
        max_workers: int | None = None,
        enable_parallel: bool | None = None,
        session_id: str | None = None,
    ):
        self.client = client
        self.model_name = model_name
        self.generation_config = generation_config or GenerationConfig()
        self.rag_config = rag_config or {}
        self.session_id = session_id

        # Parallel processing configuration
        if enable_parallel is not None:
            self.enable_parallel = enable_parallel
        else:
            env_parallel = os.getenv("SDTM_ENABLE_PARALLEL", "true").lower()
            self.enable_parallel = env_parallel in ("1", "true", "yes", "on")

        if max_workers is not None:
            self.max_workers = max(1, max_workers)
        else:
            try:
                self.max_workers = int(os.getenv("SDTM_MAX_WORKERS", "5"))
            except (TypeError, ValueError):
                self.max_workers = 5
            self.max_workers = max(1, min(self.max_workers, 20))

        if self.enable_parallel:
            self.rate_limiter = TokenBucketRateLimiter(rate_limit_config)
        else:
            self.rate_limiter = RateLimiter(rate_limit_config)

        self._progress_lock = threading.Lock()
        self._cancel_event = threading.Event()
        self.prompt_generator = SDTMPromptGenerator(language=language)
        self.debug = debug
        self.language = language
        self.enable_knowledge_base = enable_knowledge_base
        env_log_ai = os.getenv("SDTM_LOG_AI", "0").lower() in ("1", "true", "yes", "on")
        if log_ai_interactions is None:
            self.log_ai_interactions = env_log_ai or debug
        else:
            self.log_ai_interactions = bool(log_ai_interactions)
        env_save_frequency = os.getenv("SDTM_SAVE_FREQUENCY")
        if save_frequency is not None:
            self.save_frequency = max(1, int(save_frequency))
        elif env_save_frequency:
            try:
                self.save_frequency = max(1, int(env_save_frequency))
            except ValueError:
                self.save_frequency = None
        else:
            self.save_frequency = None

        # Catalog (CatalogMixin)
        self.standard_catalog = self._initialize_standard_catalog()

        kb_confidence_value = os.getenv("KB_MIN_CONFIDENCE", "0.8")
        try:
            self.kb_min_confidence = float(kb_confidence_value)
        except (TypeError, ValueError):
            self.kb_min_confidence = 0.8
        self.kb_min_confidence = max(0.0, min(self.kb_min_confidence, 1.0))
        try:
            override_conf = float(os.getenv("KB_DOMAIN_OVERRIDE_CONF", "0.85"))
        except (TypeError, ValueError):
            override_conf = 0.85
        self.domain_override_threshold = max(0.0, min(override_conf, 1.0))

        # Cascade prediction thresholds (configurable via environment)
        # Level 1: KB exact match (uses KB_MIN_CONFIDENCE threshold)
        # Level 2: KB high-confidence (annotations match etc.)
        try:
            self.cascade_kb_high_conf = float(os.getenv("CASCADE_KB_HIGH_CONF", "0.85"))
        except (TypeError, ValueError):
            self.cascade_kb_high_conf = 0.85
        self.cascade_kb_high_conf = max(0.0, min(self.cascade_kb_high_conf, 1.0))

        # Level 3: RAG high-confidence — adopt without LLM
        try:
            self.cascade_rag_high_conf = float(os.getenv("CASCADE_RAG_HIGH_CONF", "0.7"))
        except (TypeError, ValueError):
            self.cascade_rag_high_conf = 0.7
        self.cascade_rag_high_conf = max(0.0, min(self.cascade_rag_high_conf, 1.0))

        # Audit logger for GxP-compliant traceability
        audit_enabled = os.getenv("AUDIT_LOG_ENABLED", "1").lower() in ("1", "true", "yes", "on")
        if session_id and audit_enabled:
            from src.infrastructure.audit_logger import AuditLogger

            self.audit_logger = AuditLogger(
                session_id=session_id,
                enabled=True,
            )
        else:
            self.audit_logger = None

        # Data masker for PHI/PII redaction before LLM calls
        masking_enabled = os.getenv("DATA_MASKING_ENABLED", "1").lower() in ("1", "true", "yes", "on")
        if masking_enabled:
            from src.infrastructure.data_masker import DataMasker

            self.data_masker = DataMasker()
        else:
            self.data_masker = None

        # Domain inference helpers (DomainInferenceMixin)
        self.semantic_keyword_domain_map = normalize_semantic_map(ANNOTATION_KEYWORD_DOMAIN_MAP)
        self._semantic_kw_sorted = sorted(
            self.semantic_keyword_domain_map.items(),
            key=lambda kv: len(kv[0]),
            reverse=True,
        )
        self.domain_name_lookup = self._build_domain_name_lookup()

        # RAG configuration defaults
        min_score_value = (
            self.rag_config.get("min_score")
            if self.rag_config.get("min_score") is not None
            else os.getenv("RAG_MIN_SCORE")
        )
        try:
            self.rag_min_score = float(min_score_value) if min_score_value is not None else 0.55
        except (TypeError, ValueError):
            self.rag_min_score = 0.55

        char_limit_value = (
            self.rag_config.get("char_limit")
            if self.rag_config.get("char_limit") is not None
            else os.getenv("RAG_CHAR_LIMIT")
        )
        try:
            self.rag_char_limit = int(char_limit_value) if char_limit_value is not None else 1500
        except (TypeError, ValueError):
            self.rag_char_limit = 1500

        # Initialize knowledge base interface
        if self.enable_knowledge_base:
            try:
                from src.knowledge_base.llm_query_interface import LLMKnowledgeQueryInterface

                kb_base_path = (
                    self.rag_config.get("kb_base_path") or os.getenv("RAG_KB_BASE_PATH") or "data/knowledge_base"
                )
                extra_kb_files = self.rag_config.get("extra_kb_files") or []

                self.kb_query = LLMKnowledgeQueryInterface(
                    knowledge_base_path=kb_base_path,
                    extra_kb_files=extra_kb_files,
                    log_ai_interactions=self.log_ai_interactions,
                )
                kb_sources = self.kb_query.get_kb_sources()
                total_records = kb_sources.get("total_records", 0)
                if total_records > 0:
                    source_info = []
                    if kb_sources.get("default"):
                        source_info.append(f"default: {kb_sources['default']}")
                    if kb_sources.get("extra"):
                        source_info.append(f"session: {len(kb_sources['extra'])} files")
                    print(f"✅ Knowledge base enabled | {total_records} records from {', '.join(source_info)}")
                else:
                    print("✅ Knowledge base enabled | direct matching disabled (no KB files loaded)")

                kb_verbose = self.rag_config.get("kb_verbose", False)
                if kb_verbose:
                    self.kb_query.set_verbose(True)
                    print("📋 KB verbose mode enabled")
            except Exception as e:
                print(f"⚠️ Warning: Failed to initialize knowledge base: {e}")
                self.enable_knowledge_base = False
                self.kb_query = None
        else:
            self.kb_query = None

        # Initialize RAG prompt augmenter
        if self.enable_knowledge_base:
            from src.rag.prompt_augmenter import RAGPromptAugmenter

            kb_path = self.rag_config.get("kb_path") or os.getenv("RAG_KB_PATH") or "data/knowledge_base/structured"
            embedding_model = self.rag_config.get("embedding_model") or os.getenv("RAG_EMBED_MODEL") or "Qwen3-Embed"
            top_k_value = self.rag_config.get("top_k") or os.getenv("RAG_TOP_K") or 3
            try:
                top_k = int(top_k_value)
            except (TypeError, ValueError):
                top_k = 3

            rag_extra_kb_files = extra_kb_files

            try:
                self.rag_augmenter = RAGPromptAugmenter(
                    kb_path=kb_path,
                    embedding_model=embedding_model,
                    top_k=top_k,
                    cache_dir=self.rag_config.get("cache_dir"),
                    extra_kb_files=rag_extra_kb_files,
                )
                rag_stats = self.rag_augmenter.get_stats()
                if rag_stats["session_docs"] > 0:
                    print(
                        f"✅ RAG prompt augmenter ready | kb: {kb_path} | model: {embedding_model} | "
                        f"top_k: {top_k} | default: {rag_stats['default_docs']} | session: {rag_stats['session_docs']}"
                    )
                else:
                    print(f"✅ RAG prompt augmenter ready | kb: {kb_path} | model: {embedding_model} | top_k: {top_k}")
            except Exception as e:
                print(f"⚠️ Warning: Failed to initialize RAG augmenter: {e}")
                self.rag_augmenter = None
        else:
            self.rag_augmenter = None

        self.rag_verbose = self.rag_config.get("verbose", False)
        self.force_rag = self.rag_config.get("force", False)

        # KB-derived prompt hints (TESTCD prefill, disambiguation, domain examples, etc.)
        self.kb_hints: "KBDerivedHints | None" = None  # noqa: UP037
        if self.enable_knowledge_base and self.kb_query is not None:
            try:
                from src.knowledge_base.kb_derived_hints import KBDerivedHints

                if self.kb_query.ecrf_data is not None and len(self.kb_query.ecrf_data) > 0:
                    records = self.kb_query.ecrf_data.to_dict(orient="records")
                    self.kb_hints = KBDerivedHints.from_records(records)
                    if self.debug:
                        print(f"   🧠 KBDerivedHints loaded from {len(records)} KB records")
            except Exception as _e:
                logger.warning("KBDerivedHints init failed (non-fatal): %s", _e)

        self.debugger = None

    # ==================== Cascade helpers ====================

    def _kb_suggestions_to_recs(
        self,
        suggestions: list[dict[str, Any]],
        variable_name: str,
        default_confidence: float,
        table_name: str,
        target_domain: str | None,
        cascade_level: int,
    ) -> list[dict[str, Any]]:
        """Convert KB suggestions to normalized, validated domain recommendations."""
        raw_recs = []
        for suggestion in suggestions:
            raw_recs.append(
                {
                    "domain": suggestion.get("domain", ""),
                    "sdtm_variable": suggestion.get("sdtm_variable", ""),
                    "sdtm_variable_type": "standard",
                    "score": suggestion.get("confidence", suggestion.get("score", default_confidence)),
                    "variable_name": variable_name,
                    "source": "KB",
                }
            )
        max_score = max((rec.get("score", 0.0) for rec in raw_recs), default=0.0)
        enforce_domain = not (max_score >= self.domain_override_threshold)
        domain_recs = self._normalize_domain_recs(
            table_name=table_name,
            variable_name=variable_name,
            domain_recs=raw_recs,
            target_domain=target_domain,
            enforce_domain=enforce_domain,
        )
        validation_score = 1.0 if cascade_level == 1 else default_confidence
        validation_reason = f"KB match (Level {cascade_level})"
        for rec in domain_recs:
            rec["kb_validated"] = True
            rec["kb_validation"] = {
                "validation_score": validation_score,
                "validation_reason": [validation_reason],
                "kb_suggestions": [],
            }
            rec["source"] = "KB"
        return domain_recs

    def _try_cascade_shortcircuit(
        self,
        table_name: str,
        variable_name: str,
        variable_data: dict[str, Any],
        kb_context: dict[str, Any],
        target_domain: str | None,
        start_time: float,
    ) -> CascadeResult:
        """Attempt Level 1/2/3 cascade exits.

        Returns a CascadeResult.  When ``recs`` is not None the caller should
        return immediately (shortcircuit).  When ``recs`` is None the caller
        should proceed to Level 4 (LLM), using the ``rag_contexts`` /
        ``rag_info`` already collected here to avoid a duplicate retrieval.
        """
        # --- Level 1: KB eCRF direct match ---
        if (
            kb_context.get("source") == "eCRF_direct_match"
            and kb_context.get("confidence", 0) >= self.kb_min_confidence
            and kb_context.get("suggestions")
        ):
            if self.debug:
                print(f"   🎯 Level 1: KB exact match for {variable_name} (conf={kb_context.get('confidence', 0):.2f})")
            domain_recs = self._kb_suggestions_to_recs(
                kb_context.get("suggestions", []),
                variable_name,
                0.9,
                table_name,
                target_domain,
                cascade_level=1,
            )
            total_time = time.time() - start_time
            if self.debug:
                logger.info(
                    f"✓ Level 1: {len(domain_recs)} KB exact recommendations for {table_name} - {variable_name}"
                )
                kb_validated_count = sum(1 for rec in domain_recs if rec.get("kb_validated", False))
                logger.info(f"🧠 Knowledge base validated: {kb_validated_count}/{len(domain_recs)} recommendations")
                logger.info(f"⏱️  KB Level 1 processing: {total_time:.2f}s")
            self._audit_mapping_result(
                variable_data, domain_recs, cascade_level=1, processing_time_ms=total_time * 1000
            )
            return CascadeResult(recs=domain_recs, cascade_level=1)

        # --- Level 2: KB high-confidence suggestions ---
        kb_confidence = kb_context.get("confidence", 0.0)
        kb_suggestions = kb_context.get("suggestions", [])

        if kb_suggestions and kb_confidence >= self.cascade_kb_high_conf:
            if self.debug:
                print(f"   🎯 Level 2: KB high-confidence match for {variable_name} (conf={kb_confidence:.2f})")
            domain_recs = self._kb_suggestions_to_recs(
                kb_suggestions,
                variable_name,
                kb_confidence,
                table_name,
                target_domain,
                cascade_level=2,
            )
            total_time = time.time() - start_time
            if self.debug:
                print(
                    f"✓ Level 2: {len(domain_recs)} KB recommendations for {table_name} - {variable_name} (conf={kb_confidence:.2f})"
                )
                print(f"⏱️  KB Level 2 processing: {total_time:.2f}s")
            self._audit_mapping_result(
                variable_data, domain_recs, cascade_level=2, processing_time_ms=total_time * 1000
            )
            return CascadeResult(recs=domain_recs, cascade_level=2)

        # --- Level 3: RAG-enhanced retrieval ---
        rag_contexts: list = []
        rag_info: dict[str, Any] = {"query": "", "top_score": 0.0, "total": 0}

        enable_rag = self.rag_config.get("enabled", True) and os.getenv("RAG_ENABLED", "1").lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        force_rag = getattr(self, "force_rag", False)

        if enable_rag and (kb_confidence < self.cascade_kb_high_conf or force_rag):
            rag_contexts, rag_info = self._get_rag_contexts(variable_data)
            if self.debug:
                if rag_info["total"] > 0:
                    print(f"🔎 RAG retrieved {rag_info['total']} chunks (top score={rag_info['top_score']:.2f})")
                if rag_contexts:
                    print(f"📚 Using {len(rag_contexts)} RAG snippets (score ≥ {self.rag_min_score:.2f})")

            rag_top_score = rag_info.get("top_score", 0.0)
            if rag_top_score >= self.cascade_rag_high_conf and rag_contexts:
                rag_recs = self._rag_contexts_to_recommendations(
                    rag_contexts,
                    table_name,
                    variable_name,
                    target_domain,
                )
                if rag_recs:
                    max_score = max((rec.get("score", 0.0) for rec in rag_recs), default=0.0)
                    if kb_suggestions:
                        for suggestion in kb_suggestions:
                            rag_recs.append(
                                {
                                    "domain": suggestion.get("domain", ""),
                                    "sdtm_variable": suggestion.get("sdtm_variable", ""),
                                    "sdtm_variable_type": "standard",
                                    "score": suggestion.get("confidence", suggestion.get("score", kb_confidence)),
                                    "variable_name": variable_name,
                                    "source": "KB",
                                }
                            )
                        rag_recs.sort(key=lambda r: r.get("score", 0.0), reverse=True)

                    enforce_domain = not (max_score >= self.domain_override_threshold)
                    domain_recs = self._normalize_domain_recs(
                        table_name=table_name,
                        variable_name=variable_name,
                        domain_recs=rag_recs,
                        target_domain=target_domain,
                        enforce_domain=enforce_domain,
                    )
                    for rec in domain_recs:
                        if rec.get("source") not in ("KB", "LLM"):
                            rec["source"] = "RAG"

                    # --- QNAM-standard-variable conflict check ---
                    # Reject RAG recs where QNAM (from supp_variable first token, or
                    # from "QNAM=XXX" pattern in sdtm_variable) is itself a standard
                    # variable in the target domain.  Those variables should be mapped
                    # directly (e.g. EGSTAT → EG.EGSTAT), not routed through SUPPQUAL.
                    # Fall through to LLM Level 4 so the model can produce the correct
                    # standard mapping.
                    from src.processors.deterministic_validator import (
                        _extract_qnam_from_expression,
                        _extract_qnam_from_supp_variable,
                    )

                    conflicting = []
                    clean_recs = []
                    for rec in domain_recs:
                        sv = str(rec.get("sdtm_variable", "")).strip()
                        rec_domain = str(rec.get("domain", "")).strip().upper()
                        rec_var_type = str(rec.get("sdtm_variable_type", "")).lower()

                        # Determine QNAM: supp type uses supp_variable directly;
                        # standard type may encode it as "QVAL when QNAM=XXX".
                        if rec_var_type == "supp" and sv.upper() == "QVAL":
                            supp_val = str(rec.get("supp_variable", "")).strip()
                            qnam = _extract_qnam_from_supp_variable(supp_val) if supp_val else None
                        else:
                            qnam = _extract_qnam_from_expression(sv)

                        if qnam:
                            std_vars = _STANDARD_VARS_BY_DOMAIN.get(rec_domain, set())
                            if qnam in std_vars:
                                conflicting.append(f"{variable_name}: QNAM={qnam} is standard in {rec_domain}")
                                continue  # drop this rec
                        clean_recs.append(rec)

                    if conflicting:
                        if self.debug:
                            print(
                                f"⚠️  Level 3 RAG: {len(conflicting)} rec(s) rejected "
                                f"(QNAM conflicts standard var — falling to LLM): {conflicting}"
                            )
                        if not clean_recs:
                            # All RAG recs were rejected — fall through to LLM
                            return CascadeResult(
                                recs=None,
                                cascade_level=3,
                                rag_contexts=rag_contexts,
                                rag_info=rag_info,
                            )
                        domain_recs = clean_recs

                    total_time = time.time() - start_time
                    if self.debug:
                        print(
                            f"✓ Level 3: {len(domain_recs)} RAG-enhanced recommendations for {table_name} - {variable_name} (rag_top={rag_top_score:.2f})"
                        )
                        print(f"⏱️  RAG Level 3 processing: {total_time:.2f}s")
                    self._audit_mapping_result(
                        variable_data, domain_recs, cascade_level=3, processing_time_ms=total_time * 1000
                    )
                    return CascadeResult(
                        recs=domain_recs, cascade_level=3, rag_contexts=rag_contexts, rag_info=rag_info
                    )

            if self.debug and rag_info["total"] > 0 and not rag_contexts:
                print(f"ℹ️ Retrieved snippets below threshold {self.rag_min_score:.2f}; falling back to LLM")
        elif self.debug:
            if not enable_rag:
                print("ℹ️ RAG disabled; relying on KB + LLM reasoning")
            else:
                print(f"ℹ️ Skipping RAG (KB confidence={kb_confidence:.2f} >= {self.cascade_kb_high_conf})")

        # No shortcircuit — caller should proceed to Level 4
        return CascadeResult(recs=None, cascade_level=4, rag_contexts=rag_contexts, rag_info=rag_info)

    # ==================== Core variable processing ====================

    def process_variable_pair(
        self,
        table_name: str,
        variable_data: dict[str, Any],
        all_variables: list[dict[str, Any]],
        dry_run: bool = False,
        pair_number: int = 1,
        total_pairs: int = 1,
        completed_table_mappings: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]] | None:
        """Process a single variable-table pair to generate SDTM recommendations.

        Args:
            completed_table_mappings: Already-mapped sibling variable results
                from the same table, used to maintain domain/naming consistency.
        """
        variable_data = dict(variable_data)
        variable_name = variable_data.get("metadata_variable", "unknown")

        start_time = time.time()

        if dry_run:
            print(f"\n{'=' * 50}")
            print(f"DRY RUN: Processing pair {pair_number} of {total_pairs}: {table_name} - {variable_name}")
            print(f"{'=' * 50}")
        else:
            print(f"Processing pair {pair_number} of {total_pairs}: {table_name} - {variable_name}")

        # ── Direction 6: NOT_SUBMITTED pre-filter ─────────────────────────
        # If the annotation_table is known to be consistently NOT_SUBMITTED in
        # the KB (e.g. 问询页 tables), skip LLM entirely and return immediately.
        if self.kb_hints is not None and self.kb_hints.is_not_submitted_table(
            variable_data.get("annotation_table", "")
        ):
            if self.debug:
                print(f"   ⏭️  {table_name}/{variable_name}: NOT_SUBMITTED table, skipping LLM")
            return [
                {
                    "domain": "",
                    "sdtm_variable": "NOT SUBMITTED",
                    "sdtm_variable_type": "not_submitted",
                    "variable_name": variable_name,  # required for Excel source_mapping lookup
                    "score": 1.0,
                    "source": "KB_NOT_SUBMITTED",
                    "cascade_level": 0,
                    "priority": 0,
                }
            ]

        kb_context = self._get_knowledge_base_context(variable_data)
        target_domain = self._recommend_domain_from_annotation(
            variable_data.get("annotation_table", "")
        ) or kb_context.get("domain_hint")
        if target_domain:
            target_domain = target_domain.strip().upper()

        # --- Cascade Prediction Strategy (Level 1/2/3) ---
        cascade = self._try_cascade_shortcircuit(
            table_name,
            variable_name,
            variable_data,
            kb_context,
            target_domain,
            start_time,
        )
        if cascade.recs is not None:
            return cascade.recs

        # Level 4: Full LLM inference
        prompt = self._create_enhanced_prompt(
            variable_data,
            kb_context,
            all_variables,
            completed_table_mappings,
        )

        if dry_run:
            os.makedirs("data/output/prompt_previews", exist_ok=True)
            safe_filename = (
                f"{pair_number:04d}_{table_name}_{variable_name}".replace(" ", "_").replace("/", "_").replace("\\", "_")
            )
            prompt_file = os.path.join("data/output/prompt_previews", f"{safe_filename}.txt")

            with open(prompt_file, "w", encoding="utf-8") as f:
                f.write(f"=== PROMPT FOR: {table_name} - {variable_name} ===\n\n")
                f.write(f"Language: {self.language}\n")
                f.write(f"Knowledge Base Context: {len(kb_context.get('suggestions', []))} suggestions\n")
                f.write(prompt)
                f.write("\n\n=== END OF PROMPT ===")

            print(f"[Dry Run] Preview of prompt for {table_name} - {variable_name}:")
            print(f"Language: {self.language}")
            print(f"Knowledge Base Context: {len(kb_context.get('suggestions', []))} suggestions")
            if kb_context.get("suggestions"):
                print("Top KB suggestions:")
                for i, suggestion in enumerate(kb_context["suggestions"][:3], 1):
                    print(f"  {i}. {suggestion.get('domain', 'N/A')} -> {suggestion.get('sdtm_variable', 'N/A')}")
            print(f"First 100 chars: {prompt[:100]}...")
            print(f"Last 100 chars: ...{prompt[-100:]}")
            print(f"Full prompt saved to: {prompt_file}")
            print(f"Prompt length: {len(prompt)} characters")

            return [{"domain": "PLACEHOLDER", "sdtm_variable": "PLACEHOLDER", "score": 0.0, "priority": 1}]

        self.rate_limiter.wait()

        try:
            api_start_time = time.time()

            if self.log_ai_interactions:
                self._log_ai_interaction(table_name, variable_name, "INPUT", prompt, "prompt")

            response_text = self.client.generate_content(
                prompt=prompt,
                max_output_tokens=self.generation_config.max_output_tokens,
                temperature=self.generation_config.temperature,
                top_p=self.generation_config.top_p,
                top_k=self.generation_config.top_k,
            )
            api_end_time = time.time()
            api_duration = api_end_time - api_start_time

            if self.log_ai_interactions:
                self._log_ai_interaction(table_name, variable_name, "OUTPUT", response_text, "response", api_duration)

            usage = None
            if hasattr(self.client, "get_last_usage"):
                try:
                    usage = self.client.get_last_usage()
                except Exception:
                    usage = None
            if usage:
                prompt_tok = usage.get("prompt_tokens", "n/a")
                completion_tok = usage.get("completion_tokens", "n/a")
                total_tok = usage.get("total_tokens", "n/a")
                print(f"🔢 Tokens — prompt: {prompt_tok}, completion: {completion_tok}, total: {total_tok}")

            if not response_text or response_text.strip() == "":
                print(f"✗ Empty response from AI model for {table_name} - {variable_name}")
                print("Response was empty or whitespace only")
                total_time = time.time() - start_time
                print(f"⏱️  API call: {api_duration:.2f}s | Total processing: {total_time:.2f}s")
                return self._build_fallback_recommendations(
                    table_name,
                    variable_data,
                    target_domain,
                    "AI response empty",
                )

            if "```json" in response_text:
                json_content = response_text.split("```json")[1].split("```")[0].strip()
            elif "```" in response_text:
                json_content = response_text.split("```")[1].strip()
            else:
                json_content = response_text

            if not json_content or json_content.strip() == "":
                print(f"✗ No valid JSON content extracted for {table_name} - {variable_name}")
                print(f"Response was: {response_text[:200]}...")
                total_time = time.time() - start_time
                print(f"⏱️  API call: {api_duration:.2f}s | Total processing: {total_time:.2f}s")
                return self._build_fallback_recommendations(
                    table_name,
                    variable_data,
                    target_domain,
                    "AI response missing JSON",
                )

            try:
                result = json.loads(json_content)

                table_recommendations = result.get("table_recommendations", [])
                if not table_recommendations and isinstance(result, list):
                    table_recommendations = result

                validated_result = self._validate_with_knowledge_base(result, variable_data, kb_context)

                for table_rec in validated_result.get("table_recommendations", table_recommendations):
                    rec_table_name = table_rec.get("table_name", "")

                    table_matches = (
                        rec_table_name == table_name
                        or rec_table_name == variable_data.get("annotation_table", "")
                        or table_name in rec_table_name
                        or rec_table_name in table_name
                    )

                    if not table_matches:
                        continue

                    domain_recs = table_rec.get("domain_recommendations", [])
                    if domain_recs:
                        max_score = max((rec.get("score", 0.0) for rec in domain_recs), default=0.0)
                        enforce_domain = not (max_score >= self.domain_override_threshold)
                        domain_recs = self._normalize_domain_recs(
                            table_name=rec_table_name,
                            variable_name=variable_name,
                            domain_recs=domain_recs,
                            target_domain=target_domain,
                            enforce_domain=enforce_domain,
                        )

                        for rec in domain_recs:
                            rec["source"] = "LLM"

                        total_time = time.time() - start_time
                        print(
                            f"✓ Extracted {len(domain_recs)} domain recommendations for {rec_table_name} - {variable_name}"
                        )
                        if self.enable_knowledge_base:
                            kb_validated_count = sum(1 for rec in domain_recs if rec.get("kb_validated", False))
                            print(
                                f"🧠 Knowledge base validated: {kb_validated_count}/{len(domain_recs)} recommendations"
                            )
                        print(f"⏱️  API call: {api_duration:.2f}s | Total processing: {total_time:.2f}s")
                        self._audit_mapping_result(
                            variable_data, domain_recs, cascade_level=4, processing_time_ms=total_time * 1000
                        )
                        return domain_recs

                total_time = time.time() - start_time
                print(f"✗ No valid recommendations found for {table_name} - {variable_name} in response")
                print(f"⏱️  API call: {api_duration:.2f}s | Total processing: {total_time:.2f}s")
                return self._build_fallback_recommendations(
                    table_name,
                    variable_data,
                    target_domain,
                    "No recommendation matched response",
                )

            except json.JSONDecodeError as e:
                print(f"✗ Error parsing AI response for {table_name} - {variable_name}: {e}")
                preview = response_text[:100]
                print(f"Response was: {preview}...")
                return self._build_fallback_recommendations(
                    table_name,
                    variable_data,
                    target_domain,
                    "Failed to parse AI JSON",
                )

        except Exception as e:
            total_time = time.time() - start_time
            error_msg = self.client.get_error_message(e)
            print(f"✗ Error processing {table_name} - {variable_name}: {error_msg}")
            print(f"⏱️  Total processing time: {total_time:.2f}s")

            if self.debug:
                import traceback

                traceback.print_exc()

            return self._build_fallback_recommendations(
                table_name,
                variable_data,
                target_domain,
                f"Exception: {error_msg}",
            )

    # ==================== Hybrid processing (inter-table parallel, intra-table sequential) ====================

    def _process_table_sequentially(
        self,
        table_name: str,
        variables: list[tuple[dict[str, Any], list[dict[str, Any]]]],
        existing_recs: dict[str, list[dict[str, Any]]],
    ) -> tuple[dict[str, list[dict[str, Any]]], int]:
        """Process all variables in a single table sequentially.

        Each variable sees the completed mappings of preceding siblings,
        enabling domain and naming consistency within the table.

        Returns a tuple of (variable_name -> domain_recs mapping, processed count).
        """
        table_results: dict[str, list[dict[str, Any]]] = dict(existing_recs)
        processed = 0
        table_total = len(variables)

        completed_mappings: list[dict[str, Any]] = []
        for vname, recs in existing_recs.items():
            for rec in recs:
                entry = dict(rec)
                if "variable_name" not in entry:
                    entry["variable_name"] = vname
                completed_mappings.append(entry)

        for idx, (variable_data, all_variables) in enumerate(variables, 1):
            if self._cancel_event.is_set():
                break

            variable_name = variable_data.get("metadata_variable", "unknown")

            if variable_name in table_results:
                continue

            domain_recs = self.process_variable_pair(
                table_name=table_name,
                variable_data=variable_data,
                all_variables=all_variables,
                dry_run=False,
                pair_number=idx,
                total_pairs=table_total,
                completed_table_mappings=completed_mappings or None,
            )

            if domain_recs:
                table_results[variable_name] = domain_recs
                processed += 1
                for rec in domain_recs:
                    entry = dict(rec)
                    if "variable_name" not in entry:
                        entry["variable_name"] = variable_name
                    completed_mappings.append(entry)

        return table_results, processed

    def _process_mappings_hybrid(
        self,
        table_variable_pairs: list[tuple[str, dict[str, Any], list[dict[str, Any]]]],
        existing_recommendations: dict[str, dict[str, list[dict[str, Any]]]],
        progress_callback: ProgressCallback | None,
        total_pairs: int,
        skipped_count: int,
        output_file: str | None,
        save_frequency: int,
    ) -> tuple[dict[str, dict[str, list[dict[str, Any]]]], int, bool]:
        """Hybrid strategy: tables run in parallel, variables within each table run sequentially.

        This combines the speed benefit of parallelism with intra-table context
        accumulation so that later variables in the same table see earlier mappings.
        """
        all_table_recommendations = dict(existing_recommendations)
        total_processed = 0
        was_cancelled = False

        table_groups: dict[str, list[tuple[dict[str, Any], list[dict[str, Any]]]]] = {}
        for table_name, variable_data, all_variables in table_variable_pairs:
            table_groups.setdefault(table_name, []).append((variable_data, all_variables))

        tables_to_process: dict[str, list[tuple[dict[str, Any], list[dict[str, Any]]]]] = {}
        for table_name, var_list in table_groups.items():
            existing = all_table_recommendations.get(table_name, {})
            pending = [(vd, av) for vd, av in var_list if vd.get("metadata_variable", "unknown") not in existing]
            if pending:
                tables_to_process[table_name] = var_list

        if not tables_to_process:
            return all_table_recommendations, total_processed, was_cancelled

        num_tables = len(tables_to_process)
        total_pending = sum(len(v) for v in tables_to_process.values())
        workers = min(self.max_workers, num_tables)
        print(
            f"🔄 Hybrid mode: {num_tables} tables in parallel (max_workers={workers}), "
            f"{total_pending} variables sequential within each table"
        )

        results_lock = threading.Lock()
        completed_count = [skipped_count]

        def on_table_done(
            tbl: str,
            tbl_results: dict[str, list[dict[str, Any]]],
            tbl_processed: int,
        ) -> None:
            nonlocal total_processed
            with results_lock:
                all_table_recommendations[tbl] = tbl_results
                total_processed += tbl_processed
                completed_count[0] += tbl_processed

                if output_file and save_frequency and total_processed % save_frequency == 0:
                    self._save_progress_to_temp_file(output_file, all_table_recommendations)

            if progress_callback:
                should_continue = progress_callback(completed_count[0], total_pairs, tbl, None)
                if should_continue is False:
                    self._cancel_event.set()

        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_table = {}
            for table_name, var_list in tables_to_process.items():
                existing_for_table = all_table_recommendations.get(table_name, {})
                future = executor.submit(
                    self._process_table_sequentially,
                    table_name,
                    var_list,
                    existing_for_table,
                )
                future_to_table[future] = table_name

            for future in as_completed(future_to_table):
                if self._cancel_event.is_set():
                    was_cancelled = True
                    for f in future_to_table:
                        f.cancel()
                    break

                table_name = future_to_table[future]
                try:
                    tbl_results, tbl_processed = future.result(timeout=600)
                    on_table_done(table_name, tbl_results, tbl_processed)
                except Exception as e:
                    print(f"❌ Table {table_name} processing failed: {e}")
                    existing_for_table = all_table_recommendations.get(table_name, {})
                    on_table_done(table_name, existing_for_table, 0)

        self._cancel_event.clear()

        return all_table_recommendations, total_processed, was_cancelled

    # ==================== Coverage check ====================

    def _ensure_all_variables_covered(
        self, recommendations: list[dict[str, Any]], original_mappings: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Ensure every original variable has at least one recommendation record."""
        processed_df = None
        if hasattr(self, "_input_file") and self._input_file:
            input_path = Path(self._input_file)
            processed_excel_path = input_path.with_suffix(".xlsx")
            try:
                processed_df = pd.read_excel(processed_excel_path, dtype=str)
                processed_df.columns = [str(col).strip() for col in processed_df.columns]
                print(f"✓ 已加载 processed Excel 文件: {processed_excel_path} ({len(processed_df)} 行)")
            except FileNotFoundError:
                pass
            except Exception as e:
                print(f"⚠️ 无法加载 processed Excel 文件: {e}")

        covered_vars: set = set()
        table_rec_map: dict[str, dict[str, Any]] = {}

        for rec in recommendations:
            table_name = rec.get("table_name", "")
            table_rec_map[table_name] = rec

            domain_recs = rec.get("domain_recommendations", [])
            for domain_rec in domain_recs:
                variable_name = domain_rec.get("variable_name", "")
                if variable_name:
                    covered_vars.add((table_name, variable_name))

        missing_count = 0

        num_lookup: dict[tuple[str, str], str] = {}
        if processed_df is not None and "num" in processed_df.columns:
            for row in processed_df.to_dict("records"):
                key = (
                    str(row.get("metadata_table", "")).strip(),
                    str(row.get("metadata_variable", "")).strip(),
                )
                num_lookup[key] = str(row.get("num", "")).strip()

        for mapping in original_mappings:
            table_name = mapping.get("metadata_table", "")
            variable_name = mapping.get("metadata_variable", "")

            if not table_name or not variable_name:
                continue

            key = (table_name, variable_name)
            if key not in covered_vars:
                missing_count += 1

                annotation_table = mapping.get("annotation_table", "")

                num_value = num_lookup.get(key, "")

                fallback_domain = (
                    self._recommend_domain_from_annotation(annotation_table)
                    or self._map_table_to_domain(table_name)
                    or "UNMAPPED"
                )
                fallback_domain = str(fallback_domain or "UNMAPPED").strip().upper()

                unmapped_rec = {
                    "domain": fallback_domain,
                    "sdtm_variable": f"{fallback_domain}_{variable_name}_UNMAPPED".upper()
                    if fallback_domain != "UNMAPPED"
                    else f"{variable_name}_UNMAPPED".upper(),
                    "sdtm_variable_type": "standard",
                    "variable_name": variable_name,
                    "score": 0.0,
                    "priority": 999,
                    "source": "UNMAPPED",
                    "cascade_level": None,
                    "unmapped_reason": "Variable not processed or missing from recommendations",
                    "num": num_value,
                }

                if table_name in table_rec_map:
                    table_rec_map[table_name]["domain_recommendations"].append(unmapped_rec)
                else:
                    new_table_rec = {
                        "table_name": table_name,
                        "domain_recommendations": [unmapped_rec],
                        "original_mappings": [mapping],
                    }
                    recommendations.append(new_table_rec)
                    table_rec_map[table_name] = new_table_rec

                covered_vars.add(key)

        if missing_count > 0:
            print(f"⚠️ 发现 {missing_count} 个遗漏变量，已添加 UNMAPPED 占位记录")
        else:
            print(f"✓ 所有 {len(original_mappings)} 个原始变量均已有推荐记录")

        return recommendations

    # ==================== Knowledge base / RAG ====================

    def _get_knowledge_base_context(self, variable_data: dict[str, Any]) -> dict[str, Any]:
        """Query knowledge base for context about the mapping."""
        if not self.enable_knowledge_base or not self.kb_query:
            fallback_hint = self._recommend_domain_from_annotation(variable_data.get("annotation_table", ""))
            fallback_hint = fallback_hint.strip().upper() if isinstance(fallback_hint, str) else None
            return {"suggestions": [], "domain_context": {}, "domain_hint": fallback_hint}

        try:
            table_name = variable_data.get("annotation_table", "")
            variable_name = variable_data.get("metadata_variable", "")
            annotation_variable = variable_data.get("annotation_variable", "")

            domain_hint = (
                self._recommend_domain_from_annotation(table_name)
                or self._match_semantic_domain(annotation_variable)
                or self._map_table_to_domain(variable_data.get("metadata_table", ""), source="table")
            )
            domain_hint = domain_hint.strip().upper() if isinstance(domain_hint, str) else None

            kb_payload = dict(variable_data)
            if domain_hint:
                kb_payload["domain_hint"] = domain_hint

            kb_result = self.kb_query.query_variable_mapping_with_ecrf(kb_payload)

            if (
                kb_result.get("source") == "eCRF_direct_match"
                and kb_result.get("confidence", 0) >= self.kb_min_confidence
            ):
                if self.debug:
                    print(f"   ✅ Found eCRF match for {variable_name} (confidence: {kb_result['confidence']:.2f})")
                return {
                    "suggestions": kb_result.get("suggestions", []),
                    "domain_context": {},
                    "confidence": kb_result.get("confidence", 0.0),
                    "source": "eCRF_direct_match",
                    "domain_hint": domain_hint,
                }

            # Return KB results with any confidence level for cascade Level 2/3
            # (Level 2 threshold is checked in process_variable_pair)
            kb_suggestions = kb_result.get("suggestions", [])
            kb_confidence = kb_result.get("confidence", 0.0)
            kb_source = kb_result.get("source", "")

            if kb_suggestions:
                if self.debug:
                    print(
                        f"   🔍 Found KB suggestions for {variable_name} (confidence: {kb_confidence:.2f}, source: {kb_source})"
                    )
                return {
                    "suggestions": kb_suggestions,
                    "domain_context": {},
                    "confidence": kb_confidence,
                    "source": kb_source,
                    "domain_hint": domain_hint,
                }

            if self.debug:
                print(f"   🔍 No KB match for {variable_name}, will use RAG + LLM")

            return {
                "suggestions": [],
                "domain_context": {},
                "confidence": kb_confidence if kb_suggestions else 0.0,
                "source": kb_source or "LLM_reasoning",
                "domain_hint": domain_hint,
            }
        except Exception as e:
            if self.debug:
                print(f"   ⚠️ Knowledge base query failed: {e}")
            return {"suggestions": [], "domain_context": {}, "error": str(e)}

    def _create_enhanced_prompt(
        self,
        variable_data: dict[str, Any],
        kb_context: dict[str, Any],
        all_table_variables: list[dict[str, Any]] | None = None,
        completed_table_mappings: list[dict[str, Any]] | None = None,
    ) -> str:
        """Create enhanced prompt with knowledge base context.

        Args:
            variable_data: Current variable metadata.
            kb_context: Knowledge-base lookup results.
            all_table_variables: All sibling variables in the same CRF table.
            completed_table_mappings: Already-mapped sibling results (for
                table-level consistency in sequential mode).
        """
        if self.data_masker:
            variable_data = self.data_masker.mask_variable_data(variable_data)

        table_name = variable_data.get("annotation_table", "")
        variable_name = variable_data.get("metadata_variable", "")
        annotation_variable = variable_data.get("annotation_variable", "")

        candidate_domains = self._infer_candidate_domains(variable_data, kb_context)
        if self.debug and candidate_domains:
            print(f"   📚 Inferred domains: {', '.join(candidate_domains)}")

        base_prompt = self.prompt_generator.generate_variable_prompt(
            table_name=table_name,
            variable_name=variable_name,
            variable_data=variable_data,
            all_table_variables=all_table_variables or [variable_data],
            domain_hints=candidate_domains,
            completed_table_mappings=completed_table_mappings,
        )

        if kb_context.get("suggestions"):
            kb_section = "\n\n**Knowledge Base Context:**\n"
            kb_section += f"- Found {len(kb_context['suggestions'])} relevant mappings\n"

            for i, suggestion in enumerate(kb_context["suggestions"][:3], 1):
                kb_section += f"- Suggestion {i}: {suggestion.get('domain', 'N/A')} -> {suggestion.get('sdtm_variable', 'N/A')} (confidence: {suggestion.get('confidence', 0.0):.2f})\n"

            if kb_context.get("domain_context"):
                domain_info = kb_context["domain_context"]
                kb_section += f"- Domain context: {domain_info.get('domain', 'N/A')} ({domain_info.get('description', 'No description')})\n"

            base_prompt += kb_section

        if kb_context.get("domain_hint"):
            base_prompt += f"\n\n**⚠️ Domain Hint (HIGHEST PRIORITY):** The target SDTM domain is `{kb_context['domain_hint']}` (inferred from annotation table). Use `{kb_context['domain_hint']}` domain variables."

        # ── KB-derived hints (TESTCD pre-fill, disambiguation, domain examples) ──
        if self.kb_hints is not None:
            primary_domain = candidate_domains[0] if candidate_domains else ""
            hints_section = self.kb_hints.build_prompt_section(
                metadata_variable=variable_name,
                annotation_variable=annotation_variable,
                annotation_table=table_name,
                domain=primary_domain,
                n_examples=4,
            )
            if hints_section:
                base_prompt += hints_section
                if self.debug:
                    print(f"   🧠 KB hints injected for {variable_name} (domain={primary_domain})")

        return base_prompt

    def _make_rag_query(self, variable_data: dict[str, Any]) -> str:
        """Build enhanced RAG query using multiple context fields."""
        parts = []

        table_label = variable_data.get("annotation_table", "")
        domain_hint = self._recommend_domain_from_annotation(table_label) if table_label else None
        if domain_hint:
            parts.append(str(domain_hint))

        if table_label:
            parts.append(str(table_label))

        variable_label = variable_data.get("annotation_variable", "")
        if variable_label:
            parts.append(str(variable_label))

        query = " ".join(parts).strip()

        if self.debug:
            print(f"   🔍 RAG query: {query}")

        return query

    def _get_rag_contexts(
        self,
        variable_data: dict[str, Any],
        verbose: bool = False,
    ) -> tuple[list[Any], dict[str, Any]]:
        if not getattr(self, "rag_augmenter", None):
            return [], {"query": "", "top_score": 0.0, "total": 0}

        query = self._make_rag_query(variable_data)
        if not query:
            return [], {"query": "", "top_score": 0.0, "total": 0}

        variable_name = variable_data.get("metadata_variable") or variable_data.get("annotation_variable") or "unknown"

        use_verbose = verbose or getattr(self, "rag_verbose", False) or self.debug
        contexts = self.rag_augmenter.retrieve(
            query,
            verbose=use_verbose,
            variable_name=variable_name,
        )
        top_score = contexts[0].effective_score if contexts else 0.0
        threshold = self.rag_min_score if self.rag_min_score is not None else 0.0
        high_conf = [ctx for ctx in contexts if ctx.effective_score >= threshold]

        info = {
            "query": query,
            "top_score": top_score,
            "total": len(contexts),
            "used": len(high_conf),
        }
        return high_conf, info

    def _audit_mapping_result(
        self,
        variable_data: dict[str, Any],
        domain_recs: list[dict[str, Any]],
        cascade_level: int,
        processing_time_ms: float | None = None,
    ) -> None:
        """Log mapping result to the audit trail (if audit is enabled)."""
        if not domain_recs:
            return
        for rec in domain_recs:
            rec["cascade_level"] = cascade_level
        if not self.audit_logger:
            return
        for rec in domain_recs:
            validation_issues = []
            if rec.get("invalid_domain_corrected"):
                validation_issues.append("invalid_domain_corrected")
            if rec.get("variable_name_corrected"):
                validation_issues.append("variable_name_corrected")
            if rec.get("variable_name_truncated"):
                validation_issues.append("variable_name_truncated")
            if rec.get("domain_prefix_mismatch"):
                validation_issues.append("domain_prefix_mismatch")
            if rec.get("non_standard_variable"):
                validation_issues.append("non_standard_variable")
            if rec.get("auto_corrected_to_supp"):
                validation_issues.append("auto_corrected_to_supp")
            self.audit_logger.log_mapping(
                variable_data=variable_data,
                result=rec,
                cascade_level=cascade_level,
                validation_issues=validation_issues or None,
                processing_time_ms=processing_time_ms,
            )

    # Penalty applied to RAG results whose domain differs from target_domain.
    _RAG_DOMAIN_MISMATCH_PENALTY: float = 0.5

    def _rag_contexts_to_recommendations(
        self,
        rag_contexts: list[Any],
        table_name: str,
        variable_name: str,
        target_domain: str | None,
    ) -> list[dict[str, Any]]:
        """Convert RAG context results into recommendation dictionaries.

        This enables Level 3 of the cascade: when RAG returns high-confidence
        matches, we can adopt them directly without calling the LLM.

        Domain consistency: instead of discarding results whose domain differs
        from ``target_domain``, we apply a score penalty so they rank lower
        than domain-consistent results while still being available as fallback.
        """
        recommendations: list[dict[str, Any]] = []
        seen_keys: set = set()

        for ctx in rag_contexts:
            meta = ctx.metadata or {}
            score = float(ctx.effective_score)

            domain = str(meta.get("domain", meta.get("sdtm_domain", ""))).strip().upper()
            sdtm_variable = str(meta.get("sdtm_variable", meta.get("variable", ""))).strip()

            if not domain or not sdtm_variable:
                continue

            # Domain consistency weighting: penalise rather than discard
            domain_consistent = True
            if target_domain and domain != target_domain:
                is_supp_match = domain.startswith("SUPP") and domain[4:] == target_domain
                if not is_supp_match:
                    score *= self._RAG_DOMAIN_MISMATCH_PENALTY
                    domain_consistent = False

            testcd = str(meta.get("testcd", meta.get("sdtm_testcd", ""))).strip()
            supp_variable = str(meta.get("supp_variable", meta.get("sdtm_suppvar", ""))).strip()

            # Dedup key
            dedup_key = (domain, sdtm_variable, testcd, supp_variable)
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)

            rec = {
                "domain": domain,
                "sdtm_variable": sdtm_variable,
                "sdtm_variable_type": "standard",
                "score": score,
                "variable_name": variable_name,
                "source": "RAG",
            }
            if not domain_consistent:
                rec["domain_mismatch_penalized"] = True
            if testcd:
                rec["testcd"] = testcd
            if supp_variable:
                rec["supp_variable"] = supp_variable

            recommendations.append(rec)

        return recommendations

    def _validate_with_knowledge_base(
        self, llm_response: dict[str, Any], variable_data: dict[str, Any], kb_context: dict[str, Any]
    ) -> dict[str, Any]:
        """Validate LLM response with knowledge base (top recommendation only)."""
        if not self.enable_knowledge_base or not self.kb_query:
            return llm_response

        if os.getenv("KB_SKIP_VALIDATION", "0").lower() in ("1", "true", "yes"):
            return llm_response

        try:
            table_recommendations = llm_response.get("table_recommendations", [])

            for table_rec in table_recommendations:
                domain_recommendations = table_rec.get("domain_recommendations", [])

                if domain_recommendations:
                    sorted_recs = sorted(domain_recommendations, key=lambda x: x.get("score", 0), reverse=True)
                    top_rec = sorted_recs[0] if sorted_recs else None

                    if top_rec:
                        validation_result = self.kb_query.validate_mapping_suggestion(
                            suggested_mapping={
                                "domain": top_rec.get("domain", ""),
                                "sdtm_variable": top_rec.get("sdtm_variable", ""),
                                "variable_type": top_rec.get("sdtm_variable_type", ""),
                            },
                            query_context=f"Table: {variable_data.get('annotation_table', '')}, Variable: {variable_data.get('metadata_variable', '')}",
                        )

                        top_rec["kb_validated"] = validation_result.get("is_valid", False)
                        top_rec["kb_validation"] = {
                            "validation_score": validation_result.get("validation_score", 0.0),
                            "validation_reason": validation_result.get("issues", []),
                            "kb_suggestions": validation_result.get("suggestions", []),
                        }

                    for rec in sorted_recs[1:]:
                        rec["kb_validated"] = None

            return llm_response

        except Exception as e:
            if self.debug:
                print(f"   ⚠️ Knowledge base validation failed: {e}")
            return llm_response

    # ==================== Batch processing orchestration ====================

    def _determine_save_frequency(self, total_pairs: int) -> int:
        if self.save_frequency is not None:
            return max(1, self.save_frequency)
        if total_pairs <= 10:
            return 2
        adaptive = max(2, total_pairs // 10)
        return min(50, adaptive)

    def process_mappings(
        self,
        mappings: list[dict[str, Any]],
        dry_run: bool = False,
        resume: bool = False,
        output_file: str | None = None,
        progress_callback: ProgressCallback | None = None,
        input_file: str | None = None,
    ) -> list[dict[str, Any]]:
        """Process all mappings to generate SDTM recommendations."""
        self._input_file = input_file
        if output_file:
            model_suffix = f"_{self.client.get_sanitized_model_name(self.model_name)}"
            if model_suffix not in output_file:
                output_file = f"{output_file}{model_suffix}"

        table_groups: dict[str, list[dict[str, Any]]] = {}
        for mapping in mappings:
            table = mapping.get("metadata_table", "")
            if not table:
                continue
            if table not in table_groups:
                table_groups[table] = []
            table_groups[table].append(
                {
                    "metadata_variable": mapping.get("metadata_variable", ""),
                    "annotation_variable": mapping.get("annotation_variable", ""),
                    "annotation_table": mapping.get("annotation_table", ""),
                    "metadata_table": mapping.get("metadata_table", ""),
                }
            )

        table_variable_pairs = []
        for table, variables in table_groups.items():
            for variable in variables:
                table_variable_pairs.append((table, variable, variables))

        # Precompute exact matches via single KB merge
        precomputed_exact_count = 0
        if self.enable_knowledge_base and self.kb_query and hasattr(self.kb_query, "precompute_exact_matches"):
            bulk_precompute_payload: list[dict[str, Any]] = []
            for table_name, variable_data, _ in table_variable_pairs:
                payload = {
                    "metadata_variable": variable_data.get("metadata_variable", ""),
                    "annotation_variable": variable_data.get("annotation_variable", ""),
                    "metadata_table": variable_data.get("metadata_table", table_name),
                    "annotation_table": variable_data.get("annotation_table", table_name),
                }
                bulk_precompute_payload.append(payload)
            if bulk_precompute_payload:
                try:
                    precomputed_exact_count = self.kb_query.precompute_exact_matches(bulk_precompute_payload)
                except Exception as exc:
                    if self.debug:
                        print(f"⚠️ Failed to precompute exact KB matches: {exc}")
                else:
                    if precomputed_exact_count:
                        print(f"🔗 Identified {precomputed_exact_count} direct KB matches before processing")

        # Precompute RAG query embeddings (batch API call)
        if (
            self.enable_knowledge_base
            and getattr(self, "rag_augmenter", None)
            and hasattr(self.rag_augmenter, "precompute_query_embeddings")
            and not dry_run
        ):
            rag_queries: list[str] = []
            for _table_name, variable_data, _ in table_variable_pairs:
                query = self._make_rag_query(variable_data)
                if query:
                    rag_queries.append(query)

            if rag_queries:
                try:
                    cached_count, unique_count = self.rag_augmenter.precompute_query_embeddings(rag_queries)
                    if cached_count > 0:
                        print(f"📦 Precomputed {cached_count}/{unique_count} unique RAG query vectors")
                except Exception as exc:
                    if self.debug:
                        print(f"⚠️ Failed to precompute RAG query embeddings: {exc}")

        overall_start_time = time.time()

        existing_recommendations: dict[str, dict[str, list[dict[str, Any]]]] = {}
        if resume and output_file:
            existing_recommendations = self._get_existing_recommendations(output_file)

            existing_count = 0
            for table in existing_recommendations:
                existing_count += len(existing_recommendations[table])

            if existing_count > 0:
                total_pairs = len(table_variable_pairs)
                expected_to_process = total_pairs - existing_count
                if dry_run:
                    print(f"Found {existing_count} existing variable recommendations")
                    print(f"Dry run will preview prompts for {expected_to_process} remaining pairs")
                else:
                    print(f"Found {existing_count} existing variable recommendations that will be reused")
                    print(f"Will process {expected_to_process} remaining pairs")

        if dry_run and not resume:
            print(
                f"\nDRY RUN MODE: Will preview {len(table_variable_pairs)} table-variable pairs without making API calls"
            )
        elif not resume:
            print(f"Processing {len(table_variable_pairs)} table-variable pairs individually")

        all_table_recommendations = existing_recommendations or {}
        total_pairs = len(table_variable_pairs)
        processed_count = 0
        skipped_count = 0
        save_frequency = self._determine_save_frequency(total_pairs)

        if resume and existing_recommendations:
            skipped_count = sum(len(vars) for vars in existing_recommendations.values())
            if progress_callback:
                progress_callback(skipped_count, total_pairs, None, None)
        else:
            if progress_callback:
                progress_callback(0, total_pairs, None, None)

        was_cancelled = False

        if self.enable_parallel and not dry_run and (total_pairs - skipped_count) > 1:
            all_table_recommendations, processed_count, was_cancelled = self._process_mappings_hybrid(
                table_variable_pairs=table_variable_pairs,
                existing_recommendations=all_table_recommendations,
                progress_callback=progress_callback,
                total_pairs=total_pairs,
                skipped_count=skipped_count,
                output_file=output_file,
                save_frequency=save_frequency,
            )

            if was_cancelled and output_file and processed_count > 0:
                self._save_progress_to_temp_file(output_file, all_table_recommendations)
                print("Progress saved to temporary file before cancellation")
        else:
            if not dry_run and (total_pairs - skipped_count) > 1:
                print("\n🔄 Using sequential processing mode (with table-level context)")

            # Track completed mappings per table for table-level context passing.
            # In sequential mode, each new variable in a table sees all previously
            # completed siblings, which greatly improves domain/naming consistency.
            _table_completed: dict[str, list[dict[str, Any]]] = {}

            # Seed from existing recommendations (resume scenario)
            for tbl, var_dict in all_table_recommendations.items():
                for vname, recs in var_dict.items():
                    for rec in recs:
                        entry = dict(rec)
                        if "variable_name" not in entry:
                            entry["variable_name"] = vname
                        _table_completed.setdefault(tbl, []).append(entry)

            for i, (table_name, variable_data, all_variables) in enumerate(table_variable_pairs):
                pair_number = i + 1
                variable_name = variable_data.get("metadata_variable", "unknown")

                if progress_callback:
                    total_completed = skipped_count + processed_count
                    should_continue = progress_callback(total_completed, total_pairs, table_name, variable_name)
                    if should_continue is False:
                        print(f"\n⛔ Processing cancelled by user at pair {pair_number}/{total_pairs}")
                        was_cancelled = True
                        if not dry_run and output_file and processed_count > 0:
                            self._save_progress_to_temp_file(output_file, all_table_recommendations)
                            print("Progress saved to temporary file before cancellation")
                        break

                if (
                    resume
                    and table_name in all_table_recommendations
                    and variable_name in all_table_recommendations[table_name]
                ):
                    skip_message = f"Skipping pair {pair_number} of {total_pairs}: {table_name} - {variable_name} (already processed)"
                    if dry_run:
                        print(f"\n[DRY RUN] {skip_message}")
                    else:
                        print(skip_message)
                    continue

                completed_for_table = _table_completed.get(table_name, [])

                domain_recs = self.process_variable_pair(
                    table_name=table_name,
                    variable_data=variable_data,
                    all_variables=all_variables,
                    dry_run=dry_run,
                    pair_number=pair_number,
                    total_pairs=total_pairs,
                    completed_table_mappings=completed_for_table or None,
                )

                if domain_recs:
                    if table_name not in all_table_recommendations:
                        all_table_recommendations[table_name] = {}

                    all_table_recommendations[table_name][variable_name] = domain_recs
                    processed_count += 1

                    # Accumulate completed mappings for subsequent siblings
                    for rec in domain_recs:
                        entry = dict(rec)
                        if "variable_name" not in entry:
                            entry["variable_name"] = variable_name
                        _table_completed.setdefault(table_name, []).append(entry)

                    if progress_callback:
                        total_completed = skipped_count + processed_count
                        should_continue = progress_callback(total_completed, total_pairs, table_name, variable_name)
                        if should_continue is False:
                            print(f"\n⛔ Processing cancelled by user after pair {pair_number}/{total_pairs}")
                            was_cancelled = True
                            if not dry_run and output_file:
                                self._save_progress_to_temp_file(output_file, all_table_recommendations)
                                print("Progress saved to temporary file before cancellation")
                            break

                    if save_frequency and not dry_run and output_file and processed_count % save_frequency == 0:
                        self._save_progress_to_temp_file(output_file, all_table_recommendations)
                        print(
                            f"Progress saved to temporary file (processed {processed_count}/{total_pairs - skipped_count} pairs)"
                        )

        print("\nProcessing summary:")
        print(f"- Total pairs: {total_pairs}")
        print(f"- Processed: {processed_count}")
        print(f"- Skipped (already processed): {skipped_count}")
        if was_cancelled:
            remaining = total_pairs - skipped_count - processed_count
            print(f"- Remaining (not processed due to cancellation): {remaining}")
            print("- Status: Cancelled by user - progress saved for resume")

        if dry_run and resume:
            print(
                f"\nIn a real run with --resume, {skipped_count} pairs would be skipped and {processed_count} pairs would be processed."
            )

        all_recommendations = []
        for table, variable_recs in all_table_recommendations.items():
            all_variables = table_groups.get(table, [])

            all_domain_recs = []
            for _variable_name, domain_recs in variable_recs.items():
                all_domain_recs.extend(domain_recs)

            all_domain_recs.sort(key=lambda x: x.get("score", 0), reverse=True)

            table_recommendation = {
                "table_name": table,
                "domain_recommendations": all_domain_recs,
                "original_mappings": all_variables,
            }

            all_recommendations.append(table_recommendation)

            if not dry_run or self.debug:
                print(f"✓ Completed processing for table {table} with {len(all_variables)} variables")

        overall_time = time.time() - overall_start_time

        if not dry_run and output_file and processed_count > 0 and not was_cancelled:
            self._save_progress_to_temp_file(output_file, all_table_recommendations)
            print("Final progress saved to temporary file")

        if progress_callback and not was_cancelled:
            total_completed = skipped_count + processed_count
            progress_callback(total_completed, total_pairs, None, None)

        if not dry_run:
            print(f"\n⏱️  Overall processing time: {overall_time:.2f}s")
            if processed_count > 0:
                avg_time_per_pair = overall_time / processed_count
                print(f"⏱️  Average time per pair: {avg_time_per_pair:.2f}s")

        # Run MappingCritic for batch-level consistency validation
        if not dry_run and all_recommendations:
            all_domain_recs_flat: list[dict[str, Any]] = []
            all_original_mappings_flat: list[dict[str, Any]] = []
            for table_rec in all_recommendations:
                dr = table_rec.get("domain_recommendations", [])
                all_domain_recs_flat.extend(cast(list[dict[str, Any]], dr))
                om = table_rec.get("original_mappings", [])
                all_original_mappings_flat.extend(cast(list[dict[str, Any]], om))
            if all_domain_recs_flat:
                from src.processors.mapping_critic import MappingCritic

                critic = MappingCritic(debug=self.debug)
                consistency_issues = critic.criticize(
                    all_domain_recs_flat,
                    original_mappings=all_original_mappings_flat or None,
                )
                if consistency_issues:
                    error_count = sum(1 for i in consistency_issues if i.severity == "error")
                    warn_count = sum(1 for i in consistency_issues if i.severity == "warning")
                    print(
                        f"\n[MappingCritic] Consistency check: {error_count} errors, {warn_count} warnings, {len(consistency_issues) - error_count - warn_count} info"
                    )
                    for issue in consistency_issues:
                        if issue.severity == "error":
                            print(f"  ERROR: {issue.description}")
                        elif issue.severity == "warning":
                            print(f"  WARNING: {issue.description}")
                    # Attach issues to the return value for downstream consumers
                    for table_rec in all_recommendations:
                        table_rec["consistency_issues"] = [i.to_dict() for i in consistency_issues]

            # Log batch summary to audit trail
            if self.audit_logger and all_domain_recs_flat:
                kb_hits = sum(1 for r in all_domain_recs_flat if str(r.get("source", "")).upper() == "KB")
                rag_hits = sum(1 for r in all_domain_recs_flat if str(r.get("source", "")).upper() == "RAG")
                llm_calls = sum(1 for r in all_domain_recs_flat if str(r.get("source", "")).upper() == "LLM")
                scores = [r.get("score", 0.0) for r in all_domain_recs_flat if isinstance(r.get("score"), (int, float))]
                avg_conf = sum(scores) / len(scores) if scores else 0.0
                self.audit_logger.log_batch_summary(
                    total_variables=len(all_domain_recs_flat),
                    kb_hits=kb_hits,
                    rag_hits=rag_hits,
                    llm_calls=llm_calls,
                    total_time_ms=overall_time * 1000,
                    avg_confidence=avg_conf,
                )

        return all_recommendations

    # ==================== Output / Save ====================

    def save_recommendations(
        self,
        recommendations: list[dict[str, Any]],
        output_file: str,
        resume: bool = False,
        original_mappings: list[dict[str, Any]] | None = None,
        input_file: str | None = None,
    ) -> list[dict[str, Any]]:
        """Save the SDTM recommendations to JSON + Excel files."""
        if input_file:
            self._input_file = input_file

        model_suffix = f"_{self.client.get_sanitized_model_name(self.model_name)}"
        if model_suffix not in output_file:
            output_file = f"{output_file}{model_suffix}"

        if original_mappings:
            recommendations = self._ensure_all_variables_covered(recommendations, original_mappings)

        json_output = output_file + ".json"
        with open(json_output, "w", encoding="utf-8") as f:
            json.dump(recommendations, f, ensure_ascii=False, indent=2)

        excel_rows = []
        for rec in recommendations:
            table_name = rec.get("table_name", "")
            orig_mappings = rec.get("original_mappings", [])

            annotation_table = ""
            if orig_mappings:
                annotation_table = orig_mappings[0].get("annotation_table", "")

            domain_recs = rec.get("domain_recommendations", [])
            for domain_rec in domain_recs:
                domain = domain_rec.get("domain", "")
                sdtm_var = domain_rec.get("sdtm_variable", "")
                score = domain_rec.get("score", "")
                variable_name = domain_rec.get("variable_name", "")
                var_type = str(domain_rec.get("sdtm_variable_type", "")).lower()
                supp_var = domain_rec.get("supp_variable", "")
                supp_ds = domain_rec.get("supp_dataset", "")

                search_key = variable_name if variable_name else (supp_var if var_type == "supp" else "")
                source_mapping: dict[str, Any] = next(
                    (v for v in orig_mappings if v.get("metadata_variable") == search_key), {}
                )

                if var_type == "supp":
                    qnam_value = supp_var or ""
                    representative_var = f"QVAL when QNAM={qnam_value}"

                    if domain == "FA":
                        parent_testcd = self._find_parent_fatestcd(variable_name, domain_recs, orig_mappings)
                        if parent_testcd:
                            representative_var += f" when FATESTCD={parent_testcd}"
                elif sdtm_var and sdtm_var.endswith("ORRES"):
                    testcd_value = domain_rec.get("testcd", "")

                    if not testcd_value:
                        annotation_var = source_mapping.get("annotation_variable", "") if source_mapping else ""
                        testcd_value = self._infer_testcd(
                            variable_name=variable_name, annotation_variable=annotation_var, domain=domain
                        )

                    if testcd_value:
                        domain_prefix = domain[:2] if len(domain) >= 2 else domain
                        representative_var = f"{sdtm_var} when {domain_prefix}TESTCD={testcd_value}"
                    else:
                        representative_var = sdtm_var
                else:
                    representative_var = sdtm_var

                domain_display = domain
                if var_type == "supp":
                    domain_upper = str(domain).upper()
                    if domain_upper:
                        domain_display = domain_upper
                    elif supp_ds:
                        supp_ds_upper = str(supp_ds).upper()
                        if supp_ds_upper.startswith("SUPP") and len(supp_ds_upper) > 4:
                            domain_display = supp_ds_upper[4:]
                        else:
                            domain_display = supp_ds_upper
                    else:
                        domain_display = "XX"

                if not source_mapping and var_type == "supp" and supp_var:
                    source_mapping = next((v for v in orig_mappings if v.get("metadata_variable") == supp_var), {})

                if not source_mapping:
                    metadata_variable = variable_name if variable_name else "UNKNOWN_VAR"
                    annotation_variable = variable_name if variable_name else "UNKNOWN_VAR"
                else:
                    metadata_variable = source_mapping.get("metadata_variable", search_key or supp_var or "UNKNOWN")
                    annotation_variable = source_mapping.get("annotation_variable", "")

                if not metadata_variable or metadata_variable == "UNKNOWN":
                    metadata_variable = variable_name if variable_name else "UNKNOWN_VAR"

                if not annotation_variable:
                    annotation_variable = variable_name if variable_name else "UNKNOWN_VAR"

                row = {
                    "metadata_table": table_name,
                    "metadata_variable": metadata_variable,
                    "annotation_table": annotation_table,
                    "annotation_variable": annotation_variable,
                    "SDTM_Domain": domain_display,
                    "SDTM_Variable": representative_var,
                    "Score": score,
                    "Source": _recommendation_source_excel_label(domain_rec.get("source", "")),
                    "IG34_Check": _compute_ig34_check(domain_rec),
                }
                excel_rows.append(row)

        excel_output = output_file + ".xlsx"
        from src.processors.project_ingest import load_session_reference_kb

        reference_kb = load_session_reference_kb(self.session_id) if self.session_id else None
        excel_rows = attach_reference_diff(excel_rows, reference_kb)
        if excel_rows:
            df = pd.DataFrame(excel_rows)

            merged_df = self._merge_with_ecrf_sheet(df)

            # Resolve consistency_issues once (used by both merged and fallback paths)
            consistency_issues: list[dict[str, Any]] = []
            if recommendations and isinstance(recommendations, list) and len(recommendations) > 0:
                first = recommendations[0]
                if isinstance(first, dict):
                    consistency_issues = first.get("consistency_issues") or []

            if merged_df is not None:
                # Sort by row-order key: bioknow uses '编号', taimei uses 'num'
                sort_col = "编号" if "编号" in merged_df.columns else ("num" if "num" in merged_df.columns else None)
                if sort_col:
                    merged_df[sort_col] = pd.to_numeric(merged_df[sort_col], errors="coerce")
                    merged_df = merged_df.sort_values(by=sort_col).reset_index(drop=True)
                merged_df.to_excel(excel_output, index=False)
                # Phase 2: attach diff + styling + consistency sheet.
                try:
                    for row in excel_rows:
                        row.setdefault("Critic_Flag", "")

                    from src.processors.excel_styler import style_results_workbook

                    style_results_workbook(Path(excel_output), consistency_issues=consistency_issues)
                    logger.info(
                        "Applied Phase 2 Excel styling: score/source/diff + %d consistency issues",
                        len(consistency_issues),
                    )
                except Exception as styler_exc:
                    logger.warning("Excel styling skipped due to error: %s", styler_exc)
                logger.info(
                    "Recommendations saved to %s with %d rows (merged with eCRF sheet)", excel_output, len(merged_df)
                )
            else:
                df = df.sort_values(by=["metadata_table", "metadata_variable", "Score"], ascending=[True, True, False])
                df.to_excel(excel_output, index=False)
                # Phase 2: attach diff + styling + consistency sheet.
                try:
                    for row in excel_rows:
                        row.setdefault("Critic_Flag", "")

                    from src.processors.excel_styler import style_results_workbook

                    style_results_workbook(Path(excel_output), consistency_issues=consistency_issues)
                    logger.info(
                        "Applied Phase 2 Excel styling: score/source/diff + %d consistency issues",
                        len(consistency_issues),
                    )
                except Exception as styler_exc:
                    logger.warning("Excel styling skipped due to error: %s", styler_exc)
                logger.info("Recommendations saved to %s with %d rows", excel_output, len(excel_rows))
        else:
            empty_df = pd.DataFrame(
                columns=[
                    "编号",
                    "表名",
                    "表",
                    "标准表",
                    "分类",
                    "引用表",
                    "变量名",
                    "变量",
                    "组件类型",
                    "编码名称",
                    "SAS导出格式",
                    "metadata_table",
                    "annotation_table",
                    "SDTM_Domain",
                    "SDTM_Variable",
                    "Score",
                    "Source",
                ]
            )
            empty_df.to_excel(excel_output, index=False)
            # Phase 2: attach diff + styling + consistency sheet.
            try:
                for row in excel_rows:
                    row.setdefault("Critic_Flag", "")

                from src.processors.excel_styler import style_results_workbook

                style_results_workbook(Path(excel_output), consistency_issues=consistency_issues)
                logger.info(
                    "Applied Phase 2 Excel styling: score/source/diff + %d consistency issues",
                    len(consistency_issues),
                )
            except Exception as styler_exc:
                logger.warning("Excel styling skipped due to error: %s", styler_exc)
            logger.info("Created empty Excel file at %s (no recommendations found)", excel_output)

        logger.info("Recommendations saved to %s", json_output)

        temp_file = output_file + ".tmp.json"
        try:
            os.remove(temp_file)
            logger.info("Temporary file removed after successful completion")
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.warning("Could not remove temporary file %s: %s", temp_file, e)

        return excel_rows

    # ==================== Utilities ====================

    def switch_language(self, language: str) -> None:
        """Switch the language for prompt generation."""
        if self.prompt_generator:
            self.prompt_generator.switch_language(language)
            self.language = language
            print(f"Language switched to: {language}")
        else:
            print("Warning: Prompt generator not initialized")

    def get_available_domains(self) -> dict[str, str]:
        """Get available SDTM domains for reference."""
        if self.prompt_generator:
            return self.prompt_generator.get_available_domains()
        else:
            print("Warning: Prompt generator not initialized")
            return {}
