"""Price parsing helpers that normalize raw text into numeric values."""

import re


def parse_price(text: str):
    """Extract numeric value and basic currency hint from free-form price text."""
    if not text:
        return None, None

    match = re.search(r"([\d,.]+)", text)
    if not match:
        return None, None

    value = float(match.group(1).replace(",", ""))
    currency = "JPY" if "¥" in text else None

    return value, currency


def extract_number(text: str):
    """Return only numeric price value for compatibility-focused call sites."""
    value, _ = parse_price(text)
    return value
