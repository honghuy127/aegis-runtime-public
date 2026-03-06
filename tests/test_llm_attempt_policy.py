"""Unit tests for LLM attempt policy and budget management."""

import pytest
import time
from unittest.mock import Mock, patch

pytestmark = [pytest.mark.llm]

from llm.attempt_policy import (
    LLMCallBudget,
    AttemptDecider,
    load_llm_budget_from_config,
    ErrorCategory,
)


class TestLLMCallBudget:
    """Tests for LLMCallBudget tracking and state."""

    def test_budget_initialization(self):
        """Budget should initialize with correct total and zero elapsed."""
        budget = LLMCallBudget(total_wall_clock_s=240.0)
        assert budget.total_wall_clock_s == 240.0
        assert budget.remaining_s >= 239.0  # Allow small timing variance
        assert not budget.is_exhausted

    @patch("llm.attempt_policy.time.monotonic")
    def test_budget_elapsed_time_tracking(self, mock_monotonic):
        """Elapsed time should increase with mocked time."""
        mock_monotonic.side_effect = [100.0, 105.0]  # 5 second gap
        budget = LLMCallBudget(total_wall_clock_s=240.0)
        initial_elapsed = budget.elapsed_s
        budget._start_time = 100.0  # Override for testing
        mock_monotonic.return_value = 105.0
        after_time = budget.elapsed_s
        assert after_time > initial_elapsed

    @patch("llm.attempt_policy.time.monotonic")
    def test_remaining_decreases_with_time(self, mock_monotonic):
        """Remaining budget should decrease as elapsed time increases."""
        budget = LLMCallBudget(total_wall_clock_s=10.0)
        budget._start_time = 100.0
        mock_monotonic.return_value = 100.0
        initial_remaining = budget.remaining_s
        mock_monotonic.return_value = 100.1
        after_time_remaining = budget.remaining_s
        assert after_time_remaining < initial_remaining
        assert after_time_remaining == pytest.approx(9.9, abs=0.05)

    @patch("llm.attempt_policy.time.monotonic")
    def test_exhaustion_check(self, mock_monotonic):
        """Budget should be exhausted when remaining <= safety_margin."""
        mock_monotonic.return_value = 100.0
        budget = LLMCallBudget(total_wall_clock_s=0.05, safety_margin_s=0.02)
        budget._start_time = 100.0
        assert not budget.is_exhausted
        # After most of budget is spent
        mock_monotonic.return_value = 100.05
        budget._start_time = 100.0
        assert budget.is_exhausted

    def test_mark_attempt_tracking(self):
        """Marking attempts should increment stage counter."""
        budget = LLMCallBudget(total_wall_clock_s=240.0)
        budget.mark_attempt("vlm_extract")
        assert budget.attempts_used_for_stage("vlm_extract") == 1
        budget.mark_attempt("vlm_extract")
        assert budget.attempts_used_for_stage("vlm_extract") == 2
        budget.mark_attempt("html_llm")
        assert budget.attempts_used_for_stage("html_llm") == 1

    @patch("llm.attempt_policy.time.monotonic")
    def test_circuit_open_state(self, mock_monotonic):
        """Circuit open state should be time-aware."""
        mock_monotonic.return_value = 100.0
        budget = LLMCallBudget(total_wall_clock_s=240.0, circuit_open_cooldown_s=0.1)
        budget._start_time = 100.0

        # Model not circuit-open initially
        assert not budget.is_model_circuit_open("minicpm-v:8b")

        # Mark as circuit-open
        budget.set_circuit_open("minicpm-v:8b", cooldown_s=0.1)
        assert budget.is_model_circuit_open("minicpm-v:8b")

        # After cooldown, should not be circuit-open
        mock_monotonic.return_value = 100.15
        assert not budget.is_model_circuit_open("minicpm-v:8b")

    @patch("llm.attempt_policy.time.monotonic")
    def test_circuit_open_different_models_independent(self, mock_monotonic):
        """Circuit open state should be tracked per model."""
        mock_monotonic.return_value = 100.0
        budget = LLMCallBudget(total_wall_clock_s=240.0)
        budget.set_circuit_open("minicpm-v:8b", cooldown_s=1.0)
        budget.set_circuit_open("qwen2.5-coder:7b", cooldown_s=0.05)
        assert budget.is_model_circuit_open("minicpm-v:8b")
        assert budget.is_model_circuit_open("qwen2.5-coder:7b")

        # Later, second model recovers
        mock_monotonic.return_value = 100.06
        assert budget.is_model_circuit_open("minicpm-v:8b")
        assert not budget.is_model_circuit_open("qwen2.5-coder:7b")

    def test_custom_max_attempts_per_stage(self):
        """Should respect custom max_attempts_per_stage."""
        budget = LLMCallBudget(
            total_wall_clock_s=240.0,
            max_attempts_per_stage={"vlm_extract": 3, "html_llm": 1},
        )
        assert budget.max_attempts_per_stage["vlm_extract"] == 3
        assert budget.max_attempts_per_stage["html_llm"] == 1


class TestAttemptDecider:
    """Tests for AttemptDecider logic."""

    def test_should_attempt_within_budget(self):
        """Should allow attempt when budget is sufficient."""
        budget = LLMCallBudget(total_wall_clock_s=240.0)
        decider = AttemptDecider()
        should_attempt, reason = decider.should_attempt(
            stage="vlm_extract",
            model="minicpm-v:8b",
            budget=budget,
        )
        assert should_attempt is True
        assert reason is None

    def test_should_reject_exhausted_budget(self):
        """Should reject attempt when budget is exhausted."""
        budget = LLMCallBudget(total_wall_clock_s=0.01, safety_margin_s=0.02)
        with patch("llm.attempt_policy.time.monotonic") as mock_time:
            mock_time.return_value = 100.1
            budget._start_time = 100.0
            decider = AttemptDecider()
            should_attempt, reason = decider.should_attempt(
                stage="vlm_extract",
                model="minicpm-v:8b",
                budget=budget,
            )
            assert should_attempt is False
            assert reason == "budget_exhausted"

    def test_should_reject_insufficient_budget(self):
        """Should reject attempt when remaining < min_remaining_s_for_attempt."""
        budget = LLMCallBudget(
            total_wall_clock_s=25.0,
            min_remaining_s_for_attempt=20.0,
        )
        with patch("llm.attempt_policy.time.monotonic") as mock_time:
            mock_time.return_value = 106.0  # Leave only 19s remaining
            budget._start_time = 100.0
            decider = AttemptDecider()
            should_attempt, reason = decider.should_attempt(
                stage="vlm_extract",
                model="minicpm-v:8b",
                budget=budget,
            )
            assert should_attempt is False
            assert reason == "insufficient_budget"

    def test_should_reject_circuit_open_model(self):
        """Should reject attempt when model is circuit-open."""
        budget = LLMCallBudget(total_wall_clock_s=240.0)
        budget.set_circuit_open("minicpm-v:8b", cooldown_s=10.0)
        decider = AttemptDecider()
        should_attempt, reason = decider.should_attempt(
            stage="vlm_extract",
            model="minicpm-v:8b",
            budget=budget,
        )
        assert should_attempt is False
        assert reason == "circuit_open"

    def test_should_reject_max_attempts_reached(self):
        """Should reject attempt when max_attempts_per_stage is reached."""
        budget = LLMCallBudget(
            total_wall_clock_s=240.0,
            max_attempts_per_stage={"vlm_extract": 2},
        )
        budget.mark_attempt("vlm_extract")
        budget.mark_attempt("vlm_extract")
        decider = AttemptDecider()
        should_attempt, reason = decider.should_attempt(
            stage="vlm_extract",
            model="minicpm-v:8b",
            budget=budget,
        )
        assert should_attempt is False
        assert reason == "max_attempts_reached"

    def test_should_reject_after_circuit_open_error(self):
        """Should reject attempt after last error was circuit_open."""
        budget = LLMCallBudget(total_wall_clock_s=240.0)
        decider = AttemptDecider()
        should_attempt, reason = decider.should_attempt(
            stage="vlm_extract",
            model="minicpm-v:8b",
            budget=budget,
            last_error_category="circuit_open",
        )
        assert should_attempt is False
        assert reason == "circuit_open_previous_attempt"

    def test_next_timeout_respects_per_model_override(self):
        """Should use per-model timeout override if available."""
        budget = LLMCallBudget(
            total_wall_clock_s=240.0,
            per_call_timeout_s_default=30.0,
            per_model_timeout_overrides={"minicpm-v:8b": 35.0},
        )
        decider = AttemptDecider()
        timeout = decider.next_timeout_s(
            stage="vlm_extract",
            model="minicpm-v:8b",
            budget=budget,
        )
        assert timeout == pytest.approx(35.0, abs=0.5)

    def test_next_timeout_falls_back_to_default(self):
        """Should use default timeout when no override exists."""
        budget = LLMCallBudget(
            total_wall_clock_s=240.0,
            per_call_timeout_s_default=30.0,
        )
        decider = AttemptDecider()
        timeout = decider.next_timeout_s(
            stage="vlm_extract",
            model="qwen2.5-coder:7b",
            budget=budget,
        )
        assert timeout == pytest.approx(30.0, abs=0.5)

    def test_next_timeout_soft_cap_when_budget_low(self):
        """When remaining budget < 60s, use softer margin (1s vs 5s) for aggression relief."""
        budget = LLMCallBudget(
            total_wall_clock_s=50.0,
            per_call_timeout_s_default=60.0,
            safety_margin_s=5.0,
        )
        with patch("llm.attempt_policy.time.monotonic") as mock_time:
            mock_time.return_value = 101.0  # Leave ~49s remaining
            budget._start_time = 100.0
            decider = AttemptDecider()
            timeout = decider.next_timeout_s(
                stage="vlm_extract",
                model="minicpm-v:8b",
                budget=budget,
            )
            # P2 FIX: Low budget uses 1s margin instead of 5s
            # Should be ~48s (remaining 49 - soft_margin 1), not 44s (remaining 49 - hard_margin 5)
            assert timeout == pytest.approx(48.0, abs=0.5)
            assert timeout >= 1.0

    def test_next_timeout_never_zero(self):
        """Timeout should always be >= 1.0."""
        budget = LLMCallBudget(
            total_wall_clock_s=1.5,
            per_call_timeout_s_default=60.0,
            safety_margin_s=1.0,
        )
        decider = AttemptDecider()
        timeout = decider.next_timeout_s(
            stage="vlm_extract",
            model="minicpm-v:8b",
            budget=budget,
        )
        assert timeout >= 1.0


class TestLoadLLMBudgetFromConfig:
    """Tests for config loading."""

    @patch("llm.attempt_policy.get_threshold")
    def test_load_with_explicit_values(self, mock_get_threshold):
        """Should use explicit values when provided."""
        budget = load_llm_budget_from_config(
            total_wall_clock_s=300.0,
            per_call_timeout_s_default=40.0,
            per_model_timeout_overrides={"minicpm-v:8b": 50.0},
        )
        assert budget.total_wall_clock_s == 300.0
        assert budget.per_call_timeout_s_default == 40.0
        assert budget.per_model_timeout_overrides.get("minicpm-v:8b") == 50.0

    @patch("llm.attempt_policy.get_threshold")
    def test_load_with_defaults(self, mock_get_threshold):
        """Should use config defaults when not provided explicitly."""
        mock_get_threshold.side_effect = lambda key, default: {
            "llm_budget_total_s": 240.0,
            "llm_per_call_timeout_s_default": 30.0,
            "llm_timeout_overrides": {},
            "llm_stage_max_attempts": {},
            "llm_min_remaining_s_for_attempt": 20.0,
        }.get(key, default)

        budget = load_llm_budget_from_config()
        assert budget.total_wall_clock_s == 240.0
        assert budget.per_call_timeout_s_default == 30.0
        assert budget.max_attempts_per_stage["vlm_extract"] == 2  # sensible default


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
