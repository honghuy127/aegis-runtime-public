from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from utils.fixture_triage import (
    build_fixture_triage_metadata,
    validate_fixture_triage_metadata,
)


def test_fixture_triage_metadata_schema_version_and_enums_valid():
    fixture_path = Path("tests/fixtures/google_flights/results_sample.html")
    html = fixture_path.read_text(encoding="utf-8")
    data = build_fixture_triage_metadata(
        site="google_flights",
        fixture_path=fixture_path,
        html_text=html,
    )
    errors = validate_fixture_triage_metadata(data, repo_root=Path("."))
    assert data["schema_version"] == "fixture_triage_v1"
    assert data["page_kind"] in {"flights_results", "search_form", "consent", "error", "unknown"}
    assert errors == []


def test_fixture_triage_kb_refs_validation():
    fixture_path = Path("tests/fixtures/skyscanner/results_sample.html")
    html = fixture_path.read_text(encoding="utf-8")
    data = build_fixture_triage_metadata(
        site="skyscanner",
        fixture_path=fixture_path,
        html_text=html,
    )
    data["kb_refs"] = [
        {"type": "pattern", "path": "docs/kb/30_patterns/date_picker.md"},
        {"type": "card", "path": "docs/kb/40_cards/cards/does_not_exist/example.md"},
    ]
    errors = validate_fixture_triage_metadata(data, repo_root=Path("."))
    assert any("does not exist" in err for err in errors)


def test_fixture_triage_metadata_preserves_manual_expected_and_notes():
    fixture_path = Path("tests/fixtures/skyscanner/results_sample.html")
    html = fixture_path.read_text(encoding="utf-8")
    capture_meta = {
        "run_id": "run123",
        "source_path": "storage/runs/run123/artifacts/sample.html",
        "captured_at": "2026-02-22T00:00:00+00:00",
        "notes": "capture-note",
    }
    existing_triage = {
        "captured_from": {"run_id": "manual_run_override"},
        "expected": {
            "extraction": {"status": "missing_price", "reason_code": "manual_override"},
            "ui_driver": {"readiness": "unready", "reason_code": "manual_override"},
        },
        "kb_refs": [{"type": "pattern", "path": "docs/kb/30_patterns/date_picker.md"}],
        "notes": "manual-note",
    }
    data = build_fixture_triage_metadata(
        site="skyscanner",
        fixture_path=fixture_path,
        html_text=html,
        existing_capture_meta=capture_meta,
        existing_triage_meta=existing_triage,
    )

    assert data["captured_from"]["run_id"] == "manual_run_override"
    assert data["captured_from"]["source_path"] == capture_meta["source_path"]
    assert data["expected"]["extraction"]["status"] == "missing_price"
    assert data["expected"]["ui_driver"]["readiness"] == "unready"
    assert data["notes"] == "manual-note"
    assert data["kb_refs"] == existing_triage["kb_refs"]


def test_fixture_triage_metadata_cli_dry_run_no_fixtures(tmp_path: Path):
    empty_root = tmp_path / "fixtures"
    empty_root.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "utils.fixture_triage_metadata",
            "--site",
            "all",
            "--fixtures-dir",
            str(empty_root),
            "--write",
            "0",
        ],
        cwd=Path("."),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "no_fixtures_found" in (proc.stdout + proc.stderr)


def test_fixture_triage_metadata_cli_write_generates_json(tmp_path: Path):
    fixtures_dir = tmp_path / "fixtures"
    site_dir = fixtures_dir / "google_flights"
    site_dir.mkdir(parents=True, exist_ok=True)
    src_html = Path("tests/fixtures/google_flights/results_sample.html").read_text(encoding="utf-8")
    (site_dir / "sample.html").write_text(src_html, encoding="utf-8")
    (site_dir / "sample.meta.json").write_text(
        json.dumps({"run_id": "r1", "source_path": "/tmp/src.html", "captured_at": "2026-02-22T00:00:00+00:00"}),
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "utils.fixture_triage_metadata",
            "--site",
            "google_flights",
            "--fixtures-dir",
            str(fixtures_dir),
            "--write",
            "1",
        ],
        cwd=Path("."),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    triage_path = site_dir / "sample.triage.json"
    assert triage_path.exists()
    triage = json.loads(triage_path.read_text(encoding="utf-8"))
    assert triage["schema_version"] == "fixture_triage_v1"
    assert triage["captured_from"]["run_id"] == "r1"
    assert triage["site"] == "google_flights"
