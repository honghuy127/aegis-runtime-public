"""Timeout helpers for google_fill_date_via_picker.

Move-only extraction from core/scenario/google_flights.py.
"""

from core.browser import wall_clock_remaining_ms


def get_budgeted_timeout(deadline, timeout_value: int) -> int:
    """Return min(timeout_value, remaining_ms_until_deadline).

    Raises TimeoutError if deadline exceeded.
    """
    remaining_ms = wall_clock_remaining_ms(deadline)
    if remaining_ms is None:
        return max(1, int(timeout_value))
    if remaining_ms <= 0:
        raise TimeoutError("wall_clock_timeout google_date_picker")
    return max(1, min(int(timeout_value), int(remaining_ms)))
