"""Soft validators for prompt outputs (strict checks + tolerant fallback support)."""

from typing import Any, Dict, Iterable, List, Tuple

from llm.prompts.registry import (
    PROMPT_HTML_QUALITY,
    PROMPT_LLM_TRIP_PRODUCT_GUARD,
    PROMPT_PRICE_EXTRACTION,
    PROMPT_REPAIR,
    PROMPT_SCENARIO,
    PROMPT_VLM_FILL_ROI,
    PROMPT_VLM_MULTIMODAL_EXTRACTION,
    PROMPT_VLM_PRICE_EXTRACTION,
    PROMPT_VLM_PRICE_VERIFICATION,
    PROMPT_VLM_ROI_VALUE,
    PROMPT_VLM_UI_ASSIST,
)
from llm.prompts.schemas import (
    ACTION_ENUM,
    CONFIDENCE_ENUM,
    PAGE_CLASS_ENUM,
    QUALITY_ENUM,
    SUPPORT_ENUM,
    TRIP_PRODUCT_ENUM,
)


ValidationResult = Tuple[bool, str, Any]


def _missing_keys(payload: Dict[str, Any], keys: Iterable[str]) -> List[str]:
    return [key for key in keys if key not in payload]


def _validate_common_price_payload(payload: Dict[str, Any], *, require_visible_price_text: bool = False, require_route_bound: bool = False, require_page_class: bool = False, require_trip_product: bool = False, require_selector_hint: bool = False) -> ValidationResult:
    missing = _missing_keys(payload, ["price", "currency", "confidence", "reason"])
    if require_visible_price_text:
        missing += _missing_keys(payload, ["visible_price_text"])
    if require_route_bound:
        missing += _missing_keys(payload, ["route_bound"])
    if require_page_class:
        missing += _missing_keys(payload, ["page_class"])
    if require_trip_product:
        missing += _missing_keys(payload, ["trip_product"])
    if require_selector_hint:
        missing += _missing_keys(payload, ["selector_hint"])
    if missing:
        return False, "missing_keys", payload
    if payload.get("confidence") not in CONFIDENCE_ENUM:
        return False, "invalid_enum", payload
    if require_page_class and payload.get("page_class") not in PAGE_CLASS_ENUM:
        return False, "invalid_enum", payload
    if require_trip_product and payload.get("trip_product") not in TRIP_PRODUCT_ENUM:
        return False, "invalid_enum", payload
    return True, "", payload


def _validate_plan_steps(steps: Any) -> ValidationResult:
    if not isinstance(steps, list):
        return False, "wrong_shape", steps
    for step in steps:
        if not isinstance(step, dict):
            return False, "wrong_shape", steps
        if "action" not in step or "selector" not in step:
            return False, "missing_keys", steps
        if str(step.get("action", "")).strip().lower() not in ACTION_ENUM:
            return False, "invalid_enum", steps
    return True, "", steps


def validate_prompt_output(prompt_id: str, parsed: Any, raw: str = "") -> ValidationResult:
    """Soft-validate parsed payload; never raises, never throws hard failures."""
    _ = raw  # Raw kept for API stability and future diagnostics.
    if parsed is None:
        return False, "invalid_json", parsed

    pid = (prompt_id or "").strip()
    if pid in {PROMPT_SCENARIO, PROMPT_REPAIR}:
        if isinstance(parsed, list):
            return _validate_plan_steps(parsed)
        if not isinstance(parsed, dict):
            return False, "wrong_shape", parsed
        if "steps" not in parsed:
            return False, "missing_keys", parsed
        ok, err, _ = _validate_plan_steps(parsed.get("steps"))
        if not ok:
            return False, err, parsed
        return True, "", parsed

    if not isinstance(parsed, dict):
        return False, "wrong_shape", parsed

    if pid == PROMPT_PRICE_EXTRACTION:
        return _validate_common_price_payload(parsed)
    if pid == PROMPT_HTML_QUALITY:
        missing = _missing_keys(parsed, ["quality", "reason"])
        if missing:
            return False, "missing_keys", parsed
        if parsed.get("quality") not in QUALITY_ENUM:
            return False, "invalid_enum", parsed
        return True, "", parsed
    if pid == PROMPT_LLM_TRIP_PRODUCT_GUARD:
        missing = _missing_keys(parsed, ["page_class", "trip_product", "reason"])
        if missing:
            return False, "missing_keys", parsed
        if parsed.get("page_class") not in PAGE_CLASS_ENUM or parsed.get("trip_product") not in TRIP_PRODUCT_ENUM:
            return False, "invalid_enum", parsed
        return True, "", parsed
    if pid == PROMPT_VLM_PRICE_EXTRACTION:
        return _validate_common_price_payload(
            parsed,
            require_visible_price_text=True,
            require_route_bound=True,
            require_page_class=True,
            require_trip_product=True,
        )
    if pid == PROMPT_VLM_PRICE_VERIFICATION:
        missing = _missing_keys(parsed, ["accept", "support", "reason"])
        if missing:
            return False, "missing_keys", parsed
        if parsed.get("support") not in SUPPORT_ENUM:
            return False, "invalid_enum", parsed
        return True, "", parsed
    if pid == PROMPT_VLM_MULTIMODAL_EXTRACTION:
        return _validate_common_price_payload(
            parsed,
            require_route_bound=True,
            require_page_class=True,
            require_trip_product=True,
            require_selector_hint=True,
        )
    if pid == PROMPT_VLM_UI_ASSIST:
        missing = _missing_keys(
            parsed,
            [
                "page_scope",
                "page_class",
                "trip_product",
                "blocked_by_modal",
                "mode_labels",
                "product_labels",
                "fill_labels",
                "reason",
            ],
        )
        if missing:
            return False, "missing_keys", parsed
        if parsed.get("page_class") not in PAGE_CLASS_ENUM or parsed.get("trip_product") not in TRIP_PRODUCT_ENUM:
            return False, "invalid_enum", parsed
        if parsed.get("page_scope") not in {"domestic", "international", "mixed", "unknown"}:
            return False, "invalid_enum", parsed
        return True, "", parsed
    if pid == PROMPT_VLM_FILL_ROI:
        missing = _missing_keys(parsed, ["origin", "dest", "depart", "return", "reason"])
        if missing:
            return False, "missing_keys", parsed
        for role in ("origin", "dest", "depart", "return"):
            payload = parsed.get(role)
            if not isinstance(payload, dict):
                return False, "wrong_shape", parsed
            if payload.get("confidence") not in CONFIDENCE_ENUM:
                return False, "invalid_enum", parsed
        return True, "", parsed
    if pid == PROMPT_VLM_ROI_VALUE:
        missing = _missing_keys(parsed, ["value", "confidence", "reason"])
        if missing:
            return False, "missing_keys", parsed
        if parsed.get("confidence") not in CONFIDENCE_ENUM:
            return False, "invalid_enum", parsed
        return True, "", parsed

    # Unknown prompt IDs are soft-pass by design.
    return True, "", parsed
