"""Plan toggle helpers extracted from core.scenario_runner."""

from __future__ import annotations

from core.scenario_runner.selectors.fallbacks import (
    _service_search_click_fallbacks,
    _service_wait_fallbacks,
)
from core.scenario_runner.google_flights.service_runner_bridge import (
    _build_click_selectors_for_tokens,
    _maybe_append_bare_text_selectors,
    _selector_candidates,
)
from core.scenario_runner.google_flights.core_functions import _allow_bare_text_fallback


def _default_domain_toggle_step(is_domestic: bool):
    """Return a conservative domestic/international mode toggle click step."""
    if is_domestic:
        tokens = ["Domestic", "国内"]
    else:
        tokens = ["International", "海外", "国際"]
    selectors = _build_click_selectors_for_tokens(tokens)
    selectors = _maybe_append_bare_text_selectors(
        selectors,
        tokens,
        allow=_allow_bare_text_fallback(),
    )
    return {"action": "click", "selector": selectors, "optional": True}


def _domain_toggle_step_from_knowledge(knowledge, is_domestic):
    """Prefer site-local learned domestic/international toggle selectors."""
    key = "local_domestic_toggles" if is_domestic else "local_international_toggles"
    selectors = knowledge.get(key, []) if isinstance(knowledge, dict) else []
    if not isinstance(selectors, list):
        selectors = []
    selectors = [s for s in selectors if isinstance(s, str) and s.strip()]
    if not selectors:
        return None
    return {"action": "click", "selector": selectors[:8], "optional": True}


def _maybe_prepend_domain_toggle(plan, site_key, is_domestic, knowledge):
    """Inject domestic/international toggle step when knowledge indicates split-flow."""
    _ = site_key
    if not isinstance(plan, list):
        return plan
    site_type = knowledge.get("site_type") if isinstance(knowledge, dict) else None
    if site_type == "single_flow":
        return plan

    for step in plan:
        if not isinstance(step, dict) or step.get("action") != "click":
            continue
        text = " ".join(_selector_candidates(step.get("selector")))
        if any(token in text for token in ("Domestic", "International", "国内", "海外", "国際")):
            return plan

    learned = _domain_toggle_step_from_knowledge(knowledge, is_domestic)
    if learned:
        toggle_step = learned
    elif site_type == "domestic_international_split":
        toggle_step = _default_domain_toggle_step(is_domestic)
    else:
        return plan
    return [toggle_step] + plan


def _default_turn_followup_plan(site_key: str):
    """Return a lightweight follow-up plan for turn>0 when LLM is unavailable."""
    click_selectors = _service_search_click_fallbacks(site_key)
    wait_selectors = _service_wait_fallbacks(site_key)
    return [
        {
            "action": "click",
            "selector": click_selectors if len(click_selectors) > 1 else click_selectors[0],
            "optional": True,
        },
        {
            "action": "wait",
            "selector": wait_selectors if len(wait_selectors) > 1 else wait_selectors[0],
        },
    ]
