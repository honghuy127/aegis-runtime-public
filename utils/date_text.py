"""Shared date-text parsing helpers (locale-agnostic primitives)."""

from __future__ import annotations

import re
from typing import Optional, Tuple


ENGLISH_MONTH_NAME_TO_NUMBER = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def english_month_name_to_number(value: str) -> Optional[int]:
    """Return month number (1-12) for English month names/abbreviations."""
    text = str(value or "").strip().strip(".").lower()
    if not text:
        return None
    month = ENGLISH_MONTH_NAME_TO_NUMBER.get(text)
    return int(month) if isinstance(month, int) and 1 <= month <= 12 else None


def parse_english_month_day_text(
    text: str,
    *,
    reference_year: Optional[int] = None,
) -> str:
    """Normalize English month/day text (with optional weekday/year) to YYYY-MM-DD.

    Examples:
    - ``Sun, Mar 1`` -> ``2026-03-01`` (with ``reference_year=2026``)
    - ``March 8, 2026`` -> ``2026-03-08``
    """
    raw = str(text or "").strip()
    if not raw:
        return ""
    m = re.search(
        r"(?i)\b(?:mon|tue|wed|thu|fri|sat|sun)\.?,?\s+([a-z]{3,9})\.?\s+(\d{1,2})(?:,\s*(\d{4}))?\b"
        r"|\b([a-z]{3,9})\.?\s+(\d{1,2})(?:,\s*(\d{4}))?\b",
        raw,
    )
    if not m:
        return ""
    month_name = str(m.group(1) or m.group(4) or "")
    month = english_month_name_to_number(month_name)
    try:
        day = int(str(m.group(2) or m.group(5) or "0"))
    except Exception:
        day = 0
    year_text = str(m.group(3) or m.group(6) or "").strip()
    if year_text:
        try:
            year = int(year_text)
        except Exception:
            year = 0
    else:
        year = int(reference_year) if isinstance(reference_year, int) else 0
    if not (year and month and 1 <= day <= 31):
        return ""
    return f"{year:04d}-{month:02d}-{day:02d}"


def parse_english_month_year_text(text: str) -> Tuple[Optional[int], Optional[int]]:
    """Parse English ``Month YYYY`` text and return ``(year, month)``."""
    raw = str(text or "").strip()
    if not raw:
        return (None, None)
    m = re.search(r"(?i)\b([a-z]{3,9})\b\s+(\d{4})", raw)
    if not m:
        return (None, None)
    month = english_month_name_to_number(m.group(1))
    if not month:
        return (None, None)
    try:
        year = int(m.group(2))
    except Exception:
        return (None, None)
    if not (2000 <= year <= 2100):
        return (None, None)
    return (year, month)

