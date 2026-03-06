"""Shared scenario helper types."""

from dataclasses import dataclass
from typing import Any, Dict, NotRequired, Optional, TypedDict


class GoogleFillCommitResult(TypedDict):
    """Result payload for Google route field fill+commit attempts."""

    ok: bool
    selector_used: str
    activation_selector_used: str
    textbox_selector_used: str
    committed: bool
    suggestion_used: bool
    reason: str
    field: NotRequired[str]
    typed_value: NotRequired[str]
    suggestion_text: NotRequired[Optional[str]]
    enter_used: NotRequired[bool]
    commit_method: NotRequired[str]
    final_visible_text: NotRequired[Optional[str]]
    evidence_errors: NotRequired[list]


class GoogleForceBindPolicyDecision(TypedDict):
    """Policy decision for Google force-bind follow-up selection."""

    use: bool
    reason: str
    force_flights_tab: bool


class GoogleRouteMismatchRefillResult(TypedDict):
    """Result for bounded destination refill pass."""

    route_verify_meta: Dict[str, Any]
    refill_attempts: int
    refill_meta: Optional[Dict[str, Any]]

@dataclass
class ActionBudget:
    """Budget tracker for per-step action execution.

    DOC: See docs/kb/10_runtime_contracts/budgets_timeouts.md for complete contract.

    Prevents selector spam and budget exhaustion by counting and limiting:
    - click operations
    - fill operations
    - wait operations

    Each operation consumes 1 action token.
    """
    max_actions: int
    remaining: int = None  # Auto-initialize from max_actions if None

    def __post_init__(self):
        if self.remaining is None:
            self.remaining = self.max_actions

    def consume(self, count: int = 1) -> bool:
        """Consume action tokens. Return True if budget allows, False if exhausted.

        Args:
            count: Number of action tokens to consume (default 1).

        Returns:
            True if tokens available and consumed. False if budget exhausted.
        """
        if self.remaining < count:
            return False
        self.remaining -= count
        return True

    def is_exhausted(self) -> bool:
        """Return True if budget is exhausted."""
        return self.remaining <= 0

    def reset(self, max_actions: Optional[int] = None) -> None:
        """Reset budget to initial state or new amount."""
        if max_actions is not None:
            self.max_actions = max_actions
        self.remaining = self.max_actions


@dataclass
class StepResult:
    """Structured result for step execution with status and evidence.

    Enables self-healing and rich failure diagnostics at the step level.
    """
    ok: bool
    reason: str  # e.g., "success", "calendar_not_open", "date_field_not_found", "budget_hit"
    evidence: Dict[str, Any] = None  # Rich diagnostic evidence (selectors used, before/after values, etc.)
    selector_used: str = ""
    action_budget_used: int = 0  # Number of actions (clicks/fills/waits) consumed

    def __post_init__(self):
        if self.evidence is None:
            self.evidence = {}

    @staticmethod
    def success(**kwargs) -> "StepResult":
        """Create a successful step result."""
        return StepResult(ok=True, reason="success", **kwargs)

    @staticmethod
    def failure(reason: str, **kwargs) -> "StepResult":
        """Create a failed step result with reason.

        GUARD: Validates that reason is a canonical failure code (not diagnostic).
        If reason is diagnostic (starts with 'diag.'), downgrades to a safe canonical
        code and stores original in evidence['diag.original_reason'].

        Args:
            reason: Canonical failure reason code (must be in REASON_REGISTRY).
            **kwargs: Additional fields (evidence, selector_used, action_budget_used, etc.)

        Returns:
            StepResult with ok=False and validated reason code.
        """
        from core.scenario.reasons import is_diagnostic_code, assert_valid_failure_reason, normalize_reason

        if "evidence" not in kwargs:
            kwargs["evidence"] = {}

        # Normalize reason code (whitespace, case)
        reason_str = str(reason or "").strip().lower()

        # Guard: Check if reason is a diagnostic code
        if is_diagnostic_code(reason_str):
            original_reason = reason
            # Downgrade to safe canonical code
            reason_str = "selector_not_found"  # Safe fallback reason
            # Preserve original diagnostic in evidence
            kwargs["evidence"]["diag.original_reason"] = original_reason
            import logging
            logging.getLogger(__name__).warning(
                f"Diagnostic code '{original_reason}' downgraded to '{reason_str}' in failure path. "
                f"Store diagnostic details in evidence['diag.*'] only."
            )
            return StepResult(ok=False, reason=reason_str, **kwargs)

        # Guard: Validate canonical reason code
        try:
            assert_valid_failure_reason(reason_str)
        except ValueError as e:
            # If validation fails, downgrade to safe fallback
            import logging
            logging.getLogger(__name__).error(
                f"Invalid reason code '{reason}': {e}. Downgrading to safe fallback."
            )
            kwargs["evidence"]["diag.original_reason"] = reason
            kwargs["evidence"]["diag.validation_error"] = str(e)
            return StepResult(ok=False, reason="selector_not_found", **kwargs)

        # Ensure reason is canonical form after normalization
        canonical = normalize_reason(reason_str)
        return StepResult(ok=False, reason=canonical, **kwargs)