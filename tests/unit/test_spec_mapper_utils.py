"""Unit tests for spec_mapper utility modules."""

from __future__ import annotations

import logging
import time

import pytest
import yaml

from src.spec_mapper.utils.config_loader import ConfigLoader
from src.spec_mapper.utils.logger import get_default_log_filename, setup_logging
from src.spec_mapper.utils.performance import (
    PerformanceMetrics,
    PerformanceMonitor,
    get_monitor,
    reset_monitor,
)


class TestPerformanceMetrics:
    def test_str_contains_metrics(self):
        m = PerformanceMetrics(
            name="load",
            start_memory_mb=100.0,
            peak_memory_mb=150.0,
            end_memory_mb=120.0,
            duration_seconds=1.5,
        )
        s = str(m)
        assert "load" in s
        assert "1.50s" in s
        assert "100.0" in s and "150.0" in s and "120.0" in s


class TestPerformanceMonitor:
    def test_track_records_metric(self):
        monitor = PerformanceMonitor()
        with monitor.track("op1") as m:
            assert m.name == "op1"
        assert len(monitor.metrics) == 1
        assert monitor.metrics[0].name == "op1"
        assert monitor.metrics[0].duration_seconds >= 0.0

    def test_track_exception_still_records(self):
        monitor = PerformanceMonitor()
        with pytest.raises(ValueError):
            with monitor.track("oops"):
                raise ValueError("boom")
        assert len(monitor.metrics) == 1

    def test_get_total_duration_without_start(self):
        monitor = PerformanceMonitor()
        assert monitor.get_total_duration() == 0.0

    def test_start_global_then_duration_positive(self):
        monitor = PerformanceMonitor()
        monitor.start_global_monitoring()
        time.sleep(0.01)
        assert monitor.get_total_duration() > 0.0

    def test_get_summary_dict_contains_keys(self):
        monitor = PerformanceMonitor()
        monitor.start_global_monitoring()
        with monitor.track("op"):
            pass
        summary = monitor.get_summary_dict()
        assert "total_duration_seconds" in summary
        assert "operations" in summary
        assert len(summary["operations"]) == 1

    def test_log_summary_emits(self, caplog):
        monitor = PerformanceMonitor()
        monitor.start_global_monitoring()
        with monitor.track("op"):
            pass
        with caplog.at_level(logging.INFO):
            monitor.log_summary()
        text = "\n".join(r.message for r in caplog.records)
        assert "PERFORMANCE SUMMARY" in text

    def test_log_summary_without_metrics(self, caplog):
        monitor = PerformanceMonitor()
        monitor.start_global_monitoring()
        with caplog.at_level(logging.INFO):
            monitor.log_summary()
        text = "\n".join(r.message for r in caplog.records)
        assert "PERFORMANCE SUMMARY" in text


class TestGlobalMonitor:
    def test_singleton_behavior(self):
        reset_monitor()
        m1 = get_monitor()
        m2 = get_monitor()
        assert m1 is m2

    def test_reset_returns_new(self):
        reset_monitor()
        m1 = get_monitor()
        reset_monitor()
        m2 = get_monitor()
        assert m1 is not m2


class TestConfigLoader:
    def test_defaults_when_file_missing(self, tmp_path):
        # Clear singleton cache so this test gets a fresh instance
        ConfigLoader._instances.clear()
        missing = tmp_path / "does_not_exist.yaml"
        cfg = ConfigLoader(missing)
        assert cfg.get("als_columns.table") == "表"
        assert cfg.get_als_columns()["variable"] == "变量"
        assert cfg.get_template_columns()["variable_name"] == "变量名称"
        assert cfg.get_output_format()["prefix"] == "[RAW]"

    def test_loads_from_yaml(self, tmp_path):
        ConfigLoader._instances.clear()
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text(
            yaml.safe_dump({"als_columns": {"table": "CUSTOM_TABLE"}}),
            encoding="utf-8",
        )
        cfg = ConfigLoader(yaml_path)
        assert cfg.get("als_columns.table") == "CUSTOM_TABLE"

    def test_get_with_default(self, tmp_path):
        ConfigLoader._instances.clear()
        cfg = ConfigLoader(tmp_path / "missing.yaml")
        assert cfg.get("nonexistent.key", "fallback") == "fallback"

    def test_get_nested_key(self, tmp_path):
        ConfigLoader._instances.clear()
        cfg = ConfigLoader(tmp_path / "missing.yaml")
        assert cfg.get("als_columns.sdtm_domain") == "SDTM_Domain"

    def test_singleton_per_path(self, tmp_path):
        ConfigLoader._instances.clear()
        p = tmp_path / "a.yaml"
        p.write_text("", encoding="utf-8")
        c1 = ConfigLoader(p)
        c2 = ConfigLoader(p)
        assert c1 is c2

    def test_corrupt_yaml_falls_back(self, tmp_path):
        ConfigLoader._instances.clear()
        bad = tmp_path / "bad.yaml"
        bad.write_text("not: [valid: yaml", encoding="utf-8")
        cfg = ConfigLoader(bad)
        # defaults loaded on exception
        assert cfg.get("als_columns.table") == "表"


class TestSetupLogging:
    def test_invalid_level_raises(self):
        with pytest.raises(ValueError):
            setup_logging(level="NOTALEVEL")

    def test_console_only(self):
        setup_logging(level="INFO", log_file=None, console_output=True)
        assert len(logging.getLogger().handlers) >= 1

    def test_with_file(self, tmp_path):
        log_path = tmp_path / "logs" / "run.log"
        setup_logging(level="DEBUG", log_file=str(log_path), console_output=False)
        assert log_path.exists()
        # Cleanup: remove file handler to release the file handle
        for handler in list(logging.getLogger().handlers):
            if isinstance(handler, logging.FileHandler):
                handler.close()
                logging.getLogger().removeHandler(handler)


class TestDefaultLogFilename:
    def test_has_expected_prefix(self):
        name = get_default_log_filename()
        assert name.startswith("logs/als2spec_")
        assert name.endswith(".log")
