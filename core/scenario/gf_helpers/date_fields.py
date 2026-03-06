"""Date field verification helpers for Google Flights scenario.

Move-only extraction from core/scenario/google_flights.py.
Zero behavior change.
"""

from __future__ import annotations

from typing import Optional

from core.scenario.gf_helpers.date_tokens import _google_date_display_tokens
from core.scenario.gf_helpers.helpers import _dedupe_compact_selectors, _prefer_locale_token_order
from core.service_ui_profiles import get_service_ui_profile, profile_localized_list


def _gf_field_value_matches_date(field_value: str, target_date: str) -> bool:
    """Return True when a visible date field value matches target date in JA/EN formats."""
    text = str(field_value or "").strip()
    if not text:
        return False
    target = str(target_date or "").strip()
    if target and target in text:
        return True
    for token in _google_date_display_tokens(target):
        if token and token in text:
            return True
    return False


def _gf_date_role_verify_selectors(role_key: str, *, locale_hint: str = "", role_selectors: Optional[list] = None) -> list[str]:
    """Build bounded date-field verification selectors for depart/return chips/inputs."""
    rk = str(role_key or "").strip().lower()
    if rk not in {"depart", "return"}:
        return []
    # Get tokens from config for date field verification
    profile = get_service_ui_profile("google_flights") or {}
    verify_tokens_config = profile.get("date_field_verify_tokens", {}).get(rk, {})
    ja_tokens = verify_tokens_config.get("ja", [])
    en_tokens = verify_tokens_config.get("en", [])

    # Config source: service_ui_profiles.json[google_flights.date_field_verify_tokens.{depart,return}]
    # Empty tokens will cause verification to fail, which is correct for misconfigured systems.

    tokens = _prefer_locale_token_order(
        ja_tokens=ja_tokens,
        en_tokens=en_tokens,
        locale_hint=locale_hint,
    )
    selectors: list[str] = []
    # Verification should prefer stable date input/placeholder selectors first.
    # Plan-provided role selectors are kept as fallback context, but should not
    # crowd out the visible Departure/Return input under the bounded verify cap.
    seed_inputs: list[str] = []
    for token in tokens[:2]:
        seed_inputs.extend(
            [
                f"input[aria-label*='{token}']",
                f"input[placeholder*='{token}']",
            ]
        )
    for token in tokens:
        selectors.extend(
            [
                f"input[aria-label*='{token}']",
                f"input[placeholder*='{token}']",
                f"[role='button'][aria-label*='{token}']",
                f"[aria-label*='{token}']",
            ]
        )
    role_prefixed: list[str] = []
    if isinstance(role_selectors, list) and rk in {"depart", "return"}:
        role_prefixed = list(role_selectors)
    return _dedupe_compact_selectors(seed_inputs + role_prefixed + selectors, max_items=24)


def _gf_read_date_field_value(
    page,
    *,
    role_key: str,
    locale_hint: str = "",
    role_selectors: Optional[list] = None,
    target_date: str = "",
) -> str:
    """Best-effort read of visible date chip/input value for depart/return.

    Duplicate visible date inputs are common on Google Flights (header + dialog clones).
    Prefer a non-empty value that semantically matches the target date when provided.
    """
    if page is None:
        return ""
    best_value = ""
    best_score = None
    for selector in _gf_date_role_verify_selectors(role_key, locale_hint=locale_hint, role_selectors=role_selectors)[:8]:
        try:
            locator_group = page.locator(selector)
            try:
                count = int(locator_group.count())
            except Exception:
                count = 1
            for idx in range(max(1, min(count, 4))):
                locator = locator_group.nth(idx)
                try:
                    if not locator.is_visible(timeout=120):
                        continue
                except Exception:
                    continue
                try:
                    value = locator.input_value(timeout=150) or ""
                except Exception:
                    value = ""
                if not value:
                    try:
                        value = locator.get_attribute("value", timeout=150) or ""
                    except Exception:
                        value = ""
                if not value:
                    try:
                        value = locator.text_content(timeout=150) or ""
                    except Exception:
                        value = ""
                value = str(value or "").strip()
                if not value:
                    continue
                score = (
                    1 if (target_date and _gf_field_value_matches_date(value, target_date)) else 0,
                    1 if "input[" in str(selector or "").lower() else 0,
                    len(value),
                    -idx,
                )
                if best_score is None or score > best_score:
                    best_score = score
                    best_value = value
        except Exception:
            continue
    return best_value


def _gf_try_activate_date_chip(
    page,
    *,
    role_key: str,
    locale_hint: str = "",
    calendar_root=None,
    timeout_ms: int = 300,
) -> tuple[bool, str]:
    """Best-effort click of depart/return header chip inside calendar dialog."""
    if page is None:
        return False, ""
    rk = str(role_key or "").strip().lower()
    if rk not in {"depart", "return"}:
        return False, ""

    # Get tokens from service_ui_profiles for locale-aware multi-language support
    profile = get_service_ui_profile("google_flights")
    date_chip_keywords = profile.get("date_chip_activation_keywords", {})
    role_keywords = date_chip_keywords.get(rk, {})

    # Get locale-aware token list supporting any language in service_ui_profiles
    if isinstance(role_keywords, dict):
        tokens = profile_localized_list({"key": role_keywords}, "key", locale=locale_hint)
    else:
        tokens = role_keywords if isinstance(role_keywords, list) else []

    selectors: list[str] = []
    for token in tokens:
        selectors.extend(
            [
                f"[role='button'][aria-label*='{token}']",
                f"[role='button']:has-text('{token}')",
                f"button[aria-label*='{token}']",
                f"button:has-text('{token}')",
                f"[aria-label*='{token}']",
            ]
        )
    selectors = _dedupe_compact_selectors(selectors, max_items=20)
    search_root = calendar_root if calendar_root is not None else page
    for selector in selectors:
        try:
            locator = search_root.locator(selector).first
            if locator.is_visible(timeout=120):
                locator.click(timeout=max(80, int(timeout_ms or 0)))
                return True, selector
        except Exception:
            continue
    return False, ""