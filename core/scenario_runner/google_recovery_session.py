"""RecoverySession grouping for Google recovery collab followups.

This module contains a small session object moved out of the main
orchestrator to reduce closure size in `google_recovery_collab.py`.
"""

from typing import Any, Optional

from utils.logging import get_logger

from core.scenario_runner.google_recovery_plan_helpers import RecoveryPlan

log = get_logger(__name__)


class RecoverySession:
    """Encapsulate a single recovery collab followup invocation.

    This groups env/deps assembly and invokes the provided
    `try_recovery_collab_followup_impl_fn` on the context. Move-only
    grouping to reduce closure size in the orchestrator.
    """

    def __init__(self, context: Any):
        self._context = context
        self._recovery = RecoveryPlan(context)

    def run_followup(
        self,
        *,
        current_html: str,
        failed_plan: Any,
        route_core_failure: Optional[dict],
        turn_index: int,
        trigger_reason: str,
    ) -> Any:
        ctx = self._context
        recovery = self._recovery
        return ctx.try_recovery_collab_followup_impl_fn(
            current_html=current_html,
            failed_plan=failed_plan,
            route_core_failure=route_core_failure,
            turn_index=turn_index,
            env=recovery.build_env(trigger_reason),
            deps={
                "log": log,
                "is_valid_plan": ctx.is_valid_plan_fn,
                "run_vision_probe": ctx.run_vision_page_kind_probe_fn,
                "apply_vision_hints": ctx.apply_vision_page_kind_hints_fn,
                "refresh_html": recovery.refresh_html,
                "reprobe_route_core": recovery.reprobe_route_core,
                "make_base_plan": recovery.make_base_plan,
                "compose_local_hint_with_notes": ctx.compose_local_hint_with_notes_fn,
                "call_repair_plan": recovery.call_repair_plan,
                "call_generate_plan": recovery.call_generate_plan,
                "postprocess_plan": recovery.postprocess_plan,
            },
        )
