"""Tests for VLM strict validation and circuit-open fast-fail (Phase 5)."""

import pytest
from llm.vlm_validation import (
    compute_vlm_payload_hash,
    validate_vlm_extraction_schema,
    fallback_empty_payload,
    vlm_validation_decision,
)

pytestmark = [pytest.mark.vlm, pytest.mark.heavy]


def test_compute_vlm_payload_hash():
    """Hash computation should be deterministic."""
    payload1 = {"price": 100, "currency": "USD", "confidence": "high", "reason": "test"}
    payload2 = {"price": 100, "currency": "USD", "confidence": "high", "reason": "test"}

    hash1 = compute_vlm_payload_hash(payload1)
    hash2 = compute_vlm_payload_hash(payload2)

    assert hash1 == hash2
    assert len(hash1) == 64  # SHA256 hex is 64 chars


def test_compute_vlm_payload_hash_differs_for_different_payload():
    """Different payloads should produce different hashes."""
    payload1 = {"price": 100, "currency": "USD"}
    payload2 = {"price": 200, "currency": "USD"}

    hash1 = compute_vlm_payload_hash(payload1)
    hash2 = compute_vlm_payload_hash(payload2)

    assert hash1 != hash2


def test_compute_vlm_payload_hash_handles_errors():
    """Hash computation should handle serialization errors gracefully."""
    # Create non-serializable object
    class BadObject:
        pass

    payload = {"price": 100, "custom": BadObject()}
    hash_val = compute_vlm_payload_hash(payload)
    assert hash_val == "payload_hash_error"


def test_validate_vlm_extraction_schema_valid_full():
    """Valid full payload should pass validation."""
    payload = {
        "price": 250.50,
        "currency": "USD",
        "confidence": "high",
        "reason": "price_on_top_card",
    }
    is_valid, reason = validate_vlm_extraction_schema(payload)
    assert is_valid is True
    assert reason == "valid"


def test_validate_vlm_extraction_schema_valid_minimal():
    """Minimal valid payload should pass."""
    payload = {
        "price": None,
        "currency": None,
        "confidence": "low",
        "reason": "no_price_visible",
    }
    is_valid, reason = validate_vlm_extraction_schema(payload)
    assert is_valid is True
    assert reason == "valid"


def test_validate_vlm_extraction_schema_invalid_price_type():
    """Non-numeric price should fail."""
    payload = {
        "price": "250.50",  # String instead of number
        "currency": "USD",
        "confidence": "high",
        "reason": "test",
    }
    is_valid, reason = validate_vlm_extraction_schema(payload)
    assert is_valid is False
    assert "price_not_numeric" in reason


def test_validate_vlm_extraction_schema_invalid_currency_type():
    """Non-string currency should fail."""
    payload = {
        "price": 250,
        "currency": 840,  # Number instead of string
        "confidence": "high",
        "reason": "test",
    }
    is_valid, reason = validate_vlm_extraction_schema(payload)
    assert is_valid is False
    assert "currency_not_string" in reason


def test_validate_vlm_extraction_schema_invalid_confidence():
    """Invalid confidence value should fail."""
    payload = {
        "price": 250,
        "currency": "USD",
        "confidence": "very_high",  # Not in allowed set
        "reason": "test",
    }
    is_valid, reason = validate_vlm_extraction_schema(payload)
    assert is_valid is False
    assert "confidence_invalid" in reason


def test_validate_vlm_extraction_schema_missing_reason():
    """Missing reason field should fail."""
    payload = {
        "price": 250,
        "currency": "USD",
        "confidence": "high",
    }
    is_valid, reason = validate_vlm_extraction_schema(payload)
    assert is_valid is False
    assert "reason_empty_or_missing" in reason


def test_validate_vlm_extraction_schema_price_without_currency():
    """Extracted price requires currency."""
    payload = {
        "price": 250,
        "currency": None,
        "confidence": "high",
        "reason": "test",
    }
    is_valid, reason = validate_vlm_extraction_schema(payload)
    assert is_valid is False
    assert "price_without_currency" in reason


def test_validate_vlm_extraction_schema_with_page_class():
    """Valid page_class should pass."""
    payload = {
        "price": 250,
        "currency": "USD",
        "confidence": "high",
        "reason": "test",
        "page_class": "flight_only",
    }
    is_valid, reason = validate_vlm_extraction_schema(payload)
    assert is_valid is True


def test_validate_vlm_extraction_schema_invalid_page_class():
    """Invalid page_class should fail."""
    payload = {
        "price": 250,
        "currency": "USD",
        "confidence": "high",
        "reason": "test",
        "page_class": "invalid_class",
    }
    is_valid, reason = validate_vlm_extraction_schema(payload)
    assert is_valid is False
    assert "page_class_invalid" in reason


def test_fallback_empty_payload():
    """Fallback payload should be minimal valid."""
    payload = fallback_empty_payload()

    assert payload["price"] is None
    assert payload["currency"] is None
    assert payload["confidence"] == "low"
    assert payload["reason"] == "vlm_payload_validation_failed"
    assert payload["source"] == "vlm_fallback"


def test_vlm_validation_decision_accepts_valid():
    """Valid payload should be accepted."""
    payload = {
        "price": 250,
        "currency": "USD",
        "confidence": "high",
        "reason": "test",
    }
    decision = vlm_validation_decision(payload)

    assert decision["accepted"] is True
    assert decision["payload"] == payload
    assert decision["reason"] == "validation_passed"
    assert decision["validation_error"] is None


def test_vlm_validation_decision_rejects_invalid():
    """Invalid payload should be rejected."""
    payload = {
        "price": "not_a_number",  # Invalid
        "currency": "USD",
        "confidence": "high",
        "reason": "test",
    }
    decision = vlm_validation_decision(payload)

    assert decision["accepted"] is False
    assert decision["payload"] == fallback_empty_payload()
    assert "validation_failed" in decision["reason"]
    assert decision["validation_error"] is not None


def test_vlm_validation_decision_empty_payload_fast_fail():
    """Empty payload triggers fast-fail without retry."""
    payload = {
        "price": None,
        "currency": None,
        "confidence": "low",
        "reason": "no_extraction",
    }
    decision = vlm_validation_decision(payload)

    assert decision["accepted"] is False
    assert decision["reason"] == "empty_vlm_extraction"
    assert decision["validation_error"] is None


def test_vlm_validation_decision_includes_payload_hash():
    """Validation decision should include payload hash."""
    payload = {
        "price": 250,
        "currency": "USD",
        "confidence": "high",
        "reason": "test",
    }
    decision = vlm_validation_decision(payload)

    assert len(decision["payload_hash"]) == 64  # SHA256 hash length


def test_vlm_validation_decision_invalid_type():
    """Non-dict payload should be rejected."""
    decision = vlm_validation_decision("not_a_dict")

    assert decision["accepted"] is False
    assert "invalid_payload_format" in decision["reason"]
