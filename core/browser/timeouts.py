"""Browser timeout and deadline utilities for bounded action budgets."""

import time
from typing import Optional, Union

from utils.thresholds import get_threshold


# Per-selector timeout thresholds (for fallback chains)
DEFAULT_ACTION_SELECTOR_TIMEOUT_MS = int(
    get_threshold("browser_action_selector_timeout_ms", 4_000)
)
DEFAULT_WAIT_SELECTOR_TIMEOUT_MS = int(
    get_threshold("browser_wait_selector_timeout_ms", 4_000)
)
DEFAULT_SELECTOR_TIMEOUT_MIN_MS = int(
    get_threshold("browser_selector_timeout_min_ms", 800)
)


def apply_selector_timeout_strategy(
    base_timeout_ms: Optional[Union[int, str]] = None,
    action_type: str = "wait",
    site_key: str = None,
    is_optional_click: bool = False,
) -> int:
    """
    Compute a safe per-selector timeout using adaptive clamping.

    Prevents both overly aggressive timeouts (< 800ms) and cascading long waits
    when multiple selectors in a fallback chain timeout sequentially.

    Args:
        base_timeout_ms: Optional override timeout (uses defaults if None).
        action_type: Either "wait" or "action" to select threshold key.
        site_key: Optional site identifier for per-site threshold override.

    Returns:
        Clamped timeout in milliseconds.
    """
    # Select default based on action type
    if action_type == "action":
        default_timeout = DEFAULT_ACTION_SELECTOR_TIMEOUT_MS
        threshold_key = "browser_action_selector_timeout_ms"
    else:  # "wait"
        default_timeout = DEFAULT_WAIT_SELECTOR_TIMEOUT_MS
        threshold_key = "browser_wait_selector_timeout_ms"

    # Allow override by explicit threshold key name (string input)
    if isinstance(base_timeout_ms, str):
        custom_key = base_timeout_ms
        base_timeout_ms = int(get_threshold(custom_key, default_timeout))
        if site_key:
            base_timeout_ms = int(
                get_threshold(f"{custom_key}_{site_key}", base_timeout_ms)
            )
    elif is_optional_click and base_timeout_ms is None:
        custom_key = "browser_optional_click_timeout_ms"
        base_timeout_ms = int(get_threshold(custom_key, default_timeout))
        if site_key:
            base_timeout_ms = int(
                get_threshold(f"{custom_key}_{site_key}", base_timeout_ms)
            )
    elif site_key:
        threshold_key_per_site = f"{threshold_key}_{site_key}"
        base_timeout_ms = int(
            get_threshold(threshold_key_per_site, base_timeout_ms or default_timeout)
        )
    elif base_timeout_ms is None:
        base_timeout_ms = default_timeout
    else:
        base_timeout_ms = int(base_timeout_ms)

    # Safe clamp: enforce minimum threshold to prevent accidental microsecond ranges
    min_ms = DEFAULT_SELECTOR_TIMEOUT_MIN_MS
    if base_timeout_ms < min_ms:
        base_timeout_ms = min_ms

    return base_timeout_ms


def safe_min_timeout_ms(
    timeout_ms: int,
    cap_ms: int,
    min_threshold_ms: int = DEFAULT_SELECTOR_TIMEOUT_MIN_MS,
) -> int:
    """
    Safely compute min(timeout_ms, cap_ms) with guarantee that result >= min_threshold_ms.

    Prevents accidental sub-threshold timeouts when creating bounded waits.
    Essential for patterns like min(timeout_value, 600) to never drop below safe minimum.

    Args:
        timeout_ms: The main timeout in milliseconds.
        cap_ms: The upper bound cap.
        min_threshold_ms: The minimum allowed result (default 800ms).

    Returns:
        Clamped value: max(min_threshold_ms, min(timeout_ms, cap_ms))
    """
    if not isinstance(timeout_ms, int):
        timeout_ms = int(timeout_ms) if timeout_ms else min_threshold_ms
    if not isinstance(cap_ms, int):
        cap_ms = int(cap_ms) if cap_ms else min_threshold_ms

    result = min(timeout_ms, cap_ms)
    # Ensure result never drops below safe minimum
    if result < min_threshold_ms:
        result = min_threshold_ms
    return result


def wall_clock_deadline(timeout_ms: Optional[int]) -> Optional[float]:
    """Return a monotonic deadline timestamp for a wall-clock timeout budget."""
    if timeout_ms is None:
        return None
    return time.monotonic() + (max(1, int(timeout_ms)) / 1000.0)


def wall_clock_remaining_ms(deadline: Optional[float]) -> Optional[int]:
    """Return remaining wall-clock budget in milliseconds (0 if exhausted)."""
    if deadline is None:
        return None
    remaining_ms = int((deadline - time.monotonic()) * 1000)
    return max(0, remaining_ms)


def wall_clock_exhausted(deadline: Optional[float]) -> bool:
    """Return True when a wall-clock deadline has been exceeded."""
    return deadline is not None and time.monotonic() >= deadline


def enforce_wall_clock_deadline(deadline: Optional[float], *, context: str = "") -> None:
    """Raise TimeoutError when the wall-clock deadline is exceeded."""
    if wall_clock_exhausted(deadline):
        suffix = f" {context}" if context else ""
        raise TimeoutError(f"wall_clock_timeout{suffix}")
