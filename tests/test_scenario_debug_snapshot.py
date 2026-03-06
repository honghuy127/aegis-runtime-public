"""Tests for scenario debug snapshot persistence behavior."""

import pytest

import core.scenario_runner as sr


def test_write_debug_snapshot_writes_canonical_run_file_and_latest_pointer(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    payload = {"stage": "unit_test", "error": "boom"}
    sr._write_debug_snapshot(payload, run_id="run_123")

    canonical = tmp_path / "storage" / "runs" / "run_123" / "scenario_last_error.json"
    latest = tmp_path / "storage" / "latest_run_id.txt"

    assert canonical.exists()
    assert latest.exists()
    assert latest.read_text(encoding="utf-8").strip() == "run_123"
    assert not (tmp_path / "storage" / "scenario_last_error.json").exists()
    assert not (tmp_path / "storage" / "debug_html").exists()
    assert not (tmp_path / "storage" / "debug").exists()


def test_write_debug_snapshot_requires_run_id(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError, match="missing_run_id"):
        sr._write_debug_snapshot({"error": "boom"}, run_id="")
