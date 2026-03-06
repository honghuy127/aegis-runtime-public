"""Timing and budget management for gf_set_date operations.

Extracted from: core/scenario/google_flights.py gf_set_date() function
Phase A decomposition: Eliminate closure dependencies for _budgeted_timeout and _budget_check
"""

from typing import Any, Optional
import logging

from core.browser import wall_clock_remaining_ms


class BudgetedTimeoutManager:
    """Thread-safe deadline + budget enforcement for date picker operations.

    Encapsulates the formerly-nested closures _budgeted_timeout() and _budget_check()
    from gf_set_date(), eliminating variable capture dependencies.

    Dependencies eliminated:
    - deadline (captured) → self.deadline (attribute)
    - timeout_value (captured) → self.timeout_value (attribute)
    - budget (captured) → self.budget (attribute)
    - role_key (captured) → self.role_key (attribute)
    - logger (captured) → self.logger (attribute)

    Testability improved: Can create instance in isolation, inject for testing.
    """

    def __init__(
        self,
        deadline: float,
        timeout_value: int,
        budget: Any,
        role_key: str,
        logger: Optional[logging.Logger] = None,
    ):
        """Initialize timeout and budget manager.

        Args:
            deadline: Wall-clock deadline (monotonic time) for all operations
            timeout_value: Default timeout in milliseconds for individual operations
            budget: ActionBudget instance for tracking action count
            role_key: Role being processed ('depart' or 'return') for logging
            logger: Logger instance for warnings and diagnostics
        """
        self.deadline = deadline
        self.timeout_value = timeout_value
        self.budget = budget
        self.role_key = role_key
        self.logger = logger or logging.getLogger(__name__)
        self.budget_used_at_init = budget.max_actions - budget.remaining

    def get_budgeted_timeout(self) -> int:
        """Return min(timeout_value, remaining_ms_until_deadline).

        Respects the hard deadline by capping all local timeouts
        to not exceed the wall-clock deadline. If deadline is exceeded,
        raises TimeoutError immediately.

        Returns:
            Timeout in milliseconds (guaranteed >= 1)

        Raises:
            TimeoutError: If wall-clock deadline has been exceeded
        """
        remaining_ms = wall_clock_remaining_ms(self.deadline)
        if remaining_ms is None:
            return max(1, int(self.timeout_value))
        if remaining_ms <= 0:
            raise TimeoutError(f"wall_clock_timeout gf_set_date role={self.role_key}")
        return max(1, min(int(self.timeout_value), int(remaining_ms)))

    def check_and_consume_budget(self, cost: int = 1) -> bool:
        """Check if budget allows cost, and consume it if yes.

        Returns False if budget exhausted, True if cost successfully consumed.
        Logs a warning if budget is exhausted.

        Args:
            cost: Number of actions to consume (default 1)

        Returns:
            True if budget allows and consumed, False if exhausted
        """
        if not self.budget.consume(cost):
            self.logger.warning(
                "gf_set_date.budget_hit role=%s action_count=%d max=%d",
                self.role_key,
                self.budget.max_actions - self.budget.remaining,
                self.budget.max_actions,
            )
            return False
        return True

    def actions_used_since_init(self) -> int:
        """Return number of actions consumed since initialization.

        Useful for evidence tracking and final accounting.

        Returns:
            Action count consumed since __init__
        """
        return (self.budget.max_actions - self.budget.remaining) - self.budget_used_at_init
