"""Assemble extracted (form, field) pairs into canonical 4-field records.

Pure and dependency-free so the load-bearing invariants can be unit tested
directly. Two invariants come straight from ``sdtm_processor.process_mappings``
/ ``_process_table_sequentially``:

* ``metadata_table`` must be **non-empty** (else the record is dropped).
* ``metadata_variable`` must be **unique within a ``metadata_table`` group**
  (else fields collapse to one key and all but the first are silently lost).

Because a blank unique-eCRF carries no SAS dataset/variable codes, we fall back
to the form name for ``metadata_table`` and to the field label (or a synthetic
code) for ``metadata_variable``, guaranteeing both invariants here.
"""

from __future__ import annotations

from src.processors.acrf.models import AcrfConfig, AcrfRecord


def _unique(candidate: str, used: set[str]) -> str:
    """Return ``candidate`` or ``candidate_2``/``_3`` … until unused."""
    if candidate not in used:
        return candidate
    i = 2
    while f"{candidate}_{i}" in used:
        i += 1
    return f"{candidate}_{i}"


def assemble_records(
    form_fields: list[tuple[str, list[str]]],
    cfg: AcrfConfig,
) -> list[AcrfRecord]:
    """Build ordered records from ``[(form_name, [field_label, ...]), ...]``.

    ``num`` is 1-based over (form order, field order). ``metadata_variable``
    uniqueness is tracked **per ``metadata_table``** so repeated form names stay
    safe after the pipeline groups them together.
    """
    records: list[AcrfRecord] = []
    used_by_table: dict[str, set[str]] = {}
    num = 0
    for form_idx, (form_name, fields) in enumerate(form_fields, start=1):
        metadata_table = (form_name or "").strip() or f"FORM_{form_idx:02d}"
        used = used_by_table.setdefault(metadata_table, set())
        for field_idx, label in enumerate(fields, start=1):
            annotation_variable = (label or "").strip()
            if not annotation_variable:
                continue
            num += 1
            if cfg.metadata_variable_mode == "synthetic":
                base = f"F{form_idx:02d}_{field_idx:03d}"
            else:
                base = annotation_variable
            metadata_variable = _unique(base, used)
            used.add(metadata_variable)
            records.append(
                AcrfRecord(
                    num=num,
                    metadata_table=metadata_table,
                    metadata_variable=metadata_variable,
                    annotation_table=(form_name or "").strip(),
                    annotation_variable=annotation_variable,
                )
            )
    return records


def records_to_json_rows(records: list[AcrfRecord]) -> list[dict[str, str]]:
    """The 4-key rows the recommender consumes (order preserved, no ``num``)."""
    return [
        {
            "metadata_table": r.metadata_table,
            "metadata_variable": r.metadata_variable,
            "annotation_table": r.annotation_table,
            "annotation_variable": r.annotation_variable,
        }
        for r in records
    ]
