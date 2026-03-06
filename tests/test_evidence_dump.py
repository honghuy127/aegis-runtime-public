"""Tests for compact evidence checkpoint persistence."""

import json
from pathlib import Path

import pytest

from utils.evidence import evidence_path_for, write_service_evidence_checkpoint


@pytest.mark.integration
def test_write_service_evidence_checkpoint_creates_file_and_schema(tmp_path: Path):
    path = write_service_evidence_checkpoint(
        run_id="run-123",
        service="google_flights",
        checkpoint="after_initial_load",
        payload={
            "url": "https://example.test",
            "intended": {
                "origin": "HND",
                "dest": "ITM",
                "depart": "2026-03-01",
            },
        },
        enabled=True,
        base_dir=tmp_path,
    )

    assert path is not None
    assert path.exists()

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["run_id"] == "run-123"
    assert data["service"] == "google_flights"
    assert "checkpoints" in data
    assert "after_initial_load" in data["checkpoints"]
    checkpoint = data["checkpoints"]["after_initial_load"]
    assert "timestamp" in checkpoint
    assert checkpoint["data"]["intended"]["origin"] == "HND"


@pytest.mark.integration
def test_write_service_evidence_checkpoint_merges_checkpoints_and_compacts(tmp_path: Path):
    write_service_evidence_checkpoint(
        run_id="r1",
        service="google_flights",
        checkpoint="before_extraction",
        payload={"strategy": "plugin:html_llm"},
        enabled=True,
        base_dir=tmp_path,
    )

    long_text = "x" * 1000
    path = write_service_evidence_checkpoint(
        run_id="r1",
        service="google_flights",
        checkpoint="after_extraction",
        payload={"reason": long_text},
        enabled=True,
        base_dir=tmp_path,
    )

    assert path is not None
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "before_extraction" in data["checkpoints"]
    assert "after_extraction" in data["checkpoints"]
    compacted = data["checkpoints"]["after_extraction"]["data"]["reason"]
    assert "[truncated:1000]" in compacted


@pytest.mark.integration
def test_evidence_path_for_sanitizes_tokens(tmp_path: Path):
    path = evidence_path_for(
        run_id="run id/1",
        service="google flights",
        base_dir=tmp_path,
    )
    assert path == tmp_path / "run_id_1_google_flights_state.json"
