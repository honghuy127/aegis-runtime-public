"""
Phase C: Opener selection and execution.

Extracted from: gf_set_date() calendar opening logic

Handles:
- Building opener selector candidates
- Visibility-aware ordering and prefiltering
- Attempting opener clicks with fallback
- Detecting calendar root after successful open
- Evidence tracking and bounded retry logic
"""

import time
import re
from typing import List, Optional, Callable, Dict, Any, Tuple
from playwright.async_api import Page, Locator


def select_and_click_opener(
    page: Page,
    role_key: str,
    target_date: str,
    opener_selectors: List[str],
    deadline: int,
    timeout_value: int,
    budget_mgr: Any,
    logger: Any,
    debug_probe_callback: Optional[Callable] = None,
    profile: Optional[Dict] = None,
    locale_hint: Optional[str] = None,
) -> Tuple[bool, Optional[Locator], str, Dict[str, Any]]:
    """
    Attempt to find and click a calendar opener with bounded, visibility-aware retry.

    Implements:
    - Visibility-aware ordering of opener candidates
    - Pre-filtering for interactive selectors
    - Bounded click attempts with overlay detection
    - Calendar root resolution after click success

    Args:
        page: Playwright page
        role_key: "depart" or "return"
        target_date: Target date in YYYY-MM-DD format
        opener_selectors: Candidate selectors to try
        deadline: Wall clock deadline (monotonic ms)
        timeout_value: Max timeout per operation (ms)
        budget_mgr: BudgetedTimeoutManager instance
        logger: Logger instance
        debug_probe_callback: Optional debug callback
        profile: Service UI profile (for tokens)
        locale_hint: Locale hint (e.g., "ja-JP")

    Returns:
        (success, calendar_root, root_selector_used, evidence_dict)
        - success: True if calendar opened
        - calendar_root: Locator for calendar container
        - root_selector_used: Selector used to find calendar root
        - evidence_dict: Full evidence of opening process
    """
    # Import here to avoid circular dependency
    from core.scenario.gf_helpers.calendar_nav import resolve_calendar_root_opener_impl
    from core.scenario.gf_helpers.date_tokens import _google_date_opener_tokens

    evidence = {
        "calendar.opener_candidates": list(opener_selectors[:12]),
        "calendar.opener_candidate_order": [],
        "calendar.opener_visible_prefilter": {},
        "calendar.opener_attempts": [],
        "calendar.opener_selector_used": "",
        "calendar.opener_selector_index_used": None,
        "root_selector_fallback_used": False,
        "root_selector_type": "unknown",
    }

    # Debug probe callback
    if callable(debug_probe_callback):
        try:
            debug_probe_callback(
                "pre_open",
                {
                    "role": role_key,
                    "target_date": target_date,
                    "opener_selectors": list(opener_selectors[:12]),
                },
            )
        except Exception:
            pass

    # Helper: Record opener attempt in evidence
    def _record_opener_attempt(selector: str, idx: Optional[int], ok: bool,
                               visible: Optional[bool] = None, enabled: Optional[bool] = None,
                               error: str = "") -> None:
        entry: Dict[str, Any] = {
            "selector": str(selector or "")[:160],
            "ok": bool(ok),
        }
        if isinstance(idx, int) and idx >= 0:
            entry["idx"] = int(idx)
        if isinstance(visible, bool):
            entry["visible"] = bool(visible)
        if isinstance(enabled, bool):
            entry["enabled"] = bool(enabled)
        if error:
            entry["error"] = str(error)[:120]
        evidence["calendar.opener_attempts"].append(entry)

    # Helper: Resolve visible/enabled opener candidate with bounded scan
    def _resolve_visible_enabled_opener_candidate(selector: str) -> Tuple[Optional[Locator], Optional[int], bool, bool]:
        """Return (locator, idx, visible, enabled) preferring visible+enabled match."""
        locator_group = page.locator(selector)
        first_locator = locator_group.first
        any_visible = False
        any_enabled = False
        for idx in range(6):  # bounded duplicate scan
            candidate = locator_group.nth(idx)
            try:
                # Increase short probe timeouts to be more tolerant of slow-rendering
                # dynamic pages (post-refactor timing differences were causing
                # false-negative visibility probes).
                visible = bool(candidate.is_visible(timeout=250))
            except Exception:
                visible = False
            if not visible:
                continue
            any_visible = True
            try:
                enabled = bool(candidate.is_enabled(timeout=120))
            except Exception:
                enabled = False
            any_enabled = any_enabled or enabled
            if enabled:
                return candidate, idx, True, True
            if not any_enabled:
                first_locator = candidate
        # Fall back to first match if no visible/enabled found
        try:
            visible = bool(first_locator.is_visible(timeout=80))
        except Exception:
            visible = False
        try:
            enabled = bool(first_locator.is_enabled(timeout=60)) if visible else False
        except Exception:
            enabled = False
        any_visible = any_visible or visible
        any_enabled = any_enabled or enabled
        return first_locator, (0 if (visible or enabled) else None), any_visible, any_enabled

    # Helper: Determine selector kind
    def _opener_kind(selector: str) -> str:
        lower = str(selector or "").lower()
        if lower.startswith("input["):
            return "input"
        if "[role='combobox']" in lower:
            return "combobox"
        if "[role='button']" in lower or lower.startswith("button["):
            return "button"
        return "other"

    # Helper: Check if selector has role token
    def _selector_has_role_token(selector: str) -> bool:
        role_tokens = _google_date_opener_tokens(
            role=role_key,
            target_date=target_date,
            locale_hint=locale_hint or "",
        ).get("role_tokens", [])
        raw = str(selector or "")
        return any(str(tok or "").strip() and str(tok) in raw for tok in (role_tokens or []))

    # Helper: Language score for CJK detection
    def _selector_lang_score(selector: str) -> int:
        raw = str(selector or "")
        has_cjk = bool(re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", raw))
        prefer_ja = str(locale_hint or "").strip().lower().startswith("ja")
        if prefer_ja:
            return 1 if has_cjk else 0
        return 1 if not has_cjk else 0

    # Visibility-aware ordering: move visible/enabled selectors to head
    visible_ready_selectors: List[str] = []
    deferred_selectors: List[str] = []
    for selector in opener_selectors[:12]:
        if not selector or not isinstance(selector, str):
            continue
        try:
            _locator, _idx, any_visible, any_enabled = _resolve_visible_enabled_opener_candidate(selector)
            if any_visible and any_enabled:
                visible_ready_selectors.append(selector)
                evidence["calendar.opener_visible_prefilter"][selector] = "visible_enabled"
            elif any_visible:
                deferred_selectors.append(selector)
                evidence["calendar.opener_visible_prefilter"][selector] = "visible_disabled"
            else:
                deferred_selectors.append(selector)
                evidence["calendar.opener_visible_prefilter"][selector] = "hidden_or_missing"
        except Exception as exc:
            deferred_selectors.append(selector)
            evidence["calendar.opener_visible_prefilter"][selector] = f"probe_error:{str(exc)[:40]}"

    # Build final attempt list
    opener_attempt_selectors = []
    for selector in visible_ready_selectors + deferred_selectors:
        if selector and selector not in opener_attempt_selectors:
            opener_attempt_selectors.append(selector)

    # Helper: Promote specific candidates to head of list
    def _promote_into_head(predicate, *, head_cap: int = 8, insert_at: int = 0) -> None:
        if not opener_attempt_selectors:
            return
        cap = min(max(1, int(head_cap)), len(opener_attempt_selectors))
        if any(predicate(s) for s in opener_attempt_selectors[:cap]):
            return
        best_idx = None
        best_score = None
        for idx, sel in enumerate(opener_attempt_selectors[cap:], start=cap):
            if not predicate(sel):
                continue
            score = (int(_selector_has_role_token(sel)), int(_selector_lang_score(sel)), -idx)
            if best_score is None or score > best_score:
                best_score = score
                best_idx = idx
        if best_idx is None:
            return
        candidate = opener_attempt_selectors.pop(best_idx)
        opener_attempt_selectors.insert(min(max(0, int(insert_at)), cap - 1), candidate)

    # Promote role-labeled input and combobox openers to head
    _promote_into_head(
        lambda s: _opener_kind(s) == "input" and _selector_has_role_token(s),
        head_cap=8,
        insert_at=2,
    )
    _promote_into_head(
        lambda s: _opener_kind(s) == "combobox" and _selector_has_role_token(s),
        head_cap=8,
        insert_at=3,
    )

    evidence["calendar.opener_candidate_order"] = list(opener_attempt_selectors[:12])

    # Track last attempt status for evidence
    last_opener_visible = False
    last_opener_enabled = False
    last_overlay_detected = False
    opened_selectors_tried = []

    # Bounded opener loop with visibility ranking
    for selector in opener_attempt_selectors[:8]:
        if not selector or not isinstance(selector, str):
            continue

        # Check budget before attempt
        if not budget_mgr.check_and_consume_budget(1):
            return (False, None, "", {
                **evidence,
                "calendar.failure_reason": "budget_hit_during_open",
                "calendar.stage": "open",
            })

        try:
            click_start = time.monotonic()
            locator, resolved_idx, pref_visible, pref_enabled = _resolve_visible_enabled_opener_candidate(selector)

            if locator is None:
                opened_selectors_tried.append(selector)
                _record_opener_attempt(selector, idx=None, ok=False, visible=False, enabled=False, error="locator_missing")
                continue

            # Enhanced visibility/interactivity check
            is_visible = bool(pref_visible)
            is_enabled = bool(pref_enabled)
            overlay_detected = False

            if not is_visible or not is_enabled:
                try:
                    is_visible = bool(locator.is_visible(timeout=180))
                    if is_visible:
                        is_enabled = bool(locator.is_enabled(timeout=100))
                except Exception:
                    pass

            last_opener_visible = is_visible
            last_opener_enabled = is_enabled

            if not is_visible or not is_enabled:
                logger.debug(
                    "gf_set_date.open.skip_not_interactive selector=%s visible=%s enabled=%s",
                    selector, is_visible, is_enabled,
                )
                opened_selectors_tried.append(selector)
                _record_opener_attempt(selector, idx=resolved_idx, ok=False, visible=is_visible, enabled=is_enabled, error="not_interactive")
                continue

            # Check for blocking overlays
            try:
                overlay_selectors = [
                    "[role='dialog']:not(:has([role='grid']))",
                    "[class*='modal']:visible",
                    "[class*='overlay']:visible",
                ]
                for overlay_sel in overlay_selectors:
                    # Allow slightly longer overlay detection timeout to avoid
                    # missing transient overlays caused by async rendering.
                    if page.locator(overlay_sel).first.is_visible(timeout=150):
                        overlay_detected = True
                        logger.debug(
                            "gf_set_date.open.overlay_detected selector=%s overlay=%s",
                            selector, overlay_sel,
                        )
                        break
            except Exception:
                pass

            last_overlay_detected = overlay_detected

            if overlay_detected:
                opened_selectors_tried.append(selector)
                _record_opener_attempt(selector, idx=resolved_idx, ok=False, visible=is_visible, enabled=is_enabled, error="overlay_detected")
                continue

            # Click the opener
            locator.click(timeout=budget_mgr.get_budgeted_timeout())
            click_elapsed_ms = int((time.monotonic() - click_start) * 1000)

            evidence["calendar.opener_selector_used"] = selector
            evidence["calendar.opener_selector_index_used"] = int(resolved_idx) if isinstance(resolved_idx, int) else None
            opened_selectors_tried.append(selector)
            _record_opener_attempt(selector, idx=resolved_idx, ok=True, visible=is_visible, enabled=is_enabled)

            logger.debug(
                "gf_set_date.open.click_ok selector=%s click_elapsed_ms=%d",
                selector, click_elapsed_ms,
            )
            time.sleep(0.2)

            # Wait for grid cells to render
            try:
                # Wait a bit longer for grid cells to render on slower pages.
                page.locator("[role='gridcell']").first.wait_for(state="visible", timeout=1000)
                logger.debug("gf_set_date.open.grid_detected role=%s", role_key)
            except Exception:
                logger.debug("gf_set_date.open.grid_wait_timeout role=%s", role_key)

            # Resolve calendar root
            calendar_root, root_selector_used = resolve_calendar_root_opener_impl(
                page=page,
                role_key=role_key,
                target_date=target_date,
                opener_selector=selector,
                logger=logger,
                debug_probe_callback=debug_probe_callback,
                opener_debug=evidence,
            )

            if calendar_root is not None:
                evidence["root_selector_fallback_used"] = "fallback" in root_selector_used.lower()
                evidence["root_selector_type"] = "grid_aware" if "grid" in root_selector_used.lower() else "other"
                return (True, calendar_root, root_selector_used, evidence)

        except Exception as exc:
            logger.debug("gf_set_date.open.attempt_failed selector=%s error=%s", selector, str(exc)[:50])
            opened_selectors_tried.append(selector)
            _record_opener_attempt(selector, idx=None, ok=False, error=str(exc)[:80])
            continue

    # All attempts failed
    logger.warning(
        "gf_set_date.open.fail role=%s date=%s selectors_tried=%d last_visible=%s last_enabled=%s last_overlay=%s",
        role_key, target_date, len(opened_selectors_tried), last_opener_visible, last_opener_enabled, last_overlay_detected,
    )

    evidence.update({
        "calendar.failure_reason": "calendar_not_open",
        "calendar.stage": "open",
        "selector_attempts": len(opened_selectors_tried),
        "selectors_tried": opened_selectors_tried[:5],
        "ui.opener_visible": last_opener_visible,
        "ui.opener_enabled": last_opener_enabled,
        "ui.overlay_detected": last_overlay_detected,
    })

    return (False, None, "", evidence)
