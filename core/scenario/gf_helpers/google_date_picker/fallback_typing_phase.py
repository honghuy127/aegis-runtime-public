"""Phase: typing fallback for Google date picker.

Move-only extraction from core/scenario/google_flights.py.
"""

from __future__ import annotations

from typing import Callable, Optional

from core.scenario.gf_helpers.date_typing import (
    _google_date_typing_fallback as _google_date_typing_fallback_impl,
)


def attempt_typing_fallback(
    browser,
    *,
    target_date: str,
    role_key: str,
    role_selectors,
    result: dict,
    timeout_fn: Callable[[], int],
    logger,
    preferred_selectors: Optional[list] = None,
    deadline: Optional[float] = None,
    max_attempts: int = 2,
    date_formats: Optional[list] = None,
) -> dict:
    """Wrapper for date typing fallback helper."""
    return _google_date_typing_fallback_impl(
        browser,
        target_date,
        role_key,
        role_selectors,
        result,
        timeout_fn,
        logger,
        preferred_selectors=preferred_selectors,
        deadline=deadline,
        max_attempts=max_attempts,
        date_formats=date_formats,
    )
