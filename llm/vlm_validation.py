"""VLM payload validation and schema hardening helpers (Phase 5)."""

import hashlib
import json
from typing import Any, Dict, Optional


def compute_vlm_payload_hash(payload: Dict[str, Any]) -> str:
    """
    Compute SHA256 hash of VLM payload for logging and validation.

    Enables detection of payload tampering and silent truncation without
    storing the full potentially large payload in logs.

    Args:
        payload: VLM extraction result payload.

    Returns:
        Hex-encoded SHA256 hash of JSON-serialized payload.
    """
    try:
        # Deterministic JSON serialization for consistent hashing
        payload_json = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
        return hashlib.sha256(payload_json.encode('utf-8')).hexdigest()
    except Exception:
        return "payload_hash_error"


def validate_vlm_extraction_schema(payload: Dict[str, Any]) -> tuple[bool, str]:
    """
    Strict schema validation for VLM extraction payloads.

    Detects incomplete extractions, type mismatches, and missing required fields.
    Prevents downstream processing of malformed VLM outputs.

    Args:
        payload: VLM extraction result (price, confidence, etc).

    Returns:
        Tuple of (is_valid, reason_str) where:
        - is_valid: True if payload passes all schema checks
        - reason_str: Reason if invalid (e.g., "missing_price_field")
    """
    if not isinstance(payload, dict):
        return False, "payload_not_dict"

    # Required: price field (number or None for non-anchor pages)
    price = payload.get("price")
    if price is not None and not isinstance(price, (int, float)):
        return False, "price_not_numeric"

    # Required: currency field (string or None)
    currency = payload.get("currency")
    if currency is not None and not isinstance(currency, str):
        return False, "currency_not_string"

    # Required: confidence field (string in allowed set)
    confidence = str(payload.get("confidence", "low") or "low").strip().lower()
    if confidence not in {"low", "medium", "high"}:
        return False, f"confidence_invalid_{confidence}"

    # Required: reason field (string explaining decision)
    reason = payload.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        return False, "reason_empty_or_missing"

    # Check payload size reasonableness: prevent accidentally huge nested fields
    try:
        payload_size_kb = len(json.dumps(payload)) / 1024
        if payload_size_kb > 500:  # 500KB is excessive for a price extraction
            return False, f"payload_oversized_{payload_size_kb:.0f}kb"
    except Exception:
        return False, "payload_size_check_error"

    # If price is extracted, currency should be present
    if price is not None and currency is None:
        return False, "price_without_currency"

    # Validate optional scope classification fields
    page_class = str(payload.get("page_class", "") or "").strip().lower()
    if page_class and page_class not in {"flight_only", "flight_hotel_package", "non_flight_scope", "unknown"}:
        return False, f"page_class_invalid_{page_class}"

    trip_product = str(payload.get("trip_product", "") or "").strip().lower()
    if trip_product and trip_product not in {"flight_only", "flight_hotel_package", "unknown"}:
        return False, f"trip_product_invalid_{trip_product}"

    return True, "valid"


def fallback_empty_payload() -> Dict[str, Any]:
    """
    Return safe empty VLM payload when validation fails.

    Prevents cascading errors by providing a "no extraction" result
    instead of retrying with broken data.

    Returns:
        Minimal valid VLM payload indicating extraction unavailable.
    """
    return {
        "price": None,
        "currency": None,
        "confidence": "low",
        "reason": "vlm_payload_validation_failed",
        "source": "vlm_fallback",
    }


def vlm_validation_decision(
    payload: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Make validation and circuit-open decision for VLM payload.

    Implements Phase 5 strategy:
    - Strict schema validation
    - Empty payload = fast-fail (no retry)
    - Invalid payload = return fallback
    - Validation errors tracked for circuit-open

    Args:
        payload: Raw VLM extraction result.
        context: Optional scenario context with tracking.

    Returns:
        Dictionary with keys:
        - accepted: bool, True if payload accepted for use
        - payload: dict, either validated payload or fallback
        - payload_hash: str, hash of payload (for logging)
        - reason: str, validation outcome
        - validation_error: str or None, specific error if any
    """
    if not isinstance(payload, dict):
        return {
            "accepted": False,
            "payload": fallback_empty_payload(),
            "payload_hash": "invalid_type",
            "reason": "invalid_payload_format",
            "validation_error": "payload_not_dict",
        }

    # Check if payload is empty (no extraction occurred)
    price = payload.get("price")
    currency = payload.get("currency")
    if price is None and currency is None:
        # Empty payload = fast-fail, return fallback without retry
        return {
            "accepted": False,
            "payload": payload,  # Use as-is for clarity
            "payload_hash": compute_vlm_payload_hash(payload),
            "reason": "empty_vlm_extraction",
            "validation_error": None,
        }

    # Validate against schema
    is_valid, validation_reason = validate_vlm_extraction_schema(payload)
    payload_hash = compute_vlm_payload_hash(payload)

    if not is_valid:
        # Invalid schema = return fallback without retry
        return {
            "accepted": False,
            "payload": fallback_empty_payload(),
            "payload_hash": payload_hash,
            "reason": f"validation_failed_{validation_reason}",
            "validation_error": validation_reason,
        }

    # Payload is valid, accept it
    return {
        "accepted": True,
        "payload": payload,
        "payload_hash": payload_hash,
        "reason": "validation_passed",
        "validation_error": None,
    }
