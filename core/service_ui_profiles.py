"""Service UI profile registry with optional JSON overrides."""

import copy
import json
import os
from pathlib import Path
from typing import Any, Dict, List


_DEFAULT_PATH = Path("configs/service_ui_profiles.json")
_CACHE: Dict[str, Any] = {}


_DEFAULT_PROFILES: Dict[str, Dict[str, Any]] = {
    "default": {
        "search_labels": {"default": ["Search"], "ja": ["検索", "Search"]},
        "search_selectors": ["button[type='submit']", "input[type='submit']"],
        "wait_selectors": ["[role='main']", "main", "body"],
        "product_toggle_labels": {
            "default": ["Flights", "Flight", "Air"],
            "ja": ["フライト", "航空券", "Flights"],
        },
        "product_toggle_selectors": [],
        "mode_toggle_labels": {
            "domestic": {"default": ["Domestic"], "ja": ["国内", "Domestic"]},
            "international": {
                "default": ["International"],
                "ja": ["海外", "国際", "International"],
            },
        },
        "mode_toggle_selectors": {"domestic": [], "international": []},
        "fill_selectors": {},
        "fill_extras_prepend": True,
        "fill_locale_sort": False,
        "activation_clicks": {},
        "activation_keywords": {},
    },
    "google_flights": {
        "search_labels": {"default": ["Search"], "ja": ["検索", "Search"]},
        "fill_locale_sort": True,
        "active_textbox_selectors": {
            "origin": {
                "default": [
                    "input[aria-autocomplete='list']",
                    "input[aria-controls]",
                    "input[role='combobox']",
                    "[role='combobox'] input[type='text']",
                    "input[type='text']",
                ],
                "ja": [
                    "input[aria-autocomplete='list']",
                    "input[aria-controls]",
                    "input[role='combobox']",
                    "[role='combobox'] input[type='text']",
                    "input[type='text']",
                ],
            },
            "dest": {
                "default": [
                    "input[aria-autocomplete='list']",
                    "input[aria-controls]",
                    "input[role='combobox']",
                    "[role='combobox'] input[type='text']",
                    "input[type='text']",
                ],
                "ja": [
                    "input[aria-autocomplete='list']",
                    "input[aria-controls]",
                    "input[role='combobox']",
                    "[role='combobox'] input[type='text']",
                    "input[type='text']",
                ],
            },
        },
        "suggestion_list_selectors": ["[role='listbox']", "[role='menu']", "ul[role='listbox']"],
        "suggestion_option_selectors": [
            "[role='listbox'] [role='option']",
            "[role='menu'] [role='menuitem']",
            "[role='option']",
            "[role='menuitem']",
        ],
        "product_toggle_selectors": {
            "default": [
                "button:has-text('Flights')",
                "button:has-text('Flight')",
                "[role='button']:has-text('Flights')",
                "[role='button']:has-text('Flight')",
            ],
            "ja": [
                "button:has-text('フライト')",
                "button:has-text('航空券')",
                "[role='button']:has-text('フライト')",
                "[role='button']:has-text('航空券')",
            ],
        },
        "mode_toggle_selectors": {
            "domestic": {
                "default": ["button:has-text('Domestic')", "[role='button']:has-text('Domestic')"],
                "ja": ["button:has-text('国内')", "[role='button']:has-text('国内')"],
            },
            "international": {
                "default": [
                    "button:has-text('International')",
                    "[role='button']:has-text('International')",
                ],
                "ja": [
                    "button:has-text('海外')",
                    "[role='button']:has-text('海外')",
                    "[role='button']:has-text('国際')",
                ],
            },
        },
        "activation_clicks": {
            "origin": {
                "default": [
                    "[role='combobox'][aria-label*='From']",
                    "[role='button'][aria-label*='From']",
                    "[aria-label*='From']",
                ],
                "ja": [
                    "[role='combobox'][aria-label*='出発地']",
                    "[role='button'][aria-label*='出発地']",
                    "[aria-label*='出発地']",
                ],
            },
            "dest": {
                "default": [
                    "[role='combobox'][aria-label*='To']",
                    "[role='button'][aria-label*='To']",
                    "[aria-label*='To']",
                ],
                "ja": [
                    "[role='combobox'][aria-label*='目的地']",
                    "[role='button'][aria-label*='目的地']",
                    "[role='combobox'][aria-label*='到着地']",
                    "[role='button'][aria-label*='到着地']",
                    "[aria-label*='目的地']",
                ],
            },
            "depart": {
                "default": [
                    "[role='combobox'][aria-label*='Depart']",
                    "[role='button'][aria-label*='Depart']",
                    "[aria-label*='Depart']",
                    "button:has-text('Depart')",
                    "[role='button']:has-text('Depart')",
                ],
                "ja": [
                    "[role='button'][aria-label*='出発日']",
                    "[aria-label*='出発日']",
                    "button:has-text('出発日')",
                    "[role='button']:has-text('出発日')",
                ],
            },
            "return": {
                "default": [
                    "[role='combobox'][aria-label*='Return']",
                    "[role='button'][aria-label*='Return']",
                    "[aria-label*='Return']",
                    "button:has-text('Return')",
                    "[role='button']:has-text('Return')",
                ],
                "ja": [
                    "[role='button'][aria-label*='復路']",
                    "[aria-label*='復路']",
                    "button:has-text('復路')",
                    "[role='button']:has-text('復路')",
                ],
            },
        },
    },
    "skyscanner": {
        "search_labels": {"default": ["Search"], "ja": ["Search"]},
        "product_toggle_labels": {"default": ["Flights", "Flight"], "ja": ["Flights", "Flight"]},
        "product_toggle_selectors": [
            "button:has-text('Flights')",
            "[role='button']:has-text('Flights')",
        ],
        "mode_toggle_selectors": {
            "domestic": ["button:has-text('Domestic')", "[role='button']:has-text('Domestic')"],
            "international": [
                "button:has-text('International')",
                "[role='button']:has-text('International')",
            ],
        },
        "wait_selectors": [
            "[data-testid*='search-results']",
            "[data-testid*='itinerary']",
            "[id*='result']",
            "[role='main']",
            "main",
            "body",
        ],
    },
}


def _deep_merge(base: Any, override: Any) -> Any:
    """Deep-merge dictionaries; non-dict values replace base."""
    if isinstance(base, dict) and isinstance(override, dict):
        out = {k: copy.deepcopy(v) for k, v in base.items()}
        for key, value in override.items():
            out[key] = _deep_merge(out.get(key), value)
        return out
    return copy.deepcopy(override)


def _load_override_profiles(path: Path) -> Dict[str, Any]:
    """Load optional JSON profile overrides."""
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def load_service_ui_profiles(force_reload: bool = False) -> Dict[str, Any]:
    """Return merged UI profiles (defaults + optional JSON overrides)."""
    global _CACHE
    if _CACHE and not force_reload:
        return copy.deepcopy(_CACHE)

    path = Path(os.getenv("SERVICE_UI_PROFILES_PATH", str(_DEFAULT_PATH)))
    overrides = _load_override_profiles(path)
    merged = _deep_merge(_DEFAULT_PROFILES, overrides)
    if not isinstance(merged, dict):
        merged = copy.deepcopy(_DEFAULT_PROFILES)
    _CACHE = merged
    return copy.deepcopy(_CACHE)


def get_service_ui_profile(site_key: str) -> Dict[str, Any]:
    """Return one service UI profile merged with default profile."""
    profiles = load_service_ui_profiles()
    default_profile = profiles.get("default", {})
    service_profile = profiles.get((site_key or "").strip().lower(), {})
    if not isinstance(default_profile, dict):
        default_profile = {}
    if not isinstance(service_profile, dict):
        service_profile = {}
    return _deep_merge(default_profile, service_profile)


def profile_is_prefer_ja(locale: str = "") -> bool:
    """Return True when locale should prefer Japanese profile ordering."""
    return str(locale or "").strip().lower().startswith("ja")


def profile_localized_list(profile: Dict[str, Any], key: str, *, locale: str = "") -> List[str]:
    """Return localized list for `key`, keeping cross-locale fallbacks nearby.

    Supports:
    - plain list values
    - localized dict values like {"en": [...], "ja": [...]} or legacy {"default": [...], "ja": [...]}
    """
    if not isinstance(profile, dict):
        return []
    value = profile.get(key, [])
    if isinstance(value, list):
        return [str(v) for v in value if isinstance(v, str) and v.strip()]
    if not isinstance(value, dict):
        return []
    locale_key = str(locale or "").strip().lower().split("-", 1)[0]
    default_bucket = value.get("default", [])

    if locale_key == "en":
        primary = value.get("en", default_bucket)
        secondary = []
    elif locale_key and locale_key in value:
        primary = value.get(locale_key, default_bucket)
        secondary = value.get("en", default_bucket)
    else:
        primary = value.get("en", default_bucket)
        secondary = []

    def _clean(bucket: Any) -> List[str]:
        out_bucket: List[str] = []
        if not isinstance(bucket, list):
            return out_bucket
        for raw in bucket:
            if not isinstance(raw, str) or not raw.strip():
                continue
            out_bucket.append(raw)
        return out_bucket

    primary_clean = _clean(primary)
    secondary_clean = _clean(secondary)

    out: List[str] = []
    seen = set()
    max_len = max(len(primary_clean), len(secondary_clean))
    for idx in range(max_len):
        for bucket in (primary_clean, secondary_clean):
            if idx >= len(bucket):
                continue
            raw = bucket[idx]
            if raw in seen:
                continue
            seen.add(raw)
            out.append(raw)
    return out


def profile_role_list(profile: Dict[str, Any], key: str, role: str, *, locale: str = "") -> List[str]:
    """Return role-scoped selectors/keywords from profile, locale-aware.

    Supports role maps:
    - { "origin": [..] }
    - { "origin": { "default": [..], "ja": [..] } }
    """
    if not isinstance(profile, dict):
        return []
    mapping = profile.get(key, {})
    if not isinstance(mapping, dict):
        return []
    role_value = mapping.get(str(role or "").strip().lower(), [])
    if isinstance(role_value, list):
        return [str(v) for v in role_value if isinstance(v, str) and v.strip()]
    if isinstance(role_value, dict):
        return profile_localized_list({key: role_value}, key, locale=locale)
    return []


def profile_role_token_list(
    profile: Dict[str, Any],
    key: str,
    role: str,
    token_kind: str,
) -> List[str]:
    """Return nested role semantic token lists from profile.

    Supports:
    - {key: {role: {token_kind: [..]}}}
    - {key: {role: {token_kind: {"en":[..], "ja":[..]}}}}  (locale merge left to caller)
    - {key: {role: {token_kind: {"default":[..], "ja":[..]}}}}  (legacy, locale merge left to caller)

    This helper intentionally returns a flat list and leaves precedence/ranking logic
    to runtime code.
    """
    if not isinstance(profile, dict):
        return []
    mapping = profile.get(key, {})
    if not isinstance(mapping, dict):
        return []
    role_map = mapping.get(str(role or "").strip().lower(), {})
    if not isinstance(role_map, dict):
        return []
    value = role_map.get(str(token_kind or "").strip(), [])
    if isinstance(value, list):
        return [str(v) for v in value if isinstance(v, str) and v.strip()]
    if isinstance(value, dict):
        # Accept localized dict form, but keep deterministic ordering (ja then default)
        # so callers can apply their own locale ordering.
        out: List[str] = []
        seen = set()
        for bucket_key in ("ja", "en", "default"):
            bucket = value.get(bucket_key, [])
            if not isinstance(bucket, list):
                continue
            for raw in bucket:
                if not isinstance(raw, str) or not raw.strip():
                    continue
                if raw in seen:
                    continue
                seen.add(raw)
                out.append(raw)
        return out
    return []
