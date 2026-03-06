"""Context helpers extracted from scenario_runner.execute_plan.

This module keeps `execute_plan(...)` behavior stable while moving local helper
closures into a typed context object.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from core.scenario_runner.actor_calls import (
    google_recent_local_date_failure_in_turn as _google_recent_local_date_failure_in_turn_impl,
    record_graph_transition as _record_graph_transition_impl,
    run_step_action as _run_step_action_impl,
    step_wall_clock_cap_ms as _step_wall_clock_cap_ms_impl,
    trace_date_done_clicked as _trace_date_done_clicked_impl,
    trace_latest_fill_selector as _trace_latest_fill_selector_impl,
    write_before_search_evidence as _write_before_search_evidence_impl,
)


@dataclass
class ExecutePlanContext:
    browser: Any
    site_key: str
    graph_stats: Any
    attempt: int
    turn: int
    page_kind: str
    locale: str
    step_trace: List[Dict[str, Any]]
    evidence_enabled: bool
    evidence_run_id: str
    evidence_service: str
    evidence_checkpoint: str
    evidence_url: str
    expected_route_values: Dict[str, Any]
    get_threshold_fn: Callable[..., Any]
    threshold_site_value_fn: Callable[..., Any]
    selector_candidates_fn: Callable[..., Any]
    current_mimic_locale_fn: Callable[..., Any]
    prioritize_tokens_fn: Callable[..., Any]
    get_tokens_fn: Callable[..., Any]
    compact_selector_dom_probe_fn: Callable[..., Any]
    write_json_artifact_snapshot_fn: Callable[..., Any]
    write_google_date_selector_probe_fn: Callable[..., Any]
    get_current_page_url_impl_fn: Callable[..., Any]
    dom_route_bind_probe_fn: Callable[..., Any]

    def current_page_url_for_search_commit(self) -> str:
        return self.get_current_page_url_impl_fn(self.browser, self.evidence_url)

    def debug_google_date_selector_probe(
        self,
        *,
        stage_label: str,
        role_key: str,
        target_value: str,
        selectors_for_probe: List[str],
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.write_google_date_selector_probe_fn(
            browser=self.browser,
            site_key=self.site_key,
            evidence_run_id=self.evidence_run_id,
            stage_label=stage_label,
            role_key=role_key,
            target_value=target_value,
            selectors_for_probe=selectors_for_probe,
            attempt=self.attempt,
            turn=self.turn,
            extra=extra,
            compact_selector_dom_probe_fn=self.compact_selector_dom_probe_fn,
            write_json_artifact_fn=self.write_json_artifact_snapshot_fn,
        )

    def record_graph_transition(
        self,
        step_index: int,
        action: str,
        role: Optional[str],
        selector: str,
        status: str,
        error: str,
        elapsed_ms: int,
    ) -> None:
        _record_graph_transition_impl(
            step_index,
            action,
            role,
            selector,
            status,
            error,
            elapsed_ms,
            graph_stats=self.graph_stats,
            evidence_run_id=self.evidence_run_id,
            attempt=self.attempt,
            turn=self.turn,
            site_key=self.site_key,
            page_kind=self.page_kind,
            locale=self.locale,
        )

    def trace_latest_fill_selector(self, role: str) -> str:
        return _trace_latest_fill_selector_impl(role, self.step_trace)

    def trace_date_done_clicked(self) -> bool:
        return _trace_date_done_clicked_impl(
            self.step_trace,
            self.current_mimic_locale_fn(),
            lambda *args: self.get_tokens_fn("actions", "done"),
            self.prioritize_tokens_fn,
            self.selector_candidates_fn,
        )

    def write_before_search_evidence(
        self,
        *,
        form_state: Dict[str, Any],
        route_verify: Optional[Dict[str, Any]],
    ) -> None:
        _write_before_search_evidence_impl(
            form_state,
            route_verify,
            evidence_enabled=self.evidence_enabled,
            evidence_run_id=self.evidence_run_id,
            evidence_service=self.evidence_service,
            evidence_checkpoint=self.evidence_checkpoint,
            evidence_url=self.evidence_url,
            browser=self.browser,
            dom_route_bind_probe_fn=self.dom_route_bind_probe_fn,
            expected_route_values=self.expected_route_values,
        )

    def run_step_action(
        self,
        action_name: str,
        selector_candidates,
        *,
        value=None,
        timeout_ms=None,
    ):
        return _run_step_action_impl(
            self.browser,
            action_name,
            selector_candidates,
            value=value,
            timeout_ms=timeout_ms,
        )

    def google_recent_local_date_failure_in_turn(self) -> bool:
        return _google_recent_local_date_failure_in_turn_impl(
            self.site_key,
            self.step_trace,
        )

    def step_wall_clock_cap_ms(self, action_name: str) -> int:
        return _step_wall_clock_cap_ms_impl(
            action_name,
            self.site_key,
            self.get_threshold_fn,
            self.threshold_site_value_fn,
        )
