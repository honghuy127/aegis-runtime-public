from __future__ import annotations

from utils.kb_ref_resolver import (
    build_kb_refs,
    find_best_kb_card,
    suggest_kb_patterns,
    validate_kb_refs,
)


def test_find_best_kb_card_existing_reason_returns_existing_path():
    path = find_best_kb_card(
        "google_flights",
        "calendar_dialog_not_found",
        locale_hint="ja-JP",
    )
    assert path is not None
    assert path.startswith("docs/kb/40_cards/cards/google_flights/calendar_dialog_not_found/")
    assert path.endswith(".md")


def test_find_best_kb_card_missing_reason_returns_none():
    path = find_best_kb_card("skyscanner", "missing_price", locale_hint="en-US")
    assert path is None


def test_suggest_kb_patterns_calendar_signal_prefers_date_picker():
    refs = suggest_kb_patterns(
        "unknown",
        {
            "has_calendar_dialog": True,
            "has_origin_dest_inputs": False,
            "has_price_token": False,
            "has_results_list": False,
        },
    )
    assert refs == ["docs/kb/30_patterns/date_picker.md"]


def test_build_kb_refs_never_points_to_history_or_archive():
    refs = build_kb_refs(
        site="google_flights",
        locale_hint="ja-JP",
        page_kind="search_form",
        signals={
            "has_calendar_dialog": False,
            "has_origin_dest_inputs": True,
            "has_price_token": False,
            "has_results_list": False,
        },
        expected={
            "extraction": {"status": "not_applicable", "currency": "unknown"},
            "ui_driver": {"readiness": "unready", "reason_code": "calendar_dialog_not_found"},
        },
        max_refs=3,
    )
    assert len(refs) >= 1
    for ref in refs:
        assert ref["path"].startswith("docs/kb/")
        assert not ref["path"].startswith("docs/archive/")


def test_build_kb_refs_respects_max_refs_cap():
    refs = build_kb_refs(
        site="google_flights",
        locale_hint="ja-JP",
        page_kind="search_form",
        signals={
            "has_calendar_dialog": True,
            "has_origin_dest_inputs": True,
            "has_price_token": False,
            "has_results_list": False,
        },
        expected={
            "extraction": {"status": "missing_price", "currency": "unknown", "reason_code": "missing_price"},
            "ui_driver": {"readiness": "unready", "reason_code": "calendar_dialog_not_found"},
        },
        max_refs=1,
    )
    assert len(refs) <= 1


def test_validate_kb_refs_flags_invalid_paths():
    warnings = validate_kb_refs(
        [
            {"type": "pattern", "path": "docs/kb/30_patterns/date_picker.md"},
            {"type": "pattern", "path": "docs/archive/old_doc.md"},
        ]
    )
    assert any("must start with docs/kb/" in w or "archive" in w for w in warnings)
