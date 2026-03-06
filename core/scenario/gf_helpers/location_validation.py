"""Location field IATA-specific validation helpers for google_fill_and_commit_location.

This module provides structured validation logic for IATA codes vs. city names
during location field fill operations. Extracted from google_fill_and_commit_location
to enable isolation and reuse of validation patterns.
"""

from __future__ import annotations

from typing import Any, Optional

from core.scenario.gf_helpers.suggestions import (
    _text_matches_airport_alias,
    _suggestion_commit_matches,
)
from core.scenario.gf_helpers.helpers import _iata_token_in_text


def _check_aria_valuenow_for_iata(
    browser,
    fill_selector: str,
    target_value: str,
) -> tuple[bool, Optional[str]]:
    """Check aria-valuenow attribute as fallback for IATA token validation.
    
    When visible text doesn't contain IATA token, aria-valuenow attribute can serve
    as fallback verification that IATA code was committed (common on Google Flights).
    
    Args:
        browser: Browser session
        fill_selector: Selector for the location field element
        target_value: Target IATA code to verify
    
    Returns:
        (has_iata: bool, aria_valuenow_value: str or None)
    """
    if not fill_selector or not target_value:
        return False, None
    
    try:
        page = getattr(browser, "page", None)
        if page is not None:
            locator = page.locator(fill_selector).first
            aria_valuenow = locator.get_attribute("aria-valuenow", timeout=500)
            if aria_valuenow and target_value.upper() in aria_valuenow.upper():
                return True, aria_valuenow[:20]
    except Exception:
        pass
    
    return False, None


def _validate_suggestion_iata_match(
    suggestion_text: str,
    final_text: str,
    target_value: str,
) -> dict[str, Any]:
    """Validate IATA commitment when suggestion was clicked.
    
    When a suggestion was used for IATA codes:
    - Check IATA token present in suggestion
    - Check IATA token present in final visible text
    - Fallback to alias matching if IATA not in suggestion
    
    Args:
        suggestion_text: Text from the selected suggestion option
        final_text: Visible text in field after commitment
        target_value: Target IATA code
    
    Returns:
        Dict with keys: ok (bool), matched_iata (bool), reason (str)
    """
    result = {
        "ok": False,
        "matched_iata": False,
        "reason": "unknown",
    }
    
    has_iata_in_suggestion = _iata_token_in_text(suggestion_text or "", target_value)
    has_iata_in_final = _iata_token_in_text(final_text, target_value)
    
    if has_iata_in_suggestion and has_iata_in_final:
        # Strong evidence: IATA in both suggestion and final text
        result["ok"] = True
        result["matched_iata"] = True
        result["reason"] = "iata_match_suggestion_and_final"
    elif has_iata_in_suggestion:
        # Trust suggestion click even if final text hasn't updated yet
        result["ok"] = True
        result["matched_iata"] = True
        result["reason"] = "iata_match_suggestion_trust"
    elif not _text_matches_airport_alias(final_text, target_value):
        # Suggestion didn't contain IATA and final text doesn't match
        result["ok"] = False
        result["matched_iata"] = False
        result["reason"] = "commit_iata_mismatch_suggestion"
    else:
        # Suggestion matched via alias, accept even without explicit IATA
        result["ok"] = True
        result["matched_iata"] = False
        result["reason"] = "iata_match_via_alias"
    
    return result


def _validate_non_suggestion_iata_match(
    final_text: str,
    target_value: str,
) -> dict[str, Any]:
    """Validate IATA commitment when no suggestion was used.
    
    For non-suggestion IATA commits (Enter or type_active):
    - Require strict IATA token match (no fallback to aliases)
    - This is conservative to avoid city name collisions in other locales
    
    Args:
        final_text: Visible text in field after commitment
        target_value: Target IATA code
    
    Returns:
        Dict with keys: ok (bool), matched_iata (bool), reason (str)
    """
    result = {
        "ok": False,
        "matched_iata": False,
        "reason": "unknown",
    }
    
    has_iata_in_final = _iata_token_in_text(final_text, target_value)
    
    if has_iata_in_final:
        result["ok"] = True
        result["matched_iata"] = True
        result["reason"] = "iata_match_non_suggestion"
    else:
        result["ok"] = False
        result["matched_iata"] = False
        result["reason"] = "commit_iata_required"
    
    return result


def _validate_city_name_commitment(
    suggestion_text: str,
    final_text: str,
    target_value: str,
    suggestion_rank: int,
) -> dict[str, Any]:
    """Validate city name commitment (non-IATA value).
    
    For city name lookups:
    1. Prefer structured alias matching (airport provider aliases)
    2. Fall back to suggestion text match if suggestion rank >= 2
    3. Reject loose substring matches
    
    Args:
        suggestion_text: Text from the selected suggestion option (if suggestion used)
        final_text: Visible text in field after commitment
        target_value: Target city name
        suggestion_rank: Ranking score from suggestion selection (0 if not used)
    
    Returns:
        Dict with keys: ok (bool), matched_city (bool), reason (str)
    """
    result = {
        "ok": False,
        "matched_city": False,
        "reason": "unknown",
    }
    
    # Check if final text matches target or provider aliases (structured evidence)
    if _text_matches_airport_alias(final_text, target_value):
        result["ok"] = True
        result["matched_city"] = True
        result["reason"] = "city_match_alias"
    elif suggestion_rank >= 2 and _suggestion_commit_matches(
        final_text,
        target_value,
        suggestion_text or "",
    ):
        result["ok"] = True
        result["matched_city"] = True
        result["reason"] = "city_match_suggestion_text"
    else:
        # Loose substring match not sufficient for city name
        result["ok"] = False
        result["matched_city"] = False
        result["reason"] = "commit_city_insufficient_evidence"
    
    return result


def _validate_commitment(
    browser,
    result: dict[str, Any],
    target_value: str,
) -> None:
    """Validate overall commitment: Update result dict in-place with validation outcome.
    
    Applies structured validation based on value type (IATA vs. city) and commit method:
    - IATA codes: require IATA token match (with structured fallbacks)
    - City names: require alias or high-quality suggestion match
    - Non-committed: skip validation
    
    Updates result dict keys:
    - matched_iata (bool)
    - matched_city (bool)
    - ok (bool) - may be downgraded if validation fails
    - reason (str) - may be updated with validation-specific reason
    
    Args:
        browser: Browser session (for aria-valuenow access)
        result: Result dict containing committed, suggestion_used, final_visible_text, etc.
        target_value: Target airport value (IATA or city)
    """
    from core.scenario.gf_helpers.helpers import _is_iata_value
    
    # Initialize validation metadata
    result["matched_iata"] = False
    result["matched_city"] = False
    
    if not result.get("committed"):
        # Skip validation if not committed
        return
    
    final_text = str(result.get("final_visible_text", "")).strip()
    if not final_text:
        # No visible text to validate against
        return
    
    suggestion_rank = int(result.get("suggestion_rank") or 0)
    suggestion_text = result.get("suggestion_text", "")
    fill_selector = result.get("textbox_selector_used", "")
    is_iata = _is_iata_value(target_value)
    
    if is_iata:
        # IATA code validation
        has_iata_in_final = _iata_token_in_text(final_text, target_value)
        result["matched_iata"] = has_iata_in_final
        
        # Fallback: check aria-valuenow attribute
        if not has_iata_in_final:
            aria_ok, aria_value = _check_aria_valuenow_for_iata(
                browser,
                fill_selector,
                target_value,
            )
            if aria_ok:
                has_iata_in_final = True
                result["matched_iata"] = True
                result["aria_valuenow_fallback"] = aria_value or ""
        
        if result.get("suggestion_used", False):
            # Suggestion was used: validate IATA in suggestion + final text
            validate_result = _validate_suggestion_iata_match(
                suggestion_text or "",
                final_text,
                target_value,
            )
            result["matched_iata"] = validate_result["matched_iata"]
            if not validate_result["ok"]:
                result["committed"] = False
                result["ok"] = False
                result["matched_iata"] = False
                result["reason"] = validate_result["reason"]
        else:
            # No suggestion: strict IATA requirement
            validate_result = _validate_non_suggestion_iata_match(
                final_text,
                target_value,
            )
            result["matched_iata"] = validate_result["matched_iata"]
            if not validate_result["ok"]:
                result["committed"] = False
                result["ok"] = False
                result["matched_iata"] = False
                result["reason"] = validate_result["reason"]
    else:
        # City name validation
        if result.get("suggestion_used", False):
            validate_result = _validate_city_name_commitment(
                suggestion_text or "",
                final_text,
                target_value,
                suggestion_rank,
            )
            result["matched_city"] = validate_result["matched_city"]
            if not validate_result["ok"]:
                result["committed"] = False
                result["ok"] = False
                result["reason"] = validate_result["reason"]
