"""Calendar dialog root detection and gate decision helpers.

Move-only extraction from core/scenario/google_flights.py.
Zero behavior change.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def _gf_calendar_root_impl(page, dialog_locator):
    """Find and return the calendar root locator scoped to a dialog.

    DOC: See docs/kb/30_patterns/date_picker.md#calendar-root-scoping

    Priority:
    1. Element containing [role='grid'] within dialog (calendar grid)
    2. Element containing [role='gridcell'] within dialog
    3. Dialog itself if it has a grid

    If no grid-based calendar root found, returns None (treat as month_nav_exhausted).

    Args:
        page: Playwright page object
        dialog_locator: Locator for the calendar dialog ([role='dialog'])

    Returns:
        Locator pointing to calendar root, or None if not found.
    """
    if dialog_locator is None:
        return None

    # Try to find element containing grid within dialog
    root_selectors = [
        ":has([role='grid'])",     # Direct child/descendant with grid
        ":has([role='gridcell'])", # Direct child/descendant with gridcell
    ]

    for selector in root_selectors:
        try:
            locator = dialog_locator.locator(selector).first
            # Verify it's actually visible and contains grid-like elements
            if locator.is_visible(timeout=200):
                # Double-check: does it contain grid or gridcell?
                try:
                    grid_check = locator.locator("[role='grid'], [role='gridcell']").first
                    if grid_check.is_visible(timeout=100):
                        return locator
                except Exception:
                    # No grid found, continue to next selector
                    pass
        except Exception:
            pass

    # Last resort: return the dialog itself (not a visible descendant).
    # Using dialog_locator.locator(":visible").first can accidentally narrow the scope
    # to an unrelated child node and cause zero month-header candidates.
    try:
        if dialog_locator.is_visible(timeout=200):
            return dialog_locator
    except Exception:
        pass

    return None


def resolve_calendar_root_opener_impl(
    page,
    role_key: str,
    target_date: str,
    opener_selector: str,
    logger: Any,
    debug_probe_callback: Optional[Any] = None,
    opener_debug: Optional[Dict[str, Any]] = None,
) -> tuple[Optional[Any], Optional[str]]:
    """Detect calendar root by trying a series of root selectors after opener click.

    Move-only extraction from gf_set_date (lines 2703-2747).
    Zero behavior change.

    Args:
        page: Playwright page object
        role_key: Role key (e.g., 'departure_date')
        target_date: Target date string (for logging)
        opener_selector: The selector that was successfully clicked to open calendar
        logger: Logger instance
        debug_probe_callback: Optional debug callback function
        opener_debug: Optional dict to track opener metadata

    Returns:
        (calendar_root_locator, root_selector_used) tuple.
        Both None if no root found.
    """
    if page is None:
        return None, None

    calendar_root = None
    root_selector_used = None

    # Now detect calendar root - ALWAYS prefer dialog containing grid (FIX-003)
    # This ensures consistent root selection across depart/return fields
    root_selectors = [
        "[role='dialog']:has([role='grid']):visible",          # Grid-based calendar (JP Google Flights)
        "[role='dialog']:has([role='gridcell']):visible",      # Gridcell-based calendar
        "[class*='calendar']:has([role='grid']):visible",      # Calendar class with grid
        "[class*='picker']:has([role='gridcell']):visible",    # Picker class with gridcell
        "[role='dialog'][role='presentation']:has([role='grid']):visible",  # Presentation dialog with grid
        "[role='dialog']:visible",                              # Fallback: any visible dialog (last resort)
    ]

    for root_sel in root_selectors:
        try:
            root_locator = page.locator(root_sel).first
            if root_locator.is_visible(timeout=400):
                calendar_root = root_locator
                root_selector_used = root_sel
                logger.info(
                    "gf_set_date.open.ok role=%s opener_selector=%s root_selector=%s",
                    role_key,
                    opener_selector,
                    root_sel,
                )
                break
        except Exception:
            pass

    if calendar_root is not None:
        if callable(debug_probe_callback):
            try:
                debug_probe_callback(
                    "open_ok",
                    {
                        "role": role_key,
                        "target_date": target_date,
                        "opener_selector_used": str(opener_selector or ""),
                        "opener_selector_index_used": opener_debug.get("opener_selector_index_used") if opener_debug else None,
                        "root_selector_used": str(root_selector_used or ""),
                        "opener_debug": dict(opener_debug) if opener_debug else {},
                    },
                )
            except Exception:
                pass

    return calendar_root, root_selector_used


def _gf_calendar_fallback_root_month_header_gate_decision_impl(
    *,
    enabled: bool,
    root_selector_fallback_used: bool,
    header_candidate_count: int,
    header_rejected_count: int,
    evidence: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Phase-2 gate decision for fallback-root + zero month-header candidates."""
    evidence = evidence or {}
    if not enabled:
        return {"should_fail_early": False, "reason": "disabled"}
    if not root_selector_fallback_used:
        return {"should_fail_early": False, "reason": "not_fallback_root"}
    if int(header_candidate_count) > 0:
        return {"should_fail_early": False, "reason": "header_candidates_present"}
    if int(header_rejected_count) > 0:
        return {"should_fail_early": False, "reason": "header_candidates_rejected"}

    # Treat only a positive ready-probe signal as validation. Re-scope flags can be true
    # even when _gf_calendar_root() returns the same generic dialog fallback locator.
    root_probe_visible = bool(evidence.get("calendar_root_ready_probe_visible", False))
    if root_probe_visible:
        return {"should_fail_early": False, "reason": "root_validation_signal_present"}

    return {
        "should_fail_early": True,
        "reason": "fallback_root_unvalidated_zero_header_candidates",
    }
