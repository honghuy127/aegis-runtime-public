"""Google recovery collaboration setup for run_agentic_scenario."""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from utils.logging import get_logger

log = get_logger(__name__)
from core.scenario_runner.google_recovery_plan_helpers import (
    _make_base_plan_impl,
    _postprocess_plan_impl,
    _refresh_html_impl,
    _reprobe_route_core_impl,
    _call_repair_plan_impl,
    _call_generate_plan_impl,
    _build_env_impl,
    RecoveryPlan,
)
from core.scenario_runner.google_recovery_session import RecoverySession


@dataclass
class GoogleRecoveryCollabContext:
    """Context for Google recovery collaboration phase B."""
    # Route/flight parameters
    site_key: Optional[str]
    origin: Optional[str]
    dest: Optional[str]
    depart: Optional[str]
    return_date: Optional[str]
    trip_type: str
    is_domestic: bool
    max_transit: int
    mimic_locale: str
    mimic_region: str

    # Recovery configuration
    google_recovery_mode: bool
    google_recovery_collab_limits: Dict[str, Any]
    google_recovery_collab_usage: Dict[str, Any]

    # Knowledge and hints
    local_knowledge_hint: Optional[str]
    planner_notes: List[str]
    trace_memory_hint: Optional[str]
    vlm_ui_hint: Optional[Dict[str, Any]]
    global_knowledge_hint: Optional[str]

    # Runtime state
    router: Any
    browser: Any
    scenario_run_id: str

    # Dispatch functions
    site_recovery_collab_trigger_reason_dispatch_fn: Callable
    site_recovery_collab_scope_repair_plan_dispatch_fn: Callable
    threshold_site_value_fn: Callable
    soften_recovery_route_fills_fn: Callable
    retarget_plan_inputs_fn: Callable
    site_recovery_collab_focus_plan_dispatch_fn: Callable
    is_valid_plan_fn: Callable
    run_vision_page_kind_probe_fn: Callable
    apply_vision_page_kind_hints_fn: Callable
    site_recovery_pre_date_gate_dispatch_fn: Callable
    compose_local_hint_with_notes_fn: Callable
    call_repair_action_plan_bundle_fn: Callable
    call_generate_action_plan_bundle_fn: Callable
    planner_snapshot_path_fn: Callable
    try_recovery_collab_followup_impl_fn: Callable

    # Google-specific repair/gate functions
    google_non_flight_scope_repair_plan_fn: Callable
    google_route_core_only_recovery_plan_fn: Callable
    google_route_core_before_date_gate_fn: Callable


def google_recovery_collab_followup_impl(
    *,
    current_html: str,
    failed_plan: Any,
    route_core_failure: Optional[Dict[str, Any]],
    turn_index: int,
    context: GoogleRecoveryCollabContext,
) -> Any:
    """Phase B: bounded planner/VLM collaboration for route-core recovery."""
    # Build trigger_reason and delegate the heavy assembly to RecoverySession
    # to keep this function minimal and preserve call-site signatures.
    trigger_reason = (
        context.site_recovery_collab_trigger_reason_dispatch_fn(context.site_key)
        or "route_core_before_date_fill_unverified"
    )
    session = RecoverySession(context)
    return session.run_followup(
        current_html=current_html,
        failed_plan=failed_plan,
        route_core_failure=route_core_failure,
        turn_index=turn_index,
        trigger_reason=trigger_reason,
    )


# RecoverySession moved to core.scenario_runner.google_recovery_session
