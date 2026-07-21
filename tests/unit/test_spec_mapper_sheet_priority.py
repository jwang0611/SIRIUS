"""Lock the documented ALS sheet-selection priority."""

from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook

from src.spec_mapper import SpecMapper


@pytest.fixture
def workbook_paths(tmp_path: Path) -> tuple[Path, Path]:
    als_path = tmp_path / "als.xlsx"
    template_path = tmp_path / "template.xlsx"

    als_workbook = Workbook()
    als_workbook.active.title = "Sheet1"
    als_workbook.create_sheet("eCRF")
    als_workbook.save(als_path)

    template_workbook = Workbook()
    template_workbook.active.title = "CONTENT"
    template_workbook["CONTENT"]["B4"] = "3.2"
    template_workbook.save(template_path)

    return als_path, template_path


def _config_file(tmp_path: Path, sheet_name: str) -> Path:
    config_path = tmp_path / f"config-{sheet_name}.yaml"
    config_path.write_text(f'als_defaults:\n  sheet_name: "{sheet_name}"\n', encoding="utf-8")
    return config_path


def test_explicit_sheet_overrides_environment_and_config(
    workbook_paths: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    als_path, template_path = workbook_paths
    monkeypatch.setenv("ALS_DEFAULT_SHEET", "EnvironmentSheet")

    mapper = SpecMapper(
        als_file=als_path,
        template_file=template_path,
        als_sheet="ExplicitSheet",
        config_file=_config_file(tmp_path, "ConfiguredSheet"),
    )

    assert mapper.als_sheet == "ExplicitSheet"


def test_environment_sheet_overrides_config(
    workbook_paths: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    als_path, template_path = workbook_paths
    monkeypatch.setenv("ALS_DEFAULT_SHEET", "EnvironmentSheet")

    mapper = SpecMapper(
        als_file=als_path,
        template_file=template_path,
        config_file=_config_file(tmp_path, "ConfiguredSheet"),
    )

    assert mapper.als_sheet == "EnvironmentSheet"


def test_config_sheet_is_used_without_argument_or_environment(
    workbook_paths: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    als_path, template_path = workbook_paths
    monkeypatch.delenv("ALS_DEFAULT_SHEET", raising=False)

    mapper = SpecMapper(
        als_file=als_path,
        template_file=template_path,
        config_file=_config_file(tmp_path, "ConfiguredSheet"),
    )

    assert mapper.als_sheet == "ConfiguredSheet"


def test_packaged_config_defaults_to_sheet1(workbook_paths: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    als_path, template_path = workbook_paths
    monkeypatch.delenv("ALS_DEFAULT_SHEET", raising=False)

    mapper = SpecMapper(als_file=als_path, template_file=template_path)

    assert mapper.als_sheet == "Sheet1"
