"""Date token generation for Google Flights scenario.

Move-only extraction from core/scenario/google_flights.py.
Zero behavior change.
"""

from __future__ import annotations

import calendar
import re
from typing import Optional

from core.scenario.gf_helpers.helpers import (
    _dedupe_compact_selectors,
    _prefer_locale_token_order,
)
from core.service_ui_profiles import get_service_ui_profile


def _should_filter_date_token(token: str) -> bool:
    """Return True if token should be filtered from date selectors (e.g., month markers).

    Uses config-driven filtering rules from service_ui_profiles.
    By default filters Japanese month marker "月" to avoid redundant token duplication.
    """
    if not token:
        return False

    profile = get_service_ui_profile("google_flights") or {}
    filtering_rules = profile.get("date_token_filtering_rules", {})
    skip_patterns = filtering_rules.get("skip_tokens_containing", {})

    # Get skip patterns for both JA and EN from config
    # Config source: service_ui_profiles.json[google_flights.date_token_filtering_rules.skip_tokens_containing]
    ja_patterns = skip_patterns.get("ja", [])
    en_patterns = skip_patterns.get("en", [])

    # Check if token contains any skip pattern
    token_str = str(token or "")
    for pattern in (ja_patterns or []):
        if pattern and pattern in token_str:
            return True
    for pattern in (en_patterns or []):
        if pattern and pattern in token_str:
            return True

    return False


def _google_date_display_tokens(target_date: str) -> list[str]:
    """Return bounded date display tokens covering common JA/EN UI formats."""
    text = str(target_date or "").strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", text)
    if not m:
        return []
    year = int(m.group(1))
    month = int(m.group(2))
    day = int(m.group(3))
    out = [
        f"{year}年{month}月{day}日",
        f"{month}月{day}日",
        f"{year}/{month:02d}/{day:02d}",
        f"{year}/{month}/{day}",
        f"{year}-{month:02d}-{day:02d}",
    ]
    try:
        month_name = calendar.month_name[month]
        month_abbr = calendar.month_abbr[month]
        out.extend(
            [
                f"{month_name} {day}, {year}",
                f"{month_abbr} {day}, {year}",
                f"{month_name} {day}",
                f"{month_abbr} {day}",
            ]
        )
    except Exception:
        pass
    return _dedupe_compact_selectors(out, max_items=16)


def _google_date_opener_tokens(role: str, target_date: str, locale_hint: Optional[str]) -> dict:
    """Build role/date tokens used to rank Google date opener selectors."""
    role_key = str(role or "").strip().lower()
    out = {
        "role_tokens": [],
        "date_tokens": [],
        "route_date_prefix_tokens": [],
        "route_date_tokens": [],
    }

    # Get token config from profile
    profile = get_service_ui_profile("google_flights") or {}
    date_open_tokens_config = profile.get("date_open_tokens", {}).get(role_key, {})

    # Get role-aware tokens from config (separate ja/en lists)
    role_token_config = date_open_tokens_config.get("role_tokens", {})
    if role_token_config:
        out["role_tokens"] = _prefer_locale_token_order(
            ja_tokens=role_token_config.get("ja", []),
            en_tokens=role_token_config.get("en", []),
            locale_hint=locale_hint,
        )

    # Get route date prefix tokens from config
    route_prefix_config = date_open_tokens_config.get("route_date_prefix_tokens", {})
    if route_prefix_config:
        out["route_date_prefix_tokens"] = _prefer_locale_token_order(
            ja_tokens=route_prefix_config.get("ja", []),
            en_tokens=route_prefix_config.get("en", []),
            locale_hint=locale_hint,
        )

    out["date_tokens"] = _google_date_display_tokens(target_date)
    for prefix in out.get("route_date_prefix_tokens", []) or []:
        for dtok in out["date_tokens"]:
            out["route_date_tokens"].append(f"{prefix}: {dtok}")

            out["route_date_tokens"].append(f"{prefix} {dtok}")
    out["route_date_tokens"] = _dedupe_compact_selectors(out["route_date_tokens"], max_items=24)
    return out
