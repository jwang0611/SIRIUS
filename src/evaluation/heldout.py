"""Strict metadata-only held-out dataset and leakage contracts.

Release evaluation data is deliberately external to the repository.  This
module validates an opaque manifest, binds every source file by hash, and
checks the resulting input identities against the production/project
knowledge supplied to the evaluator and the built-in semantic shortcuts.
Reports contain hashes and counts only; they never echo clinical metadata.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from src.config.domain_semantic_map import (
    ANNOTATION_KEYWORD_DOMAIN_MAP,
    CHINESE_TABLE_DOMAIN_MAP,
    DOMAIN_KEYWORDS,
    is_valid_domain,
    strip_supp_prefix,
)
from src.evaluation.run_manifest import hash_file
from src.knowledge_base.exact_match_utils import normalize_deep

MANIFEST_SCHEMA_VERSION = "sirius-heldout-manifest/v1"
ALLOWED_MANIFEST_FIELDS = {"schema_version", "evaluation_profile", "distinct_studies_confirmed", "datasets"}
ALLOWED_DATASET_FIELDS = {
    "dataset_id",
    "source_class",
    "deidentified",
    "authorized_for_engineering",
    "schema_version",
    "file",
    "sha256",
    "row_count",
}
ALLOWED_SOURCE_CLASSES = {"metadata_only_als", "metadata_only_edc", "metadata_only_crf"}
KEY_FIELDS = ("annotation_table", "metadata_table", "annotation_variable", "metadata_variable")
FIELD_ALIASES = {
    "annotation_table": ("annotation_table", "表名", "表名称", "FormName"),
    "metadata_table": ("metadata_table", "表", "SASDatasetName"),
    "annotation_variable": ("annotation_variable", "变量名", "变量名/注释/内嵌表名", "ItemName"),
    "metadata_variable": ("metadata_variable", "变量", "SASFieldName"),
}
REQUIRED_MAPPING_FIELDS = (*KEY_FIELDS, "SDTM_Domain", "SDTM_Variable")
ALLOWED_ROW_FIELDS = {
    *REQUIRED_MAPPING_FIELDS,
    "evaluation_id",
}
FORBIDDEN_MANIFEST_KEYS = {
    "study_name",
    "protocol",
    "protocol_id",
    "sponsor",
    "project_name",
    "workbook",
    "site",
    "investigator",
}
_OPAQUE_DATASET_ID = re.compile(r"^study-[0-9]{3,}$")
_OPAQUE_EVALUATION_ID = re.compile(r"^EVAL-[0-9]{4,}$")
_SCHEMA_VERSION = re.compile(r"^[a-z][a-z0-9_-]{1,31}/v[1-9][0-9]*$")
_DOMAIN_SPLIT = re.compile(r"[|/;]")
_KNOWLEDGE_SUFFIXES = {".json", ".jsonl", ".yaml", ".yml", ".csv", ".tsv", ".parquet", ".xlsx", ".xls"}
_TEXT_KNOWLEDGE_SUFFIXES = {".json", ".jsonl", ".yaml", ".yml", ".csv", ".tsv"}
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_KNOWN_NON_MAPPING_SOURCES = {
    (_PROJECT_ROOT / "data/knowledge_base/structured/sdtm_spec_enhanced.json").resolve(),
    (_PROJECT_ROOT / "data/knowledge_base/structured/sdtm_spec_enhanced.parquet").resolve(),
}


def _normalized(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def mapping_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    """Return the canonical four-field input identity."""
    return tuple(_normalized(row.get(field)) for field in KEY_FIELDS)  # type: ignore[return-value]


def deep_mapping_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    """Return the production matcher's aggressive four-field identity."""
    return tuple(normalize_deep(row.get(field)) for field in KEY_FIELDS)  # type: ignore[return-value]


def _mapping_key_variants(row: dict[str, Any]) -> set[tuple[str, str, str, str]]:
    return {key for key in (mapping_key(row), deep_mapping_key(row)) if all(key)}


def _semantic_pair_variants(row: dict[str, Any]) -> set[tuple[str, str]]:
    return {
        pair
        for pair in (
            (_normalized(row.get("annotation_table")), _normalized(row.get("annotation_variable"))),
            (normalize_deep(row.get("annotation_table")), normalize_deep(row.get("annotation_variable"))),
        )
        if all(pair)
    }


def mapping_fingerprint(row: dict[str, Any]) -> str:
    """Hash an input identity so diagnostics do not disclose row content."""
    payload = json.dumps(mapping_key(row), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def manifest_fingerprint(path: Path) -> str:
    """Hash the exact normalized manifest used to authorize a baseline."""
    return hash_file(path, normalize_text=True)


def _forbidden_manifest_paths(value: Any, prefix: str = "$") -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            key_text = str(key).casefold()
            path = f"{prefix}.{key}"
            if key_text in FORBIDDEN_MANIFEST_KEYS:
                paths.append(path)
            paths.extend(_forbidden_manifest_paths(nested, path))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            paths.extend(_forbidden_manifest_paths(nested, f"{prefix}[{index}]"))
    return paths


def _load_json_list(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot load held-out JSON: {exc}") from exc
    if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
        raise ValueError("Held-out file must contain a JSON list of objects")
    return payload


def validate_dataset_manifest(
    manifest_path: Path,
    *,
    require_release: bool = True,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Validate a held-out manifest and return a redacted report plus rows."""
    errors: list[str] = []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot load held-out manifest {manifest_path.name}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ValueError("Held-out manifest must contain a JSON object")

    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        errors.append(f"schema_version must be {MANIFEST_SCHEMA_VERSION!r}")
    unexpected_manifest_fields = sorted(set(manifest) - ALLOWED_MANIFEST_FIELDS)
    if unexpected_manifest_fields:
        errors.append(f"manifest contains {len(unexpected_manifest_fields)} unsupported field(s)")
    profile = manifest.get("evaluation_profile")
    if require_release and profile != "release":
        errors.append("evaluation_profile must be 'release'")
    if require_release and manifest.get("distinct_studies_confirmed") is not True:
        errors.append("distinct_studies_confirmed must be true")
    forbidden_paths = _forbidden_manifest_paths(manifest)
    if forbidden_paths:
        errors.append(f"manifest contains identifying fields ({len(forbidden_paths)})")

    datasets = manifest.get("datasets")
    if not isinstance(datasets, list):
        datasets = []
        errors.append("datasets must be a list")
    if require_release and len(datasets) < 2:
        errors.append("release evaluation requires at least two distinct studies")

    seen_dataset_ids: set[str] = set()
    seen_keys: set[tuple[str, str, str, str]] = set()
    seen_evaluation_ids: set[str] = set()
    all_rows: list[dict[str, Any]] = []
    dataset_reports: list[dict[str, Any]] = []

    for index, dataset in enumerate(datasets):
        label = f"datasets[{index}]"
        if not isinstance(dataset, dict):
            errors.append(f"{label} must be an object")
            continue
        unexpected_dataset_fields = sorted(set(dataset) - ALLOWED_DATASET_FIELDS)
        if unexpected_dataset_fields:
            errors.append(f"{label} contains {len(unexpected_dataset_fields)} unsupported field(s)")
        dataset_id = str(dataset.get("dataset_id", ""))
        dataset_id_valid = bool(_OPAQUE_DATASET_ID.fullmatch(dataset_id))
        if not dataset_id_valid:
            errors.append(f"{label}.dataset_id must use the opaque form study-NNN")
        if dataset_id in seen_dataset_ids:
            errors.append(f"duplicate dataset_id at {label}")
        seen_dataset_ids.add(dataset_id)

        source_class = str(dataset.get("source_class", "")).strip()
        schema_version = str(dataset.get("schema_version", "")).strip()
        if source_class not in ALLOWED_SOURCE_CLASSES:
            errors.append(f"{label}.source_class must be one of {sorted(ALLOWED_SOURCE_CLASSES)}")
        schema_version_valid = bool(_SCHEMA_VERSION.fullmatch(schema_version))
        if not schema_version_valid:
            errors.append(f"{label}.schema_version must use the opaque form name/vN")
        if dataset.get("deidentified") is not True:
            errors.append(f"{label}.deidentified must be true")
        if dataset.get("authorized_for_engineering") is not True:
            errors.append(f"{label}.authorized_for_engineering must be true")

        raw_file = dataset.get("file")
        if not isinstance(raw_file, str) or not raw_file.strip():
            errors.append(f"{label}.file is required")
            continue
        source_path = Path(raw_file)
        if source_path.is_absolute() or source_path.name != raw_file or source_path.name != f"{dataset_id}.json":
            errors.append(f"{label}.file must be a colocated opaque study-NNN.json name")
            continue
        if not source_path.is_absolute():
            source_path = manifest_path.parent / source_path
        try:
            source_path = source_path.resolve(strict=True)
        except OSError:
            errors.append(f"{label}.file does not exist")
            continue
        if source_path.suffix.casefold() != ".json":
            errors.append(f"{label}.file must be JSON")
            continue

        actual_hash = hash_file(source_path, normalize_text=True)
        expected_hash = str(dataset.get("sha256", ""))
        if actual_hash != expected_hash:
            errors.append(f"{label}.sha256 does not match the source file")

        try:
            rows = _load_json_list(source_path)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if dataset.get("row_count") != len(rows):
            errors.append(f"{label}.row_count does not match the source file")

        for row_index, row in enumerate(rows):
            row_label = f"{label} row {row_index + 1}"
            unexpected = sorted(set(row) - ALLOWED_ROW_FIELDS)
            if unexpected:
                errors.append(f"{row_label} contains {len(unexpected)} non-metadata field(s)")
            if any(not str(row.get(field, "") or "").strip() for field in KEY_FIELDS):
                errors.append(f"{row_label} has an incomplete four-field input identity")
            variable = str(row.get("SDTM_Variable", "") or "").strip()
            domain = str(row.get("SDTM_Domain", "") or "").strip()
            if not variable or (not domain and variable.upper() != "NOT SUBMITTED"):
                errors.append(f"{row_label} has incomplete SDTM ground truth")
            domain_tokens = [token.strip().upper() for token in _DOMAIN_SPLIT.split(domain) if token.strip()]
            if domain_tokens and any(
                not (is_valid_domain(token) or is_valid_domain(strip_supp_prefix(token))) for token in domain_tokens
            ):
                errors.append(f"{row_label} contains an invalid SDTM domain")
            evaluation_id = str(row.get("evaluation_id", "") or "").strip()
            if not _OPAQUE_EVALUATION_ID.fullmatch(evaluation_id):
                errors.append(f"{row_label}.evaluation_id must use the opaque form EVAL-NNNN")
            qualified_id = f"{dataset_id}:{evaluation_id}"
            if qualified_id in seen_evaluation_ids:
                errors.append(f"duplicate evaluation_id within {label}")
            seen_evaluation_ids.add(qualified_id)
            key_variants = _mapping_key_variants(row)
            if seen_keys.intersection(key_variants):
                errors.append(f"duplicate input identity across held-out datasets: {mapping_fingerprint(row)}")
            seen_keys.update(key_variants)
            all_rows.append({**row, "evaluation_id": qualified_id})

        dataset_reports.append(
            {
                "dataset_id": dataset_id if dataset_id_valid else "INVALID",
                "source_class": source_class if source_class in ALLOWED_SOURCE_CLASSES else "INVALID",
                "schema_version": schema_version if schema_version_valid else "INVALID",
                "deidentified": dataset.get("deidentified") is True,
                "authorized_for_engineering": dataset.get("authorized_for_engineering") is True,
                "sha256": actual_hash,
                "row_count": len(rows),
            }
        )

    report = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "evaluation_profile_valid": profile == "release",
        "manifest_sha256": manifest_fingerprint(manifest_path),
        "dataset_count": len(datasets),
        "row_count": len(all_rows),
        "datasets": dataset_reports,
        "valid": not errors,
        "errors": errors,
    }
    return report, all_rows


def _iter_mapping_rows(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        canonical = {}
        for field, aliases in FIELD_ALIASES.items():
            matched = next((value[alias] for alias in aliases if alias in value), None)
            if matched is not None:
                canonical[field] = matched
        if all(field in canonical for field in KEY_FIELDS):
            yield canonical
        prompt_input = value.get("input")
        if isinstance(prompt_input, str) and value.get("domain") and value.get("output"):
            prompt_parts = re.split(r"[/／]", prompt_input, maxsplit=1)
            if len(prompt_parts) == 2 and all(part.strip() for part in prompt_parts):
                table, variable = (part.strip() for part in prompt_parts)
                yield {
                    "annotation_table": table,
                    "metadata_table": table,
                    "annotation_variable": variable,
                    "metadata_variable": variable,
                }
        for nested in value.values():
            yield from _iter_mapping_rows(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _iter_mapping_rows(nested)


def _load_knowledge_rows(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.casefold()
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        return list(_iter_mapping_rows(payload))
    if suffix == ".jsonl":
        return list(_iter_mapping_rows(pd.read_json(path, lines=True).to_dict(orient="records")))
    if suffix in {".yaml", ".yml"}:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        return list(_iter_mapping_rows(payload))
    if suffix == ".parquet":
        return list(_iter_mapping_rows(pd.read_parquet(path).to_dict(orient="records")))
    if suffix in {".csv", ".tsv"}:
        return list(
            _iter_mapping_rows(pd.read_csv(path, sep="\t" if suffix == ".tsv" else ",").to_dict(orient="records"))
        )
    if suffix in {".xlsx", ".xls"}:
        sheets = pd.read_excel(path, sheet_name=None)
        return [row for frame in sheets.values() for row in _iter_mapping_rows(frame.to_dict(orient="records"))]
    raise ValueError("Unsupported knowledge source format")


def _knowledge_files(roots: Iterable[Path]) -> list[Path]:
    files: set[Path] = set()
    for root in roots:
        resolved = root.resolve()
        if resolved.is_file():
            if resolved.suffix.casefold() not in _KNOWLEDGE_SUFFIXES:
                raise ValueError("Knowledge root contains an unsupported source file")
            files.add(resolved)
        elif resolved.is_dir():
            candidates = [
                path.resolve() for path in resolved.rglob("*") if path.is_file() and not path.name.startswith(".")
            ]
            unsupported = [path for path in candidates if path.suffix.casefold() not in _KNOWLEDGE_SUFFIXES]
            if unsupported:
                raise ValueError(f"Knowledge root contains {len(unsupported)} unsupported source file(s)")
            files.update(candidates)
    return sorted(files)


def _semantic_keyword_matches(row: dict[str, Any], keywords: set[str]) -> bool:
    fields = [_normalized(row.get(field)) for field in KEY_FIELDS]
    for keyword in keywords:
        if len(keyword) <= 3:
            if keyword in fields:
                return True
            if keyword.isascii() and any(keyword in re.findall(r"[a-z0-9]+", field) for field in fields):
                return True
        elif any(keyword in field for field in fields):
            return True
    return False


def scan_for_leakage(
    rows: list[dict[str, Any]],
    *,
    knowledge_roots: Iterable[Path],
) -> dict[str, Any]:
    """Return a redacted overlap report for KB and semantic-map leakage."""
    roots = list(knowledge_roots)
    missing_roots = [str(index) for index, root in enumerate(roots) if not root.exists()]
    if missing_roots:
        return {
            "valid": False,
            "overlap_count": 0,
            "overlaps": [],
            "semantic_coverage_count": 0,
            "semantic_coverage": [],
            "sources": [],
            "errors": ["Knowledge roots do not exist at argument positions: " + ", ".join(missing_roots)],
        }
    heldout_by_key = {key: mapping_fingerprint(row) for row in rows for key in _mapping_key_variants(row)}
    heldout_by_semantic_pair = {pair: mapping_fingerprint(row) for row in rows for pair in _semantic_pair_variants(row)}
    hits: set[tuple[str, str, str]] = set()
    semantic_coverage_hits: set[tuple[str, str, str]] = set()
    sources: list[dict[str, Any]] = []

    try:
        knowledge_files = _knowledge_files(roots)
    except ValueError as exc:
        return {
            "valid": False,
            "overlap_count": 0,
            "overlaps": [],
            "semantic_coverage_count": 0,
            "semantic_coverage": [],
            "sources": [],
            "errors": [str(exc)],
        }

    for path in knowledge_files:
        try:
            source_hash = hash_file(path, normalize_text=path.suffix.casefold() in _TEXT_KNOWLEDGE_SUFFIXES)
            source_ref = f"knowledge:{source_hash[:12]}"
            source_rows = _load_knowledge_rows(path)
        except (OSError, ValueError, json.JSONDecodeError, yaml.YAMLError) as exc:
            return {
                "valid": False,
                "overlap_count": 0,
                "overlaps": [],
                "semantic_coverage_count": 0,
                "semantic_coverage": [],
                "sources": sources,
                "errors": [f"Cannot inspect knowledge source ({type(exc).__name__})"],
            }
        allowlisted_non_mapping = path.resolve() in _KNOWN_NON_MAPPING_SOURCES
        sources.append(
            {
                "source_ref": source_ref,
                "sha256": source_hash,
                "mapping_rows": len(source_rows),
                "allowlisted_non_mapping": allowlisted_non_mapping,
            }
        )
        if not source_rows and not allowlisted_non_mapping:
            return {
                "valid": False,
                "overlap_count": 0,
                "overlaps": [],
                "semantic_coverage_count": 0,
                "semantic_coverage": [],
                "sources": sources,
                "errors": ["Knowledge source produced zero inspectable mapping rows"],
            }
        for source_row in source_rows:
            for key in _mapping_key_variants(source_row):
                fingerprint = heldout_by_key.get(key)
                if fingerprint:
                    hits.add((fingerprint, "knowledge_exact", source_ref))
            for semantic_pair in _semantic_pair_variants(source_row):
                fingerprint = heldout_by_semantic_pair.get(semantic_pair)
                if fingerprint:
                    hits.add((fingerprint, "knowledge_semantic_pair", source_ref))

    exact_tables = {_normalized(key) for key in CHINESE_TABLE_DOMAIN_MAP}
    keywords = {_normalized(keyword) for keyword in ANNOTATION_KEYWORD_DOMAIN_MAP if _normalized(keyword)}
    keywords.update(
        _normalized(keyword) for values in DOMAIN_KEYWORDS.values() for keyword in values if _normalized(keyword)
    )
    for row in rows:
        fingerprint = mapping_fingerprint(row)
        table = _normalized(row.get("annotation_table"))
        if table in exact_tables:
            hits.add((fingerprint, "semantic_table_exact", "domain_semantic_map.py"))
        if _semantic_keyword_matches(row, keywords):
            semantic_coverage_hits.add((fingerprint, "semantic_keyword", "domain_semantic_map.py"))

    overlaps = [
        {"input_sha256": fingerprint, "kind": kind, "source_ref": source_ref}
        for fingerprint, kind, source_ref in sorted(hits)
    ]
    semantic_coverage = [
        {"input_sha256": fingerprint, "kind": kind, "source_ref": source_ref}
        for fingerprint, kind, source_ref in sorted(semantic_coverage_hits)
    ]
    return {
        "valid": not overlaps,
        "overlap_count": len(overlaps),
        "overlaps": overlaps,
        "semantic_coverage_count": len(semantic_coverage),
        "semantic_coverage": semantic_coverage,
        "sources": sources,
        "errors": [],
    }
