"""Google Flights route_bind functions."""

from typing import Any, Callable, Dict, List, Optional, Tuple, Set
from utils.logging import get_logger

log = get_logger(__name__)

import re
from core.scenario.types import StepResult
from core.scenario_runner.google_flights.core_functions import (
    _assess_google_flights_fill_mismatch,
    _extract_google_flights_form_state,
    _google_form_candidates_from_html,
    _google_form_value_matches_airport,
    _google_form_value_matches_date,
    _google_origin_looks_unbound,
    _is_google_dest_placeholder,
)
from core.scenario_runner.google_flights.service_runner_bridge import (
    _google_quick_page_class,
    _google_route_context_matches,
    _verification_confidence_rank,
    _google_results_itinerary_matches_expected,
    _google_has_iata_token,
    _strip_nonvisible_html,
    _contains_any_token,
    _google_route_alias_tokens,
    _google_date_tokens,
    _google_missing_roles_from_reason,
    _google_step_trace_route_fill_roles_ok,
    _google_force_bind_flights_tab_selectors,
    _google_route_reset_selectors,
    _google_force_bind_dest_selectors,
    _google_date_done_selectors,
)
from core.scenario_runner.google_flights.route_recovery import (
    google_force_bind_repair_policy_impl as _google_force_bind_repair_policy_impl,
    google_force_route_bound_repair_plan_impl as _google_force_route_bound_repair_plan_impl,
    should_attempt_google_route_mismatch_reset_impl as _should_attempt_google_route_mismatch_reset_impl,
)
from core.route_binding import dom_route_bind_probe
from core.scenario_runner.google_flights.service_runner_bridge import (
    _parse_google_deeplink_context,
)
from storage.shared_knowledge_store import get_airport_aliases_for_provider
from utils.thresholds import get_threshold

# Import regex patterns and functions from scenario_runner that route_bind needs
# These will be imported dynamically via module reference to avoid circular imports
import core.scenario_runner as sr

def _build_route_state_scenario_extract_verdict(
    *,
    site_key: str,
    route_bind_verdict: Optional[Dict[str, Any]],
    scope_final: str,
    ready: Optional[bool],
    scenario_reason: str = "",
) -> Dict[str, Any]:
    """Build standardized scenario->extractor verdict scaffold for route_state artifacts.

    This is additive scaffolding only. Consumers are expected to be feature-gated.
    """
    site = str(site_key or "").strip()
    scope_value = str(scope_final or "").strip().lower()
    verdict = dict(route_bind_verdict) if isinstance(route_bind_verdict, dict) else {}
    route_bound = verdict.get("route_bound")
    route_reason = str(verdict.get("reason", "") or "").strip().lower()
    route_support = str(verdict.get("support", "") or "").strip().lower()
    scenario_reason_value = str(scenario_reason or "").strip().lower()

    def _is_non_actionable_scenario_reason(value: str) -> bool:
        reason_value = str(value or "").strip().lower()
        if not reason_value:
            return False
        if reason_value.startswith("date_fill_failure_"):
            return True
        if reason_value.startswith("blocked_interstitial_"):
            return True
        if reason_value.startswith("manual_intervention_"):
            return True
        if reason_value.startswith("demo_mode_manual_"):
            return True
        if reason_value.startswith("assist_mode_manual_"):
            return True
        if reason_value.startswith("deeplink_page_state_recovery_"):
            return True
        if reason_value in {
            "deeplink_recovery_activation_unverified",
            "deeplink_recovery_rebind_unverified",
            "demo_mode_final_html_unavailable",
        }:
            return True
        return False

    non_actionable = False
    reason = ""
    if bool(ready is False) and _is_non_actionable_scenario_reason(scenario_reason_value):
        non_actionable = True
        reason = scenario_reason_value
    if site == "google_flights" and route_bound is False:
        if bool(ready is False) and scenario_reason_value.startswith("date_fill_failure_"):
            non_actionable = True
            reason = scenario_reason_value
        elif (
            route_reason == "explicit_mismatch"
            or route_reason.startswith("scope_non_flight_")
            or scope_value == "irrelevant_page"
        ):
            non_actionable = True
            reason = "google_route_context_unbound"
        elif (
            ready is False
            and route_reason == "retries_exhausted"
        ):
            # Scenario exhausted bounded retries without a bound route; extraction/LLM
            # cannot recover price data from an unbound Google Flights state.
            non_actionable = True
            reason = "google_route_context_unbound"
    elif (
        site != "google_flights"
        and ready is False
        and route_bound is False
        and route_reason == "retries_exhausted"
    ):
        # Generic bounded-retry exhaustion on an unbound route is non-actionable
        # for extraction; preserve the concrete scenario reason for downstream gates.
        non_actionable = True
        reason = "retries_exhausted"

    payload: Dict[str, Any] = {
        "version": 1,
        "service": site,
        "source": "scenario_guard",
        "non_actionable": non_actionable,
        "reason": reason,
        "route_bound": bool(route_bound) if isinstance(route_bound, bool) else False,
        "scenario_ready": bool(ready) if isinstance(ready, bool) else False,
        "scope_class": scope_value,
        "route_bind_reason": route_reason,
        "route_bind_support": route_support,
    }
    mismatch_fields = verdict.get("mismatch_fields")
    if isinstance(mismatch_fields, list):
        payload["mismatch_fields"] = [str(x) for x in mismatch_fields]
    return payload


def _google_reconcile_ready_route_bound_consistency(
    *,
    ready: bool,
    route_bound: Optional[bool],
    verify_status: str,
    verify_override_reason: str,
    scope_page_class: str,
) -> Dict[str, Any]:
    """Clamp contradictory Google ready/unbound states when route verification was not attempted."""
    # Lazy import to avoid circular dependency
    from core.scenario_runner import _normalize_page_class

    out = {
        "ready": bool(ready),
        "verify_status": str(verify_status or ""),
        "verify_override_reason": str(verify_override_reason or ""),
        "changed": False,
        "reason": "",
    }
    if (
        bool(ready)
        and route_bound is False
        and str(verify_status or "").strip().lower() == "not_attempted"
        and _normalize_page_class(scope_page_class) in {"unknown", "flight_only", "flights_results"}
    ):
        out["ready"] = False
        out["changed"] = True
        out["reason"] = "route_bind_not_verified"
        if not out["verify_override_reason"]:
            out["verify_override_reason"] = "route_bind_not_verified"
        if out["verify_status"] == "not_attempted":
            out["verify_status"] = "not_verified"
    return out


def _google_turn_fill_success_corroborates_route_bind(step_trace: Any) -> Dict[str, Any]:
    """Return bounded same-turn fill success corroboration for Google route/date context.

    This is an additive local signal used only when route verification is unavailable/weak.
    It requires successful `fill` steps for all four core fields in the same turn.
    """
    roles_ok: Dict[str, bool] = {"origin": False, "dest": False, "depart": False, "return": False}
    if not isinstance(step_trace, list):
        return {"ok": False, "roles": roles_ok}
    for item in step_trace:
        if not isinstance(item, dict):
            continue
        if str(item.get("action", "") or "").strip().lower() != "fill":
            continue
        if str(item.get("status", "") or "").strip().lower() != "ok":
            continue
        role = str(item.get("role", "") or "").strip().lower()
        if role in roles_ok:
            roles_ok[role] = True
    return {"ok": all(roles_ok.values()), "roles": roles_ok}


def _build_route_state_return_fallback_payload(
    *,
    run_id: str,
    site_key: str,
    origin: str,
    dest: str,
    depart: str,
    return_date: str,
    reason: str,
    ready: bool,
    scope_class: str,
    route_bound: Optional[bool],
    route_support: str,
) -> Dict[str, Any]:
    """Build a minimal route_state artifact payload for early scenario returns.

    Some fast-path exits return before the full turn pipeline writes route_state.
    Emit a compact, consistent artifact so pre-extraction guards and triage retain
    deterministic route/scope context.
    """
    safe_site = str(site_key or "").strip()
    reason_lower = str(reason or "").strip().lower()
    scope_final = str(scope_class or "unknown").strip().lower() or "unknown"
    support = str(route_support or "unknown").strip().lower() or "unknown"

    inferred_route_bound = route_bound if isinstance(route_bound, bool) else None
    inferred_route_reason = reason_lower or "unknown"
    if safe_site == "google_flights":
        if scope_final == "irrelevant_page":
            inferred_route_bound = False
            inferred_route_reason = "scope_non_flight_irrelevant_page"
            if support in {"unknown", ""}:
                support = "none"
        elif reason_lower == "google_route_context_unbound":
            inferred_route_bound = False
            inferred_route_reason = "explicit_mismatch"
            if support in {"unknown", ""}:
                support = "none"

    route_verdict = {
        "route_bound": bool(inferred_route_bound)
        if isinstance(inferred_route_bound, bool)
        else False,
        "support": support,
        "source": "scenario_return",
        "reason": inferred_route_reason,
        "observed": {},
    }
    return {
        "run_id": str(run_id or ""),
        "service": safe_site,
        "expected": {
            "origin": str(origin or ""),
            "dest": str(dest or ""),
            "depart": str(depart or ""),
            "return": str(return_date or ""),
        },
        "route_bind_verdict": route_verdict,
        "scope_verdicts": {
            "heuristic": scope_final,
            "vlm": "unknown",
            "llm": "unknown",
            "final": scope_final,
            "sources": ["scenario_return_fallback"],
        },
        "scenario_extract_verdict": _build_route_state_scenario_extract_verdict(
            site_key=safe_site,
            route_bind_verdict=route_verdict,
            scope_final=scope_final,
            ready=bool(ready),
            scenario_reason=str(reason or ""),
        ),
        "scenario_return_summary": {
            "ready": bool(ready),
            "reason": str(reason or ""),
            "scope_class": scope_final,
        },
    }


def _bounded_google_mismatch_scan_html(html: str) -> str:
    """Trim large HTML snapshots for low-cost mismatch heuristics."""
    cleaned = _strip_nonvisible_html(html)
    if len(cleaned) <= 80000:
        return cleaned
    return f"{cleaned[:60000]} {cleaned[-20000:]}"


def _route_mismatch_suspected_verdict(
    *,
    service_key: str,
    origin: str,
    dest: str,
    depart: str,
    return_date: str,
    trip_type: str,
    html: str,
    last_known_form_state: Optional[dict] = None,
) -> Dict[str, Any]:
    """Return deterministic mismatch verdict for Google Flights route/date drift."""
    if (service_key or "").strip().lower() != "google_flights":
        return {"mismatch": False, "strong": False, "reason": "service_not_google"}

    strong_reasons = []
    mild_reasons = []

    state = dict(last_known_form_state or {})
    observed_origin = str(
        state.get("origin_text", state.get("origin", "")) or ""
    ).strip()
    observed_dest = str(
        state.get("dest_text", state.get("dest", "")) or ""
    ).strip()
    observed_dest_raw = str(
        state.get("dest_text_raw", observed_dest) or observed_dest
    ).strip()
    observed_depart = str(
        state.get("depart_text", state.get("depart", "")) or ""
    ).strip()
    observed_return = str(
        state.get("return_text", state.get("return", "")) or ""
    ).strip()
    state_confidence = str(state.get("confidence", "low") or "low").strip().lower()

    if _verification_confidence_rank(state_confidence) >= _verification_confidence_rank("medium"):
        if _is_google_dest_placeholder(observed_dest_raw):
            strong_reasons.append("mismatch_form_dest_placeholder")
        if observed_dest and not _google_form_value_matches_airport(observed_dest, dest):
            strong_reasons.append("mismatch_form_dest")
        if observed_depart and not _google_form_value_matches_date(observed_depart, depart):
            strong_reasons.append("mismatch_form_depart")
        if (
            trip_type == "round_trip"
            and return_date
            and observed_return
            and not _google_form_value_matches_date(observed_return, return_date)
        ):
            strong_reasons.append("mismatch_form_return")

    current_url = str(state.get("current_url", "") or "").strip()
    deeplink_ctx = _parse_google_deeplink_context(current_url)
    if isinstance(deeplink_ctx, dict):
        ctx_origin = str(deeplink_ctx.get("origin", "") or "").upper()
        ctx_dest = str(deeplink_ctx.get("dest", "") or "").upper()
        ctx_depart = str(deeplink_ctx.get("depart", "") or "")
        ctx_return = str(deeplink_ctx.get("return_date", "") or "")
        if origin and ctx_origin and ctx_origin != origin.strip().upper():
            strong_reasons.append(f"mismatch_url_origin_{ctx_origin.lower()}")
        if dest and ctx_dest and ctx_dest != dest.strip().upper():
            strong_reasons.append(f"mismatch_url_dest_{ctx_dest.lower()}")
        if depart and ctx_depart and ctx_depart != depart.strip():
            strong_reasons.append("mismatch_url_depart")
        if trip_type == "round_trip" and return_date and ctx_return and ctx_return != return_date:
            strong_reasons.append("mismatch_url_return")

    scan = _bounded_google_mismatch_scan_html(html)
    scan_upper = scan.upper()
    if scan:
        origin_tokens = _google_route_alias_tokens(origin)
        dest_tokens = _google_route_alias_tokens(dest)
        origin_seen = _contains_any_token(scan, scan_upper, origin_tokens)
        dest_seen = _contains_any_token(scan, scan_upper, dest_tokens)
        iata_tokens = {
            tok
            for tok in sr._IATA_TOKEN_RE.findall(scan_upper)
            if tok not in sr._IATA_TOKEN_IGNORE
        }
        expected_iata = {
            tok.upper()
            for tok in (origin_tokens | dest_tokens)
            if isinstance(tok, str) and tok.isascii() and len(tok) == 3
        }
        wrong_iata = sorted(
            tok for tok in iata_tokens if tok not in expected_iata and tok not in {"ANY"}
        )
        if not dest_seen and wrong_iata:
            strong_reasons.append(f"mismatch_iata_{wrong_iata[0].lower()}")
        elif not dest_seen:
            mild_reasons.append("mismatch_dest_missing")

        expected_dest = (dest or "").strip().upper()
        if expected_dest != "CTS" and "CTS" in iata_tokens and not dest_seen:
            strong_reasons.append("mismatch_iata_cts")
        if expected_dest != "CTS" and "札幌" in scan and not dest_seen:
            strong_reasons.append("mismatch_city_sapporo")

        date_literals = sr._DATE_LITERAL_RE.findall(scan)
        expected_depart_tokens = _google_date_tokens(depart or "")
        expected_return_tokens = _google_date_tokens(return_date or "")
        expected_date_tokens = expected_depart_tokens | expected_return_tokens
        depart_seen = any(token in scan for token in expected_depart_tokens) if depart else True
        return_seen = (
            any(token in scan for token in expected_return_tokens)
            if (trip_type == "round_trip" and return_date)
            else True
        )
        other_dates = [token for token in date_literals if token not in expected_date_tokens]
        route_context_present = bool(sr._ROUTE_FIELD_HINT_RE.search(scan))

        if not depart_seen and other_dates and route_context_present:
            strong_reasons.append("mismatch_dates_depart")
        elif not depart_seen:
            mild_reasons.append("mismatch_depart_missing")
        if (
            trip_type == "round_trip"
            and return_date
            and not return_seen
            and other_dates
            and route_context_present
        ):
            strong_reasons.append("mismatch_dates_return")
        elif trip_type == "round_trip" and return_date and not return_seen:
            mild_reasons.append("mismatch_return_missing")

    requires_strong = bool(
        get_threshold("google_flights_rewind_priority_requires_strong_signal", True)
    )
    strong = bool(strong_reasons)
    mild = bool(mild_reasons)
    mismatch = strong or (mild and not requires_strong)
    reason = (
        strong_reasons[0]
        if strong_reasons
        else (mild_reasons[0] if mild_reasons else "match")
    )
    return {
        "mismatch": mismatch,
        "strong": strong,
        "reason": reason,
        "strong_reasons": strong_reasons,
        "mild_reasons": mild_reasons,
    }


def _is_route_mismatch_suspected(
    *,
    service_key: str,
    origin: str,
    dest: str,
    depart: str,
    return_date: str,
    trip_type: str,
    html: str,
    last_known_form_state: Optional[dict] = None,
) -> bool:
    """Return True when strong route/date drift is suspected for Google Flights."""
    verdict = _route_mismatch_suspected_verdict(
        service_key=service_key,
        origin=origin,
        dest=dest,
        depart=depart,
        return_date=return_date,
        trip_type=trip_type,
        html=html,
        last_known_form_state=last_known_form_state,
    )
    return bool(verdict.get("mismatch"))


def _should_prioritize_google_route_mismatch_rewind(
    *,
    service_key: str,
    mismatch_suspected: bool,
    enabled: bool,
    uses: int,
    max_per_attempt: int,
) -> bool:
    """Return True when mismatch-driven rewind should be prioritized this turn."""
    if (service_key or "").strip().lower() != "google_flights":
        return False
    if not enabled or not mismatch_suspected:
        return False
    return int(uses) < max(0, int(max_per_attempt))


def _prioritized_google_route_mismatch_rewind_followup(
    *,
    service_key: str,
    mismatch_suspected: bool,
    enabled: bool,
    uses: int,
    max_per_attempt: int,
    plan,
    step_trace,
    scope_class: str,
    origin: str,
    dest: str,
    depart: str,
    return_date: str,
    trip_type: str,
    is_domestic: bool,
    vlm_hint: Optional[dict] = None,
) -> Dict[str, Any]:
    """Build prioritized rewind followup when mismatch policy allows."""
    if not _should_prioritize_google_route_mismatch_rewind(
        service_key=service_key,
        mismatch_suspected=mismatch_suspected,
        enabled=enabled,
        uses=uses,
        max_per_attempt=max_per_attempt,
    ):
        return {"followup": None, "uses": int(uses)}

    followup = _scope_rewind_followup_plan(
        site_key=service_key,
        plan=plan,
        step_trace=step_trace,
        scope_class=scope_class,
        origin=origin,
        dest=dest,
        depart=depart,
        return_date=return_date,
        trip_type=trip_type,
        is_domestic=bool(is_domestic),
        vlm_hint=vlm_hint,
    )
    if not sr._is_valid_plan(followup) or not sr._is_actionable_plan(
        followup,
        trip_type,
        site_key=service_key,
    ):
        return {"followup": None, "uses": int(uses)}
    return {"followup": followup, "uses": int(uses) + 1}


def _google_force_route_bound_repair_plan(
    *,
    origin: str,
    dest: str,
    depart: str,
    return_date: str = "",
    trip_type: str = "one_way",
    is_domestic: bool = False,
    scope_class: str = "unknown",
    vlm_hint: Optional[dict] = None,
    force_flights_tab: bool = False,
):
    """Deterministic bounded Google Flights route/date rebind recovery plan."""
    product_step = sr._service_product_toggle_step(
        "google_flights",
        scope_class=scope_class,
        vlm_hint=vlm_hint,
    )

    mode_step = sr._service_mode_toggle_step(
        "google_flights",
        is_domestic=bool(is_domestic),
        vlm_hint=vlm_hint,
        fallback_default=False,
    )
    fill_selectors_by_role = {
        "origin": sr._service_fill_fallbacks("google_flights", "origin"),
        "depart": sr._service_fill_fallbacks("google_flights", "depart"),
        "return": sr._service_fill_fallbacks("google_flights", "return"),
    }
    return _google_force_route_bound_repair_plan_impl(
        origin=origin,
        dest=dest,
        depart=depart,
        return_date=return_date,
        trip_type=trip_type,
        force_flights_tab=bool(force_flights_tab),
        flights_tab_selectors=_google_force_bind_flights_tab_selectors(),
        product_step=product_step,
        mode_step=mode_step,
        route_reset_selectors=_google_route_reset_selectors(),
        dest_selectors=_google_force_bind_dest_selectors(),
        fill_selectors_by_role=fill_selectors_by_role,
        date_done_selectors=_google_date_done_selectors(),
        search_selectors=sr._service_search_click_fallbacks("google_flights"),
        wait_selectors=sr._service_wait_fallbacks("google_flights"),
    )


def _google_force_bind_repair_policy(
    *,
    service_key: str,
    enabled: bool,
    uses: int,
    max_per_attempt: int,
    verify_status: str,
    scope_class: str,
    observed_dest_raw: str,
    observed_origin_raw: str = "",
    expected_origin: str = "",
) -> Dict[str, Any]:
    """Policy gate for bounded Google force-bind recovery turns."""
    # Lazy import to avoid circular dependency
    from core.scenario_runner import _normalize_page_class

    return _google_force_bind_repair_policy_impl(
        is_google_service=(service_key or "").strip().lower() == "google_flights",
        enabled=bool(enabled),
        uses=int(uses),
        max_per_attempt=int(max_per_attempt),
        verify_status=str(verify_status or ""),
        normalized_scope=_normalize_page_class(scope_class),
        origin_unbound=_google_origin_looks_unbound(
            observed_origin_raw,
            expected_origin=str(expected_origin or ""),
        ),
        dest_is_placeholder=_is_google_dest_placeholder(observed_dest_raw),
    )


def _should_attempt_google_route_mismatch_reset(
    *,
    mismatch_detected: bool,
    enabled: bool,
    attempts: int,
    max_attempts: int,
) -> bool:
    """Return True when bounded mismatch reset should run."""
    return _should_attempt_google_route_mismatch_reset_impl(
        mismatch_detected=bool(mismatch_detected),
        enabled=bool(enabled),
        attempts=int(attempts),
        max_attempts=int(max_attempts),
    )


def _run_google_route_mismatch_reset(
    browser,
    *,
    deeplink_url: str,
    wait_selectors,
) -> bool:
    """Run one bounded mismatch reset sequence (renavigate + optional clear + wait)."""
    if not isinstance(deeplink_url, str) or not deeplink_url.strip():
        return False
    try:
        browser.goto(deeplink_url.strip())
    except Exception as exc:
        log.warning("scenario.mismatch_reset.goto_failed error=%s", exc)
        return False

    clear_timeout_ms = int(get_threshold("google_flights_reset_clear_timeout_ms", 900))
    reset_selectors = _google_route_reset_selectors()
    chosen_selector = None

    for selector in reset_selectors:
        try:
            browser.click(selector, timeout_ms=clear_timeout_ms)
            chosen_selector = selector
            time.sleep(0.5)  # Brief wait after successful click to allow fields to clear
            break
        except Exception:
            continue

    log.info(
        "scenario.mismatch_reset.clear attempted_count=%d chosen=%s",
        len(reset_selectors),
        chosen_selector or "none",
    )

    wait_timeout_ms = int(get_threshold("google_flights_reset_wait_timeout_ms", 4500))
    selectors = sr._selector_candidates(wait_selectors)
    if not selectors:
        return True
    for selector in selectors:
        try:
            browser.wait(selector, timeout_ms=wait_timeout_ms)
            return True
        except Exception:
            continue
    # Recovery already renavigated; keep this as soft-success to continue fallback flow.
    return True


def _expected_route_values_from_plan(plan) -> Dict[str, str]:
    """Infer expected route/date values from current plan fill steps."""
    # Lazy import to avoid circular dependency
    from core.scenario_runner import _infer_fill_role

    out = {"origin": "", "dest": "", "depart": "", "return": ""}
    if not isinstance(plan, list):
        return out
    for step in plan:
        if not isinstance(step, dict) or step.get("action") != "fill":
            continue
        role = _infer_fill_role(step)
        if role not in out:
            continue
        value = str(step.get("value", "") or "").strip()
        if value:
            out[role] = value
    return out


def _google_route_core_before_date_gate(
    *,
    html: str,
    page=None,
    expected_origin: str,
    expected_dest: str,
    expected_depart: str = "",
    expected_return: str = "",
) -> Dict[str, Any]:
    """Verify Google route core (origin+dest) before date fill in recovery mode.

    Phase A invariant: in deeplink recovery mode, date picker interactions are
    blocked until origin and destination are verifiably rebound. This prevents
    calendar failures on generic explore/irrelevant surfaces.
    """
    if not isinstance(expected_origin, str) or not expected_origin.strip():
        return {"ok": True, "reason": "missing_expected_origin", "evidence": {}}
    if not isinstance(expected_dest, str) or not expected_dest.strip():
        return {"ok": True, "reason": "missing_expected_dest", "evidence": {}}
    probe = {}
    try:
        probe = dom_route_bind_probe(
            str(html or ""),
            origin=expected_origin,
            dest=expected_dest,
            depart=expected_depart or "",
            return_date=expected_return or "",
        )
    except Exception as exc:
        return {
            "ok": False,
            "reason": "route_core_probe_error",
            "evidence": {"verify.route_probe_error": str(exc)[:200]},
        }

    live_state = {}
    live_origin_ok = False
    live_dest_ok = False
    live_probe_used = False
    live_probe_reason = ""
    live_probe_confidence = ""
    live_observed_origin = ""
    live_observed_dest = ""
    if page is not None:
        try:
            live_state = sr._extract_google_flights_form_state(page)
        except Exception:
            live_state = {}
        if isinstance(live_state, dict) and live_state:
            live_probe_used = True
            live_probe_reason = str(live_state.get("reason", "") or "").strip().lower()
            live_probe_confidence = str(live_state.get("confidence", "") or "").strip().lower()
            live_observed_origin = str(
                live_state.get("origin_text_raw", live_state.get("origin_text", "")) or ""
            ).strip()
            live_observed_dest = str(
                live_state.get("dest_text_raw", live_state.get("dest_text", "")) or ""
            ).strip()
            if live_observed_origin:
                live_origin_ok = _google_form_value_matches_airport(live_observed_origin, expected_origin)
            if live_observed_dest and not _is_google_dest_placeholder(live_observed_dest):
                live_dest_ok = _google_form_value_matches_airport(live_observed_dest, expected_dest)

    probe_reason = str(probe.get("reason", "") or "").strip().lower()
    probe_support = str(probe.get("support", "none") or "none").strip().lower()
    observed = dict(probe.get("observed", {}) or {})
    observed_origin = str(observed.get("origin", "") or "").strip()
    observed_dest = str(observed.get("dest", "") or "").strip()
    mismatch_fields = list(probe.get("mismatch_fields", []) or [])

    origin_ok = bool(
        observed_origin
        and _contains_any_token(
            observed_origin,
            observed_origin.upper(),
            get_airport_aliases_for_provider(expected_origin, "google_flights") or {expected_origin},
        )
    )
    dest_ok = bool(
        observed_dest
        and not _is_google_dest_placeholder(observed_dest)
        and _contains_any_token(
            observed_dest,
            observed_dest.upper(),
            get_airport_aliases_for_provider(expected_dest, "google_flights") or {expected_dest},
        )
    )

    reason = "route_core_verified"
    ok = bool(origin_ok and dest_ok)
    if not ok and live_probe_used and live_origin_ok and live_dest_ok:
        origin_ok = True
        dest_ok = True
        ok = True
        reason = "route_core_verified_live_dom_form"
    results_itinerary_match = False
    if (
        not ok
        and (probe_support in {"none", "weak"} or "dest" in mismatch_fields)
        and _google_results_itinerary_matches_expected(
            str(html or ""),
            expected_origin=expected_origin,
            expected_dest=expected_dest,
            expected_depart=expected_depart or "",
        )
    ):
        # Results-card itinerary metadata is stronger than low-confidence chip extraction
        # for route-core gating (origin/dest) before date fill.
        results_itinerary_match = True
        origin_ok = True
        dest_ok = True
        ok = True
        reason = "route_core_verified_results_itinerary"
    if not ok:
        if probe_reason.startswith("scope_non_flight_"):
            reason = probe_reason
        elif "dest" in mismatch_fields:
            reason = "route_core_dest_mismatch"
        elif "origin" in mismatch_fields:
            reason = "route_core_origin_mismatch"
        elif not observed_dest or _is_google_dest_placeholder(observed_dest):
            reason = "route_core_dest_uncommitted"
        elif not observed_origin:
            reason = "route_core_origin_missing"
        else:
            reason = "route_core_unverified"

    return {
        "ok": ok,
        "reason": reason,
        "evidence": {
            "verify.route_core_probe_reason": probe_reason,
            "verify.route_core_probe_support": probe_support,
            "verify.route_core_origin_ok": bool(origin_ok),
            "verify.route_core_dest_ok": bool(dest_ok),
            "verify.route_core_mismatch_fields": list(mismatch_fields),
            "verify.route_core_observed_origin": observed_origin[:140],
            "verify.route_core_observed_dest": observed_dest[:140],
            "verify.route_core_results_itinerary_match": bool(results_itinerary_match),
            "verify.route_core_live_probe_used": bool(live_probe_used),
            "verify.route_core_live_probe_reason": live_probe_reason,
            "verify.route_core_live_probe_confidence": live_probe_confidence,
            "verify.route_core_live_observed_origin": live_observed_origin[:140],
            "verify.route_core_live_observed_dest": live_observed_dest[:140],
            "verify.route_core_live_origin_ok": bool(live_origin_ok),
            "verify.route_core_live_dest_ok": bool(live_dest_ok),
        },
        "probe": probe if isinstance(probe, dict) else {},
    }


def _scope_rewind_followup_plan(
    *,
    site_key: str,
    plan,
    step_trace,
    scope_class: str = "unknown",
    origin: str = "",
    dest: str = "",
    depart: str = "",
    return_date: str = "",
    trip_type: str = "one_way",
    is_domestic: bool = False,
    vlm_hint: Optional[dict] = None,
):
    """Build a rewind-style scope repair plan from recent step trace memory."""
    if not bool(
        sr._threshold_site_value(
            "scenario_scope_repair_rewind_enabled",
            site_key,
            True,
        )
    ):
        return None
    if not isinstance(plan, list) or not isinstance(step_trace, list):
        return None

    pivot = sr._scope_feedback_step(step_trace)
    pivot_index = pivot.get("index") if isinstance(pivot, dict) else None
    try:
        pivot_index = int(pivot_index) if pivot_index is not None else None
    except Exception:
        pivot_index = None

    max_replay_fills = max(
        1,
        int(
            sr._threshold_site_value(
                "scenario_scope_repair_rewind_max_replay_fills",
                site_key,
                4,
            )
        ),
    )
    value_by_role = {}
    for item in step_trace:
        if not isinstance(item, dict):
            continue
        if item.get("action") != "fill":
            continue
        if item.get("status") not in {"ok", "already_bound_soft_pass"}:
            continue
        idx = item.get("index")
        try:
            idx = int(idx)
        except Exception:
            continue
        if pivot_index is not None and idx > pivot_index:
            continue
        if idx < 0 or idx >= len(plan):
            continue
        role = str(item.get("role", "") or "").strip().lower()
        if role not in {"origin", "dest", "depart", "return"}:
            role = sr._infer_fill_role(plan[idx])
        if role not in {"origin", "dest", "depart", "return"}:
            continue
        source_step = plan[idx]
        if not isinstance(source_step, dict):
            continue
        value = source_step.get("value")
        if not isinstance(value, str) or not value.strip():
            continue
        value_by_role[role] = value.strip()

    # Fall back to requested trip values for missing roles.
    if origin and "origin" not in value_by_role:
        value_by_role["origin"] = origin
    if dest and "dest" not in value_by_role:
        value_by_role["dest"] = dest
    if depart and "depart" not in value_by_role:
        value_by_role["depart"] = depart
    if return_date and "return" not in value_by_role:
        value_by_role["return"] = return_date

    replay_roles = ["origin", "dest", "depart"]
    if trip_type == "round_trip" and return_date:
        replay_roles.append("return")
    replay_steps = []
    for role in replay_roles:
        value = value_by_role.get(role)
        if not isinstance(value, str) or not value.strip():
            continue
        step = {
            "action": "fill",
            "selector": sr._service_fill_fallbacks(site_key, role),
            "value": value.strip(),
            "optional": True,
        }
        if role in {"origin", "dest", "depart"}:
            step["required_for_actionability"] = True
        replay_steps.append(step)
        if len(replay_steps) >= max_replay_fills:
            break
    if not replay_steps:
        return None

    followup = []
    use_service_toggles = bool(
        sr._threshold_site_value(
            "scenario_scope_repair_rewind_use_service_toggles",
            site_key,
            True,
        )
    )
    if use_service_toggles and sr._is_non_flight_page_class(scope_class):
        product_step = sr._service_product_toggle_step(
            site_key,
            scope_class=scope_class,
            vlm_hint=vlm_hint,
        )
        if isinstance(product_step, dict):
            followup.append(product_step)

        mode_step = sr._service_mode_toggle_step(
            site_key,
            is_domestic=bool(is_domestic),
            vlm_hint=vlm_hint,
            fallback_default=(site_key == "google_flights"),
        )
        if isinstance(mode_step, dict):
            mode_step = dict(mode_step)
            mode_step["optional"] = True
            followup.append(mode_step)

    followup.extend(replay_steps)
    click_selectors = sr._service_search_click_fallbacks(site_key)
    if click_selectors:
        followup.append(
            {
                "action": "click",
                "selector": click_selectors if len(click_selectors) > 1 else click_selectors[0],
                "optional": True,
            }
        )
    wait_selectors = sr._service_wait_fallbacks(site_key)
    if wait_selectors:
        followup.append(
            {
                "action": "wait",
                "selector": wait_selectors if len(wait_selectors) > 1 else wait_selectors[0],
            }
        )
    return followup if sr._is_valid_plan(followup) else None


def _soften_recovery_route_fills(plan):
    """Convert route/date fills into soft-fail recovery steps while preserving actionability."""
    if not isinstance(plan, list):
        return plan
    softened = []
    for step in plan:
        if not isinstance(step, dict) or step.get("action") != "fill":
            softened.append(step)
            continue
        role = sr._infer_fill_role(step)
        if role not in {"origin", "dest", "depart", "return"}:
            softened.append(step)
            continue
        new_step = dict(step)
        new_step["optional"] = True
        if role in {"origin", "dest", "depart"}:
            new_step["required_for_actionability"] = True
        softened.append(new_step)
    return softened
