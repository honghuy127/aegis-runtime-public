"""
Calendar month header parsing utilities.

Handles robust month/year extraction from calendar UI text with support for:
- Japanese: "2026年3月", "2026年 3月", full-width digits
- English: "March 2026", "3/2026", "2026-03"
- Fallback numeric patterns

Purpose: Replace brittle regex patterns scattered throughout gf_set_date
with a single, tested, locale-aware parser.
"""

import re
from typing import Optional, Tuple

from utils.date_text import english_month_name_to_number, parse_english_month_year_text


def normalize_month_text(text: str) -> str:
    """
    Normalize calendar month header text for parsing.

    Handles:
    - Excess whitespace (tabs, newlines, multiple spaces)
    - Full-width digits (Japanese １２３ → 123)
    - Mixed encodings

    Args:
        text: Raw text from calendar header

    Returns:
        Normalized text ready for parsing
    """
    if not text:
        return ""

    # Strip leading/trailing whitespace
    text = text.strip()

    # Replace full-width digits with ASCII (Japanese: １２３ → 123)
    full_width_digits = "０１２３４５６７８９"
    ascii_digits = "0123456789"
    for fw, aw in zip(full_width_digits, ascii_digits):
        text = text.replace(fw, aw)

    # Collapse multiple spaces/tabs to single space
    text = re.sub(r'\s+', ' ', text)

    return text


def parse_month_year(text: str, locale: str = "en") -> Tuple[Optional[int], Optional[int]]:
    """
    Parse month and year from calendar header text.

    Handles multiple formats per locale:

    Japanese (locale="ja-JP"):
      - "2026年3月" → (2026, 3)
      - "2026年 3月" → (2026, 3)
      - "３月２０２６" → (2026, 3)

    English (locale="en"):
      - "March 2026" → (2026, 3)
      - "3 2026" → (2026, 3)
      - "2026-03" → (2026, 3)
      - "2026/3" → (2026, 3)

    Args:
        text: Calendar header text (may include extraneous content)
        locale: Language hint ("ja-JP", "ja", "en", "en-US", etc.)

    Returns:
        Tuple (year: int, month: int) or (None, None) if unparseable
        Year is always 4-digit (e.g., 2026).
        Month is 1-12.
    """
    if not text:
        return (None, None)

    text = normalize_month_text(text)

    # Determine if Japanese or English
    is_japanese = "ja" in locale.lower()

    # ====================================================================
    # JAPANESE FORMAT: "YYYY年M月" or "YYYY年 M月" or "M月YYYY年"
    # ====================================================================
    if is_japanese:
        # Try: YYYY年 then M月 (e.g., "2026年3月" or "2026年 3月")
        year_match = re.search(r'(\d{4})\s*年', text)
        month_match = re.search(r'(\d{1,2})\s*月', text)

        if year_match and month_match:
            year = int(year_match.group(1))
            month = int(month_match.group(1))
            if 2000 <= year <= 2100 and 1 <= month <= 12:
                return (year, month)

    # ====================================================================
    # ENGLISH FORMAT: "Month YYYY" or "M YYYY" or "YYYY/M" or "YYYY-MM"
    # ====================================================================

    # Pattern 1: Month name followed by year
    # e.g., "March 2026", "January 2026"
    month_names = (
        r"(?:january|february|march|april|may|june|"
        r"july|august|september|october|november|december)"
    )
    month_name_match = re.search(
        rf"({month_names})\s+(\d{{4}})",
        text,
        re.IGNORECASE
    )
    if month_name_match:
        year, month = parse_english_month_year_text(month_name_match.group(0))
        if year and month:
            return (year, month)

    # Pattern 2: Numeric month/year patterns
    # e.g., "2026-03", "2026/3", "3/2026", "03-2026"

    # YYYY-MM or YYYY/MM or YYYY-M or YYYY/M
    pattern_yyyy_first = re.search(r'(\d{4})[/\-](\d{1,2})', text)
    if pattern_yyyy_first:
        year = int(pattern_yyyy_first.group(1))
        month = int(pattern_yyyy_first.group(2))
        if 2000 <= year <= 2100 and 1 <= month <= 12:
            return (year, month)

    # M/YYYY or MM/YYYY or M-YYYY or MM-YYYY
    pattern_month_first = re.search(r'(\d{1,2})[/\-](\d{4})', text)
    if pattern_month_first:
        month = int(pattern_month_first.group(1))
        year = int(pattern_month_first.group(2))
        if 2000 <= year <= 2100 and 1 <= month <= 12:
            return (year, month)

    # Pattern 3: Fallback to any 4-digit year + adjacent 1-2 digit month
    # (loose match, used only if above patterns fail)
    year_match = re.search(r'\b(20\d{2})\b', text)
    month_match = re.search(r'\b(\d{1,2})\b', text)
    if year_match and month_match:
        year = int(year_match.group(1))
        month = int(month_match.group(1))
        if 2000 <= year <= 2100 and 1 <= month <= 12:
            # Only return if order makes sense (year before month in text)
            if text.find(year_match.group(0)) < text.find(month_match.group(0)):
                return (year, month)

    # Could not parse
    return (None, None)


def parse_month_only(text: str, locale: str = "en") -> Optional[int]:
    """
    Parse a month value from text when the year is omitted (e.g., "4月", "March").

    This is used as a contextual fallback by calendar drivers that can infer the year
    from the target date and bounded navigation constraints.
    """
    if not text:
        return None

    text = normalize_month_text(text)
    is_japanese = "ja" in str(locale or "").lower()

    if is_japanese:
        m = re.search(r'(\d{1,2})\s*月', text)
        if m:
            month = int(m.group(1))
            if 1 <= month <= 12:
                return month
        return None

    month_names = (
        r"(?:january|february|march|april|may|june|"
        r"july|august|september|october|november|december)"
    )
    month_name_match = re.search(rf"\b({month_names})\b", text, re.IGNORECASE)
    if month_name_match:
        return english_month_name_to_number(month_name_match.group(1))

    return None


def infer_year_for_visible_month(
    *,
    visible_month: int,
    target_year: int,
    target_month: int,
    max_nav_steps: int = 8,
) -> Optional[int]:
    """
    Infer year for a visible month-only header using bounded calendar navigation constraints.

    Chooses the candidate year in {target_year-1, target_year, target_year+1} whose
    month delta to the target is minimal, and only accepts if the delta is within a
    small bounded range derived from max_nav_steps.
    """
    if not (1 <= int(visible_month) <= 12):
        return None

    best_year = None
    best_abs_delta = None
    allowed_delta = max(1, int(max_nav_steps)) + 1  # dual-pane calendars often show adjacent month

    for candidate_year in (target_year - 1, target_year, target_year + 1):
        delta = month_delta(candidate_year, int(visible_month), target_year, target_month)
        abs_delta = abs(delta)
        if best_abs_delta is None or abs_delta < best_abs_delta:
            best_abs_delta = abs_delta
            best_year = candidate_year

    if best_abs_delta is None or best_abs_delta > allowed_delta:
        return None
    return best_year


def month_delta(from_year: int, from_month: int, to_year: int, to_month: int) -> int:
    """
    Calculate month delta between two month/year pairs.

    Positive delta means target is in the future.
    Negative delta means target is in the past.

    Args:
        from_year, from_month: Starting date
        to_year, to_month: Target date

    Returns:
        Month difference (e.g., 2 means target is 2 months ahead)
    """
    return (to_year - from_year) * 12 + (to_month - from_month)
