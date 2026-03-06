"""Browser framework utilities: deadline management, logging, error handling.

This module provides low-level utilities that support browser session operations:
- Deadline/timeout budget management
- Low remaining time warning logs
- Exception propagation helpers
- Locator type checking utilities
"""

import time
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

# Import the constant from session module (will be passed as parameter instead)
DEFAULT_PLAYWRIGHT_ATTEMPT_TIMEOUT_FLOOR_MS = 150


class BrowserFrameworkHelper:
    """Framework utilities for deadline management and session support."""

    def __init__(self, browser_session):
        """Initialize with reference to parent BrowserSession."""
        self.session = browser_session

    @staticmethod
    def start_deadline(timeout_ms: int) -> float:
        """Build a monotonic deadline for one action timeout budget.

        Args:
            timeout_ms: Timeout budget in milliseconds

        Returns:
            Monotonic timestamp representing the deadline
        """
        return time.monotonic() + (max(1, int(timeout_ms)) / 1000.0)

    @staticmethod
    def deadline_exceeded(deadline: float) -> bool:
        """Return True when per-action timeout budget is fully consumed.

        Args:
            deadline: Monotonic timestamp to check against

        Returns:
            True if current time >= deadline
        """
        return time.monotonic() >= deadline

    @staticmethod
    def remaining_timeout_ms(deadline: float, floor_ms: int = None) -> int:
        """Return remaining timeout budget in milliseconds, clamped to floor minimum.

        Args:
            deadline: Monotonic timestamp representing timeout deadline
            floor_ms: Minimum remaining ms to return (default: 150ms)

        Returns:
            Remaining milliseconds, clamped to floor_ms minimum
        """
        if floor_ms is None:
            floor_ms = DEFAULT_PLAYWRIGHT_ATTEMPT_TIMEOUT_FLOOR_MS
        remaining_ms = int((deadline - time.monotonic()) * 1000)
        floor_ms = max(1, int(floor_ms))
        return max(floor_ms, remaining_ms)

    def ensure_deadline_not_exceeded(self, deadline: float, action_label: str) -> None:
        """Raise PlaywrightTimeoutError before Playwright call if deadline is exhausted.

        Args:
            deadline: Monotonic timestamp to check
            action_label: Human-readable action name for error message

        Raises:
            PlaywrightTimeoutError: If deadline has been exceeded
        """
        if self.deadline_exceeded(deadline):
            label = str(action_label or "action").strip().lower()
            raise PlaywrightTimeoutError(f"action_deadline_exceeded_before_{label}")

    def log_low_remaining_ms(
        self,
        *,
        action: str,
        selector: str,
        timeout_ms: int,
        deadline: float,
        attempt: str = "unknown",
    ) -> None:
        """Warn when remaining timeout budget is dangerously low.

        Args:
            action: Action being performed (e.g., "click", "fill")
            selector: CSS selector being targeted
            timeout_ms: Original timeout budget
            deadline: Monotonic timestamp deadline
            attempt: Attempt identifier for debugging
        """
        remaining_ms = int((deadline - time.monotonic()) * 1000)
        if remaining_ms >= 300:
            return
        from utils.logging import get_logger
        log = get_logger(__name__)
        log.warning(
            "browser.action.low_remaining action=%s selector=%s timeout_ms=%d remaining_ms=%d attempt=%s human_mimic=%s",
            str(action or "unknown"),
            (selector or "")[:100],
            int(timeout_ms),
            int(remaining_ms),
            str(attempt or "unknown"),
            bool(getattr(self.session, "human_mimic", False)),
        )

    @staticmethod
    def reraise_interrupt(exc: Exception) -> None:
        """Propagate wall-clock timeout / user interrupts without swallowing.

        Args:
            exc: Exception to check and potentially re-raise

        Raises:
            TimeoutError or KeyboardInterrupt: If exc is one of these types
        """
        if isinstance(exc, (TimeoutError, KeyboardInterrupt)):
            raise exc

    @staticmethod
    def is_hidden_input_locator(locator) -> bool:
        """Best-effort check whether locator points to a hidden input.

        Args:
            locator: Playwright locator to check

        Returns:
            True if locator points to <input type="hidden">, False otherwise
        """
        try:
            # Avoid implicit 30s Playwright waits on missing selectors.
            if locator.count() == 0:
                return False
            tag_name = (locator.evaluate("el => el.tagName") or "").strip().lower()
            if tag_name != "input":
                return False
            input_type = (locator.get_attribute("type") or "").strip().lower()
            return input_type == "hidden"
        except Exception:
            return False
