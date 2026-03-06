"""Phase: date-field verification for Google date picker.

Move-only extraction from core/scenario/google_flights.py.
"""

from __future__ import annotations

from typing import Optional, Tuple

from core.scenario.gf_helpers.date_fields import (
    _gf_date_role_verify_selectors,
    _gf_field_value_matches_date,
)


def verify_date_field_value(
    page,
    *,
    role_key: str,
    target_date: str,
    locale_hint: str,
    role_selectors: Optional[list],
) -> Tuple[bool, str]:
    """Best-effort verify date field value matches target date."""
    if page is None:
        return False, ""
    verify_selectors = _gf_date_role_verify_selectors(
        role_key,
        locale_hint=locale_hint,
        role_selectors=role_selectors,
    )
    for selector in verify_selectors[:6]:
        try:
            locator = page.locator(selector).first
            if not locator.is_visible(timeout=120):
                continue
            field_value = ""
            try:
                field_value = locator.input_value(timeout=150) or ""
            except Exception:
                field_value = ""
            if not field_value:
                try:
                    field_value = locator.get_attribute("value", timeout=150) or ""
                except Exception:
                    field_value = ""
            if _gf_field_value_matches_date(field_value, target_date):
                return True, str(field_value or "")
        except Exception:
            continue
    return False, ""
