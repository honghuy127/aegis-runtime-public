"""Prompt-template integrity checks after reliability tuning."""

from collections import defaultdict

import pytest

from llm import prompts as p
from llm.prompts.registry import (
    PROMPT_HTML_QUALITY,
    PROMPT_LLM_TRIP_PRODUCT_GUARD,
    PROMPT_PRICE_EXTRACTION,
    PROMPT_SCENARIO,
    get_prompt,
)
from llm.prompts.validate import validate_prompt_output

pytestmark = [pytest.mark.llm, pytest.mark.heavy]


def test_prompt_templates_keep_schema_blocks_and_core_keys():
    """Prompt templates should keep JSON schema blocks and required key names."""
    assert "JSON schema:" in p.PRICE_EXTRACTION_PROMPT
    assert '"price": number | null' in p.PRICE_EXTRACTION_PROMPT
    assert '"currency": string | null' in p.PRICE_EXTRACTION_PROMPT
    assert '"confidence": "high" | "medium" | "low"' in p.PRICE_EXTRACTION_PROMPT
    assert '"reason": string' in p.PRICE_EXTRACTION_PROMPT

    assert "JSON schema:" in p.HTML_QUALITY_PROMPT
    assert '"quality": "good" | "uncertain" | "garbage"' in p.HTML_QUALITY_PROMPT
    assert '"reason": string' in p.HTML_QUALITY_PROMPT

    assert "JSON schema:" in p.LLM_TRIP_PRODUCT_GUARD_PROMPT
    assert '"page_class": "flight_only" | "flight_hotel_package" | "garbage_page" | "irrelevant_page" | "unknown"' in p.LLM_TRIP_PRODUCT_GUARD_PROMPT
    assert '"trip_product": "flight_only" | "flight_hotel_package" | "unknown"' in p.LLM_TRIP_PRODUCT_GUARD_PROMPT

    assert "JSON schema:" in p.SCENARIO_PROMPT
    assert '"steps": [' in p.SCENARIO_PROMPT


def test_registry_prompt_lookup_still_returns_existing_templates():
    """Registry IDs should resolve to prompt templates for integrated call sites."""
    assert "quality" in get_prompt(PROMPT_HTML_QUALITY, fallback="")
    assert "page_class" in get_prompt(PROMPT_LLM_TRIP_PRODUCT_GUARD, fallback="")
    assert "price" in get_prompt(PROMPT_PRICE_EXTRACTION, fallback="")


def test_planner_and_repair_prompts_still_format_with_literal_json_examples():
    rendered_scenario = p.SCENARIO_PROMPT.format_map(
        defaultdict(
            str,
            {
            "html": "<html></html>",
            "origin": "HND",
            "dest": "ITM",
            "depart": "2026-03-01",
            "return_date": "2026-03-08",
            "trip_type": "round_trip",
            "is_domestic": True,
            "max_transit": "",
            "turn_index": 1,
            "global_knowledge": "",
            "local_knowledge": "",
            "site_key": "google_flights",
            "mimic_locale": "ja-JP",
            "mimic_region": "JP",
            },
        )
    )
    rendered_repair = p.REPAIR_PROMPT.format(plan="[]", html="<html></html>")
    assert '"steps": [' in rendered_scenario
    assert "previous_plan:" in rendered_repair


def test_validators_still_accept_existing_payload_shapes():
    """Soft validators should continue accepting existing valid outputs."""
    ok, err, _ = validate_prompt_output(
        PROMPT_PRICE_EXTRACTION,
        {
            "price": 12345.0,
            "currency": "JPY",
            "confidence": "high",
            "selector_hint": None,
            "reason": "",
        },
        raw="{}",
    )
    assert ok is True
    assert err == ""

    ok, err, _ = validate_prompt_output(
        PROMPT_SCENARIO,
        [
            {"action": "fill", "selector": "input[name='from']", "value": "HND"},
            {"action": "wait", "selector": "[data-testid='results']"},
        ],
        raw="[]",
    )
    assert ok is True
    assert err == ""
