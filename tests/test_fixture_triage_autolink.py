from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "utils.fixture_triage_metadata", *args],
        cwd=Path("."),
        capture_output=True,
        text=True,
        check=False,
    )


def test_fixture_triage_autolink_produces_card_ref_when_reason_matches(tmp_path: Path):
    fixtures_dir = tmp_path / "fixtures"
    site_dir = fixtures_dir / "google_flights"
    site_dir.mkdir(parents=True, exist_ok=True)
    fixture_html = Path("tests/fixtures/google_flights/results_sample.html").read_text(encoding="utf-8")
    (site_dir / "sample.html").write_text(fixture_html, encoding="utf-8")
    (site_dir / "sample.triage.json").write_text(
        json.dumps(
            {
                "schema_version": "fixture_triage_v1",
                "site": "google_flights",
                "fixture_name": "sample",
                "fixture_path": str((site_dir / "sample.html").as_posix()),
                "captured_from": {},
                "page_kind": "search_form",
                "locale_hint": "ja-JP",
                "signals": {
                    "has_price_token": False,
                    "has_results_list": False,
                    "has_calendar_dialog": True,
                    "has_origin_dest_inputs": True,
                },
                "expected": {
                    "extraction": {"status": "not_applicable", "currency": "unknown"},
                    "ui_driver": {"readiness": "unready", "reason_code": "calendar_dialog_not_found"},
                },
                "kb_refs": [],
                "notes": "",
            }
        ),
        encoding="utf-8",
    )

    proc = _run_cli(
        "--site", "google_flights",
        "--fixtures-dir", str(fixtures_dir),
        "--write", "1",
        "--autolink", "1",
        "--overwrite", "1",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

    triage = json.loads((site_dir / "sample.triage.json").read_text(encoding="utf-8"))
    assert len(triage["kb_refs"]) >= 1
    assert any(ref["type"] == "card" for ref in triage["kb_refs"])
    for ref in triage["kb_refs"]:
        assert ref["path"].startswith("docs/kb/")


def test_fixture_triage_autolink_keeps_manual_refs_when_disabled(tmp_path: Path):
    fixtures_dir = tmp_path / "fixtures"
    site_dir = fixtures_dir / "skyscanner"
    site_dir.mkdir(parents=True, exist_ok=True)
    fixture_html = Path("tests/fixtures/skyscanner/results_sample.html").read_text(encoding="utf-8")
    (site_dir / "sample.html").write_text(fixture_html, encoding="utf-8")
    manual_refs = [{"type": "pattern", "path": "docs/kb/30_patterns/selectors.md"}]
    (site_dir / "sample.triage.json").write_text(
        json.dumps(
            {
                "schema_version": "fixture_triage_v1",
                "site": "skyscanner",
                "fixture_name": "sample",
                "fixture_path": str((site_dir / "sample.html").as_posix()),
                "captured_from": {},
                "page_kind": "unknown",
                "locale_hint": "en-US",
                "signals": {
                    "has_price_token": True,
                    "has_results_list": True,
                    "has_calendar_dialog": False,
                    "has_origin_dest_inputs": False,
                },
                "expected": {
                    "extraction": {"status": "ok", "currency": "USD"},
                    "ui_driver": {"readiness": "ready"},
                },
                "kb_refs": manual_refs,
                "notes": "manual",
            }
        ),
        encoding="utf-8",
    )

    proc = _run_cli(
        "--site", "skyscanner",
        "--fixtures-dir", str(fixtures_dir),
        "--write", "1",
        "--autolink", "0",
        "--overwrite", "1",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    triage = json.loads((site_dir / "sample.triage.json").read_text(encoding="utf-8"))
    assert triage["kb_refs"] == manual_refs


def test_fixture_triage_autolink_respects_max_refs(tmp_path: Path):
    fixtures_dir = tmp_path / "fixtures"
    site_dir = fixtures_dir / "google_flights"
    site_dir.mkdir(parents=True, exist_ok=True)
    fixture_html = Path("tests/fixtures/google_flights/results_sample.html").read_text(encoding="utf-8")
    (site_dir / "sample.html").write_text(fixture_html, encoding="utf-8")
    (site_dir / "sample.triage.json").write_text(
        json.dumps(
            {
                "schema_version": "fixture_triage_v1",
                "site": "google_flights",
                "fixture_name": "sample",
                "fixture_path": str((site_dir / "sample.html").as_posix()),
                "captured_from": {},
                "page_kind": "search_form",
                "locale_hint": "ja-JP",
                "signals": {
                    "has_price_token": False,
                    "has_results_list": False,
                    "has_calendar_dialog": True,
                    "has_origin_dest_inputs": True,
                },
                "expected": {
                    "extraction": {"status": "missing_price", "currency": "unknown", "reason_code": "missing_price"},
                    "ui_driver": {"readiness": "unready", "reason_code": "calendar_dialog_not_found"},
                },
                "kb_refs": [],
                "notes": "",
            }
        ),
        encoding="utf-8",
    )

    proc = _run_cli(
        "--site", "google_flights",
        "--fixtures-dir", str(fixtures_dir),
        "--write", "1",
        "--autolink", "1",
        "--max-refs", "1",
        "--overwrite", "1",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    triage = json.loads((site_dir / "sample.triage.json").read_text(encoding="utf-8"))
    assert len(triage["kb_refs"]) <= 1
