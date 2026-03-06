"""Tier-0 Model Router for intelligent planner/coder model selection."""

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from llm.code_model import PLANNER_MODEL, CODER_MODEL

logger = logging.getLogger(__name__)


@dataclass
class FailureEvent:
    """Structured failure event for router decision-making."""
    event_type: str
    timestamp: float
    metadata: Dict[str, Any] = field(default_factory=dict)


class ModelRouter:
    """Per-scenario router that tracks failures and selects appropriate models."""

    def __init__(self):
        """Initialize empty event tracking for one scenario run."""
        self.events: List[FailureEvent] = []

    def record_event(self, event_type: str, **metadata) -> None:
        """Record a failure event with timestamp and metadata.

        Args:
            event_type: Event category (route_fill_mismatch, stuck_step, etc.)
            **metadata: Event-specific context (role, step_index, elapsed_ms, etc.)
        """
        event = FailureEvent(
            event_type=event_type,
            timestamp=time.monotonic(),
            metadata=metadata,
        )
        self.events.append(event)
        logger.debug(
            f"scenario.event.{event_type} {' '.join(f'{k}={v}' for k, v in metadata.items())}"
        )

    def decide_model(self, task_type: str, context: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
        """Select appropriate model based on failure history and task type.

        Hard routing rules:
        - ≥2 route_fill_mismatch → planner (repeated form fill failures)
        - foreign_timeout → coder (timeout budget bug)
        - stuck_step or ui_commit_failed → planner (UI navigation issues)
        - deadline_bug → coder (timing calculation error)

        Args:
            task_type: Task category ("plan", "repair", "extract")
            context: Optional task-specific context

        Returns:
            Tuple of (model_name, reason)
        """
        context = context or {}

        # Count event types
        route_mismatch_count = sum(1 for e in self.events if e.event_type == "route_fill_mismatch")
        has_foreign_timeout = any(e.event_type == "foreign_timeout" for e in self.events)
        has_stuck_step = any(e.event_type == "stuck_step" for e in self.events)
        has_ui_commit_failed = any(e.event_type == "ui_commit_failed" for e in self.events)
        has_deadline_bug = any(e.event_type == "deadline_bug" for e in self.events)

        # Hard rule: ≥2 route mismatches MUST trigger planner
        if route_mismatch_count >= 2:
            logger.info(
                f"llm.route decision=planner model={PLANNER_MODEL} reason=repeated_route_mismatch count={route_mismatch_count}"
            )
            return PLANNER_MODEL, "repeated_route_mismatch"

        # Hard rule: foreign_timeout MUST trigger coder
        if has_foreign_timeout:
            logger.info(
                f"llm.route decision=coder model={CODER_MODEL} reason=foreign_timeout_detected"
            )
            return CODER_MODEL, "foreign_timeout_detected"

        # Stuck step or commit failure → planner (UI/navigation issue)
        if has_stuck_step or has_ui_commit_failed:
            reason = "stuck_step_detected" if has_stuck_step else "ui_commit_failed"
            logger.info(
                f"llm.route decision=planner model={PLANNER_MODEL} reason={reason}"
            )
            return PLANNER_MODEL, reason

        # Deadline bug → coder (timing/budget calculation issue)
        if has_deadline_bug:
            logger.info(
                f"llm.route decision=coder model={CODER_MODEL} reason=deadline_bug_detected"
            )
            return CODER_MODEL, "deadline_bug_detected"

        # Fallback to task-type defaults
        if task_type in ("plan", "repair"):
            default_model = PLANNER_MODEL
            reason = f"default_{task_type}"
        elif task_type == "extract":
            default_model = CODER_MODEL
            reason = "default_extract"
        else:
            default_model = PLANNER_MODEL
            reason = "default_unknown_task"

        logger.debug(
            f"llm.route decision={task_type} model={default_model} reason={reason}"
        )
        return default_model, reason

    def get_event_summary(self) -> Dict[str, int]:
        """Return event counts by type for logging/diagnostics.

        Returns:
            Dict mapping event_type to count
        """
        summary: Dict[str, int] = {}
        for event in self.events:
            summary[event.event_type] = summary.get(event.event_type, 0) + 1
        return summary
