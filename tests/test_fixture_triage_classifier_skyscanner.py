from __future__ import annotations

from pathlib import Path

from utils.fixture_triage import classify_fixture


def test_skyscanner_fixture_classifier_stable():
    html = Path("tests/fixtures/skyscanner/results_sample.html").read_text(encoding="utf-8")
    result = classify_fixture(html, "skyscanner")

    assert result["page_kind"] == "flights_results"
    assert result["locale_hint"] in {"en-US", "unknown"}
    assert result["signals"]["has_price_token"] is True
    assert result["signals"]["has_results_list"] is True
