"""
Enhanced Knowledge Base Query Interface for LLM Testing

This module provides advanced query capabilities for testing LLM reasoning
against the SDTM knowledge base.

Features:
- Exact matching on all four fields
- Annotation field matching
- Vector-based fuzzy matching (using embeddings for semantic similarity)
"""

import hashlib
import logging
import os
import pickle
import time

# from difflib import SequenceMatcher  # 已删除冗余模糊匹配，不再需要
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
import pandas as pd

from src.infrastructure.data_masker import DataMasker

from .exact_match_utils import (
    DEEP_NORM_SUFFIX,
    build_exact_match_key,
    fuzzy_score,
    normalize_deep,
    normalize_exact_match_value,
    prepare_match_column,
)
from .knowledge_manager import KnowledgeBaseManager
from .ranking_utils import (
    calculate_confidence,
    extract_key_terms,
    find_similar_variable_names,
    generate_functional_query,
    get_domain_description,
    rank_suggestions,
    validate_naming_convention,
)

logger = logging.getLogger(__name__)


class LLMKnowledgeQueryInterface:
    VECTOR_CACHE_VERSION: ClassVar[str] = "masked-v1"
    EXACT_MATCH_COLUMNS: ClassVar[list[str]] = [
        "metadata_variable",
        "annotation_variable",
        "metadata_table",
        "annotation_table",
    ]
    NORMALIZED_SUFFIX: str = "__norm"
    LEGACY_KB_FILENAMES: ClassVar[dict[str, str]] = {
        "ALS2SDTM_TEST.json": "ALS2SDTM_Mapping_Template_v1.0.json",
        "ALS2SDTM_TEST.parquet": "ALS2SDTM_Mapping_Template_v1.0.parquet",
    }
    """
    Enhanced query interface for LLM testing against SDTM knowledge base.

    This class provides advanced query capabilities specifically designed
    for testing and evaluating LLM reasoning about SDTM standards.
    """

    def __init__(
        self,
        knowledge_base_path: str = "data/knowledge_base",
        extra_kb_files: list[str | Path] | None = None,
        log_ai_interactions: bool | None = None,
        data_masker: DataMasker | None = None,
    ):
        """
        Initialize the LLM knowledge query interface.

        Args:
            knowledge_base_path: Path to the knowledge base directory
            extra_kb_files: Optional list of additional KB files to merge with the default.
                These are typically user-uploaded session KB files.
            data_masker: Optional masker shared with the calling processor. A default
                masker is always created so embedding payloads are redacted.
        """
        self.kb_manager = KnowledgeBaseManager(knowledge_base_path)
        self.kb_path = Path(knowledge_base_path)
        self.structured_path = self.kb_path / "structured"
        self.data_masker = data_masker or DataMasker()

        # Default KB file (from env RAG_KB_DEFAULT_FILE)
        default_kb = os.getenv("RAG_KB_DEFAULT_FILE")
        self.default_kb_filename = Path(default_kb) if default_kb else None

        # Extra KB files for accumulation (user-uploaded session KB files)
        self.extra_kb_files: list[Path] = []
        if extra_kb_files:
            self.extra_kb_files = [Path(f) for f in extra_kb_files if f]
        # Session/project KBs may contain sensitive metadata. Keep their vectors
        # process-local so cache files cannot outlive session cleanup or be reused
        # by another session.
        self._allow_persistent_vector_cache = not bool(self.extra_kb_files)

        # Load eCRF data for direct matching (merges all KB sources)
        self.ecrf_data = None
        self._load_ecrf_data()
        env_log_ai = os.getenv("SDTM_LOG_AI", "0").lower() in ("1", "true", "yes", "on")
        env_save_ai = os.getenv("SAVE_AI_INTERACTIONS", "0").lower() in ("1", "true", "yes", "on")
        if log_ai_interactions is None:
            self.log_ai_interactions = env_log_ai or env_save_ai
        else:
            self.log_ai_interactions = bool(log_ai_interactions)
        try:
            min_conf = float(os.getenv("ECRF_MIN_CONFIDENCE", "0.8"))
        except (TypeError, ValueError):
            min_conf = 0.8
        self.ecrf_min_confidence = max(0.0, min(1.0, min_conf))
        try:
            fuzzy_conf = float(os.getenv("ECRF_FUZZY_CONFIDENCE", "0.75"))
        except (TypeError, ValueError):
            fuzzy_conf = 0.75
        try:
            metadata_fuzzy_conf = float(os.getenv("ECRF_METADATA_FUZZY_CONFIDENCE", "0.65"))
        except (TypeError, ValueError):
            metadata_fuzzy_conf = 0.65
        self.ecrf_strategy_thresholds: dict[str, float] = {
            "fuzzy_similarity": max(0.0, min(1.0, fuzzy_conf)),
            "vector_similarity": max(0.0, min(1.0, fuzzy_conf)),  # Same threshold for vector
            "exact_annotation_fields": max(0.0, min(1.0, metadata_fuzzy_conf)),
        }
        self._normalized_ecrf_frame: pd.DataFrame | None = None
        self._precomputed_exact_matches: dict[str, dict[str, Any]] = {}

        # ========== 向量化模糊匹配支持 ==========
        # Embedding model for vector similarity
        self._embedding_model = os.getenv("RAG_EMBED_MODEL", "Qwen3-Embed")
        self._embed_client = None  # Lazy initialization

        # Vector cache directory
        self._vector_cache_dir = Path("data/cache/kb_vectors")
        self._vector_cache_dir.mkdir(parents=True, exist_ok=True)

        # KB vectors (loaded lazily)
        self._kb_vectors: np.ndarray | None = None
        self._kb_vector_indices: list[int] | None = None  # Maps vector index to DataFrame row
        self._kb_texts: list[str] | None = None  # Masked texts for in-memory debugging only
        self._kb_vector_signature: str | None = None  # For cache invalidation

        # Enable/disable vector matching
        self._enable_vector_matching = os.getenv("KB_ENABLE_VECTOR_MATCHING", "true").lower() in (
            "1",
            "true",
            "yes",
            "on",
        )

        # Minimum score threshold for vector matching (default 0.95 for high precision)
        try:
            self._vector_min_score = float(os.getenv("KB_VECTOR_MIN_SCORE", "0.95"))
        except (TypeError, ValueError):
            self._vector_min_score = 0.95
        self._vector_min_score = max(0.0, min(1.0, self._vector_min_score))

        # Verbose mode for KB vector matching (detailed logging)
        self._kb_verbose = os.getenv("KB_VERBOSE", "false").lower() in ("1", "true", "yes", "on")

        logger.info(
            "LLMKnowledgeQueryInterface initialized (vector_matching=%s, min_score=%.2f, verbose=%s)",
            self._enable_vector_matching,
            self._vector_min_score,
            self._kb_verbose,
        )

    def _mask_text(self, text: str) -> str:
        """Redact sensitive text before remote embedding or diagnostic output."""
        masker = getattr(self, "data_masker", None)
        if masker is None:
            masker = DataMasker()
            self.data_masker = masker
        return masker.mask_text(text)

    def _persistent_vector_cache_allowed(self) -> bool:
        """Return whether this KB instance may read or write the shared disk cache."""
        return bool(getattr(self, "_allow_persistent_vector_cache", True)) and not bool(
            getattr(self, "extra_kb_files", [])
        )

    def _log_kb_event(self, message: str) -> None:
        """Write KB-specific debug info to the shared AI interaction log."""
        if not getattr(self, "log_ai_interactions", False):
            return
        try:
            log_dir = Path("data/output/logs")
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = log_dir / f"ai_interactions_{datetime.now():%Y%m%d}.log"
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with log_file.open("a", encoding="utf-8") as fh:
                fh.write(f"\n[KB] {timestamp} {self._mask_text(message)}")
        except Exception as exc:
            logger.debug("Failed to write KB log entry (%s)", type(exc).__name__)

    def add_extra_kb_file(self, filename: str | Path) -> None:
        """
        Add an additional KB file and reload all KB sources.

        Args:
            filename: Path or filename to add to the extra KB files
        """
        if filename:
            new_path = Path(filename)
            if new_path not in self.extra_kb_files:
                self.extra_kb_files.append(new_path)
                self._allow_persistent_vector_cache = False
                self._load_ecrf_data()
                logger.info("Added extra KB file: %s", new_path.name)

    def get_kb_sources(self) -> dict[str, Any]:
        """Get information about currently loaded KB sources."""
        sources = {
            "default": str(self.default_kb_filename) if self.default_kb_filename else None,
            "extra": [str(f) for f in self.extra_kb_files],
            "total_records": len(self.ecrf_data) if self.ecrf_data is not None else 0,
        }
        if self.ecrf_data is not None and "_kb_source" in self.ecrf_data.columns:
            sources["records_by_source"] = self.ecrf_data["_kb_source"].value_counts().to_dict()
        return sources

    def _load_single_kb_file(self, kb_file: Path) -> pd.DataFrame | None:
        """Load a single KB file and return as DataFrame."""
        if not kb_file.exists():
            logger.warning("KB file not found: %s", kb_file.name)
            return None

        try:
            suffix = kb_file.suffix.lower()
            if suffix == ".parquet":
                df = pd.read_parquet(kb_file)
            elif suffix in {".json", ".jsonl"}:
                df = pd.read_json(kb_file, lines=suffix == ".jsonl")
            elif suffix in {".csv", ".tsv"}:
                sep = "\t" if suffix == ".tsv" else ","
                df = pd.read_csv(kb_file, sep=sep)
            else:
                logger.warning("Unsupported KB file format: %s", kb_file.suffix)
                return None

            logger.info("Loaded KB file '%s': %d records", kb_file.name, len(df))
            return df
        except Exception as exc:
            logger.error("Failed to load KB file '%s' (%s)", kb_file.name, type(exc).__name__)
            return None

    def _resolve_kb_path(self, filename: Path | None) -> Path | None:
        """Resolve KB paths and migrate the legacy default filename in place."""
        if not filename:
            return None

        if filename.is_absolute():
            candidates = [filename]
        elif filename.parent == Path("."):
            candidates = [self.structured_path / filename]
        else:
            candidates = [filename, self.structured_path / filename]

        for candidate in candidates:
            if candidate.exists():
                return candidate

        for candidate in candidates:
            replacement_name = self.LEGACY_KB_FILENAMES.get(candidate.name)
            if replacement_name:
                replacement = candidate.with_name(replacement_name)
                if replacement.exists():
                    logger.warning(
                        "Legacy KB filename '%s' is deprecated; using '%s'. Update RAG_KB_DEFAULT_FILE.",
                        candidate.name,
                        replacement.name,
                    )
                    return replacement

        return candidates[0]

    def _load_ecrf_data(self):
        """Load and merge all KB sources for direct matching."""
        self.ecrf_data = None
        all_dfs: list[pd.DataFrame] = []

        try:
            # 1. Load default KB file (from RAG_KB_DEFAULT_FILE env)
            if self.default_kb_filename:
                default_path = self._resolve_kb_path(self.default_kb_filename)
                if default_path:
                    df = self._load_single_kb_file(default_path)
                    if df is not None:
                        df["_kb_source"] = f"default:{default_path.name}"
                        all_dfs.append(df)

            # 2. Load extra KB files (user-uploaded session KB files)
            for extra_file in self.extra_kb_files:
                extra_path = self._resolve_kb_path(extra_file)
                if extra_path:
                    df = self._load_single_kb_file(extra_path)
                    if df is not None:
                        df["_kb_source"] = f"session:{extra_path.name}"
                        all_dfs.append(df)

            # Merge all DataFrames
            if not all_dfs:
                logger.info("No KB files specified; direct matching disabled")
                return

            self.ecrf_data = pd.concat(all_dfs, ignore_index=True)

            # Remove duplicates (keep first occurrence, which respects priority: default > session)
            dedup_cols = ["annotation_table", "metadata_table", "annotation_variable", "metadata_variable"]
            existing_cols = [c for c in dedup_cols if c in self.ecrf_data.columns]
            if existing_cols:
                before_count = len(self.ecrf_data)
                self.ecrf_data = self.ecrf_data.drop_duplicates(subset=existing_cols, keep="first")
                after_count = len(self.ecrf_data)
                if before_count > after_count:
                    logger.info(f"Removed {before_count - after_count} duplicate KB records")

            logger.info(f"Total KB records after merge: {len(self.ecrf_data)} (from {len(all_dfs)} sources)")
            self._normalized_ecrf_frame = None
            self._precomputed_exact_matches = {}
            # Invalidate vector cache when KB data changes
            self._kb_vectors = None
            self._kb_vector_indices = None
            self._kb_texts = None
            self._kb_vector_signature = None

        except Exception as exc:
            logger.error("Failed to load KB data (%s)", type(exc).__name__)
            self.ecrf_data = None
            self._normalized_ecrf_frame = None
            self._precomputed_exact_matches = {}
            self._kb_vectors = None
            self._kb_vector_indices = None
            self._kb_texts = None
            self._kb_vector_signature = None

    def _get_ecrf_exact_frame(self) -> pd.DataFrame | None:
        """Return cached eCRF frame with normalized columns for exact matching."""
        if self.ecrf_data is None:
            return None
        if self._normalized_ecrf_frame is not None:
            return self._normalized_ecrf_frame

        try:
            ecrf_data_clean = self.ecrf_data.copy()
            match_columns = [*list(self.EXACT_MATCH_COLUMNS), "SDTM_Domain", "SDTM_Variable"]
            for column in match_columns:
                if column in ecrf_data_clean.columns:
                    ecrf_data_clean[column] = self._prepare_match_column(ecrf_data_clean[column])
                else:
                    ecrf_data_clean[column] = ""

            normalized_suffix = self.NORMALIZED_SUFFIX
            for column in self.EXACT_MATCH_COLUMNS:
                normalized_col = f"{column}{normalized_suffix}"
                ecrf_data_clean[normalized_col] = ecrf_data_clean[column].map(self._normalize_exact_match_value)

            deep_suffix = DEEP_NORM_SUFFIX
            for column in self.EXACT_MATCH_COLUMNS:
                deep_col = f"{column}{deep_suffix}"
                ecrf_data_clean[deep_col] = ecrf_data_clean[column].map(normalize_deep)

            self._normalized_ecrf_frame = ecrf_data_clean
            return self._normalized_ecrf_frame
        except Exception as exc:
            logger.error("Failed to normalize eCRF data for exact matching (%s)", type(exc).__name__)
            return None

    # ==================== 向量化模糊匹配方法 ====================

    def _get_embed_client(self):
        """Lazily initialize the embedding client."""
        if self._embed_client is None:
            try:
                from src.rag.embeddings import OpenRouterEmbeddingClient

                self._embed_client = OpenRouterEmbeddingClient(model=self._embedding_model)
                logger.info("Initialized embedding client with model: %s", self._embedding_model)
            except Exception as exc:
                logger.error("Failed to initialize embedding client (%s)", type(exc).__name__)
                self._embed_client = None
        return self._embed_client

    def _compute_kb_signature(self) -> str:
        """Compute a signature for the current KB data to detect changes."""
        if self.ecrf_data is None:
            return ""

        # The explicit version invalidates legacy caches that could contain raw
        # record text. Session/project KBs never use this persistent signature.
        parts = [f"cache_version:{self.VECTOR_CACHE_VERSION}"]
        if self.default_kb_filename:
            resolved = self._resolve_kb_path(self.default_kb_filename)
            if resolved and resolved.exists():
                stat = resolved.stat()
                parts.append(f"default:{resolved.name}:{int(stat.st_mtime)}:{stat.st_size}")

        for extra_file in self.extra_kb_files:
            resolved = self._resolve_kb_path(extra_file)
            if resolved and resolved.exists():
                stat = resolved.stat()
                parts.append(f"extra:{resolved.name}:{int(stat.st_mtime)}:{stat.st_size}")

        parts.append(f"rows:{len(self.ecrf_data)}")
        parts.append(f"model:{self._embedding_model}")

        payload = "|".join(parts).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _get_kb_vector_cache_path(self) -> Path:
        """Get the cache file path for KB vectors."""
        signature = self._compute_kb_signature()
        return self._vector_cache_dir / f"kb_vectors_{signature[:16]}.pkl"

    def _load_kb_vectors_from_cache(self) -> bool:
        """Try to load KB vectors from disk cache."""
        if not self._persistent_vector_cache_allowed():
            return False

        cache_path = self._get_kb_vector_cache_path()
        if not cache_path.exists():
            return False

        try:
            with cache_path.open("rb") as f:
                payload = pickle.load(f)

            if not isinstance(payload, dict) or payload.get("cache_version") != self.VECTOR_CACHE_VERSION:
                logger.info("Ignoring legacy KB vector cache")
                return False

            # Verify signature matches
            if payload.get("signature") != self._compute_kb_signature():
                logger.info("KB vector cache signature mismatch, will rebuild")
                return False

            self._kb_vectors = np.array(payload["vectors"], dtype=np.float32)
            self._kb_vector_indices = payload["indices"]
            self._kb_texts = None
            self._kb_vector_signature = payload["signature"]

            logger.info("Loaded %d KB vectors from cache: %s", len(self._kb_vectors), cache_path.name)
            return True
        except Exception as exc:
            logger.warning("Failed to load KB vectors from cache (%s)", type(exc).__name__)
            return False

    def _save_kb_vectors_to_cache(self) -> None:
        """Save KB vectors to disk cache."""
        if self._kb_vectors is None or not self._persistent_vector_cache_allowed():
            return

        cache_path = self._get_kb_vector_cache_path()
        try:
            payload = {
                "cache_version": self.VECTOR_CACHE_VERSION,
                "signature": self._kb_vector_signature,
                "vectors": self._kb_vectors.tolist(),
                "indices": self._kb_vector_indices,
                "model": self._embedding_model,
                "saved_at": time.time(),
            }
            with cache_path.open("wb") as f:
                pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
            logger.info("Saved %d KB vectors to cache: %s", len(self._kb_vectors), cache_path.name)
        except Exception as exc:
            logger.warning("Failed to save KB vectors to cache (%s)", type(exc).__name__)

    def _build_kb_record_text(self, row) -> str:
        """Build a text representation of a KB record for embedding.

        Prioritizes semantic fields (annotation_table, annotation_variable) over
        technical codes (metadata_variable, metadata_table) by repeating semantic
        content. This produces higher similarity for records with the same CRF
        meaning but different internal codes across projects.
        """
        annotation_table = str(row.get("annotation_table", "")).strip()
        annotation_var = str(row.get("annotation_variable", "")).strip()
        metadata_var = str(row.get("metadata_variable", "")).strip()
        metadata_table = str(row.get("metadata_table", "")).strip()

        # Semantic-focused construction: repeat annotation fields for weight
        # Format: "{table} {variable_label} | {table} {variable_label} | {meta_var}"
        # This ensures ~2/3 of the text is semantic content
        parts = []
        semantic = " ".join(filter(None, [annotation_table, annotation_var]))
        if semantic:
            parts.append(semantic)
            parts.append(semantic)
        if metadata_var:
            parts.append(metadata_var)
        if metadata_table and metadata_table.lower() != annotation_table.lower():
            parts.append(metadata_table)

        text = " | ".join(parts) if parts else ""
        return self._mask_text(text)

    def _ensure_kb_vectors(self) -> bool:
        """Ensure KB vectors are loaded (from cache or computed)."""
        if self._kb_vectors is not None:
            return True

        if self.ecrf_data is None or self.ecrf_data.empty:
            return False

        if not self._enable_vector_matching:
            return False

        # Try loading from cache first
        if self._load_kb_vectors_from_cache():
            return True

        # Need to compute vectors
        embed_client = self._get_embed_client()
        if embed_client is None:
            logger.warning("No embedding client available, vector matching disabled")
            return False

        logger.info("Building KB vectors for %d records...", len(self.ecrf_data))

        try:
            # Build text representations
            texts = []
            indices = []
            for idx, row in enumerate(self.ecrf_data.to_dict("records")):
                text = self._build_kb_record_text(row)
                if text:
                    texts.append(text)
                    indices.append(idx)

            if not texts:
                logger.warning("No valid KB records for vector embedding")
                return False

            # Batch embed (API call)
            batch_size = 100
            all_vectors = []
            for i in range(0, len(texts), batch_size):
                batch_texts = texts[i : i + batch_size]
                batch_vectors = embed_client.embed_texts(batch_texts)
                all_vectors.extend(batch_vectors)
                if len(texts) > batch_size:
                    logger.info("Embedded %d/%d KB records", min(i + batch_size, len(texts)), len(texts))

            self._kb_vectors = np.array(all_vectors, dtype=np.float32)
            self._kb_vector_indices = indices
            self._kb_texts = texts
            self._kb_vector_signature = self._compute_kb_signature()

            # Save to cache
            self._save_kb_vectors_to_cache()

            logger.info("Built KB vectors: %d records, dimension %d", len(self._kb_vectors), self._kb_vectors.shape[1])
            return True

        except Exception as exc:
            logger.error("Failed to build KB vectors (%s)", type(exc).__name__)
            return False

    def _vector_fuzzy_search(
        self,
        query_data: dict[str, Any],
        top_k: int = 5,
        min_score: float = 0.5,
        verbose: bool = False,
    ) -> list[tuple[float, int]]:
        """
        Search KB using vector similarity.

        Args:
            query_data: Variable data to search for
            top_k: Number of top results to return
            min_score: Minimum cosine similarity score
            verbose: Whether to output detailed logs

        Returns:
            List of (score, dataframe_index) tuples
        """
        variable_name = query_data.get("metadata_variable") or query_data.get("annotation_variable") or "unknown"
        safe_variable_name = self._mask_text(str(variable_name))

        if not self._ensure_kb_vectors():
            if verbose:
                print(f"[KB-Vector] {safe_variable_name}: KB vectors not available")
            return []

        embed_client = self._get_embed_client()
        if embed_client is None:
            if verbose:
                print(f"[KB-Vector] {safe_variable_name}: Embedding client not available")
            return []

        # Build query text
        query_text = self._build_kb_record_text(pd.Series(query_data))
        if not query_text:
            if verbose:
                print(f"[KB-Vector] {safe_variable_name}: Empty query text")
            return []

        try:
            kb_mat = self._kb_vectors
            kb_ix = self._kb_vector_indices
            if kb_mat is None or kb_ix is None:
                return []

            # Get query embedding
            query_vector = np.array(embed_client.embed_texts([query_text])[0], dtype=np.float32)

            # Compute cosine similarity
            # Normalize vectors
            kb_norms = np.linalg.norm(kb_mat, axis=1, keepdims=True)
            kb_norms = np.where(kb_norms == 0, 1, kb_norms)  # Avoid division by zero
            kb_normalized = kb_mat / kb_norms

            query_norm = np.linalg.norm(query_vector)
            if query_norm == 0:
                return []
            query_normalized = query_vector / query_norm

            similarities = np.dot(kb_normalized, query_normalized)

            # Get top-k indices above threshold
            valid_mask = similarities >= min_score
            valid_indices = np.where(valid_mask)[0]
            valid_scores = similarities[valid_mask]

            if len(valid_indices) == 0:
                if verbose:
                    print(f"[KB-Vector] {safe_variable_name}: No matches above threshold {min_score}")
                return []

            # Sort by score descending
            sorted_order = np.argsort(valid_scores)[::-1][:top_k]

            results = []
            for i in sorted_order:
                vector_idx = valid_indices[i]
                df_idx = kb_ix[int(vector_idx)]
                score = float(valid_scores[i])
                results.append((score, df_idx))

            # Verbose output
            if verbose and results:
                print(f"\n{'=' * 70}")
                print(f"[KB-Vector] Variable: {safe_variable_name}")
                print(f"{'=' * 70}")
                print(f"[Query] {query_text[:100]}{'...' if len(query_text) > 100 else ''}")
                print(f"[Stats] KB vectors: {len(kb_mat)} | Matches: {len(valid_indices)} | Top-{top_k}")
                print(f"{'-' * 70}")

                ecrf_data = self.ecrf_data
                if ecrf_data is None:
                    print(f"{'=' * 70}\n")
                    return results

                for rank, (score, df_idx) in enumerate(results, 1):
                    row = ecrf_data.loc[df_idx]
                    domain = self._mask_text(str(row.get("SDTM_Domain", "?")))
                    sdtm_var = self._mask_text(str(row.get("SDTM_Variable", "?")))
                    ann_var = self._mask_text(str(row.get("annotation_variable", "")))
                    ann_table = self._mask_text(str(row.get("annotation_table", "")))
                    meta_var = self._mask_text(str(row.get("metadata_variable", "")))

                    print(f"  [{rank}] Score: {score:.4f} | Domain: {domain} | SDTM: {sdtm_var}")
                    print(f"      KB Variable: {meta_var} | KB Label: {ann_var}")
                    print(f"      KB Table: {ann_table}")

                    # Build and show the matched KB text for comparison
                    kb_text = self._build_kb_record_text(row)
                    kb_preview = kb_text[:80].replace("\n", " ")
                    print(f"      KB Text: {kb_preview}{'...' if len(kb_text) > 80 else ''}")
                    print()

                print(f"{'=' * 70}\n")

            return results

        except Exception as exc:
            logger.error("Vector fuzzy search failed (%s)", type(exc).__name__)
            if verbose:
                print(f"[KB-Vector] {safe_variable_name}: Error ({type(exc).__name__})")
            return []

    def get_kb_vector_stats(self) -> dict[str, Any]:
        """Get statistics about KB vectors."""
        return {
            "enabled": self._enable_vector_matching,
            "vectors_loaded": self._kb_vectors is not None,
            "num_vectors": len(self._kb_vectors) if self._kb_vectors is not None else 0,
            "dimension": self._kb_vectors.shape[1] if self._kb_vectors is not None else 0,
            "embedding_model": self._embedding_model,
            "cache_dir": str(self._vector_cache_dir),
            "verbose": self._kb_verbose,
        }

    def set_verbose(self, verbose: bool) -> None:
        """Enable or disable verbose logging for KB vector matching."""
        self._kb_verbose = verbose

    # ==================== 原有方法 ====================

    def _build_exact_match_key(self, variable_data: dict[str, Any]) -> str:
        """Create a stable key for exact matching using the four required columns."""
        return build_exact_match_key(variable_data, columns=self.EXACT_MATCH_COLUMNS)

    def _bulk_exact_merge(self, variable_records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """Perform a single merge operation to find all exact matches for provided variables."""
        matches_by_key: dict[str, dict[str, Any]] = {}
        ecrf_frame = self._get_ecrf_exact_frame()
        if ecrf_frame is None or not variable_records:
            return matches_by_key

        normalized_suffix = self.NORMALIZED_SUFFIX
        merge_cols = [f"{col}{normalized_suffix}" for col in self.EXACT_MATCH_COLUMNS]

        normalized_rows: list[dict[str, str]] = []
        for record in variable_records:
            key = self._build_exact_match_key(record)
            if not key:
                continue
            normalized_row = {"__match_key": key}
            skip_record = False
            for column, merge_col in zip(self.EXACT_MATCH_COLUMNS, merge_cols, strict=False):
                value = self._normalize_exact_match_value(record.get(column, ""))
                if not value:
                    skip_record = True
                    break
                normalized_row[merge_col] = value
            if skip_record:
                continue
            normalized_rows.append(normalized_row)

        if not normalized_rows:
            return matches_by_key

        input_df = pd.DataFrame(normalized_rows).drop_duplicates(subset="__match_key")
        merged_df = input_df.merge(
            ecrf_frame,
            on=merge_cols,
            how="inner",
        )
        if merged_df.empty:
            return matches_by_key

        for match_key, group_df in merged_df.groupby("__match_key"):
            suggestions: list[dict[str, Any]] = []
            for row in group_df.to_dict("records"):
                suggestions.append(
                    {
                        "domain": row.get("SDTM_Domain", ""),
                        "sdtm_variable": row.get("SDTM_Variable", ""),
                        "variable_name": row.get("metadata_variable", ""),
                        "variable_label": row.get("annotation_variable", ""),
                        "annotation_table": row.get("annotation_table", ""),
                        "metadata_table": row.get("metadata_table", ""),
                        "score": 1.0,
                        "confidence": 1.0,
                        "match_type": "exact_all_fields",
                    }
                )
            if suggestions:
                matches_by_key[match_key] = {
                    "suggestions": suggestions,
                    "confidence": 1.0,
                    "match_strategy": "exact_all_fields",
                }
        return matches_by_key

    def precompute_exact_matches(self, variable_records: list[dict[str, Any]]) -> int:
        """
        Merge all provided variables with the KB once to capture exact matches.

        Returns:
            Number of unique variable keys with exact matches.
        """
        if not variable_records or self.ecrf_data is None:
            self._precomputed_exact_matches = {}
            return 0

        try:
            matches = self._bulk_exact_merge(variable_records)
            self._precomputed_exact_matches = matches
            if matches:
                logger.info("Precomputed %d exact KB matches via bulk merge", len(matches))
            return len(matches)
        except Exception as exc:
            logger.error("Failed to precompute exact matches (%s)", type(exc).__name__)
            self._precomputed_exact_matches = {}
            return 0

    def query_variable_mapping_with_ecrf(self, variable_data: dict[str, Any]) -> dict[str, Any]:
        """
        Query for variable mapping using eCRF data first, then fallback to LLM reasoning.

        Args:
            variable_data: Complete variable data containing all four fields

        Returns:
            Dictionary with mapping suggestions and confidence scores
        """
        # Step 1: Try direct matching with eCRF data
        match_key = self._build_exact_match_key(variable_data)
        if match_key and match_key in self._precomputed_exact_matches:
            ecrf_matches = self._precomputed_exact_matches[match_key]
        else:
            ecrf_matches = self._match_with_ecrf_data(variable_data)

        if ecrf_matches:
            confidence = ecrf_matches.get("confidence", 0.0)
            strategy = ecrf_matches.get("match_strategy")
            strategy_key = strategy if isinstance(strategy, str) else None
            threshold = (
                self.ecrf_strategy_thresholds.get(strategy_key, self.ecrf_min_confidence)
                if strategy_key
                else self.ecrf_min_confidence
            )
        else:
            confidence = 0.0
            strategy = None
            threshold = self.ecrf_min_confidence

        if ecrf_matches and confidence >= threshold:
            # High confidence match found in eCRF data
            suggestions = ecrf_matches.get("suggestions", [])
            num_matches = len(suggestions)
            summary = (
                "KB result source=eCRF "
                f"strategy={ecrf_matches.get('match_strategy')} "
                f"confidence={confidence:.2f} "
                f"threshold={threshold:.2f} "
                f"matches={num_matches}"
            )
            # 如果存在一对多映射，记录详细信息
            if num_matches > 1:
                multi_map_detail = " | ".join(
                    f"{s.get('domain', '')}.{s.get('sdtm_variable', '')}" for s in suggestions
                )
                summary += f" [MULTI_MAPPING: {multi_map_detail}]"
            logger.info(summary)
            self._log_kb_event(summary)
            return {
                "query": variable_data.get("metadata_variable", ""),
                "context": variable_data.get("annotation_table", ""),
                "suggestions": suggestions,
                "confidence": ecrf_matches.get("confidence", 0),
                "source": "eCRF_direct_match",
                "total_matches": num_matches,
            }

        # Step 2: Fallback to LLM reasoning for unmatched variables
        variable_name = variable_data.get("metadata_variable", "")
        table_name = variable_data.get("annotation_table", "")
        domain_hint = variable_data.get("domain_hint") or variable_data.get("domain")
        llm_result = self.query_variable_mapping(
            variable_name,
            table_name,
            domain=domain_hint.upper() if isinstance(domain_hint, str) else None,
        )
        llm_result["source"] = "LLM_reasoning"
        summary = (
            "KB result source=LLM "
            f"domain_scoped={bool(domain_hint)} confidence={llm_result.get('confidence', 0.0):.2f} "
            f"threshold={threshold:.2f}"
        )
        logger.info(summary)
        self._log_kb_event(summary)
        return llm_result

    def _match_with_ecrf_data(self, variable_data: dict[str, Any]) -> dict[str, Any]:
        """
        Match input variable data with eCRF data for direct mapping.
        Requires all four fields to match: annotation_table, metadata_table, annotation_variable, metadata_variable

        Args:
            variable_data: Input variable data containing all four fields

        Returns:
            Dictionary with matches and confidence scores
        """
        if self.ecrf_data is None:
            return {"suggestions": [], "confidence": 0.0}

        try:
            strategy_used: str | None = None
            # Extract input fields
            input_annotation_table = variable_data.get("annotation_table", "")
            input_annotation_variable = variable_data.get("annotation_variable", "")

            if not input_annotation_table or not input_annotation_variable:
                logger.warning("Missing required fields (annotation_table, annotation_variable) for KB matching")
                return {"suggestions": [], "confidence": 0.0}

            matches = []
            ecrf_data_clean = self._get_ecrf_exact_frame()
            if ecrf_data_clean is None:
                return {"suggestions": [], "confidence": 0.0}

            # ========== Step 1: 精准匹配 (annotation_table + annotation_variable) ==========
            # 只需要 annotation_table 和 annotation_variable 字段一致即为精准匹配
            exact_matches = ecrf_data_clean[
                (ecrf_data_clean["annotation_table"].str.lower() == input_annotation_table.lower())
                & (ecrf_data_clean["annotation_variable"].str.lower() == input_annotation_variable.lower())
            ]

            if not exact_matches.empty:
                for row in exact_matches.to_dict("records"):
                    matches.append(
                        {
                            "domain": row.get("SDTM_Domain", ""),
                            "sdtm_variable": row.get("SDTM_Variable", ""),
                            "variable_name": row.get("metadata_variable", ""),
                            "variable_label": row.get("annotation_variable", ""),
                            "annotation_table": row.get("annotation_table", ""),
                            "metadata_table": row.get("metadata_table", ""),
                            "score": 1.0,
                            "confidence": 1.0,
                            "match_type": "exact_match",
                        }
                    )
                strategy_used = "exact_match"

            # ========== Step 1.5: 深度规范化模糊匹配 ==========
            # 处理表名/变量名的常见变体：括号、斜杠、下划线、空格等
            if not matches:
                deep_suffix = DEEP_NORM_SUFFIX
                input_table_deep = normalize_deep(input_annotation_table)
                input_var_deep = normalize_deep(input_annotation_variable)

                if input_table_deep and input_var_deep:
                    ann_table_col = f"annotation_table{deep_suffix}"
                    ann_var_col = f"annotation_variable{deep_suffix}"

                    # Step 1.5a: 深度规范化后精确匹配（两字段均一致）
                    deep_exact = ecrf_data_clean[
                        (ecrf_data_clean[ann_table_col] == input_table_deep)
                        & (ecrf_data_clean[ann_var_col] == input_var_deep)
                    ]
                    if not deep_exact.empty:
                        for row in deep_exact.to_dict("records"):
                            matches.append(
                                {
                                    "domain": row.get("SDTM_Domain", ""),
                                    "sdtm_variable": row.get("SDTM_Variable", ""),
                                    "variable_name": row.get("metadata_variable", ""),
                                    "variable_label": row.get("annotation_variable", ""),
                                    "annotation_table": row.get("annotation_table", ""),
                                    "metadata_table": row.get("metadata_table", ""),
                                    "score": 0.95,
                                    "confidence": 0.95,
                                    "match_type": "normalized_exact",
                                }
                            )
                        strategy_used = "normalized_exact"

                    # Step 1.5b: 表名模糊 + 变量规范化精确
                    if not matches:
                        var_exact_rows = ecrf_data_clean[ecrf_data_clean[ann_var_col] == input_var_deep]
                        if not var_exact_rows.empty:
                            _FUZZY_TABLE_THRESHOLD = 0.75
                            best_score = 0.0
                            best_rows: list[dict[str, Any]] = []
                            for row in var_exact_rows.to_dict("records"):
                                kb_table_deep = row.get(ann_table_col, "")
                                score = fuzzy_score(input_table_deep, kb_table_deep)
                                if score >= _FUZZY_TABLE_THRESHOLD:
                                    if score > best_score:
                                        best_score = score
                                        best_rows = [row]
                                    elif score == best_score:
                                        best_rows.append(row)
                            if best_rows:
                                conf = round(0.80 + best_score * 0.12, 4)
                                for row in best_rows:
                                    matches.append(
                                        {
                                            "domain": row.get("SDTM_Domain", ""),
                                            "sdtm_variable": row.get("SDTM_Variable", ""),
                                            "variable_name": row.get("metadata_variable", ""),
                                            "variable_label": row.get("annotation_variable", ""),
                                            "annotation_table": row.get("annotation_table", ""),
                                            "metadata_table": row.get("metadata_table", ""),
                                            "score": round(best_score, 4),
                                            "confidence": conf,
                                            "match_type": "fuzzy_table_normalized_var",
                                        }
                                    )
                                strategy_used = "fuzzy_table_normalized_var"

                    # Step 1.5c: 表名规范化精确 + 变量模糊
                    if not matches:
                        table_exact_rows = ecrf_data_clean[ecrf_data_clean[ann_table_col] == input_table_deep]
                        if not table_exact_rows.empty:
                            _FUZZY_VAR_THRESHOLD = 0.78
                            best_score = 0.0
                            best_rows = []
                            for row in table_exact_rows.to_dict("records"):
                                kb_var_deep = row.get(ann_var_col, "")
                                score = fuzzy_score(input_var_deep, kb_var_deep)
                                if score >= _FUZZY_VAR_THRESHOLD:
                                    if score > best_score:
                                        best_score = score
                                        best_rows = [row]
                                    elif score == best_score:
                                        best_rows.append(row)
                            if best_rows:
                                conf = round(0.78 + best_score * 0.10, 4)
                                for row in best_rows:
                                    matches.append(
                                        {
                                            "domain": row.get("SDTM_Domain", ""),
                                            "sdtm_variable": row.get("SDTM_Variable", ""),
                                            "variable_name": row.get("metadata_variable", ""),
                                            "variable_label": row.get("annotation_variable", ""),
                                            "annotation_table": row.get("annotation_table", ""),
                                            "metadata_table": row.get("metadata_table", ""),
                                            "score": round(best_score, 4),
                                            "confidence": conf,
                                            "match_type": "normalized_table_fuzzy_var",
                                        }
                                    )
                                strategy_used = "normalized_table_fuzzy_var"

            # ========== Step 2: KB 向量匹配 (相似度 >= 0.95，取最高分) ==========
            if not matches and self._enable_vector_matching:
                vector_results = self._vector_fuzzy_search(
                    variable_data,
                    top_k=1,  # 只取最高分
                    min_score=self._vector_min_score,  # 阈值 (默认 0.95)
                    verbose=self._kb_verbose,
                )

                if vector_results:
                    score, df_idx = vector_results[0]  # 只取最高分的一个
                    row = ecrf_data_clean.loc[df_idx]
                    matches.append(
                        {
                            "domain": row.get("SDTM_Domain", ""),
                            "sdtm_variable": row.get("SDTM_Variable", ""),
                            "variable_name": row.get("metadata_variable", ""),
                            "variable_label": row.get("annotation_variable", ""),
                            "annotation_table": row.get("annotation_table", ""),
                            "metadata_table": row.get("metadata_table", ""),
                            "score": round(score, 4),
                            "confidence": 0.95,
                            "match_type": "vector_similarity",
                        }
                    )
                    strategy_used = "vector_similarity"

            # 日志记录
            if strategy_used and matches:
                top_match = matches[0]
                summary = (
                    f"KB strategy={strategy_used} "
                    f"conf={top_match['confidence']:.2f} "
                    f"domain={top_match.get('domain', '')} "
                    f"sdtm={top_match.get('sdtm_variable', '')}"
                )
                # 如果存在一对多映射，记录所有匹配
                if len(matches) > 1:
                    multi_map_info = ", ".join(f"{m.get('domain', '')}.{m.get('sdtm_variable', '')}" for m in matches)
                    summary += f" | MULTI_MAPPING[{len(matches)}]: {multi_map_info}"
                logger.info(summary)
                self._log_kb_event(summary)

            # 返回所有匹配结果，支持一对多映射
            return {
                "suggestions": matches,  # 返回所有匹配，不再限制为[:1]
                "confidence": matches[0]["confidence"] if matches else 0.0,
                "match_strategy": strategy_used,
            }

        except Exception as exc:
            logger.error("Error matching with eCRF data (%s)", type(exc).__name__)
            return {"suggestions": [], "confidence": 0.0}

    @staticmethod
    def _prepare_match_column(series: pd.Series) -> pd.Series:
        """Sanitize column values for text-based matching, avoiding categorical issues."""
        return prepare_match_column(series)

    @staticmethod
    def _normalize_exact_match_value(value: Any) -> str:
        """Normalize merge keys for case-insensitive exact matching."""
        return normalize_exact_match_value(value)

    # ==================== 冗余模糊匹配方法已删除 ====================
    # 原有: _calculate_fuzzy_score, _string_similarity, _tokenize_chinese,
    #       _jaccard_similarity, _hybrid_similarity
    # 简化后只使用: 精准匹配 + KB向量匹配
    # ================================================================

    def query_variable_mapping(
        self,
        query_text: str,
        context: str | None = None,
        domain: str | None = None,
    ) -> dict[str, Any]:
        """
        Query for variable mapping based on natural language description.

        Args:
            query_text: Natural language description of what to map
            context: Optional context (e.g., domain, study type)
            domain: Optional SDTM domain hint to scope the search

        Returns:
            Dictionary with mapping suggestions and confidence scores
        """
        # Extract key terms from query
        key_terms = self._extract_key_terms(query_text)

        # Search for matching variables
        results = []
        for term in key_terms:
            matches = self.kb_manager.search_variables(term, domain=domain)
            if not matches.empty:
                results.append(matches)
                logger.info(
                    "KB variable search domain_scoped=%s -> %d hits",
                    bool(domain),
                    len(matches),
                )
                self._log_kb_event(f"search domain_scoped={bool(domain)} hits={len(matches)}")

        if not results:
            return {
                "query": query_text,
                "context": context,
                "suggestions": [],
                "confidence": 0.0,
                "message": "No matching variables found",
            }

        # Combine and rank results
        combined_df = pd.concat(results, ignore_index=True).drop_duplicates()
        ranked_results = self._rank_mapping_suggestions(combined_df, query_text, context)

        response = {
            "query": query_text,
            "context": context,
            "suggestions": ranked_results,
            "confidence": self._calculate_confidence(ranked_results),
            "total_matches": len(combined_df),
        }
        summary = (
            f"search complete domain_scoped={bool(domain)} "
            f"suggestions={len(ranked_results)} confidence={response['confidence']:.2f}"
        )
        logger.info(summary)
        self._log_kb_event(summary)
        if ranked_results:
            top = ranked_results[0]
            detail = (
                f"best_match domain={top.get('domain', '')} "
                f"sdtm_variable={top.get('sdtm_variable', '')} "
                f"score={top.get('score', 0.0):.2f}"
            )
            logger.info(detail)
            self._log_kb_event(detail)
        return response

    def get_domain_context(self, domain: str) -> dict[str, Any]:
        """
        Get comprehensive context about a domain for LLM reasoning.

        Args:
            domain: Domain code (e.g., 'AE', 'DM', 'LB')

        Returns:
            Dictionary with domain context information
        """
        try:
            domain_vars = self.kb_manager.get_domain_variables(domain)
            required_vars = self.kb_manager.get_required_variables(domain)

            # Analyze domain structure
            roles = domain_vars["Role"].value_counts().to_dict()
            core_types = domain_vars["Core"].value_counts().to_dict()
            variable_types = domain_vars["Type"].value_counts().to_dict()

            # Get domain description
            domain_info = self._get_domain_description(domain)

            return {
                "domain": domain,
                "description": domain_info,
                "total_variables": len(domain_vars),
                "required_variables": len(required_vars),
                "roles": roles,
                "core_types": core_types,
                "variable_types": variable_types,
                "required_variable_list": required_vars[["Variable Name", "Variable Label", "Role"]].to_dict("records"),
                "all_variables": domain_vars[["Variable Name", "Variable Label", "Role", "Core", "Type"]].to_dict(
                    "records"
                ),
            }

        except Exception as exc:
            logger.error("Error getting domain context (%s)", type(exc).__name__)
            return {"domain": domain, "error": type(exc).__name__}

    def validate_mapping_suggestion(self, suggested_mapping: dict[str, Any], query_context: str) -> dict[str, Any]:
        """
        Validate a mapping suggestion against the knowledge base.

        Args:
            suggested_mapping: Mapping suggestion from LLM
            query_context: Original query context

        Returns:
            Validation results with scores and feedback
        """
        validation_results: dict[str, Any] = {
            "suggested_mapping": suggested_mapping,
            "query_context": query_context,
            "validation_score": 0.0,
            "issues": [],
            "suggestions": [],
            "is_valid": False,
        }

        try:
            # Extract suggested domain and variable
            suggested_domain = suggested_mapping.get("domain", "").upper()
            suggested_variable = suggested_mapping.get("sdtm_variable", "")

            # Check if domain exists
            if suggested_domain:
                domain_vars = self.kb_manager.get_domain_variables(suggested_domain)
                if domain_vars.empty:
                    validation_results["issues"].append(f"Domain '{suggested_domain}' not found in knowledge base")
                else:
                    validation_results["validation_score"] += 0.3

                    # Check if variable exists in domain
                    if suggested_variable:
                        var_match = domain_vars[domain_vars["Variable Name"] == suggested_variable]
                        if not var_match.empty:
                            validation_results["validation_score"] += 0.5
                            validation_results["is_valid"] = True

                            # Get variable details for additional validation
                            var_info = var_match.iloc[0]
                            validation_results["variable_info"] = {
                                "label": var_info.get("Variable Label", ""),
                                "role": var_info.get("Role", ""),
                                "core": var_info.get("Core", ""),
                                "type": var_info.get("Type", ""),
                            }
                        else:
                            validation_results["issues"].append(
                                f"Variable '{suggested_variable}' not found in domain '{suggested_domain}'"
                            )

                            # Suggest similar variables
                            similar_vars = self._find_similar_variables(suggested_variable, domain_vars)
                            if similar_vars:
                                validation_results["suggestions"].extend(similar_vars)

            # Check variable naming conventions
            if suggested_variable:
                naming_score = self._validate_naming_convention(suggested_variable, suggested_domain)
                validation_results["validation_score"] += naming_score * 0.2

        except Exception as exc:
            logger.error("Error validating mapping suggestion (%s)", type(exc).__name__)
            validation_results["issues"].append(f"Validation error: {type(exc).__name__}")

        return validation_results

    def generate_test_cases(self, domain: str, num_cases: int = 10) -> list[dict[str, Any]]:
        """
        Generate test cases for LLM evaluation.

        Args:
            domain: Domain to generate test cases for
            num_cases: Number of test cases to generate

        Returns:
            List of test cases with queries and expected answers
        """
        try:
            domain_vars = self.kb_manager.get_domain_variables(domain)
            if domain_vars.empty:
                return []

            test_cases = []

            # Generate different types of test cases
            for i in range(min(num_cases, len(domain_vars))):
                var_info = domain_vars.iloc[i]

                # Test case 1: Direct variable name query
                test_case_1 = {
                    "id": f"{domain}_{i}_direct",
                    "type": "direct_mapping",
                    "query": f"Map '{var_info['Variable Label']}' to SDTM variable",
                    "expected_domain": domain,
                    "expected_variable": var_info["Variable Name"],
                    "context": f"Domain: {domain}, Role: {var_info.get('Role', 'Unknown')}",
                }

                # Test case 2: Functional description query
                test_case_2 = {
                    "id": f"{domain}_{i}_functional",
                    "type": "functional_mapping",
                    "query": self._generate_functional_query(var_info),
                    "expected_domain": domain,
                    "expected_variable": var_info["Variable Name"],
                    "context": f"Domain: {domain}, Type: {var_info.get('Type', 'Unknown')}",
                }

                test_cases.extend([test_case_1, test_case_2])

            return test_cases[:num_cases]

        except Exception as exc:
            logger.error("Error generating test cases (%s)", type(exc).__name__)
            return []

    def _extract_key_terms(self, query_text: str) -> list[str]:
        """Extract key terms from natural language query."""
        return extract_key_terms(query_text)

    def _rank_mapping_suggestions(self, df: pd.DataFrame, query: str, context: str | None) -> list[dict[str, Any]]:
        """Rank mapping suggestions based on relevance."""
        return rank_suggestions(df, query=query, context=context)

    def _calculate_confidence(self, suggestions: list[dict[str, Any]]) -> float:
        """Calculate overall confidence score."""
        return calculate_confidence(suggestions)

    def _get_domain_description(self, domain: str) -> str:
        """Get human-readable description of domain."""
        return get_domain_description(domain)

    def _find_similar_variables(self, target_var: str, domain_vars: pd.DataFrame) -> list[str]:
        """Find variables similar to target variable."""
        if domain_vars is None or "Variable Name" not in domain_vars.columns:
            return []
        names = (str(v) for v in domain_vars["Variable Name"])
        return find_similar_variable_names(target_var, names)

    def _validate_naming_convention(self, variable_name: str, domain: str) -> float:
        """Validate SDTM variable naming conventions."""
        return validate_naming_convention(variable_name, domain)

    def _generate_functional_query(self, var_info: pd.Series) -> str:
        """Generate functional description query for test case."""
        return generate_functional_query(var_info)
