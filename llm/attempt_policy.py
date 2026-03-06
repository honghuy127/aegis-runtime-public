"""LLM call attempt policy: budgeted retries, circuit awareness, fast-fail gating.

Provides adaptive timeout calculation and circuit-aware fallback routing.
All timing is in seconds (wall-clock, not CPU).
"""

import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Literal

from utils.logging import get_logger
from utils.thresholds import get_threshold

log = get_logger(__name__)

# Error categories returned by LLM call sites
ErrorCategory = Literal["timeout", "circuit_open", "http_error", "invalid_response", "oom", "unknown"]


@dataclass
class LLMCallBudget:
    """Budget for LLM/VLM calls during one extraction/scenario phase.

    Tracks wall-clock time, attempts per stage, and circuit state.
    All values in seconds.
    """

    total_wall_clock_s: float
    """Total wall-clock budget for all LLM calls (e.g., 240s per extraction phase)."""

    per_call_timeout_s_default: float = 30.0
    """Default per-call timeout (seconds)."""

    per_model_timeout_overrides: Dict[str, float] = field(default_factory=dict)
    """Model-specific timeout overrides (e.g., {'minicpm-v:8b': 35, 'qwen2.5-coder:7b': 90})."""

    max_attempts_per_stage: Dict[str, int] = field(default_factory=lambda: {
        "vlm_fill_verify": 1,
        "vlm_extract": 2,
        "html_llm": 1,
    })
    """Max attempts per stage (e.g., vlm_extract: retry once on timeout, but only if budget remains)."""

    circuit_open_cooldown_s: float = 120.0
    """Default circuit-open cooldown (seconds). Can be overridden by error message."""

    min_remaining_s_for_attempt: float = 20.0
    """Minimum remaining budget required to start a call (safety margin, seconds)."""

    safety_margin_s: float = 2.0
    """Safety margin: timeout_s = remaining_budget_s - safety_margin_s."""

    # Track timing
    _start_time: float = field(default_factory=time.monotonic, init=False)
    _attempts_used: Dict[str, int] = field(default_factory=dict, init=False)
    _circuit_open_until_by_model: Dict[str, float] = field(default_factory=dict, init=False)

    @property
    def elapsed_s(self) -> float:
        """Elapsed time since budget creation (wall-clock)."""
        return time.monotonic() - self._start_time

    @property
    def remaining_s(self) -> float:
        """Remaining budget (clamped to >= 0)."""
        return max(0.0, self.total_wall_clock_s - self.elapsed_s)

    @property
    def is_exhausted(self) -> bool:
        """True if no time remains or only safety margin left."""
        return self.remaining_s <= self.safety_margin_s

    def mark_attempt(self, stage: str) -> None:
        """Record one attempt for a stage."""
        self._attempts_used[stage] = self._attempts_used.get(stage, 0) + 1
        log.debug(
            "llm.budget.attempt_recorded stage=%s attempts=%d remaining_s=%.1f",
            stage,
            self._attempts_used[stage],
            self.remaining_s,
        )

    def set_circuit_open(self, model: str, cooldown_s: Optional[float] = None) -> None:
        """Mark a model as circuit-open until time T."""
        until_s = cooldown_s or self.circuit_open_cooldown_s
        until_ts = time.monotonic() + until_s
        self._circuit_open_until_by_model[model] = until_ts
        log.debug(
            "llm.budget.circuit_open_set model=%s cooldown_s=%.1f",
            model,
            until_s,
        )

    def is_model_circuit_open(self, model: str) -> bool:
        """Check if a model is currently circuit-open."""
        until_ts = self._circuit_open_until_by_model.get(model, 0.0)
        if until_ts <= 0.0:
            return False
        is_open = time.monotonic() < until_ts
        return is_open

    def attempts_used_for_stage(self, stage: str) -> int:
        """Get number of attempts already used for a stage."""
        return self._attempts_used.get(stage, 0)


class AttemptDecider:
    """Decides whether to attempt an LLM call and what timeout to use.

    Rules:
    - Never start if remaining_budget_s < min_remaining_s_for_attempt
    - If model is circuit_open: return False (caller should fallback/skip)
    - Timeout is capped: min(per_call_timeout, remaining_budget - safety_margin)
    - Respects max_attempts_per_stage
    """

    def should_attempt(
        self,
        stage: str,
        model: str,
        budget: LLMCallBudget,
        last_error_category: Optional[ErrorCategory] = None,
    ) -> tuple[bool, Optional[str]]:
        """Decide if we should attempt an LLM call.

        Args:
            stage: e.g., "vlm_fill_verify", "vlm_extract", "html_llm"
            model: model name, e.g., "minicpm-v:8b"
            budget: LLMCallBudget instance
            last_error_category: if not None, indicates why this model failed before

        Returns:
            (should_attempt: bool, skip_reason: Optional[str])
            If should_attempt=False, skip_reason explains why (for evidence).
        """
        # Check budget exhaustion
        if budget.is_exhausted:
            return False, "budget_exhausted"

        if budget.remaining_s < budget.min_remaining_s_for_attempt:
            return False, "insufficient_budget"

        # Check circuit-open state
        if budget.is_model_circuit_open(model):
            return False, "circuit_open"

        # Check max attempts for stage
        max_attempts = budget.max_attempts_per_stage.get(stage, 1)
        attempts_used = budget.attempts_used_for_stage(stage)
        if attempts_used >= max_attempts:
            return False, "max_attempts_reached"

        # If last error was circuit_open, do NOT retry same model
        if last_error_category == "circuit_open":
            return False, "circuit_open_previous_attempt"

        return True, None

    def next_timeout_s(
        self,
        stage: str,
        model: str,
        budget: LLMCallBudget,
    ) -> float:
        """Compute timeout for the next LLM call.

        Rules:
        - Use per_model_timeout_overrides[model] if available
        - Fallback to per_call_timeout_s_default
        - Cap to remaining_budget_s - safety_margin_s
        - Floor to 1.0 (never 0)

        Args:
            stage: e.g., "vlm_fill_verify"
            model: model name
            budget: LLMCallBudget instance

        Returns:
            timeout_s (float)
        """
        per_model = budget.per_model_timeout_overrides.get(model)
        if per_model is not None:
            base_timeout = max(1.0, float(per_model))
        else:
            base_timeout = max(1.0, float(budget.per_call_timeout_s_default))

        # P2 FIX: Avoid aggressive timeout capping when budget is depleted.
        # Root cause: Low timeout (3-5s at 30s budget) triggers VLM failure → 120s circuit
        # open → lost extraction attempts. Soften margin reduction when budget < 60s.
        # Evidence: Episode 20260221_174202_101c45 showed 9s timeout at 30s remaining
        # triggering cascade that exhausted entire 240s extraction budget.
        # Solution: Use smaller safety_margin (1s instead of 5s) when budget is low,
        # giving calls fair timeout window without ignoring budget constraints.
        remaining = budget.remaining_s
        if remaining < 60:
            # Low budget: use minimal margin to allow reasonable timeouts
            soft_margin = 1.0
            capped = min(base_timeout, max(1.0, remaining - soft_margin))
        else:
            # Normal budget: standard safety margin
            capped = min(base_timeout, max(1.0, remaining - budget.safety_margin_s))

        return max(1.0, capped)


def load_llm_budget_from_config(
    total_wall_clock_s: Optional[float] = None,
    per_call_timeout_s_default: Optional[float] = None,
    per_model_timeout_overrides: Optional[Dict[str, float]] = None,
    max_attempts_per_stage: Optional[Dict[str, int]] = None,
) -> LLMCallBudget:
    """Load LLM budget from config with sensible defaults.

    Config keys (from thresholds.yaml or run.yaml):
    - llm_budget_total_s: total wall-clock budget (default 240)
    - llm_per_call_timeout_s_default: default per-call timeout (default 30)
    - llm_timeout_overrides: dict like {"minicpm-v:8b": 35, "qwen2.5-coder:7b": 90}
    - llm_stage_max_attempts: dict like {"vlm_extract": 2, "html_llm": 1}
    - llm_min_remaining_s_for_attempt: safety margin (default 20)
    """
    if total_wall_clock_s is None:
        total_wall_clock_s = get_threshold("llm_budget_total_s", 240.0)

    if per_call_timeout_s_default is None:
        per_call_timeout_s_default = get_threshold("llm_per_call_timeout_s_default", 30.0)

    if per_model_timeout_overrides is None:
        # Load from config
        timeout_overrides = get_threshold("llm_timeout_overrides", {})
        per_model_timeout_overrides = (
            dict(timeout_overrides) if isinstance(timeout_overrides, dict) else {}
        )

    if max_attempts_per_stage is None:
        # Load from config with sensible defaults
        stage_attempts = get_threshold("llm_stage_max_attempts", {})
        max_attempts_per_stage = dict(stage_attempts) if isinstance(stage_attempts, dict) else {}
        # Ensure sensible defaults
        max_attempts_per_stage.setdefault("vlm_fill_verify", 1)
        max_attempts_per_stage.setdefault("vlm_extract", 2)
        max_attempts_per_stage.setdefault("html_llm", 1)

    min_remaining = get_threshold("llm_min_remaining_s_for_attempt", 20.0)

    return LLMCallBudget(
        total_wall_clock_s=float(total_wall_clock_s),
        per_call_timeout_s_default=float(per_call_timeout_s_default),
        per_model_timeout_overrides=per_model_timeout_overrides,
        max_attempts_per_stage=max_attempts_per_stage,
        min_remaining_s_for_attempt=float(min_remaining),
    )


__all__ = [
    "LLMCallBudget",
    "AttemptDecider",
    "load_llm_budget_from_config",
    "ErrorCategory",
]
