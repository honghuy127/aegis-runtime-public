"""Tests for prompt registry soft validators (Stage 3)."""

import pytest

from llm.prompts.registry import (
    PROMPT_HTML_QUALITY,
    PROMPT_LLM_TRIP_PRODUCT_GUARD,
    PROMPT_REPAIR,
    PROMPT_SCENARIO,
    get_prompt,
)
from llm.prompts.validate import validate_prompt_output

pytestmark = [pytest.mark.heavy]


def test_html_quality_validator_accepts_valid_payload():
    parsed = {"quality": "good", "reason": "good_route_bound_results"}
    ok, err, normalized = validate_prompt_output(PROMPT_HTML_QUALITY, parsed, raw='{"quality":"good"}')
    assert ok is True
    assert err == ""
    assert normalized["quality"] == "good"


def test_html_quality_validator_missing_key_fails_soft():
    parsed = {"quality": "good"}
    ok, err, normalized = validate_prompt_output(PROMPT_HTML_QUALITY, parsed, raw='{"quality":"good"}')
    assert ok is False
    assert err == "missing_keys"
    assert normalized == parsed


def test_trip_product_validator_invalid_enum_fails_soft():
    parsed = {"page_class": "flight_only", "trip_product": "bad_enum", "reason": ""}
    ok, err, normalized = validate_prompt_output(PROMPT_LLM_TRIP_PRODUCT_GUARD, parsed, raw="{}")
    assert ok is False
    assert err == "invalid_enum"
    assert normalized == parsed


def test_scenario_validator_accepts_list_compat():
    parsed = [
        {"action": "fill", "selector": "input[name='from']", "value": "HND"},
        {"action": "wait", "selector": "[data-testid='results']"},
    ]
    ok, err, normalized = validate_prompt_output(PROMPT_SCENARIO, parsed, raw="[]")
    assert ok is True
    assert err == ""
    assert isinstance(normalized, list)


def test_repair_validator_accepts_dict_steps_shape():
    parsed = {
        "steps": [
            {"action": "click", "selector": "button[aria-label*='Search']"},
            {"action": "wait", "selector": "[data-testid='results']"},
        ],
        "notes": "ok",
    }
    ok, err, normalized = validate_prompt_output(PROMPT_REPAIR, parsed, raw="{}")
    assert ok is True
    assert err == ""
    assert "steps" in normalized


def test_registry_returns_known_prompt_template():
    template = get_prompt(PROMPT_HTML_QUALITY, fallback="")
    assert isinstance(template, str)
    assert "quality" in template
