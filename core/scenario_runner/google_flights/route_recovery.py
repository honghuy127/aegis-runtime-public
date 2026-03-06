"""Google Flights route recovery and repair policy implementations.

This module contains the orchestration logic for:
- Route form activation recovery (deeplink fast-path)
- Route/date rebind recovery plan generation
- Force-bind recovery policy gates
- Destination refill loop for mismatch recovery

These functions are called from scenario_runner.py and scenario_runner/google_flights/route_bind.py.
See docs/kb/20_decision_system/runtime_playbook.md for orchestration guidance.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional

from core.browser import (
    enforce_wall_clock_deadline,
    safe_min_timeout_ms,
    wall_clock_deadline,
    wall_clock_remaining_ms,
)
from core.service_ui_profiles import get_service_ui_profile, profile_localized_list
from utils.thresholds import get_threshold


def google_activate_route_form_recovery_impl(
    browser,
    *,
    locale_hint: str = "",
    action_timeout_ms: Optional[int] = None,
    settle_wait_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """Best-effort bounded activation of Google Flights route form controls.

    Used by deeplink fast-path recovery when a fallback reload lands on a generic
    flights/explore page. This helper only attempts to activate the canonical
    route form; it does not fill fields or navigate months.
    """
    timeout_ms = int(
        action_timeout_ms
        if action_timeout_ms is not None
        else get_threshold("google_flights_deeplink_page_state_recovery_action_timeout_ms", 1800)
    )
    settle_ms = int(
        settle_wait_ms
        if settle_wait_ms is not None
        else get_threshold("google_flights_deeplink_page_state_recovery_settle_ms", 250)
    )
    timeout_ms = max(200, timeout_ms)
    settle_ms = max(0, settle_ms)

    # Build locale-aware activation keywords from service_ui_profiles
    profile = get_service_ui_profile("google_flights")
    route_form_keywords = profile.get("route_form_activation_keywords", {})

    origin_kw_dict = route_form_keywords.get("origin", {})
    origin_keywords = profile_localized_list({"key": origin_kw_dict}, "key", locale=locale_hint)

    dest_kw_dict = route_form_keywords.get("dest", {})
    dest_keywords = profile_localized_list({"key": dest_kw_dict}, "key", locale=locale_hint)

    activation_roles = [
        ("origin", origin_keywords),
        ("dest", dest_keywords),
    ]
    activated_role = ""
    activation_mode = ""

    if hasattr(browser, "activate_field_by_keywords"):
        for role, keywords in activation_roles:
            try:
                if browser.activate_field_by_keywords(keywords, timeout_ms=timeout_ms):
                    activated_role = role
                    activation_mode = "keywords"
                    break
            except Exception:
                continue

    selector_used = ""
    if not activated_role and hasattr(browser, "click"):
        # Build selector candidates from locale-aware keywords
        selector_candidates: list[str] = []
        for role, keywords in activation_roles:
            for keyword in keywords[:3]:  # Limit to top 3 keywords per role
                selector_candidates.append(f"[role='combobox'][aria-label*='{keyword}']")
        # Fallback selectors for generic button patterns from service_ui_profiles
        fallback_labels_dict = profile.get("route_form_fallback_button_labels", {})
        if isinstance(fallback_labels_dict, dict):
            fallback_labels = profile_localized_list({"key": fallback_labels_dict}, "key", locale=locale_hint)
        else:
            fallback_labels = fallback_labels_dict if isinstance(fallback_labels_dict, list) else []
        for label in fallback_labels[:3]:  # Limit to top 3 fallback labels
            selector_candidates.append(f"button[aria-label*='{label}']")
        for selector in selector_candidates:
            try:
                browser.click(selector, timeout_ms=safe_min_timeout_ms(timeout_ms, 1200))
                selector_used = selector
                activation_mode = "selector_click"
                break
            except Exception:
                continue

    if settle_ms > 0:
        try:
            page = getattr(browser, "page", None)
            if page is not None and hasattr(page, "wait_for_timeout"):
                page.wait_for_timeout(settle_ms)
        except Exception:
            pass

    html = ""
    try:
        html = str(browser.content() or "")
    except Exception:
        html = ""

    ok = bool(activated_role or selector_used)
    reason = "activated_route_form" if ok else "route_form_activation_failed"
    return {
        "ok": ok,
        "reason": reason,
        "activation_mode": activation_mode or "none",
        "activated_role": activated_role,
        "selector_used": selector_used,
        "locale_hint": str(locale_hint or ""),
        "html": html,
    }


def google_force_route_bound_repair_plan_impl(
    *,
    origin: str,
    dest: str,
    depart: str,
    return_date: str = "",
    trip_type: str = "one_way",
    force_flights_tab: bool = False,
    flights_tab_selectors=None,
    product_step: Optional[Dict[str, Any]] = None,
    mode_step: Optional[Dict[str, Any]] = None,
    route_reset_selectors=None,
    dest_selectors=None,
    fill_selectors_by_role: Optional[Dict[str, Any]] = None,
    date_done_selectors=None,
    search_selectors=None,
    wait_selectors=None,
):
    """Build deterministic bounded Google Flights route/date rebind recovery plan."""
    plan = []
    if force_flights_tab:
        plan.append(
            {
                "action": "click",
                "selector": flights_tab_selectors or [],
                "optional": True,
            }
        )

    if isinstance(product_step, dict):
        step = dict(product_step)
        step["optional"] = True
        plan.append(step)

    if isinstance(mode_step, dict):
        step = dict(mode_step)
        step["optional"] = True
        plan.append(step)

    plan.append(
        {
            "action": "click",
            "selector": route_reset_selectors or [],
            "optional": True,
        }
    )

    role_map = dict(fill_selectors_by_role or {})
    role_values = [("origin", origin), ("dest", dest), ("depart", depart)]
    if trip_type == "round_trip" and isinstance(return_date, str) and return_date.strip():
        role_values.append(("return", return_date.strip()))
    for role, value in role_values:
        if not isinstance(value, str) or not value.strip():
            continue
        selectors = dest_selectors if role == "dest" else role_map.get(role, [])
        step = {
            "action": "fill",
            "selector": selectors,
            "value": value.strip(),
        }
        if role in {"origin", "dest"}:
            step["force_bind_commit"] = True
        if role == "return":
            step["optional"] = True
        plan.append(step)

    plan.append(
        {
            "action": "click",
            "selector": date_done_selectors or [],
            "optional": True,
        }
    )
    plan.append(
        {
            "action": "click",
            "selector": search_selectors or [],
            "optional": True,
        }
    )
    plan.append(
        {
            "action": "wait",
            "selector": wait_selectors or [],
        }
    )
    return plan


def google_force_bind_repair_policy_impl(
    *,
    is_google_service: bool,
    enabled: bool,
    uses: int,
    max_per_attempt: int,
    verify_status: str,
    normalized_scope: str,
    origin_unbound: bool,
    dest_is_placeholder: bool,
) -> Dict[str, Any]:
    """Policy gate for bounded Google force-bind recovery turns."""
    if not is_google_service:
        return {"use": False, "reason": "service_not_google", "force_flights_tab": False}
    if not enabled:
        return {"use": False, "reason": "disabled", "force_flights_tab": False}
    if int(uses) >= max(0, int(max_per_attempt)):
        return {"use": False, "reason": "budget_exhausted", "force_flights_tab": False}
    status = str(verify_status or "").strip().lower()
    if status == "route_fill_mismatch":
        return {
            "use": True,
            "reason": "route_fill_mismatch",
            "force_flights_tab": bool(origin_unbound),
        }
    if normalized_scope in {"irrelevant_page", "garbage_page"}:
        return {
            "use": True,
            "reason": f"scope_{normalized_scope}",
            "force_flights_tab": True,
        }
    if origin_unbound:
        return {"use": True, "reason": "origin_unbound", "force_flights_tab": True}
    if dest_is_placeholder:
        return {"use": True, "reason": "dest_placeholder", "force_flights_tab": False}
    return {"use": False, "reason": "no_trigger", "force_flights_tab": False}


def should_attempt_google_route_mismatch_reset_impl(
    *,
    mismatch_detected: bool,
    enabled: bool,
    attempts: int,
    max_attempts: int,
) -> bool:
    """Return True when bounded mismatch reset should run."""
    if not mismatch_detected or not enabled:
        return False
    return int(attempts) < max(0, int(max_attempts))


def google_refill_dest_on_mismatch_impl(
    *,
    route_verify_meta: Dict[str, Any],
    refill_limit: int,
    browser,
    probe_target,
    expected_route_values: Dict[str, str],
    timeout_ms: Optional[int],
    locale_hint: str,
    dest_selectors,
    fill_commit_fn: Callable[..., Dict[str, Any]],
    extract_form_state_fn: Callable[[Any], Dict[str, Any]],
    assess_fill_mismatch_fn: Callable[..., Dict[str, Any]],
    trace_latest_fill_selector_fn: Callable[[str], str],
    trace_date_done_clicked_fn: Callable[[], bool],
    logger,
    deadline: Optional[float] = None,
) -> Dict[str, Any]:
    """Run bounded destination refill loop for route-fill mismatch recovery."""
    current_meta = dict(route_verify_meta or {})
    refill_attempts = 0
    refill_meta: Optional[Dict[str, Any]] = None
    latest_form_state: Dict[str, Any] = {}
    mismatch_fields = list(current_meta.get("mismatches", []) or [])
    start_time = time.monotonic()
    deadline = deadline or wall_clock_deadline(timeout_ms)

    if not bool(current_meta.get("block")):
        return {
            "route_verify_meta": current_meta,
            "refill_attempts": refill_attempts,
            "refill_meta": refill_meta,
            "form_state": latest_form_state,
        }

    if not (
        refill_limit > 0
        and ("dest" in mismatch_fields or bool(current_meta.get("dest_is_placeholder")))
    ):
        return {
            "route_verify_meta": current_meta,
            "refill_attempts": refill_attempts,
            "refill_meta": refill_meta,
            "form_state": latest_form_state,
        }

    for _ in range(refill_limit):
        enforce_wall_clock_deadline(deadline, context="google_refill_dest")
        remaining_ms = wall_clock_remaining_ms(deadline)
        if remaining_ms is not None and remaining_ms <= 0:
            raise TimeoutError("wall_clock_timeout google_refill_dest")
        call_timeout_ms = timeout_ms
        if remaining_ms is not None:
            call_timeout_ms = min(int(timeout_ms or remaining_ms), int(remaining_ms))
        refill_attempts += 1
        refill_meta = fill_commit_fn(
            browser,
            role="dest",
            value=expected_route_values.get("dest", ""),
            selectors=dest_selectors,
            locale_hint=locale_hint,
            timeout_ms=call_timeout_ms,
            deadline=deadline,
        )
        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        logger.info(
            "scenario.google_force_bind.refill_dest attempted=%s ok=%s elapsed_ms=%d selector=%s",
            refill_attempts,
            bool((refill_meta or {}).get("ok")),
            elapsed_ms,
            str((refill_meta or {}).get("selector_used", "") or ""),
        )
        form_state = extract_form_state_fn(probe_target)
        latest_form_state = dict(form_state or {})
        current_meta = assess_fill_mismatch_fn(
            form_state=form_state,
            expected_origin=expected_route_values.get("origin", ""),
            expected_dest=expected_route_values.get("dest", ""),
            expected_depart=expected_route_values.get("depart", ""),
            expected_return=expected_route_values.get("return", ""),
        )
        if isinstance(current_meta, dict):
            current_meta["dest_selector_used"] = trace_latest_fill_selector_fn("dest")
            current_meta["date_picker_done_clicked"] = trace_date_done_clicked_fn()
            current_meta["dest_refill_attempted"] = refill_attempts
            current_meta["dest_committed"] = bool((refill_meta or {}).get("committed", False))
            current_meta["dest_commit_reason"] = str((refill_meta or {}).get("reason", "") or "")
            current_meta["suggestion_used"] = bool((refill_meta or {}).get("suggestion_used", False))
        if not bool(current_meta.get("block")):
            break

    return {
        "route_verify_meta": current_meta,
        "refill_attempts": refill_attempts,
        "refill_meta": refill_meta,
        "form_state": latest_form_state,
    }


def google_activate_route_form_recovery(
    browser,
    *,
    locale_hint: str = "",
    action_timeout_ms: Optional[int] = None,
    settle_wait_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """Best-effort bounded activation of Google Flights route form controls.

    Used by deeplink fast-path recovery when a fallback reload lands on a generic
    flights/explore page. This helper only attempts to activate the canonical
    route form; it does not fill fields or navigate months.
    """
    return google_activate_route_form_recovery_impl(
        browser,
        locale_hint=locale_hint,
        action_timeout_ms=action_timeout_ms,
        settle_wait_ms=settle_wait_ms,
    )


def google_force_route_bound_repair_plan(
    *,
    origin: str,
    dest: str,
    depart: str,
    return_date: str = "",
    trip_type: str = "one_way",
    force_flights_tab: bool = False,
    flights_tab_selectors=None,
    product_step: Optional[Dict[str, Any]] = None,
    mode_step: Optional[Dict[str, Any]] = None,
    route_reset_selectors=None,
    dest_selectors=None,
    fill_selectors_by_role: Optional[Dict[str, Any]] = None,
    date_done_selectors=None,
    search_selectors=None,
    wait_selectors=None,
):
    """Build deterministic bounded Google Flights route/date rebind recovery plan."""
    return google_force_route_bound_repair_plan_impl(
        origin=origin,
        dest=dest,
        depart=depart,
        return_date=return_date,
        trip_type=trip_type,
        force_flights_tab=force_flights_tab,
        flights_tab_selectors=flights_tab_selectors,
        product_step=product_step,
        mode_step=mode_step,
        route_reset_selectors=route_reset_selectors,
        dest_selectors=dest_selectors,
        fill_selectors_by_role=fill_selectors_by_role,
        date_done_selectors=date_done_selectors,
        search_selectors=search_selectors,
        wait_selectors=wait_selectors,
    )


def google_force_bind_repair_policy(
    *,
    is_google_service: bool,
    enabled: bool,
    uses: int,
    max_per_attempt: int,
    verify_status: str,
    normalized_scope: str,
    origin_unbound: bool,
    dest_is_placeholder: bool,
) -> Dict[str, Any]:
    """Policy gate for bounded Google force-bind recovery turns."""
    return google_force_bind_repair_policy_impl(
        is_google_service=is_google_service,
        enabled=enabled,
        uses=uses,
        max_per_attempt=max_per_attempt,
        verify_status=verify_status,
        normalized_scope=normalized_scope,
        origin_unbound=origin_unbound,
        dest_is_placeholder=dest_is_placeholder,
    )


def should_attempt_google_route_mismatch_reset(
    *,
    mismatch_detected: bool,
    enabled: bool,
    attempts: int,
    max_attempts: int,
) -> bool:
    """Return True when bounded mismatch reset should run."""
    return should_attempt_google_route_mismatch_reset_impl(
        mismatch_detected=mismatch_detected,
        enabled=enabled,
        attempts=attempts,
        max_attempts=max_attempts,
    )


def google_refill_dest_on_mismatch(
    *,
    route_verify_meta: Dict[str, Any],
    refill_limit: int,
    browser,
    probe_target,
    expected_route_values: Dict[str, str],
    timeout_ms: Optional[int],
    locale_hint: str,
    dest_selectors,
    fill_commit_fn: Callable[..., Dict[str, Any]],
    extract_form_state_fn: Callable[[Any], Dict[str, Any]],
    assess_fill_mismatch_fn: Callable[..., Dict[str, Any]],
    trace_latest_fill_selector_fn: Callable[[str], str],
    trace_date_done_clicked_fn: Callable[[], bool],
    logger,
    deadline: Optional[float] = None,
) -> Dict[str, Any]:
    """Run bounded destination refill loop for route-fill mismatch recovery."""
    return google_refill_dest_on_mismatch_impl(
        route_verify_meta=route_verify_meta,
        refill_limit=refill_limit,
        browser=browser,
        probe_target=probe_target,
        expected_route_values=expected_route_values,
        timeout_ms=timeout_ms,
        locale_hint=locale_hint,
        dest_selectors=dest_selectors,
        fill_commit_fn=fill_commit_fn,
        extract_form_state_fn=extract_form_state_fn,
        assess_fill_mismatch_fn=assess_fill_mismatch_fn,
        trace_latest_fill_selector_fn=trace_latest_fill_selector_fn,
        trace_date_done_clicked_fn=trace_date_done_clicked_fn,
        logger=logger,
        deadline=deadline,
    )
