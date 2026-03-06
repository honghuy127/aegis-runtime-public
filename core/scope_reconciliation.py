"""Scope reconciliation helpers for dual-layer VLM/heuristic conflict resolution (Phase 3)."""

from typing import Any, Dict, Optional


class ScopeOverrideTracker:
    """Track VLM scope override attempts to prevent loops and cascading overrides."""

    def __init__(self, max_overrides: int = 2, context_key: str = "_scope_override_count"):
        """
        Initialize tracker.

        Args:
            max_overrides: Maximum number of VLM overrides allowed per scenario.
            context_key: Context dictionary key for storing override count.
        """
        self.max_overrides = max_overrides
        self.context_key = context_key

    def get_override_count(self, ctx: Dict[str, Any]) -> int:
        """Get current override count from context."""
        if not isinstance(ctx, dict):
            return 0
        return int(ctx.get(self.context_key, 0) or 0)

    def increment_override_count(self, ctx: Dict[str, Any]) -> int:
        """Increment override count and return new count."""
        if not isinstance(ctx, dict):
            return 1
        count = self.get_override_count(ctx)
        new_count = count + 1
        ctx[self.context_key] = new_count
        return new_count

    def can_apply_override(self, ctx: Dict[str, Any]) -> bool:
        """Return True if more VLM overrides are permitted."""
        count = self.get_override_count(ctx)
        return count < self.max_overrides

    def should_fail_without_override(self, ctx: Dict[str, Any]) -> bool:
        """Return True if scope conflict would fail without override and none remaining."""
        count = self.get_override_count(ctx)
        return count >= self.max_overrides


def evaluate_vlm_scope_override(
    vlm_signal: Optional[Dict[str, Any]],
    heuristic_verdict: str,
    context: Dict[str, Any],
    max_overrides: int = 2,
) -> tuple[bool, str]:
    """
    Determine if VLM signal should override conflicting heuristic verdict.

    Implements dual-layer reconciliation: when vision (VLM) and heuristics (LLM)
    disagree on scope, VLM's confidence can override heuristic rejection up to
    a limit to prevent cascade failures.

    Args:
        vlm_signal: VLM scope probe result (page_class, trip_product, etc).
        heuristic_verdict: Current heuristic/LLM scope determination (fail/pass).
        context: Scenario context with override tracking.
        max_overrides: Maximum number of overrides allowed (default 2).

    Returns:
        Tuple of (should_override, reason_str) where:
        - should_override: True to use VLM signal instead of heuristic
        - reason_str: Explanation for decision (e.g., "vlm_affirms_flight", "override_limit_reached")
    """
    tracker = ScopeOverrideTracker(max_overrides=max_overrides)

    if not isinstance(vlm_signal, dict):
        return False, "vlm_unavailable"

    vlm_page_class = str(vlm_signal.get("page_class", "") or "").strip().lower()
    vlm_trip_product = str(vlm_signal.get("trip_product", "") or "").strip().lower()

    # VLM strongly affirms flight scope
    if vlm_page_class == "flight_only" or vlm_trip_product == "flight_only":
        if heuristic_verdict == "fail":
            if tracker.can_apply_override(context):
                new_count = tracker.increment_override_count(context)
                return True, f"vlm_affirms_flight_override_{new_count}"
            else:
                return False, "override_limit_reached"
        return False, "vlm_affirms_flight_no_override_needed"

    # VLM is inconclusive but heuristic rejected
    if heuristic_verdict == "fail":
        vlm_has_signal = bool(
            vlm_page_class not in {"unknown", ""}
            or vlm_trip_product not in {"unknown", ""}
        )
        if vlm_has_signal and tracker.can_apply_override(context):
            new_count = tracker.increment_override_count(context)
            return True, f"vlm_provides_signal_override_{new_count}"
        if not tracker.can_apply_override(context):
            return False, "override_limit_reached"

    return False, "no_override_needed"


def evaluate_irrelevant_page_downgrade(
    vlm_probe: Optional[Dict[str, Any]],
    heuristic_reason: str,
    context: Dict[str, Any],
    max_overrides: int = 2,
) -> Dict[str, Any]:
    """
    Evaluate whether to downgrade irrelevant_page block when VLM affirms flights_results.

    Implements Phase 3.1 VLM downgrade: when heuristic blocks as "irrelevant_page"
    but VLM says "flights_results" with medium+ confidence, downgrade the block
    to allow processing to continue.

    Args:
        vlm_probe: VLM scope probe result with page_kind and confidence.
        heuristic_reason: Heuristic block reason (should be scope_guard_non_flight_irrelevant_page).
        context: Scenario context with override tracking.
        max_overrides: Maximum number of overrides allowed (default 2).

    Returns:
        Dictionary with keys:
        - should_downgrade: bool, True if block should be downgraded to warn/pass
        - reason: str, explanation
        - override_count: int, current override count if downgraded
        - override_applied: bool, True if downgrade applied
    """
    tracker = ScopeOverrideTracker(max_overrides=max_overrides)

    # Check if heuristic is blocking on irrelevant_page
    if not isinstance(heuristic_reason, str) or "irrelevant_page" not in heuristic_reason.lower():
        return {
            "should_downgrade": False,
            "reason": "heuristic_not_irrelevant_page",
            "override_count": 0,
            "override_applied": False,
        }

    # Check VLM signal
    if not isinstance(vlm_probe, dict):
        return {
            "should_downgrade": False,
            "reason": "vlm_probe_unavailable",
            "override_count": 0,
            "override_applied": False,
        }

    page_kind = str(vlm_probe.get("page_kind", "") or "").strip().lower()
    confidence = str(vlm_probe.get("confidence", "low") or "low").strip().lower()

    # VLM must say flights_results (or flight-related page_kind)
    # and confidence must be medium or higher
    if page_kind not in {"flights_results", "flight_only"}:
        return {
            "should_downgrade": False,
            "reason": f"vlm_page_kind_not_flight_{page_kind}",
            "override_count": tracker.get_override_count(context),
            "override_applied": False,
        }

    if confidence not in {"medium", "high"}:
        return {
            "should_downgrade": False,
            "reason": f"vlm_confidence_too_low_{confidence}",
            "override_count": tracker.get_override_count(context),
            "override_applied": False,
        }

    # Check override limit
    if not tracker.can_apply_override(context):
        return {
            "should_downgrade": False,
            "reason": "override_limit_reached",
            "override_count": tracker.get_override_count(context),
            "override_applied": False,
        }

    # Apply downgrade
    new_count = tracker.increment_override_count(context)
    return {
        "should_downgrade": True,
        "reason": f"vlm_flights_results_downgrade_irrelevant_page_{new_count}",
        "override_count": new_count,
        "override_applied": True,
    }


def reconcile_scope_layers(
    method1_verdict: str,  # e.g., "fail" from LLM check
    method1_basis: str,  # e.g., "heuristic"
    method2_signal: Optional[Dict[str, Any]],  # e.g., VLM probe result
    method2_basis: str,  # e.g., "vlm"
    context: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Reconcile two-layer scope verdicts (heuristic + VLM).

    When first layer (heuristic LLM) says "fail" but second layer (VLM vision)
    provides affirmative signal, this evaluates whether to reconcile
    the conflict by trusting the vision signal.

    Args:
        method1_verdict: First layer verdict ("pass"/"fail"/"skip").
        method1_basis: First layer basis/method name.
        method2_signal: Second layer signal data.
        method2_basis: Second layer basis/method name.
        context: Scenario context for override tracking.

    Returns:
        Dictionary with keys:
        - resolved: bool, True if conflict was reconciled
        - final_verdict: str, the reconciled verdict ("pass"/"fail")
        - reason: str, explanation
        - override_applied: bool, True if override was used
        - override_count: int, current override count
    """
    tracker = ScopeOverrideTracker()

    # No conflict if both agree
    if method1_verdict == "pass":
        return {
            "resolved": True,
            "final_verdict": "pass",
            "reason": f"{method1_basis}_passed",
            "override_applied": False,
            "override_count": tracker.get_override_count(context),
        }

    # Evaluate VLM override on heuristic rejection
    should_override, override_reason = evaluate_vlm_scope_override(
        method2_signal, method1_verdict, context
    )

    if should_override:
        return {
            "resolved": True,
            "final_verdict": "pass",
            "reason": override_reason,
            "override_applied": True,
            "override_count": tracker.get_override_count(context),
        }

    # Conflict unresolved
    return {
        "resolved": False,
        "final_verdict": "fail",
        "reason": "scope_conflict_unresolved",
        "override_applied": False,
        "override_count": tracker.get_override_count(context),
    }
