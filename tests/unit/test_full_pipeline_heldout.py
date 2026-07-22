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
KEY_FIELDS = ("annotation_table", "metadata_table", "annotation_variable", "metadata_variable")


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _key(row: dict) -> tuple[str, str, str, str]:
    return tuple(str(row.get(field, "") or "").strip().lower() for field in KEY_FIELDS)


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
    assert cohorts == {"KB_OVERLAP": 181, "AI_RECOMMENDATION": 309}
    assert dict(reference_sources) == manifest["curation"]["reference_source_counts"]
    assert dict(cohorts) == manifest["curation"]["evaluation_cohort_counts"]


def test_heldout_cohort_labels_match_current_production_kb():
    rows = _load_json(HELDOUT_PATH)
    kb_keys = {_key(row) for row in _load_json(KB_PATH)}

    for row in rows:
        expected = "KB_OVERLAP" if _key(row) in kb_keys else "AI_RECOMMENDATION"
        assert row["evaluation_cohort"] == expected


def test_manifest_is_bound_to_current_production_kb():
    manifest = _load_json(MANIFEST_PATH)
    actual_hash = hashlib.sha256(KB_PATH.read_bytes()).hexdigest()

    assert manifest["production_kb"]["sha256"] == actual_hash
    assert manifest["source"]["workbook_sha256"] == ("92883e555254fa93e455f58938a662ce9b4cf678d52c83beb284e98bf7fbd414")
    assert manifest["curation"]["included_rows"] == 490
    assert manifest["curation"]["excluded_rows"] == 26
