from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from core.scenario_runner.execute_plan_helpers import (
    trace_latest_fill_selector_in_plan as _trace_latest_fill_selector_impl,
    run_step_action_with_fallback as _run_step_action_impl,
    get_step_wall_clock_cap_ms as _get_step_wall_clock_cap_ms_impl,
)
from core.scenario_runner.graph_trace import GraphTransitionContext, record_graph_transition_impl
from core.scenario_runner.evidence import EvidenceContext, write_before_search_evidence_impl
from core.scenario_runner.google_flights.trace_helpers import (
    has_recent_google_date_failure_in_trace,
    has_google_date_done_clicked_in_trace,
)


def record_graph_transition(
    step_index: int,
    action: str,
    role: str,
    selector: str,
    status: str,
    error: str,
    elapsed_ms: int,
    *,
    graph_stats,
    evidence_run_id: str,
    attempt: int,
    turn: int,
    site_key: str,
    page_kind: str,
    locale: str,
) -> None:
    ctx = GraphTransitionContext(
        graph_stats=graph_stats,
        evidence_run_id=evidence_run_id,
        attempt=attempt,
        turn=turn,
        site_key=site_key,
        page_kind=page_kind,
        locale=locale,
    )
    record_graph_transition_impl(
        step_index, action, role, selector, status, error, elapsed_ms, context=ctx
    )


def trace_latest_fill_selector(role: str, step_trace: list) -> str:
    return _trace_latest_fill_selector_impl(role, step_trace)


def trace_date_done_clicked(
    step_trace: list,
    current_mimic_locale: str,
    get_tokens_fn: Callable[..., list],
    prioritize_tokens_fn: Callable[..., list],
    selector_candidates_fn: Callable[..., list],
) -> bool:
    return has_google_date_done_clicked_in_trace(
        step_trace,
        current_mimic_locale,
        get_tokens_fn,
        prioritize_tokens_fn,
        selector_candidates_fn,
    )


def write_before_search_evidence(
    form_state: Dict[str, Any],
    route_verify: Optional[Dict[str, Any]],
    *,
    evidence_enabled: bool,
    evidence_run_id: str,
    evidence_service: str,
    evidence_checkpoint: str,
    evidence_url: str,
    browser,
    dom_route_bind_probe_fn: Callable[..., Any],
    expected_route_values: Dict[str, Any],
) -> None:
    ctx = EvidenceContext(
        evidence_enabled=evidence_enabled,
        evidence_run_id=evidence_run_id,
        evidence_service=evidence_service,
        evidence_checkpoint=evidence_checkpoint,
        evidence_url=evidence_url,
        browser_content_fn=lambda: getattr(browser, "content")() if browser else None,
        dom_route_bind_probe_fn=dom_route_bind_probe_fn,
        expected_route_values=expected_route_values,
    )
    write_before_search_evidence_impl(form_state=form_state, route_verify=route_verify, context=ctx)


def run_step_action(browser, action_name: str, selector_candidates, *, value=None, timeout_ms=None):
    return _run_step_action_impl(browser, action_name, selector_candidates, value=value, timeout_ms=timeout_ms)


def google_recent_local_date_failure_in_turn(site_key: str, step_trace: list) -> bool:
    return has_recent_google_date_failure_in_trace(site_key, step_trace)


def step_wall_clock_cap_ms(action_name: str, site_key: str, get_threshold_fn: Callable[..., int], threshold_site_value_fn: Callable[..., Any]) -> int:
    return _get_step_wall_clock_cap_ms_impl(action_name, site_key, get_threshold_fn, threshold_site_value_fn)
