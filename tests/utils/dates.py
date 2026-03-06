"""Deterministic dynamic date helpers for tests.

Why this exists:
- Fixed calendar dates in runtime-flow tests rot over time and create brittle behavior.
- Scenario/integration tests should always use coherent future trip dates relative to
  today's UTC date while remaining deterministic.

Governance reference:
- docs/kb/50_governance/tests_hygiene.md#dynamic-date-policy
- tests/test_governance_no_fixed_dates.py
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from random import Random
from typing import Optional, Tuple

SESSION_SEED = 20260302
_rng = Random(SESSION_SEED)


def utc_today() -> date:
    """Return today's UTC date."""
    return datetime.now(timezone.utc).date()


def iso(d: date) -> str:
    """Return YYYY-MM-DD."""
    return d.isoformat()


def future_date(days_ahead_min: int = 7, days_ahead_max: int = 30) -> date:
    """Return a deterministic future UTC date in [min, max] days ahead."""
    if days_ahead_min < 0:
        raise ValueError("days_ahead_min must be >= 0")
    if days_ahead_max < days_ahead_min:
        raise ValueError("days_ahead_max must be >= days_ahead_min")
    days_ahead = _rng.randint(days_ahead_min, days_ahead_max)
    return utc_today() + timedelta(days=days_ahead)


def trip_dates(
    *,
    days_ahead_min: int = 7,
    days_ahead_max: int = 30,
    trip_min: int = 3,
    trip_max: int = 14,
    round_trip: bool = True,
) -> Tuple[str, Optional[str]]:
    """Return coherent deterministic ISO trip dates (depart, optional return)."""
    if trip_min < 0:
        raise ValueError("trip_min must be >= 0")
    if trip_max < trip_min:
        raise ValueError("trip_max must be >= trip_min")

    depart = future_date(days_ahead_min=days_ahead_min, days_ahead_max=days_ahead_max)
    if not round_trip:
        return iso(depart), None

    trip_len = _rng.randint(trip_min, trip_max)
    return_date = depart + timedelta(days=trip_len)
    return iso(depart), iso(return_date)


def format_date(d: date, fmt: str = "iso") -> str:
    """Return a simple deterministic date rendering used by UI-like tests."""
    key = str(fmt or "iso").strip().lower()
    if key == "iso":
        return d.isoformat()
    if key == "md":
        return f"{d.month}/{d.day}"
    if key == "english_month_day":
        return d.strftime("%b %-d")
    if key == "english_month_day_comma":
        return d.strftime("%a, %b %-d")
    raise ValueError(f"Unsupported fmt: {fmt}")
