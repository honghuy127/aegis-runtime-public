"""Tests for service UI profile registry and overrides."""

import json
from typing import Any

import pytest

from core import service_ui_profiles as sup
from core.service_ui_profiles import (
    get_service_ui_profile,
    load_service_ui_profiles,
    profile_localized_list,
    profile_role_list,
)


@pytest.fixture(autouse=True)
def _reset_profile_cache_after_test():
    """Avoid cross-test cache leakage when tests override SERVICE_UI_PROFILES_PATH."""
    yield
    load_service_ui_profiles(force_reload=True)


def test_default_profile_contains_generic_wait_selectors():
    """Default profile should provide conservative generic wait selectors."""
    profile = get_service_ui_profile("unknown_service")
    waits = profile.get("wait_selectors", [])
    assert isinstance(waits, list)
    assert "body" in waits


def test_google_profile_enables_locale_sorting():
    """Google Flights profile should keep locale-aware fill ranking enabled."""
    profile = get_service_ui_profile("google_flights")
    assert profile.get("fill_locale_sort") is True


def test_profile_override_file_is_merged(tmp_path, monkeypatch):
    """External JSON override should merge with defaults by service key."""
    override_path = tmp_path / "service_ui_profiles.json"
    override_path.write_text(
        json.dumps(
            {
                "google_flights": {
                    "search_labels": {
                        "en": ["Find flights"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SERVICE_UI_PROFILES_PATH", str(override_path))
    load_service_ui_profiles(force_reload=True)

    profile = get_service_ui_profile("google_flights")
    assert profile.get("search_labels", {}).get("en") == ["Find flights"]


def test_python_default_profiles_avoid_bare_text_selectors():
    """Python fallback profiles should avoid bare text= selectors."""

    def _scan(node: Any) -> list[str]:
        out: list[str] = []
        if isinstance(node, str):
            if node.strip().lower().startswith("text="):
                out.append(node)
            return out
        if isinstance(node, list):
            for item in node:
                out.extend(_scan(item))
            return out
        if isinstance(node, dict):
            for value in node.values():
                out.extend(_scan(value))
        return out

    hits = _scan(sup._DEFAULT_PROFILES)  # pylint: disable=protected-access
    assert hits == []


def test_profile_localized_list_interleaves_locale_variants():
    profile = {
        "search_selectors": {
            "ja": ["ja1", "ja2"],
            "en": ["en1", "en2"],
        }
    }

    out_ja = profile_localized_list(profile, "search_selectors", locale="ja-JP")
    out_en = profile_localized_list(profile, "search_selectors", locale="en-US")

    assert out_ja[:4] == ["ja1", "en1", "ja2", "en2"]
    assert out_en[:2] == ["en1", "en2"]


def test_google_profile_role_list_preserves_cross_locale_fallback_near_front():
    profile = get_service_ui_profile("google_flights")

    origin_ja = profile_role_list(profile, "force_bind_location_input_selectors", "origin", locale="ja-JP")

    head = origin_ja[:8]
    assert any("出発地" in s for s in head)
    assert any("Where from" in s or "From" in s for s in head)
