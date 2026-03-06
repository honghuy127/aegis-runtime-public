"""Adaptive escalation policy for debug mode when run is stuck in recovery loop.

The escalation policy detects "stuckness" signals during a run and recommends
escalating the thresholds_profile from "default" to "debug" for the next episode.

Signals:
  - Repeated reason codes (e.g., calendar_not_open, month_nav_exhausted)
  - Soft failure loops (multiple soft_fail in same turn)
  - Route fill mismatch loops
  - Low progress (ready=False persisting across turns)

Decision:
  - Idempotent: At most one escalation per run_id
  - Safe: Never escalates if already at debug profile
  - Explainable: Includes which rule triggered in reason field
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, Optional
import json
import re
import logging

logger = logging.getLogger(__name__)


@dataclass
class EscalationDecision:
    """Result of escalation policy evaluation."""
    should_escalate: bool
    current_profile: str
    next_profile: str
    reason: str  # Human-readable explanation
    key_counts: Dict[str, int]  # Evidence counts for decision (for logging/debugging)
    fired_rules: list  # Which rules triggered


def extract_signals_from_log(log_text: str) -> Dict[str, Any]:
    """Extract signals from run log text.

    Looks for:
      - Reason codes in lines like: "reason_code=..."
      - "soft_fail" for soft failure markers
      - "ready=False" for progress signals
      - Turn counts
      - Action budget hits

    Args:
        log_text: Full log from stdout/stderr or run output

    Returns:
        Dict with keys:
          - reason_code_counts: dict[str, int] - count of each reason
          - soft_fail_count: int
          - turn_count: int - max turn seen
          - ready_false_count: int
          - budget_hit_count: int
          - action_deadline_count: int
          - calendar_not_open_count: int
          - month_nav_exhausted_count: int
          - route_fill_mismatch_count: int
    """
    signals = {
        "reason_code_counts": {},
        "soft_fail_count": 0,
        "turn_count": 0,
        "ready_false_count": 0,
        "budget_hit_count": 0,
        "action_deadline_count": 0,
        "calendar_not_open_count": 0,
        "month_nav_exhausted_count": 0,
        "route_fill_mismatch_count": 0,
    }

    for line in log_text.split("\n"):
        # Extract reason_code=XXX pattern
        match = re.search(r"\breason_code=(\w+)", line)
        if match:
            reason = match.group(1)
            signals["reason_code_counts"][reason] = signals["reason_code_counts"].get(reason, 0) + 1

            # Track specific reasons
            if reason == "budget_hit":
                signals["budget_hit_count"] += 1
            elif reason == "action_deadline_exceeded_before_click":
                signals["action_deadline_count"] += 1
            elif reason == "calendar_not_open":
                signals["calendar_not_open_count"] += 1
            elif reason == "month_nav_exhausted":
                signals["month_nav_exhausted_count"] += 1
            elif reason == "route_fill_mismatch":
                signals["route_fill_mismatch_count"] += 1

        # Count soft failures
        if "soft_fail" in line.lower():
            signals["soft_fail_count"] += 1

        # Count ready=False patterns
        if "ready=False" in line or "ready: False" in line:
            signals["ready_false_count"] += 1

        # Track turn numbers for max turns
        match = re.search(r"\bturn[_:](\d+)", line)
        if match:
            turn = int(match.group(1))
            signals["turn_count"] = max(signals["turn_count"], turn)

    return signals


def decide_escalation(
    signals: Dict[str, Any],
    current_profile: str = "default",
    config: Optional[Dict[str, Any]] = None,
) -> EscalationDecision:
    """Decide whether to escalate thresholds_profile to debug.

    Escalates if stuckness is detected AND not already in debug profile.

    Args:
        signals: Dict from extract_signals_from_log or manually constructed
        current_profile: Current thresholds_profile (default: "default")
        config: Config dict with escalation thresholds. If None, uses defaults:
            {
                "escalation_reason_repeat_threshold": 2,
                "escalation_soft_fail_threshold": 3,
                "escalation_max_turns_without_ready": 2,
                "escalation_route_fill_mismatch_threshold": 2,
                "escalation_calendar_loop_detection": True,
            }

    Returns:
        EscalationDecision with should_escalate, reason, and evidence
    """
    # Use config or defaults
    if config is None:
        config = {}

    repeat_threshold = config.get("escalation_reason_repeat_threshold", 2)
    soft_fail_threshold = config.get("escalation_soft_fail_threshold", 3)
    max_turns_without_ready = config.get("escalation_max_turns_without_ready", 2)
    route_mismatch_threshold = config.get("escalation_route_fill_mismatch_threshold", 2)
    calendar_loop_detection = config.get("escalation_calendar_loop_detection", True)

    # Already at debug, no need to escalate again
    if current_profile == "debug":
        return EscalationDecision(
            should_escalate=False,
            current_profile=current_profile,
            next_profile=current_profile,
            reason="Already at debug profile (no escalation needed)",
            key_counts={},
            fired_rules=[],
        )

    fired_rules = []
    key_counts = {}

    reason_code_counts = signals.get("reason_code_counts", {})
    soft_fail_count = signals.get("soft_fail_count", 0)
    turn_count = signals.get("turn_count", 0)
    ready_false_count = signals.get("ready_false_count", 0)
    route_mismatch_count = signals.get("route_fill_mismatch_count", 0)
    calendar_not_open_count = signals.get("calendar_not_open_count", 0)
    month_nav_count = signals.get("month_nav_exhausted_count", 0)

    # Rule 1: Same reason code repeated >= threshold (e.g., month_nav_exhausted >= 2)
    for reason, count in reason_code_counts.items():
        key_counts[f"reason_{reason}"] = count
        if count >= repeat_threshold:
            fired_rules.append(f"reason_repeat({reason}={count} >= {repeat_threshold})")

    # Rule 2: Soft fails exceed threshold (suggests retry loop)
    key_counts["soft_fails"] = soft_fail_count
    if soft_fail_count >= soft_fail_threshold:
        fired_rules.append(f"soft_fail_loop(count={soft_fail_count} >= {soft_fail_threshold})")

    # Rule 3: Route fill mismatch repeated
    key_counts["route_fill_mismatch"] = route_mismatch_count
    if route_mismatch_count >= route_mismatch_threshold:
        fired_rules.append(f"route_mismatch_loop(count={route_mismatch_count} >= {route_mismatch_threshold})")

    # Rule 4: Calendar navigation loop (both not_open and nav_exhausted occur)
    if calendar_loop_detection and calendar_not_open_count > 0 and month_nav_count > 0:
        key_counts["calendar_not_open"] = calendar_not_open_count
        key_counts["month_nav_exhausted"] = month_nav_count
        fired_rules.append(f"calendar_loop(not_open={calendar_not_open_count}, nav_exhausted={month_nav_count})")

    # Rule 5: High turn count with low progress (ready=False persists)
    key_counts["turns"] = turn_count
    key_counts["ready_false"] = ready_false_count
    if turn_count >= 3 and ready_false_count >= max_turns_without_ready:
        fired_rules.append(f"low_progress(turns={turn_count}, ready_false={ready_false_count} >= {max_turns_without_ready})")

    should_escalate = len(fired_rules) > 0

    if should_escalate:
        reason_str = "Escalating to debug: " + " + ".join(fired_rules)
    else:
        reason_str = "No escalation needed (no stuckness detected)"

    return EscalationDecision(
        should_escalate=should_escalate,
        current_profile=current_profile,
        next_profile="debug" if should_escalate else current_profile,
        reason=reason_str,
        key_counts=key_counts,
        fired_rules=fired_rules,
    )


def write_escalation_artifact(
    run_id: str,
    decision: EscalationDecision,
    run_dir: Path
) -> Path:
    """Write escalation decision to storage/runs/<run_id>/escalation.json.

    Idempotent: If file already exists, returns existing path without overwriting.

    Args:
        run_id: Unique run identifier
        decision: EscalationDecision from decide_escalation()
        run_dir: Path to storage/runs/<run_id>/

    Returns:
        Path to written/existing escalation.json
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    escalation_path = run_dir / "escalation.json"

    # Idempotent: don't overwrite if already exists
    if escalation_path.exists():
        return escalation_path

    artifact = {
        "run_id": run_id,
        "from_profile": decision.current_profile,
        "to_profile": decision.next_profile,
        "should_escalate": decision.should_escalate,
        "reason": decision.reason,
        "fired_rules": decision.fired_rules,
        "key_counts": decision.key_counts,
    }

    escalation_path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")

    # Log as single INFO line for easy grep
    logger.info(
        f"escalation.decision run_id={run_id} "
        f"from_profile={decision.current_profile} "
        f"to_profile={decision.next_profile} "
        f"should_escalate={decision.should_escalate} "
        f"reason={decision.reason}"
    )

    return escalation_path


def load_escalation_artifact(run_dir: Path) -> Optional[Dict[str, Any]]:
    """Load escalation decision from run_dir/escalation.json if it exists.

    Args:
        run_dir: Path to storage/runs/<run_id>/

    Returns:
        Dict from escalation.json, or None if file doesn't exist
    """
    escalation_path = run_dir / "escalation.json"
    if not escalation_path.exists():
        return None

    return json.loads(escalation_path.read_text(encoding="utf-8"))
