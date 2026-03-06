"""Prompt registry with stable prompt IDs and versions."""

from dataclasses import dataclass
from typing import Dict

from llm.prompts import (
    HTML_QUALITY_PROMPT,
    LLM_TRIP_PRODUCT_GUARD_PROMPT,
    PRICE_EXTRACTION_PROMPT,
    REPAIR_PROMPT,
    SCENARIO_PROMPT,
    VLM_FILL_ROI_PROMPT,
    VLM_MULTIMODAL_EXTRACTION_PROMPT,
    VLM_PRICE_EXTRACTION_PROMPT,
    VLM_PRICE_VERIFICATION_PROMPT,
    VLM_ROI_VALUE_PROMPT,
    VLM_UI_ASSIST_PROMPT,
)


PROMPT_PRICE_EXTRACTION = "price_extraction"
PROMPT_HTML_QUALITY = "html_quality"
PROMPT_LLM_TRIP_PRODUCT_GUARD = "llm_trip_product_guard"
PROMPT_VLM_PRICE_EXTRACTION = "vlm_price_extraction"
PROMPT_VLM_PRICE_VERIFICATION = "vlm_price_verification"
PROMPT_VLM_MULTIMODAL_EXTRACTION = "vlm_multimodal_extraction"
PROMPT_VLM_UI_ASSIST = "vlm_ui_assist"
PROMPT_VLM_FILL_ROI = "vlm_fill_roi"
PROMPT_VLM_ROI_VALUE = "vlm_roi_value"
PROMPT_SCENARIO = "scenario"
PROMPT_REPAIR = "repair"


@dataclass(frozen=True)
class PromptSpec:
    """Registered prompt template metadata."""

    prompt_id: str
    version: str
    template: str


PROMPT_REGISTRY: Dict[str, PromptSpec] = {
    PROMPT_PRICE_EXTRACTION: PromptSpec(PROMPT_PRICE_EXTRACTION, "v1", PRICE_EXTRACTION_PROMPT),
    PROMPT_HTML_QUALITY: PromptSpec(PROMPT_HTML_QUALITY, "v1", HTML_QUALITY_PROMPT),
    PROMPT_LLM_TRIP_PRODUCT_GUARD: PromptSpec(
        PROMPT_LLM_TRIP_PRODUCT_GUARD,
        "v1",
        LLM_TRIP_PRODUCT_GUARD_PROMPT,
    ),
    PROMPT_VLM_PRICE_EXTRACTION: PromptSpec(
        PROMPT_VLM_PRICE_EXTRACTION,
        "v1",
        VLM_PRICE_EXTRACTION_PROMPT,
    ),
    PROMPT_VLM_PRICE_VERIFICATION: PromptSpec(
        PROMPT_VLM_PRICE_VERIFICATION,
        "v1",
        VLM_PRICE_VERIFICATION_PROMPT,
    ),
    PROMPT_VLM_MULTIMODAL_EXTRACTION: PromptSpec(
        PROMPT_VLM_MULTIMODAL_EXTRACTION,
        "v1",
        VLM_MULTIMODAL_EXTRACTION_PROMPT,
    ),
    PROMPT_VLM_UI_ASSIST: PromptSpec(PROMPT_VLM_UI_ASSIST, "v1", VLM_UI_ASSIST_PROMPT),
    PROMPT_VLM_FILL_ROI: PromptSpec(PROMPT_VLM_FILL_ROI, "v1", VLM_FILL_ROI_PROMPT),
    PROMPT_VLM_ROI_VALUE: PromptSpec(PROMPT_VLM_ROI_VALUE, "v1", VLM_ROI_VALUE_PROMPT),
    PROMPT_SCENARIO: PromptSpec(PROMPT_SCENARIO, "v1", SCENARIO_PROMPT),
    PROMPT_REPAIR: PromptSpec(PROMPT_REPAIR, "v1", REPAIR_PROMPT),
}


def get_prompt(prompt_id: str, *, fallback: str = "") -> str:
    """Resolve prompt template by ID with optional fallback."""
    spec = PROMPT_REGISTRY.get((prompt_id or "").strip())
    if spec is None:
        return fallback
    return spec.template

