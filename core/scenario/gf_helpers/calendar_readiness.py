"""Calendar interactive readiness detection for Google Flights.

Move-only extraction from google_fill_date_via_picker().
Converts nested functions to module-level with explicit parameters.
Zero behavior change.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from core.service_ui_profiles import profile_localized_list


def _deadline_exceeded_impl(stage: str, deadline: Optional[float], logger, result: Dict[str, Any]) -> bool:
    """Check if deadline has been exceeded."""
    if deadline is None:
        return False
    if time.monotonic() > deadline:
        logger.warning("gf.date.cap.hit stage=%s", stage)
        result["reason"] = f"cap_hit_{stage}"
        return True
    return False


def _record_confirmation_impl(stage: str, ok: bool, logger, result: Dict[str, Any], **meta) -> None:
    """Record confirmation entry in result dict."""
    entry = {"stage": stage, "ok": bool(ok)}
    for k, v in meta.items():
        if v is None:
            continue
        entry[str(k)] = v
    result.setdefault("action_confirmations", []).append(entry)
    logger.info(
        "gf.date.confirm stage=%s ok=%s %s",
        stage,
        bool(ok),
        " ".join(f"{k}={v}" for k, v in entry.items() if k not in {"stage", "ok"})[:160],
    )


def _calendar_surface_visible_impl(page) -> bool:
    """Check if calendar dialog/grid is visible."""
    if page is None:
        return False
    surface_selectors = [
        "[role='dialog']:has([role='grid'])",
        "[role='dialog']:has([role='gridcell'])",
        "[role='grid']",
        "[class*='calendar']",
    ]
    for selector in surface_selectors:
        try:
            if page.locator(selector).first.is_visible(timeout=120):
                return True
        except Exception:
            continue
    return False


def _calendar_loading_hint_visible_impl(page, profile: Dict[str, Any]) -> bool:
    """Detect calendar loading/progress indicators."""
    if page is None:
        return False
    # Build loading indicator selectors from config
    loading_labels_config = profile.get("calendar_loading_indicator_labels", {})
    ja_labels = loading_labels_config.get("ja", ["結果を読み込んでいます"])
    en_labels = loading_labels_config.get("en", ["Loading"])

    loading_selectors = [
        "[role='progressbar']",
        "[aria-busy='true']",
    ]
    for label in ja_labels:
        loading_selectors.append(f"[aria-label*='{label}']")
    for label in en_labels:
        loading_selectors.append(f"[aria-label*='{label}']")

    for selector in loading_selectors:
        try:
            if page.locator(selector).first.is_visible(timeout=80):
                return True
        except Exception:
            continue
    return False


def _calendar_interactive_day_surface_ready_impl(
    page, profile: Dict[str, Any], locale_hint: str
) -> bool:
    """Check if calendar day cells are interactive."""
    if page is None:
        return False
    # Config-only: calendar_month_year_aria_tokens.month from service_ui_profiles.json
    month_tokens_config = profile.get("calendar_month_year_aria_tokens", {}).get("month", {})
    if isinstance(month_tokens_config, dict):
        month_tokens = profile_localized_list({"key": month_tokens_config}, "key", locale=locale_hint)
    else:
        month_tokens = month_tokens_config if isinstance(month_tokens_config, list) else []

    ready_selectors = [
        "[role='gridcell']",
        "[role='dialog'] [role='button']",
        "[role='grid'] [role='button']",
    ]
    for token in month_tokens:
        ready_selectors.append(f"[role='button'][aria-label*='{token}']")
    for selector in ready_selectors:
        try:
            if page.locator(selector).first.is_visible(timeout=100):
                return True
        except Exception:
            continue
    return False


def _wait_for_calendar_interactive_ready_impl(
    page,
    profile: Dict[str, Any],
    locale_hint: str,
    role_key: str,
    deadline: Optional[float],
    logger,
    result: Dict[str, Any],
    *,
    stage: str,
    max_checks: int = 6
) -> tuple[bool, str]:
    """Bounded wait for Google calendar rerender after date-field transitions.

    Google Flights can re-render the calendar after departure selection (often with
    per-day prices), temporarily showing a progress/loading state before return-date
    day cells become interactive.
    """
    if page is None:
        return False, "missing_page"
    saw_loading = False
    checks = 0
    while checks < max_checks:
        checks += 1
        if _deadline_exceeded_impl(f"{stage}_wait_ready", deadline, logger, result):
            return False, "deadline"
        surface_open = _calendar_surface_visible_impl(page)
        if not surface_open:
            return False, "surface_not_open"
        loading = _calendar_loading_hint_visible_impl(page, profile)
        saw_loading = saw_loading or loading
        ready = _calendar_interactive_day_surface_ready_impl(page, profile, locale_hint)
        if ready and not loading:
            _record_confirmation_impl(
                "calendar_interactive_ready",
                True,
                logger,
                result,
                role=role_key,
                wait_stage=stage,
                checks=checks,
                saw_loading=saw_loading,
            )
            return True, "ready"
        # Bounded settle for loader -> price grid transition.
        try:
            if page is not None:
                page.wait_for_timeout(120 if loading else 90)
            else:
                time.sleep(0.12 if loading else 0.09)
        except Exception:
            time.sleep(0.12 if loading else 0.09)
    _record_confirmation_impl(
        "calendar_interactive_ready",
        False,
        logger,
        result,
        role=role_key,
        wait_stage=stage,
        checks=checks,
        saw_loading=saw_loading,
    )
    return False, ("loading_persisted" if saw_loading else "not_interactive")
