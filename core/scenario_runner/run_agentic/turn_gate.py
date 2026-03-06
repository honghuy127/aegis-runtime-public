"""Turn-start gating helpers for run_agentic_scenario."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional


def run_turn_start_gate(
    *,
    browser: Any,
    site_key: str,
    url: str,
    scenario_run_id: str,
    attempt: int,
    turn_idx: int,
    max_turns: int,
    wall_clock_cap_exhausted_fn: Callable[[], bool],
    budget_almost_exhausted_fn: Callable[[], bool],
    budget_remaining_sec_fn: Callable[[], Optional[float]],
    write_progress_snapshot_fn: Callable[..., None],
    scenario_return_fn: Callable[..., str],
    vision_stage_cooldown: Dict[str, str],
    logger: Any,
) -> Dict[str, Any]:
    """Run per-turn gate checks and progress snapshot write."""
    vision_stage_cooldown.clear()
    if wall_clock_cap_exhausted_fn():
        return {
            "should_return": True,
            "result_html": scenario_return_fn(
                browser.content(),
                ready=False,
                reason="scenario_wall_clock_cap",
                scope_class="unknown",
                route_bound=False,
                route_support="none",
            ),
        }
    if budget_almost_exhausted_fn():
        remaining = budget_remaining_sec_fn()
        logger.warning(
            "scenario.budget.soft_stop stage=turn_start site=%s attempt=%s turn=%s/%s remaining_s=%.2f",
            site_key,
            attempt + 1,
            turn_idx + 1,
            max_turns,
            remaining if remaining is not None else -1.0,
        )
        return {
            "should_return": True,
            "result_html": scenario_return_fn(
                browser.content(),
                ready=False,
                reason="scenario_budget_soft_stop",
                scope_class="unknown",
                route_bound=False,
                route_support="none",
            ),
        }
    logger.info(
        "scenario.turn.start attempt=%s turn=%s/%s",
        attempt + 1,
        turn_idx + 1,
        max_turns,
    )
    write_progress_snapshot_fn(
        stage="turn_start",
        run_id=scenario_run_id,
        site_key=site_key,
        url=url,
        attempt=attempt + 1,
        turn=turn_idx + 1,
        max_turns=max_turns,
    )
    return {"should_return": False, "result_html": ""}
