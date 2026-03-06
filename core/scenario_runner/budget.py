"""Budget and timeout tracking helpers for agentic scenario execution.

Move-only extraction from core/scenario_runner.py to reduce file size.
No behavior changes.
"""

import time
from typing import Optional


def budget_remaining_sec(
    *,
    scenario_budget_sec: float,
    scenario_started_at: float,
) -> Optional[float]:
    """Calculate remaining budget time in seconds.

    Args:
        scenario_budget_sec: Total budget in seconds (0 or negative means no budget)
        scenario_started_at: Start time from time.monotonic()

    Returns:
        Remaining seconds, or None if no budget is set
    """
    if scenario_budget_sec <= 0:
        return None
    elapsed = time.monotonic() - scenario_started_at
    return float(scenario_budget_sec) - float(elapsed)


def budget_almost_exhausted(
    *,
    scenario_budget_sec: float,
    scenario_started_at: float,
    scenario_budget_soft_margin_sec: float,
) -> bool:
    """Check if budget is almost exhausted (within soft margin).

    Args:
        scenario_budget_sec: Total budget in seconds
        scenario_started_at: Start time from time.monotonic()
        scenario_budget_soft_margin_sec: Soft margin threshold

    Returns:
        True if remaining time is within the soft margin
    """
    remaining = budget_remaining_sec(
        scenario_budget_sec=scenario_budget_sec,
        scenario_started_at=scenario_started_at,
    )
    return remaining is not None and remaining <= float(scenario_budget_soft_margin_sec)


def wall_clock_cap_exhausted(
    *,
    scenario_started_at: float,
    scenario_wall_clock_cap_sec: float,
    wall_clock_cap_reached_fn,
) -> bool:
    """Check if wall clock cap has been reached.

    Args:
        scenario_started_at: Start time from time.monotonic()
        scenario_wall_clock_cap_sec: Wall clock cap in seconds
        wall_clock_cap_reached_fn: Function to check if cap reached (for compatibility)

    Returns:
        True if wall clock cap has been exhausted
    """
    return wall_clock_cap_reached_fn(
        started_at=scenario_started_at,
        cap_sec=scenario_wall_clock_cap_sec,
    )
