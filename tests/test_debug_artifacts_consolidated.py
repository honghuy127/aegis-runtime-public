"""Regression tests for canonical run-id-centric artifact paths."""

from datetime import datetime, UTC
from pathlib import Path

from utils.run_paths import ensure_run_dirs, read_latest_run_id, write_latest_run_id


def test_run_paths_create_canonical_structure(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    dirs = ensure_run_dirs("run_001")

    assert dirs["run_dir"] == Path("storage/runs/run_001")
    assert dirs["artifacts_dir"] == Path("storage/runs/run_001/artifacts")
    assert dirs["episode_dir"] == Path("storage/runs/run_001/episode")
    assert dirs["artifacts_dir"].exists()
    assert dirs["episode_dir"].exists()


def test_latest_run_id_pointer_round_trip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    pointer = write_latest_run_id("run_002")

    assert pointer == Path("storage/latest_run_id.txt")
    assert pointer.read_text(encoding="utf-8").strip() == "run_002"
    assert read_latest_run_id() == "run_002"


def test_triage_discovers_canonical_run_via_latest_run_id(tmp_path, monkeypatch):
    from utils.triage import find_canonical_artifacts_dir

    monkeypatch.chdir(tmp_path)
    run_dir = tmp_path / "storage" / "runs" / "run_003"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "scenario_last_error.json").write_text(
        f'{{"timestamp":"{datetime.now(UTC).isoformat()}","reason":"test_reason","evidence":{{}}}}',
        encoding="utf-8",
    )
    write_latest_run_id("run_003")

    found = find_canonical_artifacts_dir()
    assert found is not None
    assert found.resolve() == run_dir.resolve()


def test_triage_collect_error_events_does_not_create_legacy_debug_dir(tmp_path, monkeypatch):
    from utils.triage import collect_error_events

    monkeypatch.chdir(tmp_path)
    run_dir = tmp_path / "storage" / "runs" / "run_004"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "scenario_last_error.json").write_text(
        f'{{"timestamp":"{datetime.now(UTC).isoformat()}","reason":"calendar_not_open","evidence":{{}}}}',
        encoding="utf-8",
    )
    write_latest_run_id("run_004")

    events = collect_error_events(lookback_hours=48)

    assert events
    assert not (tmp_path / "storage" / "debug").exists()
