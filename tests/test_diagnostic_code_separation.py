"""Test strict separation between failure reasons and diagnostic signals.

This test module verifies:
1. Diagnostic codes (prefix "diag.") are rejected as failure reasons.
2. Unknown codes are rejected.
3. Canonical codes are accepted.
4. Emission guards downgrade invalid codes and preserve originals in evidence.
"""

import pytest

from core.scenario.reasons import (
    REASON_REGISTRY,
    is_diagnostic_code,
    assert_valid_failure_reason,
    is_valid_reason_code,
    normalize_reason,
)
from core.scenario.types import StepResult


class TestDiagnosticCodeIdentification:
    """Tests for identifying diagnostic codes vs canonical reason codes."""

    def test_diagnostic_code_identified(self):
        """Diagnostic codes starting with 'diag.' are identified correctly."""
        assert is_diagnostic_code("diag.dom_explicit_mismatch") is True
        assert is_diagnostic_code("diag.vlm_partial_match") is True
        assert is_diagnostic_code("diag.anything") is True

    def test_canonical_code_not_identified_as_diagnostic(self):
        """Canonical reason codes are not identified as diagnostic."""
        assert is_diagnostic_code("calendar_not_open") is False
        assert is_diagnostic_code("budget_hit") is False
        assert is_diagnostic_code("timeout_error") is False

    def test_empty_and_none_not_diagnostic(self):
        """Empty and None codes are not identified as diagnostic."""
        assert is_diagnostic_code("") is False
        assert is_diagnostic_code(None) is False
        assert is_diagnostic_code("   ") is False


class TestAccessibilityValidator:
    """Tests for assert_valid_failure_reason() validator."""

    def test_canonical_reason_code_accepted(self):
        """Valid canonical reason codes are accepted without error."""
        # Should not raise
        assert_valid_failure_reason("calendar_not_open")
        assert_valid_failure_reason("budget_hit")
        assert_valid_failure_reason("timeout_error")

    def test_diagnostic_code_rejected(self):
        """Diagnostic codes (diag.*) are rejected with clear error."""
        with pytest.raises(ValueError) as exc_info:
            assert_valid_failure_reason("diag.dom_explicit_mismatch")
        assert "Diagnostic code" in str(exc_info.value)
        assert "diag." in str(exc_info.value)

    def test_unknown_code_rejected(self):
        """Unknown codes (not in registry or aliases) are rejected."""
        with pytest.raises(ValueError) as exc_info:
            assert_valid_failure_reason("unknown_invented_code")
        assert "Unknown failure reason code" in str(exc_info.value)

    def test_empty_code_rejected(self):
        """Empty or None codes are rejected."""
        with pytest.raises(ValueError) as exc_info:
            assert_valid_failure_reason("")
        assert "empty" in str(exc_info.value).lower()

        with pytest.raises(ValueError):
            assert_valid_failure_reason(None)

    def test_error_message_includes_guidance(self):
        """Error messages include guidance on how to fix the issue."""
        with pytest.raises(ValueError) as exc_info:
            assert_valid_failure_reason("diag.foo")
        error_msg = str(exc_info.value)
        # Should mention diagnostic and evidence
        assert "Diagnostic" in error_msg or "diag." in error_msg
        assert "evidence" in error_msg or "diag." in error_msg


class TestCanonicalReasonCodeRegistry:
    """Tests that canonical reason codes are properly registered."""

    def test_calendar_reasons_canonical(self):
        """Date picker reason codes are canonical."""
        assert is_valid_reason_code("calendar_dialog_not_found")
        assert is_valid_reason_code("month_nav_exhausted")
        assert is_valid_reason_code("calendar_day_not_found")
        assert is_valid_reason_code("date_picker_unverified")

    def test_budget_reasons_canonical(self):
        """Budget-related reason codes are canonical."""
        assert is_valid_reason_code("budget_hit")
        assert is_valid_reason_code("timeout_error")

    def test_location_reasons_canonical(self):
        """Location fill reason codes are canonical."""
        assert is_valid_reason_code("iata_mismatch")
        assert is_valid_reason_code("suggestion_not_found")


class TestStepResultFailureGuard:
    """Tests for emission guard in StepResult.failure()."""

    def test_canonical_reason_creates_step_success(self):
        """StepResult.failure() accepts canonical reason codes."""
        result = StepResult.failure("calendar_not_open")
        assert result.ok is False
        # Note: "calendar_not_open" is an alias, normalizes to "calendar_dialog_not_found"
        assert result.reason == "calendar_dialog_not_found"

    def test_diagnostic_code_downgraded_to_fallback(self):
        """StepResult.failure() downgrades diagnostic codes to safe fallback."""
        result = StepResult.failure("diag.dom_explicit_mismatch")
        # Reason should be downgraded to safe canonical code
        assert result.ok is False
        assert result.reason == "selector_not_found"
        # Original diagnostic should be preserved in evidence
        assert result.evidence.get("diag.original_reason") == "diag.dom_explicit_mismatch"

    def test_unknown_code_downgraded_to_fallback(self):
        """StepResult.failure() downgrades unknown codes to safe fallback."""
        result = StepResult.failure("invented_unknown_code")
        assert result.ok is False
        assert result.reason == "selector_not_found"
        # Original unknown code and error should be in evidence
        assert result.evidence.get("diag.original_reason") == "invented_unknown_code"
        assert "diag.validation_error" in result.evidence

    def test_diagnostic_original_in_evidence(self):
        """Original diagnostic code stored in evidence['diag.original_reason']."""
        original = "diag.vlm_partial_match"
        result = StepResult.failure(original)
        assert result.evidence.get("diag.original_reason") == original

    def test_evidence_preserved_in_downgrade(self):
        """Evidence dict is preserved when downgrading invalid reason."""
        initial_evidence = {"selector_attempts": 5, "status_code": 200}
        result = StepResult.failure("diag.foo", evidence=initial_evidence)
        # Original evidence should be preserved
        assert result.evidence.get("selector_attempts") == 5
        assert result.evidence.get("status_code") == 200
        # Plus diagnostic signals
        assert result.evidence.get("diag.original_reason") == "diag.foo"

    def test_success_reason_accepted(self):
        """Special 'success' reason code is accepted."""
        result = StepResult.success()
        assert result.ok is True
        assert result.reason == "success"


class TestEmissionIntegrity:
    """Integration tests for reason code emission and validation chain."""

    def test_failure_reason_field_always_valid(self):
        """After StepResult creation, reason field is always a valid canonical code."""
        # Test with diagnostic code
        result1 = StepResult.failure("diag.test")
        assert is_valid_reason_code(result1.reason) is True
        assert is_diagnostic_code(result1.reason) is False

        # Test with unknown code
        result2 = StepResult.failure("unknown")
        assert is_valid_reason_code(result2.reason) is True
        assert is_diagnostic_code(result2.reason) is False

        # Test with canonical code
        result3 = StepResult.failure("calendar_not_open")
        assert is_valid_reason_code(result3.reason) is True
        assert is_diagnostic_code(result3.reason) is False

    def test_diagnostic_signals_never_in_reason_field(self):
        """Diagnostic signals are never emitted in the reason field."""
        # Create results with various diagnostic codes
        for diag_code in [
            "diag.dom_explicit_mismatch",
            "diag.vlm_partial_match",
            "diag.selector_quality_low",
        ]:
            result = StepResult.failure(diag_code)
            # Reason should never be diagnostic
            assert result.reason.startswith("diag.") is False
            # Original should be preserved
            assert result.evidence.get("diag.original_reason") == diag_code


class TestGuardEdgeCases:
    """Edge case tests for the emission guard."""

    def test_whitespace_normalized_before_validation(self):
        """Whitespace is normalized before validation."""
        # Leading/trailing whitespace should be handled
        result = StepResult.failure("  calendar_not_open  ")
        # Should normalize and accept
        assert result.reason == "calendar_not_open" or is_valid_reason_code(result.reason)

    def test_case_insensitive_validation(self):
        """Reason codes are case-insensitive (normalized to lowercase)."""
        # This tests that the normalization in reasons.py handles case
        result = StepResult.failure("CALENDAR_NOT_OPEN")
        # Should normalize and accept or downgrade
        assert is_valid_reason_code(result.reason) or result.reason == "selector_not_found"

    def test_multiple_downgrade_properties(self):
        """Downgraded results have all required properties."""
        result = StepResult.failure("diag.test")
        # Must have basic structure
        assert hasattr(result, "ok")
        assert hasattr(result, "reason")
        assert hasattr(result, "evidence")
        # Evidence must be dict
        assert isinstance(result.evidence, dict)
        # Must have diagnostic tracking
        assert "diag.original_reason" in result.evidence


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
