"""Failure reason codes and metadata for scenario execution.

This module defines the MINIMAL, STABLE registry of failure reason codes that can be
emitted by scenario steps (gf_set_date, google_fill_and_commit_location, etc.).

Each reason code is associated with:
- code: Canonical machine-readable identifier
- summary: One-line description
- emitter: Stable "module:function" locator
- required_evidence: Namespaced evidence keys (ui.*, verify.*, budget.*, etc.)
- kb_links: Links to diagnostic docs (docs/kb/...)
- retry_hint: Whether safe to retry
- severity: warning/error/critical

Operational guidance (likely causes, next actions) lives in:
  - docs/kb/20_decision_system/triage_runbook.md (diagnostic decision trees)
  - docs/kb/10_runtime_contracts/evidence.md (evidence field reference)

Do not add likely_causes or next_actions to this file.

DOC: See docs/kb/20_decision_system/triage_runbook.md for troubleshooting guidance.

Usage:
    from core.scenario.reasons import REASON_REGISTRY, is_valid_reason_code

    if is_valid_reason_code("calendar_not_open"):
        meta = REASON_REGISTRY["calendar_not_open"]
        print(meta.summary)
"""

from dataclasses import dataclass
from typing import Dict, List, Literal, Optional


@dataclass(frozen=True)
class ReasonMeta:
    """Minimal metadata for a failure reason code (machine-readable, stable)."""

    code: str
    """Canonical reason code (e.g., 'calendar_not_open')."""

    summary: str
    """One-line human-readable summary (<=120 chars)."""

    emitter: str
    """Stable emitter locator: 'module.path:function' (e.g., 'core.scenario.google_flights:gf_set_date')."""

    required_evidence: List[str]
    """Namespaced evidence keys expected in StepResult.evidence (e.g., ['ui.selector_attempts', 'verify.root_detected'])."""

    kb_links: List[str]
    """Paths to KB docs under docs/kb/ (e.g., ['docs/kb/20_decision_system/triage_runbook.md#calendar_not_open', 'docs/kb/30_patterns/date_picker.md'])."""

    retry_hint: Literal["no_retry", "safe_retry", "retry_after_wait"] = "no_retry"
    """Whether safe to retry: no_retry (logic error), safe_retry (transient), retry_after_wait (timing)."""

    severity: Literal["warning", "error", "critical"] = "error"
    """Severity: warning (recoverable), error (logic failure), critical (resource exhaustion)."""


# Date Picker Failure Reasons

_CALENDAR_DIALOG_NOT_FOUND = ReasonMeta(
    code="calendar_dialog_not_found",
    summary="Date field click succeeded but calendar dialog never appeared.",
    emitter="core.scenario.google_flights:gf_set_date",
    required_evidence=["ui.selector_attempts", "ui.root_detected", "ui.opened_count"],
    kb_links=["docs/kb/20_decision_system/triage_runbook.md#calendar_dialog_not_found", "docs/kb/30_patterns/date_picker.md"],
    retry_hint="no_retry",
    severity="error",
)

_MONTH_NAV_EXHAUSTED = ReasonMeta(
    code="month_nav_exhausted",
    summary="Calendar opened but target month unreachable within 8 navigation steps.",
    emitter="core.scenario.google_flights:gf_set_date",
    required_evidence=["calendar.nav_steps", "calendar.current_month", "calendar.target_month"],
    kb_links=["docs/kb/20_decision_system/triage_runbook.md#month_nav_exhausted", "docs/kb/30_patterns/date_picker.md#month-navigation"],
    retry_hint="no_retry",
    severity="error",
)

_CALENDAR_DAY_NOT_FOUND = ReasonMeta(
    code="calendar_day_not_found",
    summary="Target day element not found in calendar after month navigation.",
    emitter="core.scenario.google_flights:gf_set_date",
    required_evidence=["ui.day_selectors_tried", "calendar.month_parsed", "calendar.day_parsed"],
    kb_links=["docs/kb/20_decision_system/triage_runbook.md#calendar_day_not_found", "docs/kb/30_patterns/date_picker.md#day-selection"],
    retry_hint="no_retry",
    severity="error",
)

_DATE_PICKER_UNVERIFIED = ReasonMeta(
    code="date_picker_unverified",
    summary="Calendar interactions completed but final date field value unverified.",
    emitter="core.scenario.google_flights:gf_set_date",
    required_evidence=["verify.committed", "verify.value", "ui.selectors_tried"],
    kb_links=["docs/kb/20_decision_system/triage_runbook.md#date_picker_unverified", "docs/kb/30_patterns/date_picker.md#verification"],
    retry_hint="safe_retry",
    severity="warning",
)

# Location/Combobox Failure Reasons

_IATA_MISMATCH = ReasonMeta(
    code="iata_mismatch",
    summary="IATA code committed but final field value doesn't contain matching IATA.",
    emitter="core.scenario.google_flights:google_fill_and_commit_location",
    required_evidence=["suggest.text", "suggest.rank", "verify.text", "verify.iata_code"],
    kb_links=["docs/kb/20_decision_system/triage_runbook.md#iata_mismatch", "docs/kb/30_patterns/combobox_commit.md#iata-matching"],
    retry_hint="no_retry",
    severity="error",
)

_SUGGESTION_NOT_FOUND = ReasonMeta(
    code="suggestion_not_found",
    summary="Typed airport code but no suggestion with matching IATA appeared.",
    emitter="core.scenario.google_flights:google_fill_and_commit_location",
    required_evidence=["input.typed_value", "suggest.seen", "suggest.count"],
    kb_links=["docs/kb/20_decision_system/triage_runbook.md#suggestion_not_found", "docs/kb/30_patterns/combobox_commit.md#suggestions"],
    retry_hint="no_retry",
    severity="error",
)

# Budget & Timeout Failure Reasons

_BUDGET_HIT = ReasonMeta(
    code="budget_hit",
    summary="Action budget exhausted (too many clicks, fills, waits attempted).",
    emitter="core.scenario.scenario_runner:execute_action_batch",
    required_evidence=["budget.action_count", "budget.max_actions", "ui.selector_attempts"],
    kb_links=["docs/kb/20_decision_system/triage_runbook.md#budget_hit", "docs/kb/10_runtime_contracts/budgets_timeouts.md#action-budget"],
    retry_hint="no_retry",
    severity="critical",
)

_DEADLINE_HIT = ReasonMeta(
    code="deadline_hit",
    summary="Step execution deadline exceeded (timeout in a specific action).",
    emitter="core.browser:wait_for_selector",
    required_evidence=["time.deadline_ms", "time.elapsed_ms"],
    kb_links=["docs/kb/20_decision_system/triage_runbook.md#deadline_hit", "docs/kb/10_runtime_contracts/budgets_timeouts.md#step-timeout"],
    retry_hint="retry_after_wait",
    severity="critical",
)

_WALL_CLOCK_TIMEOUT = ReasonMeta(
    code="wall_clock_timeout",
    summary="Wall-clock scenario timeout exceeded (infrastructure-level failure).",
    emitter="core.scenario.scenario_runner:run",
    required_evidence=["time.remaining_ms", "time.deadline"],
    kb_links=["docs/kb/20_decision_system/triage_runbook.md#wall_clock_timeout", "docs/kb/10_runtime_contracts/budgets_timeouts.md#wall-clock-timeout"],
    retry_hint="no_retry",
    severity="critical",
)

_SELECTOR_NOT_FOUND = ReasonMeta(
    code="selector_not_found",
    summary="Selector did not match any elements in DOM.",
    emitter="core.browser:wait_for_selector",
    required_evidence=["ui.selector_tried", "ui.attempts", "time.timeout_ms"],
    kb_links=["docs/kb/20_decision_system/triage_runbook.md#selector_not_found", "docs/kb/30_patterns/selectors.md"],
    retry_hint="no_retry",
    severity="error",
)

_GOOGLE_DEEPLINK_PAGE_STATE_RECOVERY_FAILED = ReasonMeta(
    code="deeplink_recovery_activation_unverified",
    summary="Google deeplink page-state recovery could not activate route form from irrelevant page.",
    emitter="core.scenario.scenario_runner:run",
    required_evidence=["ui.page_class", "verify.route_bind_reason", "ui.recovery_action"],
    kb_links=[
        "docs/kb/20_decision_system/triage_runbook.md",
        "docs/kb/20_decision_system/runtime_playbook.md",
        "docs/kb/10_runtime_contracts/evidence.md",
    ],
    retry_hint="no_retry",
    severity="warning",
)

_GOOGLE_DEEPLINK_PAGE_STATE_RECOVERY_UNREADY = ReasonMeta(
    code="deeplink_recovery_rebind_unverified",
    summary="Google deeplink page-state recovery activated route form but quick rebind stayed unready.",
    emitter="core.scenario.scenario_runner:run",
    required_evidence=["ui.page_class", "verify.rebind_reason", "verify.recovery_stage"],
    kb_links=[
        "docs/kb/20_decision_system/triage_runbook.md",
        "docs/kb/20_decision_system/runtime_playbook.md",
        "docs/kb/10_runtime_contracts/evidence.md",
    ],
    retry_hint="no_retry",
    severity="warning",
)

_GOOGLE_ROUTE_CORE_UNVERIFIED_BEFORE_DATE_FILL = ReasonMeta(
    code="route_core_before_date_fill_unverified",
    summary="Google recovery blocked date fill because origin/destination route core was not rebound.",
    emitter="core.scenario.scenario_runner:execute_plan",
    required_evidence=[
        "verify.route_core_probe_reason",
        "verify.route_core_observed_origin",
        "verify.route_core_observed_dest",
    ],
    kb_links=[
        "docs/kb/20_decision_system/triage_runbook.md",
        "docs/kb/20_decision_system/runtime_playbook.md",
        "docs/kb/10_runtime_contracts/evidence.md",
    ],
    retry_hint="no_retry",
    severity="warning",
)

# Registry mapping canonical code -> ReasonMeta

REASON_REGISTRY: Dict[str, ReasonMeta] = {
    reason.code: reason
    for reason in [
        _CALENDAR_DIALOG_NOT_FOUND,
        _MONTH_NAV_EXHAUSTED,
        _CALENDAR_DAY_NOT_FOUND,
        _DATE_PICKER_UNVERIFIED,
        _IATA_MISMATCH,
        _SUGGESTION_NOT_FOUND,
        _BUDGET_HIT,
        _DEADLINE_HIT,
        _WALL_CLOCK_TIMEOUT,
        _SELECTOR_NOT_FOUND,
        _GOOGLE_DEEPLINK_PAGE_STATE_RECOVERY_FAILED,
        _GOOGLE_DEEPLINK_PAGE_STATE_RECOVERY_UNREADY,
        _GOOGLE_ROUTE_CORE_UNVERIFIED_BEFORE_DATE_FILL,
    ]
}

# Aliases for backward compatibility: legacy_code -> canonical_code

REASON_ALIASES: Dict[str, str] = {
    "calendar_not_open": "calendar_dialog_not_found",
    "day_not_found": "calendar_day_not_found",
    "verify_mismatch": "date_picker_unverified",
    "month_header_not_found": "month_nav_exhausted",
    "no_suggestion_match": "suggestion_not_found",
    "timeout_error": "wall_clock_timeout",
    "route_core_unverified_before_date_fill": "route_core_before_date_fill_unverified",
    "deeplink_page_state_recovery_failed_non_flight_scope_irrelevant_page": "deeplink_recovery_activation_unverified",
    "deeplink_page_state_recovery_unready_non_flight_scope_irrelevant_page": "deeplink_recovery_rebind_unverified",
}



def is_valid_reason_code(code: str) -> bool:
    """Check if a reason code is valid and registered (canonical or alias).

    Args:
        code: Reason code string (e.g., "calendar_not_open" or alias "day_not_found").

    Returns:
        True if code is in REASON_REGISTRY or REASON_ALIASES, False otherwise.
    """
    code_str = str(code).strip() if code else ""
    return code_str in REASON_REGISTRY or code_str in REASON_ALIASES


def get_reason(code: str) -> Optional[ReasonMeta]:
    """Get metadata for a reason code (canonical or alias)."""
    canonical = normalize_reason(code)
    if canonical in REASON_REGISTRY:
        return REASON_REGISTRY[canonical]
    return None


def validate_reason_code(code: str, raise_on_invalid: bool = False) -> bool:
    """Validate a reason code and optionally raise if invalid."""
    if is_valid_reason_code(code):
        return True
    if raise_on_invalid:
        raise ValueError(f"Invalid reason code: {code}")
    return False


def normalize_reason(code: str) -> str:
    """Normalize a reason code to canonical form.

    Maps aliases to canonical codes, unknown codes to "unknown".
    Strips whitespace and lowercases.

    Args:
        code: Reason code string (may be alias or unknown).

    Returns:
        Canonical code if known/aliased, else "unknown".
    """
    code_str = str(code or "").strip().lower()

    if code_str in REASON_REGISTRY:
        return code_str

    if code_str in REASON_ALIASES:
        return REASON_ALIASES[code_str]

    return "unknown"


def get_reason_meta(code: str, default: Optional[ReasonMeta] = None) -> Optional[ReasonMeta]:
    """Get metadata for a reason code with safe fallback.

    Args:
        code: Reason code string (canonical or alias).
        default: Fallback ReasonMeta if code not found.

    Returns:
        ReasonMeta instance, or default if not found.
    """
    canonical = normalize_reason(code)
    if canonical in REASON_REGISTRY:
        return REASON_REGISTRY[canonical]
    return default


# Compatibility alias for internal usage (not part of public API surface)
FAILURE_REASONS = REASON_REGISTRY


def is_diagnostic_code(code: Optional[str]) -> bool:
    """Check if a code is a diagnostic signal (not a failure reason).

    Diagnostic codes are prefixed with 'diag.' and are intended for internal
    instrumentation only. They MUST NOT be used as failure reason codes.

    Args:
        code: Code string to check.

    Returns:
        True if code starts with 'diag.', False otherwise.
    """
    code_str = str(code or "").strip()
    return code_str.startswith("diag.")


def assert_valid_failure_reason(code: Optional[str]) -> None:
    """Validate that a code is a canonical failure reason (not diagnostic).

    Raises ValueError if:
    - code starts with 'diag.' (diagnostic signal, not a failure reason)
    - code is not in the registry or aliases (unknown reason code)
    - code is None/empty (must be explicit)

    Args:
        code: Reason code to validate.

    Raises:
        ValueError: If code is invalid, diagnostic, or unknown.
    """
    code_str = str(code or "").strip()

    if not code_str:
        raise ValueError("Failure reason code cannot be empty or None")

    if is_diagnostic_code(code_str):
        raise ValueError(
            f"Diagnostic code '{code_str}' cannot be used as a failure reason. "
            f"Diagnostic signals must be stored in evidence['diag.*'] only, not in the reason field."
        )

    canonical = normalize_reason(code_str)
    if canonical == "unknown":
        raise ValueError(
            f"Unknown failure reason code: '{code_str}'. "
            f"Reason must be a canonical code from REASON_REGISTRY or an alias. "
            f"Valid codes: {sorted(list(REASON_REGISTRY.keys())[:5])}... "
            f"See core/scenario/reasons.py for complete list."
        )


# Export public API
__all__ = [
    "REASON_REGISTRY",
    "REASON_ALIASES",
    "is_valid_reason_code",
    "normalize_reason",
    "get_reason_meta",
    "is_diagnostic_code",
    "assert_valid_failure_reason",
]
