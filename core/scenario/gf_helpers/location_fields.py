"""Location field read/verify helpers for Google Flights scenario.

Move-only extraction from core/scenario/google_flights.py.
Zero behavior change.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from core.scenario.gf_helpers.helpers import _normalize_commit_text
from core.service_ui_profiles import get_service_ui_profile


def _extract_selector_visible_text(browser, selector: str) -> tuple[Optional[str], Optional[str]]:
    """Best-effort extraction of user-visible text/value for one selector."""
    if not isinstance(selector, str) or not selector.strip():
        return None, None
    page = getattr(browser, "page", None)
    if page is None:
        return None, "missing_page"
    try:
        locator = page.locator(selector).first
        for reader in (
            lambda: locator.input_value(),
            lambda: locator.get_attribute("value"),
            lambda: locator.get_attribute("aria-valuenow"),  # Prioritize airport code
            lambda: locator.get_attribute("aria-valuetext"),
            lambda: locator.get_attribute("aria-label"),  # Demoted: may contain city name
            lambda: locator.text_content(),
            lambda: locator.inner_text(),
        ):
            try:
                normalized = _normalize_commit_text(reader())
            except Exception:
                normalized = None
            if normalized:
                return normalized, None
    except Exception as exc:
        return None, f"locator_error:{exc}"
    return None, None


def _read_google_field_visible_text(
    browser,
    *,
    role: str,
    selectors,
    fill_selector=None,
) -> tuple[Optional[str], list, dict]:
    """Read likely committed visible field text after one fill+commit attempt.

    Args:
        browser: Browser instance
        role: Field role ("origin" or "dest")
        selectors: List of selectors to try (legacy parameter)
        fill_selector: Selector used for filling (prioritized first if provided)

    Returns:
        Tuple of (text, errors, evidence) where evidence contains:
        - verify.{role}_selector_used: Which selector returned text
        - verify.value_source: "fill_selector", "provided_selector", or "fallback"
    """
    role_key = str(role or "").strip().lower()
    candidates = []
    seen = set()

    # Prepend fill_selector if provided (highest priority)
    if fill_selector:
        fill_token = str(fill_selector).strip()
        if fill_token:
            candidates.append(fill_token)
            seen.add(fill_token)

    # Add provided selectors
    for selector in selectors or []:
        token = str(selector or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        candidates.append(token)

    # Track where fallbacks start (for evidence)
    fallback_start_index = len(candidates)

    # Add role-based fallback selectors from config
    profile = get_service_ui_profile("google_flights") or {}
    fallback_tokens_config = profile.get("location_field_fallback_tokens", {}).get(role_key, {})
    ja_tokens = fallback_tokens_config.get("ja", [])
    en_tokens = fallback_tokens_config.get("en", [])

    # Config source: service_ui_profiles.json[google_flights.location_field_fallback_tokens.{origin,dest}]
    # Empty tokens will cause selector generation to return minimal set, which is correct for misconfigured systems.

    # Build fallback selectors from tokens
    for en_token in en_tokens:
        candidates.extend([
            f"input[aria-label*='{en_token}']",
            f"[role='combobox'][aria-label*='{en_token}']",
        ])
    for ja_token in ja_tokens:
        candidates.extend([
            f"input[aria-label*='{ja_token}']",
            f"[role='combobox'][aria-label*='{ja_token}']",
        ])

    errors = []
    evidence = {}
    for index, selector in enumerate(candidates):
        text, error = _extract_selector_visible_text(browser, selector)
        if text:
            # Track which selector succeeded
            evidence_key = f"verify.{role_key}_selector_used"
            evidence[evidence_key] = selector

            # Track value source
            if fill_selector and index == 0:
                evidence["verify.value_source"] = "fill_selector"
            elif index < fallback_start_index:
                evidence["verify.value_source"] = "provided_selector"
            else:
                evidence["verify.value_source"] = "fallback"

            return text, errors, evidence
        if error:
            errors.append(error)
    return None, errors, evidence


def _log_google_fill_commit_evidence(logger, payload: Dict[str, Any]) -> None:
    """Emit compact deterministic commit evidence log."""
    evidence = {
        "field": payload.get("field"),
        "activation_selector_used": payload.get("activation_selector_used") or None,
        "textbox_selector_used": payload.get("textbox_selector_used") or None,
        "active_aria_label": payload.get("active_aria_label") or None,
        "refocus_attempt_count": payload.get("refocus_attempt_count") or 0,
        "typed_value": payload.get("typed_value"),
        "suggestion_used": bool(payload.get("suggestion_used", False)),
        "suggestion_text": payload.get("suggestion_text"),
        "suggestion_rank": payload.get("suggestion_rank"),
        "enter_used": bool(payload.get("enter_used", False)),
        "commit_method": payload.get("commit_method", "unknown"),
        "committed": bool(payload.get("committed", False)),
        "reason": payload.get("reason", ""),
        "final_visible_text": payload.get("final_visible_text"),
    }
    errors = payload.get("evidence_errors")
    if isinstance(errors, list) and errors:
        evidence["errors"] = errors[:4]
    logger.info("scenario.google_fill_commit.evidence %s", evidence)