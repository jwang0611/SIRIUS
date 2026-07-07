"""
Post-processing Mixin
Normalises and enriches LLM / KB domain recommendations.
"""

import logging
import os
import re
from typing import Any

from src.config.domain_semantic_map import is_valid_domain
from src.processors.deterministic_validator import DeterministicValidator
from src.processors.mixin_typing import SDTMProcessorHost

logger = logging.getLogger(__name__)

_MULTI_DOMAIN_SPLIT = re.compile(r"[|/;]")
_WHEN_CLAUSE_RE = re.compile(r"\s+when\s+", re.IGNORECASE)

try:
    _KB_MAX_RECOMMENDATIONS = max(1, int(os.getenv("KB_MAX_RECOMMENDATIONS", "5")))
except ValueError:
    _KB_MAX_RECOMMENDATIONS = 5


class PostprocessMixin:
    """Methods for normalising, validating, and enriching recommendations."""

    # ------------------------------------------------------------------
    # Expected instance attributes (initialised by the host class):
    #   self.debug : bool
    #   self.domain_override_threshold : float
    #   _STANDARD_VARS_BY_DOMAIN (module-level dict in sdtm_processor.py)
    # ------------------------------------------------------------------

    def _normalize_domain_recs(
        self: SDTMProcessorHost,
        table_name: str,
        variable_name: str,
        domain_recs: list[dict[str, Any]],
        target_domain: str | None = None,
        enforce_domain: bool = True,
    ) -> list[dict[str, Any]]:
        """
        Normalize recommendations to ensure SUPP variables are correctly marked and populated.
        """
        from src.processors.sdtm_processor import _STANDARD_VARS_BY_DOMAIN

        qvars = {"QNAM", "QLABEL", "QVAL", "QORIG", "QEVAL", "IDVAR", "IDVARVAL"}
        target_upper = target_domain.strip().upper() if isinstance(target_domain, str) else None
        if enforce_domain and target_upper:
            filtered = []
            for rec in domain_recs:
                domain_value = str(rec.get("domain", "")).upper()
                tokens = re.split(r"[|/,\s]+", domain_value)
                normalized_tokens = []
                for token in tokens:
                    token = token.strip()
                    if not token:
                        continue
                    normalized_tokens.append(token)
                    if token.startswith("SUPP") and len(token) > 4:
                        normalized_tokens.append(token[4:])
                if target_upper in normalized_tokens:
                    filtered.append(rec)
            if not filtered:
                if self.debug:
                    print(
                        f"[DomainFilter] {table_name}.{variable_name}: no recommendations returned for domain {target_upper}"
                    )
                return []
            domain_recs = filtered
        elif not enforce_domain and self.debug:
            print(
                f"[DomainOverride] {table_name}.{variable_name}: allowing domain {target_upper or 'ANY'} with high-confidence score"
            )

        normalized: list[dict[str, Any]] = []
        comment_keywords = [
            "备注",
            "说明",
            "注释",
            "评论",
            "自由文本",
            "其他",
            "其他说明",
            "其他指定",
            "comment",
            "comments",
            "note",
            "notes",
            "remark",
            "remarks",
            "other specify",
            "other",
        ]
        varname_lower = str(variable_name).lower()

        sorted_recs = sorted(domain_recs, key=lambda x: x.get("score", 0), reverse=True)

        if not sorted_recs:
            return normalized

        recs_to_process = sorted_recs[:_KB_MAX_RECOMMENDATIONS]

        if len(domain_recs) > 1:
            print(
                f"📊 Processing {len(recs_to_process)}/{len(domain_recs)} recommendations for {variable_name} (scores: {[r.get('score', 0) for r in recs_to_process]})"
            )

        standard_suffixes = {
            "TRT",
            "DECOD",
            "STDTC",
            "ENDTC",
            "DOSE",
            "DOSU",
            "DOSFRM",
            "DOSFRQ",
            "ROUTE",
            "INDC",
            "CAT",
            "SCAT",
            "TERM",
            "PRESP",
            "OCCUR",
            "STAT",
            "LOC",
            "ORRES",
            "ORRESU",
            "STRESC",
            "STRESN",
            "STRESU",
            "DTC",
            "SEQ",
            "SPID",
            "GRPID",
            "REFID",
            "LNKID",
            "TESTCD",
            "TEST",
            "SER",
            "REL",
            "ACN",
            "OUT",
            "TOXGR",
            "ONGO",
            "BODSYS",
            "SOC",
        }

        def is_standard_variable_name(name: str) -> bool:
            if not name or len(name) < 3:
                return False
            upper = name.upper()
            if len(upper) >= 4 and upper[:2].isalpha():
                suffix = upper[2:]
                if suffix in standard_suffixes or any(suffix.endswith(s) for s in standard_suffixes):
                    return True
            return False

        for rec in recs_to_process:
            rec = dict(rec)
            var_type = str(rec.get("sdtm_variable_type", "")).lower()
            domain = str(rec.get("domain", "")).upper()
            sdtm_var = rec.get("sdtm_variable")
            supp_var = rec.get("supp_variable")
            supp_ds = rec.get("supp_dataset")

            is_from_kb = rec.get("kb_validated") or rec.get("source") == "KB"

            original_kb_domain = rec.get("domain", "") if is_from_kb else None
            original_kb_sdtm_var = rec.get("sdtm_variable", "") if is_from_kb else None

            # Decompose composite "when" clauses that some LLMs embed in
            # sdtm_variable (e.g. "FAORRES when FATESTCD=THDIAG").
            # For KB results, only skip decomposition when the sdtm_variable
            # contains "|" — indicating a multi-domain/multi-variable pattern
            # (e.g. "ECSTDTC when ECMOOD=已执行|EXSTDTC") that must be preserved.
            # Simple KB patterns like "QVAL when QNAM=EXREAS" still need decomposition.
            _has_pipe = isinstance(sdtm_var, str) and "|" in sdtm_var
            if not (is_from_kb and _has_pipe) and isinstance(sdtm_var, str) and _WHEN_CLAUSE_RE.search(sdtm_var):
                parts = _WHEN_CLAUSE_RE.split(sdtm_var)
                rec["sdtm_variable"] = parts[0].strip()
                sdtm_var = rec["sdtm_variable"]
                for clause in parts[1:]:
                    clause = clause.strip()
                    if "=" not in clause:
                        continue
                    key, _, val = clause.partition("=")
                    key_upper = key.strip().upper()
                    val = val.strip()
                    if key_upper.endswith("TESTCD") and not rec.get("testcd"):
                        rec["testcd"] = val
                    elif key_upper == "QNAM" and not rec.get("supp_variable"):
                        rec["supp_variable"] = val
                        supp_var = val
                # After decomposition, update the KB snapshot so cleaned_rec
                # uses the decomposed value (not the raw "QVAL when QNAM=xxx").
                if is_from_kb:
                    original_kb_sdtm_var = rec["sdtm_variable"]

            if not is_from_kb and domain == "FA" and isinstance(sdtm_var, str) and sdtm_var.upper() == "FADTC":
                rec["sdtm_variable"] = "FAORRES"
                sdtm_var = "FAORRES"
                if not rec.get("testcd"):
                    rec["testcd"] = self._infer_testcd(variable_name, "", "FA")

            if not is_from_kb and _MULTI_DOMAIN_SPLIT.search(domain):
                domain_parts = [d.strip().upper() for d in _MULTI_DOMAIN_SPLIT.split(domain) if d.strip()]
                original_multi_domain = domain

                for d in domain_parts:
                    if is_valid_domain(d):
                        domain = d
                        break
                else:
                    domain = domain_parts[0] if domain_parts else domain

                if sdtm_var and _MULTI_DOMAIN_SPLIT.search(str(sdtm_var)):
                    var_parts = [v.strip() for v in _MULTI_DOMAIN_SPLIT.split(str(sdtm_var)) if v.strip()]
                    try:
                        domain_idx = domain_parts.index(domain)
                    except ValueError:
                        domain_idx = 0
                    if domain_idx < len(var_parts):
                        sdtm_var = var_parts[domain_idx]
                    else:
                        sdtm_var = var_parts[0] if var_parts else sdtm_var
                    rec["sdtm_variable"] = sdtm_var

                rec["domain"] = domain
                rec["original_multi_domain"] = original_multi_domain
                print(f"📌 Multi-domain '{original_multi_domain}' resolved to '{domain}' for {variable_name}")

            is_comment_like = any(kw in varname_lower for kw in comment_keywords)
            if is_comment_like and var_type != "supp":
                var_type = "supp"
                rec["sdtm_variable_type"] = "supp"

            if var_type not in ("standard", "supp"):
                if (
                    supp_ds
                    or supp_var
                    or (isinstance(sdtm_var, str) and sdtm_var.upper() in qvars)
                    or domain.startswith("SUPP")
                ):
                    var_type = "supp"
                else:
                    var_type = "standard"
                rec["sdtm_variable_type"] = var_type

            # Correct mis-typed "standard" that is actually SUPP (common for
            # KB results where sdtm_variable_type is not stored).
            if var_type == "standard" and (
                supp_var or (isinstance(sdtm_var, str) and sdtm_var.upper() in qvars) or domain.startswith("SUPP")
            ):
                var_type = "supp"
                rec["sdtm_variable_type"] = "supp"

            if var_type == "standard" and not is_from_kb and sdtm_var:
                sdtm_upper = str(sdtm_var).upper()
                valid_vars = _STANDARD_VARS_BY_DOMAIN.get(domain, set())
                if valid_vars and sdtm_upper not in valid_vars:
                    print(
                        f"⚠️ [PostValidate] {variable_name}: LLM returned '{sdtm_var}' as standard "
                        f"in {domain}, but it is NOT in the standard variable list → correcting to SUPP"
                    )
                    var_type = "supp"
                    rec["sdtm_variable_type"] = "supp"
                    rec["auto_corrected_to_supp"] = True
                    rec["original_sdtm_variable"] = sdtm_var
                    rec["score"] = min(rec.get("score", 0.5) * 0.8, 0.7)

            if var_type == "supp":
                sdtm_upper = str(sdtm_var).upper() if sdtm_var else ""

                if not supp_ds:
                    if sdtm_upper.startswith("SUPP"):
                        supp_ds = sdtm_upper
                    elif domain.startswith("SUPP"):
                        supp_ds = domain
                    elif len(domain) == 2 or domain:
                        supp_ds = f"SUPP{domain}"
                    else:
                        supp_ds = "SUPPXX"
                    rec["supp_dataset"] = supp_ds

                supp_var_upper = supp_var.upper() if supp_var else ""
                needs_regenerate = not supp_var or supp_var_upper in qvars or is_standard_variable_name(supp_var)

                if needs_regenerate:
                    if sdtm_upper and sdtm_upper not in qvars and not is_standard_variable_name(sdtm_upper):
                        supp_var = sdtm_upper
                    else:
                        supp_var = variable_name.upper() if variable_name else "COMMENT"
                        if (
                            self.debug
                            and rec.get("supp_variable")
                            and is_standard_variable_name(rec.get("supp_variable", ""))
                        ):
                            print(f"   ⚠️ Corrected invalid QNAM: {rec.get('supp_variable')} → {supp_var}")
                    rec["supp_variable"] = supp_var
                else:
                    rec["supp_variable"] = supp_var

                if domain == "FA":
                    supp_var = "FAOROTH"
                    rec["supp_variable"] = "FAOROTH"

                if sdtm_upper and sdtm_upper not in qvars:
                    if sdtm_upper.startswith("SUPP") and not supp_ds:
                        rec["supp_dataset"] = sdtm_upper
                    rec["sdtm_variable"] = "QVAL"
                elif not sdtm_upper:
                    rec["sdtm_variable"] = "QVAL"

                cleaned_rec = {
                    "domain": original_kb_domain if original_kb_domain else rec.get("domain", ""),
                    "sdtm_variable": original_kb_sdtm_var if original_kb_sdtm_var else rec.get("sdtm_variable", ""),
                    "sdtm_variable_type": rec.get("sdtm_variable_type", ""),
                    "score": rec.get("score", 0.9),
                    "supp_dataset": rec.get("supp_dataset", ""),
                    "supp_variable": rec.get("supp_variable", ""),
                    "testcd": rec.get("testcd", ""),
                    "source": rec.get("source", ""),
                    "variable_name": variable_name,
                }
                normalized.append(cleaned_rec)
            else:
                cleaned_rec = {
                    "domain": original_kb_domain if original_kb_domain else rec.get("domain", ""),
                    "sdtm_variable": original_kb_sdtm_var if original_kb_sdtm_var else rec.get("sdtm_variable", ""),
                    "sdtm_variable_type": rec.get("sdtm_variable_type", ""),
                    "score": rec.get("score", 0.9),
                    "testcd": rec.get("testcd", ""),
                    "source": rec.get("source", ""),
                    "variable_name": variable_name,
                }
                normalized.append(cleaned_rec)

        # --- Deduplicate: same (domain, sdtm_variable, testcd, supp_variable)
        #     keep the entry with the highest score.
        seen: dict[tuple, int] = {}
        deduped: list[dict[str, Any]] = []
        for rec in normalized:
            key = (
                str(rec.get("domain", "")).upper(),
                str(rec.get("sdtm_variable", "")).upper(),
                str(rec.get("testcd", "")).upper(),
                str(rec.get("supp_variable", "")).upper(),
            )
            score = rec.get("score", 0)
            if key in seen:
                existing_idx = seen[key]
                if score > deduped[existing_idx].get("score", 0):
                    deduped[existing_idx] = rec
            else:
                seen[key] = len(deduped)
                deduped.append(rec)

        if len(deduped) < len(normalized):
            logger.info(f"Deduped {variable_name}: {len(normalized)} → {len(deduped)} recommendations")

        # --- Deterministic validation: enforce CDISC constraints regardless of source ---
        validator = getattr(self, "_deterministic_validator", None)
        if validator is None:
            validator = DeterministicValidator(debug=self.debug)
            self._deterministic_validator = validator
        for i, rec in enumerate(deduped):
            corrected, _issues = validator.validate_and_correct(
                rec,
                variable_name,
                domain_hint=target_upper,
            )
            deduped[i] = corrected

        return deduped

    def _build_fallback_recommendations(
        self: SDTMProcessorHost,
        table_name: str,
        variable_data: dict[str, Any],
        target_domain: str | None,
        reason: str,
    ) -> list[dict[str, Any]]:
        """
        Build a placeholder recommendation so every variable has at least one entry.
        """
        variable_name = variable_data.get("metadata_variable", "UNKNOWN_VAR")
        annotation_table = variable_data.get("annotation_table", "")
        fallback_domain = (
            (target_domain or "").strip()
            or self._recommend_domain_from_annotation(annotation_table)
            or self._map_table_to_domain(table_name)
            or "UNMAPPED"
        )
        fallback_domain = str(fallback_domain or "UNMAPPED").strip().upper()
        placeholder_var = f"{fallback_domain}_{variable_name}_PENDING".upper()
        print(f"⚠️  Using fallback recommendation for {table_name} - {variable_name}: {reason}")
        return [
            {
                "domain": fallback_domain,
                "sdtm_variable": placeholder_var,
                "sdtm_variable_type": "standard",
                "variable_name": variable_name,
                "score": 0.0,
                "priority": 999,
                "source": "FALLBACK",
                "fallback_reason": reason,
            }
        ]

    def _infer_testcd(self, variable_name: str, annotation_variable: str, domain: str) -> str:
        """Infer TESTCD value when the LLM did not provide one."""
        testcd_mappings = {
            "SYSBP": ["收缩压", "血压收缩压", "systolic", "sysbp", "sbp"],
            "DIABP": ["舒张压", "血压舒张压", "diastolic", "diabp", "dbp"],
            "PULSE": ["脉搏", "pulse", "pulse rate"],
            "RESP": ["呼吸", "呼吸频率", "respiratory", "respiration", "resp"],
            "TEMP": ["体温", "temperature", "temp"],
            "HEIGHT": ["身高", "height"],
            "WEIGHT": ["体重", "weight"],
            "BMI": ["体重指数", "bmi", "body mass index"],
            "HR": ["心率", "heart rate", "hr"],
            # 心电图 (EG)
            "ECGHR": ["心电图心率", "ecg heart rate"],
            "RR": ["rr间期", "rr interval"],
            "PR": ["pr间期", "pr interval"],
            "QRS": ["qrs时限", "qrs duration"],
            "QT": ["qt间期", "qt interval"],
            "QTC": ["qtc间期", "qtc", "corrected qt"],
        }

        search_text = f"{variable_name} {annotation_variable}".lower()

        for testcd, keywords in testcd_mappings.items():
            for keyword in keywords:
                if keyword.lower() in search_text:
                    return testcd

        if domain == "FA" and variable_name:
            return variable_name.upper()[:8]

        if variable_name:
            var_upper = variable_name.upper()
            if len(var_upper) > 2:
                potential_testcd = var_upper[2:]
                if potential_testcd.endswith("ORRES"):
                    potential_testcd = potential_testcd[:-5]
                if potential_testcd:
                    return potential_testcd

        return ""

    def _find_parent_fatestcd(
        self,
        supp_variable_name: str,
        domain_recs: list[dict[str, Any]],
        original_mappings: list[dict[str, Any]],
    ) -> str:
        """
        Find the parent variable's FATESTCD for an FA SUPP variable.
        """
        if not supp_variable_name:
            return ""

        vn_upper = supp_variable_name.upper()

        parent_candidates = []
        suffixes_to_strip = ["OTHER", "OTHR", "OTH", "O"]
        for suffix in suffixes_to_strip:
            if vn_upper.endswith(suffix) and len(vn_upper) > len(suffix):
                candidate = vn_upper[: -len(suffix)]
                if candidate not in parent_candidates:
                    parent_candidates.append(candidate)

        for parent_name in parent_candidates:
            for rec in domain_recs:
                rec_vn = str(rec.get("variable_name", "")).upper()
                if (
                    rec_vn == parent_name
                    and str(rec.get("domain", "")).upper() == "FA"
                    and str(rec.get("sdtm_variable_type", "")).lower() == "standard"
                ):
                    testcd = rec.get("testcd", "")
                    if testcd:
                        return testcd
                    ann_var = ""
                    for m in original_mappings:
                        if str(m.get("metadata_variable", "")).upper() == parent_name:
                            ann_var = m.get("annotation_variable", "")
                            break
                    inferred = self._infer_testcd(parent_name, ann_var, "FA")
                    if inferred:
                        return inferred

        return ""
