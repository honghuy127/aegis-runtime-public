"""
Phase B: Input validation and date parsing for gf_set_date.

Extracted from gf_set_date() to enable:
- Independent validation testing
- Clear separation of concerns (validation → opening → navigation → selection)
- Reusable input validation functions
- Easier error handling and evidence tracking

Pattern:
- validate_role(role) -> (ok: bool, reason: str, role_key: Optional[str])
- validate_date_string(date) -> (ok: bool, reason: str, date_str: Optional[str])
- parse_date(date_str) -> (ok: bool, reason: str, date_parts: Optional[Tuple[int, int, int]])
- validate_gf_set_date_inputs(role, date) -> (ok: bool, result_dict_or_none, parsed_data_or_none)
"""

from datetime import datetime
from typing import Tuple, Optional, Dict, Any


def validate_role(role: Any) -> Tuple[bool, str, Optional[str]]:
    """Validate that role is 'depart' or 'return'.

    Args:
        role: Input role value (any type)

    Returns:
        (ok, reason, role_key) tuple:
        - ok: True if role is valid
        - reason: "role_valid" or "unsupported_role"
        - role_key: Normalized "depart" or "return", or None if invalid
    """
    role_key = str(role or "").strip().lower()

    if role_key not in {"depart", "return"}:
        return (False, "unsupported_role", None)

    return (True, "role_valid", role_key)


def validate_date_string(date: Any) -> Tuple[bool, str, Optional[str]]:
    """Validate that date is a non-empty string.

    Args:
        date: Input date value (any type)

    Returns:
        (ok, reason, date_str) tuple:
        - ok: True if date is non-empty string
        - reason: "date_string_valid" or "empty_value"
        - date_str: Normalized date string, or None if invalid
    """
    target_date = str(date or "").strip()

    if not target_date:
        return (False, "empty_value", None)

    return (True, "date_string_valid", target_date)


def parse_date(date_str: str) -> Tuple[bool, str, Optional[Tuple[int, int, int]]]:
    """Parse date string in YYYY-MM-DD format.

    Args:
        date_str: Date string in YYYY-MM-DD format

    Returns:
        (ok, reason, date_parts) tuple:
        - ok: True if parsing succeeds
        - reason: "date_parsed" or "invalid_date_format"
        - date_parts: (year, month, day) tuple, or None if invalid
    """
    try:
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        target_year = date_obj.year
        target_month = date_obj.month
        target_day = date_obj.day
        return (True, "date_parsed", (target_year, target_month, target_day))
    except Exception as exc:
        return (False, "invalid_date_format", None)


def validate_gf_set_date_inputs(
    role: Any,
    date: Any,
) -> Tuple[bool, Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """
    Comprehensive input validation for gf_set_date.

    Validates role, date string, and date format in sequence.
    Returns either a failure result dict (for early return) or parsed data dict.

    Args:
        role: Input role ('depart' or 'return')
        date: Input date string (YYYY-MM-DD format)

    Returns:
        (ok, result_or_none, parsed_or_none) tuple:
        - ok: True if all validations pass
        - result_or_none: Failure result dict (for returning to caller) if ok=False
        - parsed_or_none: Parsed data dict with role_key, target_date, year, month, day if ok=True

    Failure result dict format:
        {
            "ok": False,
            "reason": "unsupported_role" | "empty_value" | "invalid_date_format",
            "evidence": {...},
            "selector_used": "",
            "action_budget_used": 0,
        }

    Parsed data dict format:
        {
            "role_key": str,
            "target_date": str,
            "target_year": int,
            "target_month": int,
            "target_day": int,
        }
    """
    # Step 1: Validate role
    role_ok, role_reason, role_key = validate_role(role)
    if not role_ok:
        return (
            False,
            {
                "ok": False,
                "reason": role_reason,
                "evidence": {"role": str(role or "").strip().lower()},
                "selector_used": "",
                "action_budget_used": 0,
            },
            None,
        )

    # Step 2: Validate date string
    date_ok, date_str_reason, target_date = validate_date_string(date)
    if not date_ok:
        return (
            False,
            {
                "ok": False,
                "reason": date_str_reason,
                "evidence": {},
                "selector_used": "",
                "action_budget_used": 0,
            },
            None,
        )

    # Step 3: Parse date
    parse_ok, parse_reason, date_parts = parse_date(target_date)
    if not parse_ok:
        return (
            False,
            {
                "ok": False,
                "reason": parse_reason,
                "evidence": {"target_date": target_date, "error": "Invalid YYYY-MM-DD format"},
                "selector_used": "",
                "action_budget_used": 0,
            },
            None,
        )

    # All validations passed
    target_year, target_month, target_day = date_parts
    return (
        True,
        None,
        {
            "role_key": role_key,
            "target_date": target_date,
            "target_year": target_year,
            "target_month": target_month,
            "target_day": target_day,
        },
    )


if __name__ == "__main__":
    # Quick test
    import sys

    # Test valid inputs
    ok, result, parsed = validate_gf_set_date_inputs("depart", "2026-03-15")
    print(f"Valid: ok={ok}, parsed={parsed}")

    # Test invalid role
    ok, result, parsed = validate_gf_set_date_inputs("invalid", "2026-03-15")
    print(f"Invalid role: ok={ok}, result={result}")

    # Test empty date
    ok, result, parsed = validate_gf_set_date_inputs("depart", "")
    print(f"Empty date: ok={ok}, result={result}")

    # Test invalid date format
    ok, result, parsed = validate_gf_set_date_inputs("depart", "03-15-2026")
    print(f"Invalid format: ok={ok}, result={result}")
