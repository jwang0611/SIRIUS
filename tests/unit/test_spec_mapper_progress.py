"""Real stage progress reporting for Spec Mapper."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.spec_mapper import SpecMapper


def test_process_reports_real_stages_in_order(tmp_path):
    als_file = tmp_path / "als.xlsx"
    template_file = tmp_path / "template.xlsx"
    output_file = tmp_path / "out.xlsx"
    als_file.write_bytes(b"fixture")
    template_file.write_bytes(b"fixture")

    reader = MagicMock()
    reader.read_als_records.return_value = []
    reader.read_template_records.return_value = []
    writer = MagicMock()
    writer.workbook.sheetnames = []
    mapper_core = MagicMock()
    mapper_core.map.return_value = ([], [], [], [], [])
    events: list[tuple[str, int, int]] = []

    with (
        patch("src.spec_mapper.ExcelReader", return_value=reader),
        patch("src.spec_mapper.ExcelWriter", return_value=writer),
        patch("src.spec_mapper.Mapper", return_value=mapper_core),
        patch("src.spec_mapper.shutil.copy2"),
    ):
        mapper = SpecMapper(als_file=als_file, template_file=template_file)
        stats = mapper.process(output_file=output_file, progress_callback=lambda *args: events.append(args))

    assert stats["als_records"] == 0
    assert [completed for _, completed, _ in events] == [0, 1, 2, 3, 4, 5]
    assert all(total == 5 for _, _, total in events)
    assert events[-1][0] == "工作簿生成完成"
