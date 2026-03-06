"""Tests for scenario runner default fallback plan generation.

Validates that each supported service produces valid fallback plans and
that selector ordering follows the configured policy (avoid bare text by default).
"""

import pytest

import core.scenario_runner as sr
from core.scenario_runner import (
    _default_plan_for_service,
    _is_valid_plan,
)


def test_default_plans_exist_for_all_supported_services():
    """Each supported service should produce a structurally valid fallback plan."""
    for service_key in ("google_flights", "skyscanner"):
        plan = _default_plan_for_service(
            site_key=service_key,
            origin="HND",
            dest="ITM",
            depart="2099-03-01",
        )
        assert _is_valid_plan(plan)


def test_default_service_plans_avoid_bare_text_by_default(monkeypatch):
    """Default fallback plans should avoid bare text selectors unless explicitly enabled."""
    monkeypatch.setattr(
        sr,
        "get_threshold",
        lambda key, default=None: (
            False if key == "scenario_selector_allow_bare_text_fallback" else default
        ),
    )
    for service_key in ("skyscanner",):
        plan = _default_plan_for_service(
            site_key=service_key,
            origin="HND",
            dest="ITM",
            depart="2099-03-01",
        )
        click_steps = [step for step in plan if step.get("action") == "click"]
        assert click_steps
        selectors = click_steps[0].get("selector", [])
        if isinstance(selectors, str):
            selectors = [selectors]
        assert all(not selector.strip().lower().startswith("text=") for selector in selectors)
