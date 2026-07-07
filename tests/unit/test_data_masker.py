"""Unit tests for ``src.infrastructure.data_masker``."""

from __future__ import annotations

import pytest

from src.infrastructure.data_masker import DataMasker


@pytest.fixture
def masker() -> DataMasker:
    return DataMasker()


class TestMaskText:
    def test_empty_returns_empty(self, masker):
        assert masker.mask_text("") == ""

    def test_none_returns_none(self, masker):
        assert masker.mask_text(None) is None

    def test_plain_text_preserved(self, masker):
        assert masker.mask_text("Adverse event term") == "Adverse event term"

    def test_redacts_ssn(self, masker):
        out = masker.mask_text("SSN is 123-45-6789")
        assert "123-45-6789" not in out
        assert "[REDACTED]" in out

    def test_redacts_subject_id_pattern(self, masker):
        out = masker.mask_text("Subject 001-0023 enrolled")
        assert "[REDACTED]" in out

    def test_redacts_full_name(self, masker):
        out = masker.mask_text("Patient: Smith, John")
        assert "Smith, John" not in out

    def test_redacts_dob_context(self, masker):
        out = masker.mask_text("DOB: 1980-05-15")
        assert "[REDACTED]" in out

    def test_preserves_non_dob_dates(self, masker):
        out = masker.mask_text("Visit date 2024-01-01")
        assert "2024-01-01" in out

    def test_redacts_email(self, masker):
        out = masker.mask_text("Contact user@example.com for details")
        assert "user@example.com" not in out

    def test_redacts_phone(self, masker):
        out = masker.mask_text("Call 555-123-4567 now")
        assert "555-123-4567" not in out

    def test_multiple_patterns(self, masker):
        out = masker.mask_text("SSN 123-45-6789 email x@y.com")
        assert out.count("[REDACTED]") >= 2


class TestMaskVariableData:
    def test_masks_annotation_fields(self, masker):
        data = {
            "metadata_table": "DM",
            "metadata_variable": "SUBJID",
            "annotation_variable": "Patient 001-0023",
            "annotation_table": "Contact: x@y.com",
        }
        out = masker.mask_variable_data(data)
        assert "001-0023" not in out["annotation_variable"]
        assert "x@y.com" not in out["annotation_table"]

    def test_does_not_mask_metadata(self, masker):
        data = {
            "metadata_table": "DM",
            "metadata_variable": "SUBJID",
            "annotation_variable": "subject id",
        }
        out = masker.mask_variable_data(data)
        assert out["metadata_table"] == "DM"
        assert out["metadata_variable"] == "SUBJID"

    def test_returns_copy_not_mutation(self, masker):
        data = {"annotation_variable": "123-45-6789"}
        out = masker.mask_variable_data(data)
        assert data["annotation_variable"] == "123-45-6789"
        assert out["annotation_variable"] != data["annotation_variable"]

    def test_non_string_values_unchanged(self, masker):
        data = {"annotation_variable": 42}
        out = masker.mask_variable_data(data)
        assert out["annotation_variable"] == 42

    def test_missing_fields_ok(self, masker):
        out = masker.mask_variable_data({"metadata_variable": "X"})
        assert out["metadata_variable"] == "X"
