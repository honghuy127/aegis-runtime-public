"""Plan helper implementations for Google recovery collaboration.

These functions are extracted from ``google_recovery_collab.py`` to avoid
large closure captures inside the orchestrator. They are pure move-only
helpers and keep signatures/behavior identical but accept the full
``context`` explicitly.
"""
from typing import Any


def _make_base_plan_impl(context: Any, _failed_plan, _vision_hint_payload) -> Any:
    """Implementation of base plan construction extracted from nested helper.

    Keeps behavior identical but accepts `context` explicitly to avoid large
    closure captures when used as a dependency.
    """
    base = (
        _failed_plan
        if context.is_valid_plan_fn(_failed_plan)
        else context.site_recovery_collab_scope_repair_plan_dispatch_fn(
            site_key=context.site_key,
            origin=context.origin,
            dest=context.dest,
            depart=context.depart,
            return_date=context.return_date,
            trip_type=context.trip_type,
            is_domestic=bool(context.is_domestic),
            scope_class="irrelevant_page",
            vlm_hint=_vision_hint_payload or context.vlm_ui_hint,
            google_scope_repair_plan_fn=context.google_non_flight_scope_repair_plan_fn,
        )
    )
    if bool(
        context.threshold_site_value_fn(
            "scenario_recovery_force_soft_fill",
            context.site_key,
            True,
        )
    ):
        base = context.soften_recovery_route_fills_fn(base)
    return base


def _postprocess_plan_impl(context: Any, _plan) -> Any:
    """Implementation of postprocess_plan extracted from nested helper.

    Accepts `context` explicitly and preserves behavior.
    """
    out = context.retarget_plan_inputs_fn(
        plan=_plan,
        origin=context.origin,
        dest=context.dest,
        depart=context.depart,
        return_date=context.return_date,
        trip_type=context.trip_type,
        site_key=context.site_key,
    )
    if bool(context.google_recovery_collab_limits.get("route_core_only_first", True)):
        out = context.site_recovery_collab_focus_plan_dispatch_fn(
            out,
            site_key=context.site_key,
            origin=context.origin,
            dest=context.dest,
            google_focus_plan_fn=context.google_route_core_only_recovery_plan_fn,
        )
    if bool(
        context.threshold_site_value_fn(
            "scenario_recovery_force_soft_fill",
            context.site_key,
            True,
        )
    ):
        out = context.soften_recovery_route_fills_fn(out)
    return out


def _refresh_html_impl(context: Any) -> str:
    """Return the current browser HTML content as a string.

    Extracted from the inline lambda to reduce closure captures.
    """
    return str(context.browser.content() or "")


def _reprobe_route_core_impl(context: Any, html_text: str) -> Any:
    """Invoke the site's pre-date gate probe for a given HTML snapshot.

    Mirrors the original inline reprobe lambda but accepts `context` and
    `html_text` explicitly.
    """
    return context.site_recovery_pre_date_gate_dispatch_fn(
        site_key=context.site_key,
        html=html_text,
        page=getattr(context.browser, "page", None),
        expected_origin=context.origin or "",
        expected_dest=context.dest or "",
        expected_depart=context.depart or "",
        expected_return=context.return_date or "",
        google_gate_fn=context.google_route_core_before_date_gate_fn,
    )


def _call_repair_plan_impl(
    context: Any, *, base_plan, current_html, turn_index, timeout_sec
) -> Any:
    """Invoke the configured repair-plan bundler with orchestrator args.

    Extracted from an inline lambda to reduce closure captures. Mirrors the
    original call to `context.call_repair_action_plan_bundle_fn`.
    """
    return context.call_repair_action_plan_bundle_fn(
        base_plan,
        current_html,
        router=context.router,
        site_key=context.site_key,
        turn_index=turn_index,
        origin=context.origin,
        dest=context.dest,
        depart=context.depart,
        return_date=context.return_date or "",
        is_domestic=context.is_domestic,
        mimic_locale=context.mimic_locale,
        mimic_region=context.mimic_region,
        screenshot_path=context.planner_snapshot_path_fn(context.site_key, ["last", "initial"], run_id=context.scenario_run_id),
        trace_memory_hint=context.trace_memory_hint,
        timeout_sec=timeout_sec,
    )


def _call_generate_plan_impl(
    context: Any, *, current_html, local_hint, turn_index, timeout_sec
) -> Any:
    """Invoke the configured plan-generator bundler with orchestrator args.

    Extracted from an inline lambda to reduce closure captures. Mirrors the
    original call to `context.call_generate_action_plan_bundle_fn`.
    """
    return context.call_generate_action_plan_bundle_fn(
        router=context.router,
        html=current_html,
        origin=context.origin,
        dest=context.dest,
        depart=context.depart,
        return_date=context.return_date,
        trip_type=context.trip_type,
        is_domestic=context.is_domestic,
        max_transit=context.max_transit,
        turn_index=turn_index,
        global_knowledge=context.global_knowledge_hint,
        local_knowledge=local_hint,
        site_key=context.site_key,
        mimic_locale=context.mimic_locale,
        mimic_region=context.mimic_region,
        screenshot_path=context.planner_snapshot_path_fn(context.site_key, ["last", "initial"], run_id=context.scenario_run_id),
        trace_memory_hint=context.trace_memory_hint,
        timeout_sec=timeout_sec,
    )


def _build_env_impl(context: Any, trigger_reason: str) -> dict:
    """Build the `env` mapping passed to the recovery collab followup.

    Extracted from the inline env literal to reduce closure captures.
    """
    return {
        "site_key": context.site_key,
        "recovery_mode": bool(context.google_recovery_mode),
        "limits": context.google_recovery_collab_limits,
        "usage": context.google_recovery_collab_usage,
        "trigger_reason": trigger_reason,
        "local_knowledge_hint": context.local_knowledge_hint,
        "planner_notes": list(context.planner_notes or []),
        "trace_memory_hint": context.trace_memory_hint,
    }


class RecoveryPlan:
    """Small helper class grouping recovery plan helper methods.

    This class wraps the extracted impl functions to provide a compact
    object for use inside `google_recovery_collab_followup_impl` while
    keeping the original behavior unchanged.
    """

    def __init__(self, context: Any):
        self._ctx = context

    def make_base_plan(self, _failed_plan, _vision_hint_payload):
        return _make_base_plan_impl(self._ctx, _failed_plan, _vision_hint_payload)

    def postprocess_plan(self, _plan):
        return _postprocess_plan_impl(self._ctx, _plan)

    def refresh_html(self) -> str:
        return _refresh_html_impl(self._ctx)

    def reprobe_route_core(self, html_text: str) -> Any:
        return _reprobe_route_core_impl(self._ctx, html_text)

    def call_repair_plan(self, *, base_plan, current_html, turn_index, timeout_sec) -> Any:
        return _call_repair_plan_impl(
            self._ctx,
            base_plan=base_plan,
            current_html=current_html,
            turn_index=turn_index,
            timeout_sec=timeout_sec,
        )

    def call_generate_plan(self, *, current_html, local_hint, turn_index, timeout_sec) -> Any:
        return _call_generate_plan_impl(
            self._ctx,
            current_html=current_html,
            local_hint=local_hint,
            turn_index=turn_index,
            timeout_sec=timeout_sec,
        )

    def build_env(self, trigger_reason: str) -> dict:
        return _build_env_impl(self._ctx, trigger_reason)
