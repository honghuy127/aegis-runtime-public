"""Graph transition recording for execute_plan."""

from dataclasses import dataclass
from typing import Any, Optional

from core.scenario.reasons import normalize_reason
from utils.graph_policy_stats import GraphPolicyStats
from utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class GraphTransitionContext:
    """Context for recording graph policy stats transitions."""
    graph_stats: Optional[GraphPolicyStats]
    evidence_run_id: Optional[str]
    attempt: int
    turn: int
    site_key: Optional[str]
    page_kind: str
    locale: Optional[str]


def record_graph_transition_impl(
    step_index: int,
    action: str,
    role: str,
    selector: str,
    status: str,
    error: str,
    elapsed_ms: int,
    *,
    context: GraphTransitionContext,
) -> None:
    """Record graph policy stats transition if enabled."""
    if not context.graph_stats or not context.evidence_run_id:
        return

    # Map step status to outcome category
    if status == "ok":
        outcome = "ok"
        reason_code = "success"
    elif status in ("hard_fail", "route_fill_mismatch"):
        outcome = "hard_fail"
        # Extract reason from error if available
        error_lower = str(error).lower()
        if "selector_not_found" in error_lower or "no such element" in error_lower:
            reason_code = "selector_not_found"
        elif "timeout" in error_lower or "timed out" in error_lower:
            reason_code = "timeout_error"
        elif "route_fill_mismatch" in error_lower:
            reason_code = "route_fill_mismatch"
        elif "calendar" in error_lower:
            reason_code = "calendar_not_open"
        else:
            reason_code = str(error).split(":")[0] if ":" in str(error) else "unknown_error"
    else:
        # soft_fail, skip_already_bound, etc.
        outcome = "soft_fail"
        if "already_bound" in status:
            reason_code = "already_bound"
        elif "optional" in status or "visibility" in status:
            reason_code = "selector_not_found"
        else:
            reason_code = status

    # Keep graph stats taxonomy aligned with canonical reason registry.
    canonical_reason = normalize_reason(reason_code)
    if canonical_reason != "unknown":
        reason_code = canonical_reason

    try:
        context.graph_stats.record_transition(
            run_id=context.evidence_run_id,
            attempt=context.attempt,
            turn=context.turn,
            step_index=step_index,
            site=context.site_key or "unknown",
            page_kind=context.page_kind,
            locale=context.locale or "unknown",
            role=role or "none",
            action=action or "unknown",
            selector=selector or "unknown",
            strategy_id="",  # TODO: could extract from evidence if available
            outcome=outcome,
            reason_code=reason_code,
            elapsed_ms=elapsed_ms,
        )
    except Exception as stats_exc:
        # Never let stats recording break the main flow
        log.debug("graph_stats.record_failed error=%s", stats_exc)
