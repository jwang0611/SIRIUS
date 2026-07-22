"""Unit tests for the Spec Mapper structured write-result observability.

Covers:
  * the :mod:`write_result` data models (counts, invariants, serialization,
    safety of the serialized payload);
  * :meth:`ExcelWriter.update_cells` per-cell success / failure accounting;
  * :meth:`ExcelWriter.write_codelist_records` insert / merge / skip accounting
    against the **real** IG 3.2 template (copied into tmp_path).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from src.spec_mapper.core.excel_writer import ExcelWriter
from src.spec_mapper.models import CellUpdate, CodelistRecord
from src.spec_mapper.models.write_result import (
    STAGE_CELL_UPDATES,
    StageWriteResult,
    WriteIssue,
    WriteResult,
)

V32_TEMPLATE = Path("data/knowledge_base/template_spec/SDTM_template_IG3.2.xlsx")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
class TestWriteResultModel:
    def test_stage_invariant_attempted_equals_written_skipped_errors(self) -> None:
        stage = StageWriteResult(stage="s")
        stage.record_written()
        stage.record_written()
        stage.record_skipped(WriteIssue(code="sheet_not_found", stage="s", operation="op"))
        stage.record_error(WriteIssue(code="boom", stage="s", operation="op", detail="ValueError"))

        assert stage.attempted == 4
        assert stage.written == 2
        assert stage.skipped == 1
        assert len(stage.errors) == 1
        # invariant: attempted == written + skipped + len(errors)
        assert stage.attempted == stage.written + stage.skipped + len(stage.errors)

    def test_add_warning_does_not_change_counts(self) -> None:
        stage = StageWriteResult(stage="s")
        stage.record_written()
        stage.add_warning(WriteIssue(code="supp_label_too_long", stage="s", operation="op"))
        assert stage.attempted == 1
        assert stage.written == 1
        assert len(stage.warnings) == 1

    def test_aggregate_properties_and_write_problems(self) -> None:
        result = WriteResult()
        good = StageWriteResult(stage="a")
        good.record_written()
        good.record_written()
        bad = StageWriteResult(stage="b")
        bad.record_error(WriteIssue(code="boom", stage="b", operation="op"))
        result.merge_stage(good)
        result.merge_stage(bad)

        assert result.attempted == 3
        assert result.written == 2
        assert result.error_count == 1
        assert result.has_errors is True
        assert result.has_write_problems is True

    def test_clean_result_has_no_write_problems(self) -> None:
        result = WriteResult()
        stage = StageWriteResult(stage="a")
        stage.record_written()
        result.merge_stage(stage)
        assert result.has_write_problems is False
        assert result.summary() == {
            "attempted": 1,
            "written": 1,
            "skipped": 0,
            "warnings": 0,
            "errors": 0,
        }

    def test_skip_only_counts_as_write_problem(self) -> None:
        """A safe skip (written < attempted) must be surfaced as a problem."""
        result = WriteResult()
        stage = StageWriteResult(stage="a")
        stage.record_skipped(WriteIssue(code="domain_not_found", stage="a", operation="op"))
        result.merge_stage(stage)
        assert result.written < result.attempted
        assert result.has_write_problems is True

    def test_merge_stage_accumulates_same_stage_name(self) -> None:
        result = WriteResult()
        first = StageWriteResult(stage="cell_updates")
        first.record_written()
        second = StageWriteResult(stage="cell_updates")
        second.record_written()
        result.merge_stage(first)
        result.merge_stage(second)
        assert set(result.stages) == {"cell_updates"}
        assert result.stages["cell_updates"].written == 2

    def test_to_dict_is_serializable_and_safe(self) -> None:
        """Serialized issues expose only safe fields — never traceback / message text."""
        result = WriteResult()
        stage = StageWriteResult(stage="cell_updates")
        stage.record_error(
            WriteIssue(
                code="cell_write_failed",
                stage="cell_updates",
                operation="update_cell",
                sheet="DM",
                row=15,
                column=8,
                detail="ValueError",
            )
        )
        result.merge_stage(stage)

        payload = result.to_dict()
        import json

        json.dumps(payload)  # must be JSON-serializable

        assert payload["summary"]["errors"] == 1
        issue = payload["errors"][0]
        assert set(issue) == {"code", "stage", "operation", "sheet", "row", "column", "detail"}
        # detail is only the exception class name, not a message/traceback/path.
        assert issue["detail"] == "ValueError"


# ---------------------------------------------------------------------------
# ExcelWriter.update_cells
# ---------------------------------------------------------------------------
def _domain_workbook(tmp_path: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "DM"
    ws.cell(row=15, column=8, value="")
    path = tmp_path / "wb.xlsx"
    wb.save(path)
    return path


class TestUpdateCellsAccounting:
    def test_written_only_counts_successful_mutations(self, tmp_path: Path) -> None:
        writer = ExcelWriter(_domain_workbook(tmp_path))
        updates = [
            CellUpdate(sheet_name="DM", row=15, col=8, value="[RAW]SUBJECT.STUDYID"),
            # Targets a sheet that does not exist -> recoverable per-cell error.
            CellUpdate(sheet_name="NOPE", row=15, col=8, value="x"),
        ]
        result = writer.update_cells(updates)

        assert result.stage == STAGE_CELL_UPDATES
        assert result.attempted == 2
        assert result.written == 1
        assert len(result.errors) == 1
        err = result.errors[0]
        assert err.code == "cell_write_failed"
        assert err.sheet == "NOPE"
        # The good cell really was written.
        assert writer.workbook["DM"].cell(row=15, column=8).value == "[RAW]SUBJECT.STUDYID"

    def test_empty_updates_yield_empty_result(self, tmp_path: Path) -> None:
        writer = ExcelWriter(_domain_workbook(tmp_path))
        result = writer.update_cells([])
        assert result.attempted == 0
        assert result.written == 0


class TestGuardedMethodsReturnMutationCounts:
    """Guarded write methods must report *actual* mutation counts so a no-op is
    never miscounted as a real write."""

    def _domain(self, tmp_path: Path, *, with_content: bool) -> Path:
        wb = Workbook()
        ws = wb.active
        ws.title = "DM"
        ws.cell(row=14, column=1, value="STUDYID")
        ws.cell(row=15, column=1, value="DOMAIN")
        if with_content:
            ws.cell(row=20, column=1, value="CONTENT")
        path = tmp_path / "wb.xlsx"
        wb.save(path)
        return path

    def test_add_content_link_returns_zero_without_content_cell(self, tmp_path: Path) -> None:
        writer = ExcelWriter(self._domain(tmp_path, with_content=False))
        assert writer.add_content_link_to_domain("DM") == 0  # no-op

    def test_add_content_link_returns_one_with_content_cell(self, tmp_path: Path) -> None:
        writer = ExcelWriter(self._domain(tmp_path, with_content=True))
        assert writer.add_content_link_to_domain("DM") == 1  # real mutation

    def test_set_active_sheet_reports_missing_as_zero(self, tmp_path: Path) -> None:
        writer = ExcelWriter(self._domain(tmp_path, with_content=False))
        assert writer.set_active_sheet("NOPE") == 0
        assert writer.set_active_sheet("DM") == 1


# ---------------------------------------------------------------------------
# ExcelWriter.write_codelist_records (real template)
# ---------------------------------------------------------------------------
def _codelist_row_count(path: Path) -> int:
    wb = load_workbook(path)
    ws = wb["CODELIST"]
    count = sum(1 for r in range(3, ws.max_row + 1) if ws.cell(row=r, column=1).value)
    wb.close()
    return count


@pytest.mark.skipif(not V32_TEMPLATE.exists(), reason="IG 3.2 template not present")
class TestCodelistAccounting:
    def test_insert_new_record_counts_as_written(self, tmp_path: Path) -> None:
        tpl = tmp_path / "tpl.xlsx"
        shutil.copy2(V32_TEMPLATE, tpl)
        before = _codelist_row_count(tpl)

        writer = ExcelWriter(tpl)
        rec = CodelistRecord(
            domain="ZZ",
            testcd_var="ZZTESTCD",
            testcd_value="ZZVAL",
            source_variable="源变量",
            metadata_variable="ZZRAW",
        )
        result = writer.write_codelist_records([rec])
        out = tmp_path / "out.xlsx"
        writer.save(out)
        writer.close()

        assert result.written == 1
        assert result.attempted == 1
        assert not result.errors
        # A brand-new row was inserted for the new key.
        assert _codelist_row_count(out) == before + 1

    def test_merge_into_existing_row_fills_blank_and_inserts_nothing(self, tmp_path: Path) -> None:
        tpl = tmp_path / "tpl.xlsx"
        shutil.copy2(V32_TEMPLATE, tpl)

        # Discover an existing CODELIST row (domain, ID, Term) whose EDC VAR (J)
        # is still blank so the merge path fills it in place.
        wb = load_workbook(tpl)
        ws = wb["CODELIST"]
        target = None
        for r in range(3, ws.max_row + 1):
            a = ws.cell(row=r, column=1).value
            b = ws.cell(row=r, column=2).value
            f = ws.cell(row=r, column=6).value
            j = ws.cell(row=r, column=10).value
            if a and b and f and not j:
                target = (str(a), str(b), str(f), r)
                break
        wb.close()
        assert target is not None, "expected at least one mergeable CODELIST row in the real template"
        domain, testcd_var, testcd_value, row_idx = target
        before = _codelist_row_count(tpl)

        writer = ExcelWriter(tpl)
        rec = CodelistRecord(
            domain=domain,
            testcd_var=testcd_var,
            testcd_value=testcd_value,
            source_variable="",  # leave I alone
            metadata_variable="MERGEDVAR",
        )
        result = writer.write_codelist_records([rec])
        out = tmp_path / "out.xlsx"
        writer.save(out)
        writer.close()

        assert result.written == 1
        assert not result.errors

        wb2 = load_workbook(out)
        ws2 = wb2["CODELIST"]
        assert str(ws2.cell(row=row_idx, column=10).value) == "MERGEDVAR"
        wb2.close()
        # Merge must not append a duplicate row.
        assert _codelist_row_count(out) == before

    def test_missing_codelist_sheet_marks_records_skipped(self, tmp_path: Path) -> None:
        wb = Workbook()
        wb.active.title = "DM"
        path = tmp_path / "no_codelist.xlsx"
        wb.save(path)

        writer = ExcelWriter(path)
        rec = CodelistRecord(domain="EG", testcd_var="EGTESTCD", testcd_value="QT", source_variable="s")
        result = writer.write_codelist_records([rec])

        assert result.written == 0
        assert result.skipped == 1
        assert result.warnings[0].code == "sheet_not_found"
