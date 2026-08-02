"""Integrity contract for the curated full-pipeline held-out dataset."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELDOUT_PATH = ROOT / "data/evaluation/full_pipeline_heldout_v1.json"
MANIFEST_PATH = ROOT / "data/evaluation/full_pipeline_heldout_v1.manifest.json"
KB_PATH = ROOT / "data/knowledge_base/structured/ALS2SDTM_Mapping_Template_v1.0.json"
GITATTRIBUTES_PATH = ROOT / ".gitattributes"
KEY_FIELDS = ("annotation_table", "metadata_table", "annotation_variable", "metadata_variable")


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _key(row: dict) -> tuple[str, str, str, str]:
    return tuple(str(row.get(field, "") or "").strip().lower() for field in KEY_FIELDS)


def _normalized_mapping(row: dict) -> tuple[str, str]:
    domain = "".join(str(row.get("SDTM_Domain", "") or "").split()).upper()
    variable = " ".join(str(row.get("SDTM_Variable", "") or "").split()).upper()
    for separator in ("=", "|", "/", ";"):
        variable = variable.replace(f" {separator}", separator).replace(f"{separator} ", separator)
    return domain, variable


def _sha256_with_lf_endings(path: Path) -> str:
    """Hash text data independently of Git's checkout line-ending mode."""
    normalized = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(normalized).hexdigest()


def test_heldout_is_complete_unique_and_metadata_only():
    rows = _load_json(HELDOUT_PATH)

    assert len(rows) == 490
    assert len({_key(row) for row in rows}) == len(rows)
    assert all(all(_key(row)) for row in rows)
    assert all(row["SDTM_Variable"] for row in rows)
    assert all(row["SDTM_Domain"] or row["SDTM_Variable"] == "NOT SUBMITTED" for row in rows)
    assert all(row["SDTM_Domain"] == "" for row in rows if row["SDTM_Variable"] == "NOT SUBMITTED")

    forbidden = {"AI_SDTM_Domain", "AI_SDTM_Variable", "Score", "Comments", "reviewer"}
    assert all(forbidden.isdisjoint(row) for row in rows)


def test_heldout_contains_both_reference_sources_and_pipeline_cohorts():
    rows = _load_json(HELDOUT_PATH)
    manifest = _load_json(MANIFEST_PATH)

    reference_sources = Counter(row["reference_source"] for row in rows)
    cohorts = Counter(row["evaluation_cohort"] for row in rows)

    assert reference_sources == {"KB": 278, "LLM": 212}
    assert cohorts == {"AI_RECOMMENDATION": 309, "KB_AGREE": 107, "KB_DISAGREE": 74}
    assert dict(reference_sources) == manifest["curation"]["reference_source_counts"]
    assert dict(cohorts) == manifest["curation"]["evaluation_cohort_counts"]


def test_heldout_cohort_labels_match_current_production_kb():
    rows = _load_json(HELDOUT_PATH)
    kb_by_key = {_key(row): row for row in _load_json(KB_PATH)}

    for row in rows:
        kb_row = kb_by_key.get(_key(row))
        if kb_row is None:
            expected = "AI_RECOMMENDATION"
        elif _normalized_mapping(kb_row) == _normalized_mapping(row):
            expected = "KB_AGREE"
        else:
            expected = "KB_DISAGREE"
        assert row["evaluation_cohort"] == expected


def test_manifest_is_bound_to_current_production_kb():
    manifest = _load_json(MANIFEST_PATH)
    actual_hash = _sha256_with_lf_endings(KB_PATH)

    assert manifest["evaluation_profile"] == "characterization_only"
    assert manifest["release_gate_eligible"] is False
    assert set(manifest["release_gate_blockers"]) == {
        "single_source_workbook",
        "production_knowledge_base_overlap",
    }
    assert manifest["production_kb"]["sha256"] == actual_hash
    assert manifest["source"]["workbook_sha256"] == ("92883e555254fa93e455f58938a662ce9b4cf678d52c83beb284e98bf7fbd414")
    assert manifest["curation"]["included_rows"] == 490
    assert manifest["curation"]["excluded_rows"] == 26
    assert manifest["production_kb"]["mapping_agreement"] == {
        "overlap_rows": 181,
        "agree_rows": 107,
        "disagree_rows": 74,
        "exact_rate_against_manual_ground_truth": 107 / 181,
        "comparison_fields": ["SDTM_Domain", "SDTM_Variable"],
        "normalization": (
            "Case-insensitive comparison after trimming whitespace and normalizing mapping-expression separators."
        ),
    }


def test_hash_pinned_data_json_is_forced_to_lf():
    attributes = GITATTRIBUTES_PATH.read_text(encoding="utf-8")

    assert "data/**/*.json text eol=lf" in attributes.splitlines()


def test_manifest_hash_is_independent_of_windows_line_endings(tmp_path):
    lf_path = tmp_path / "lf.json"
    crlf_path = tmp_path / "crlf.json"
    lf_path.write_bytes(b'{\n  "value": 1\n}\n')
    crlf_path.write_bytes(b'{\r\n  "value": 1\r\n}\r\n')

    assert _sha256_with_lf_endings(lf_path) == _sha256_with_lf_endings(crlf_path)
