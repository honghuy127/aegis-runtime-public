"""Selector utility helpers for Google Flights scenario.

Move-only extraction from core/scenario/google_flights.py.
Zero behavior change.
"""

from __future__ import annotations

import re

from core.scenario.gf_helpers.helpers import _dedupe_compact_selectors
from core.service_ui_profiles import get_service_ui_profile, profile_localized_list


def _expected_field_tokens(role: str, locale_hint: str = "") -> list[str]:
    """Return ordered label tokens that should identify the focused field."""
    role_key = str(role or "").strip().lower()

    # Get field token keywords from service_ui_profiles
    profile = get_service_ui_profile("google_flights")
    field_token_dict = profile.get("field_token_keywords", {})
    tokens_dict = field_token_dict.get(role_key, {})

    # Use profile_localized_list to get locale-aware tokens
    if isinstance(tokens_dict, dict):
        tokens = profile_localized_list({"key": tokens_dict}, "key", locale=locale_hint)
    else:
        # Fallback to dict if structure is unexpected
        tokens = tokens_dict if isinstance(tokens_dict, list) else []

    return [token for token in tokens if token]


def _field_specific_textbox_selectors(role: str, locale_hint: str = "") -> list[str]:
    """Build field-specific textbox selectors for Google Flights."""
    tokens = _expected_field_tokens(role, locale_hint)
    selectors: list[str] = []
    for token in tokens:
        quoted = str(token).replace("'", "\\'")
        selectors.extend(
            [
                f"input[role='combobox'][aria-label*='{quoted}']",
                f"input[aria-label*='{quoted}']",
                f"input[placeholder*='{quoted}']",
                f"[role='combobox'][aria-label*='{quoted}']",
            ]
        )
    return _dedupe_compact_selectors(selectors, max_items=20)


def _selector_matches_expected_tokens(selector: str, tokens: list[str]) -> bool:
    """Return True when selector string includes any expected token."""
    if not isinstance(selector, str):
        return False
    blob = selector.lower()
    for token in tokens or []:
        if not token:
            continue
        if token.isascii():
            if token.lower() in blob:
                return True
        elif token in selector:
            return True
    return False


def _filter_field_specific_selectors(selectors, tokens: list[str]) -> list[str]:
    """Keep only field-specific selectors, skipping generic combobox inputs."""
    out: list[str] = []
    for selector in selectors or []:
        if not isinstance(selector, str) or not selector.strip():
            continue
        lowered = selector.strip().lower()
        if "input[role='combobox']" in lowered and "aria-label" not in lowered and "placeholder" not in lowered:
            continue
        if _selector_matches_expected_tokens(selector, tokens):
            out.append(selector)
    return _dedupe_compact_selectors(out, max_items=16)


def _ranked_suggestion_selector_candidates(selectors, *, max_rank: int = 6) -> list[str]:
    """Expand nth-match selectors into a small ranked candidate set."""
    out: list[str] = []
    for selector in selectors or []:
        token = str(selector or "").strip()
        if not token:
            continue
        out.append(token)
        matched = re.search(r"^:nth-match\((.+),\s*(\d+)\)$", token)
        if not matched:
            continue
        inner = str(matched.group(1) or "").strip()
        if not inner:
            continue
        for rank in range(1, max(1, int(max_rank)) + 1):
            out.append(f":nth-match({inner}, {rank})")
    return _dedupe_compact_selectors(out, max_items=max_rank * 4)
