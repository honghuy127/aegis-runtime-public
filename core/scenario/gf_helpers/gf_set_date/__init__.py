"""gf_set_date modular decomposition.

Extracted from: core/scenario/google_flights.py gf_set_date() function

This package decomposes the 1133-line gf_set_date() function into focused,
testable modules organized by phase:

- validation_phase: Input validation and parsing (Phase B)
- timing_budget: Timeout and budget management (Phase A)
- opener_phase: Calendar opener selection (Phase C)
- month_nav_phase: Month navigation (Phase D)
- day_select_phase: Day selection (Phase E)
- verification_phase: Date field verification (Phase F)
- fallback_typing_phase: Fallback typing when calendar fails (Phase G)

Each module eliminates closure dependencies and is independently testable.
"""

from core.scenario.gf_helpers.gf_set_date.timing_budget import BudgetedTimeoutManager
from core.scenario.gf_helpers.gf_set_date.validation_phase import (
    validate_role,
    validate_date_string,
    parse_date,
    validate_gf_set_date_inputs,
)
from core.scenario.gf_helpers.gf_set_date.opener_phase import select_and_click_opener
from core.scenario.gf_helpers.gf_set_date.month_nav_phase import navigate_to_target_month
from core.scenario.gf_helpers.gf_set_date.day_select_phase import select_calendar_day
from core.scenario.gf_helpers.gf_set_date.verification_phase import verify_date_field
from core.scenario.gf_helpers.gf_set_date.fallback_typing_phase import attempt_typing_fallback

__all__ = [
    "BudgetedTimeoutManager",
    "validate_role",
    "validate_date_string",
    "parse_date",
    "validate_gf_set_date_inputs",
    "select_and_click_opener",
    "navigate_to_target_month",
    "select_calendar_day",
    "verify_date_field",
    "attempt_typing_fallback",
]
