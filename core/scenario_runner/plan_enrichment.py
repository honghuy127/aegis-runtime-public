"""Plan enrichment helpers extracted from core.scenario_runner."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from core.scenario_runner.google_flights.service_runner_bridge import (
    _maybe_append_bare_text_selectors,
    _selector_candidates,
)
from core.scenario_runner.google_flights.core_functions import _allow_bare_text_fallback
from core.scenario_runner.plan_hygiene_helpers import (
    _annotate_fill_roles,
    _infer_fill_role,
)
from core.scenario_runner.plan_utils_helpers import (
    _maybe_filter_failed_selectors,
    _maybe_prioritize_fill_steps_from_knowledge,
)
from core.scenario_runner.selectors.fallbacks import (
    _service_fill_fallbacks,
    _service_search_click_fallbacks,
    _service_wait_fallbacks,
)
from core.scenario_runner.selectors_helpers import _looks_non_fillable_selector_blob
from core.scenario_runner.vlm.ui_steps import _maybe_prepend_vlm_ui_steps


def maybe_enrich_wait_step(plan, knowledge):
    """Append one wait-step with learned global/local result selectors if needed."""
    if not isinstance(plan, list):
        return plan

    learned_wait = []
    for key in ("local_wait_selectors", "global_wait_selectors"):
        raw = knowledge.get(key, []) if isinstance(knowledge, dict) else []
        if isinstance(raw, list):
            learned_wait.extend([s for s in raw if isinstance(s, str) and s.strip()])

    if not learned_wait:
        return plan

    for step in plan:
        if not isinstance(step, dict) or step.get("action") != "wait":
            continue
        existing = set(_selector_candidates(step.get("selector")))
        if existing.intersection(set(learned_wait)):
            return plan

    merged = []
    for selector in learned_wait:
        if selector not in merged:
            merged.append(selector)
        if len(merged) >= 8:
            break
    if not merged:
        return plan
    return list(plan) + [{"action": "wait", "selector": merged}]


def maybe_prepend_modal_step(plan, knowledge):
    """Prepend learned modal/consent dismiss click when available."""
    if not isinstance(plan, list):
        return plan
    selectors = knowledge.get("local_modal_selectors", []) if isinstance(knowledge, dict) else []
    if not isinstance(selectors, list):
        selectors = []
    selectors = [s for s in selectors if isinstance(s, str) and s.strip()][:6]
    if not selectors:
        return plan

    for step in plan[:2]:
        if not isinstance(step, dict) or step.get("action") != "click":
            continue
        text = " ".join(_selector_candidates(step.get("selector"))).lower()
        if any(token in text for token in ("cookie", "consent", "close", "dismiss", "同意", "閉じる")):
            return plan
    return [{"action": "click", "selector": selectors, "optional": True}] + list(plan)


def maybe_harden_fill_steps(plan, site_key: str):
    """Add fallback fill selectors when model outputs hidden/code field selectors."""
    if not isinstance(plan, list):
        return plan
    out = []
    for step in plan:
        if not isinstance(step, dict) or step.get("action") != "fill":
            out.append(step)
            continue
        selectors = _selector_candidates(step.get("selector"))
        selector_blob = " ".join(selectors)
        role = _infer_fill_role(step)
        if not role or not selectors:
            out.append(step)
            continue
        if not _looks_non_fillable_selector_blob(selector_blob):
            out.append(step)
            continue

        merged = []
        prioritized = _service_fill_fallbacks(site_key, role) + selectors
        for selector in prioritized:
            if _looks_non_fillable_selector_blob(selector):
                continue
            if selector not in merged:
                merged.append(selector)
        if not merged:
            merged = selectors
        new_step = dict(step)
        new_step["selector"] = merged if len(merged) > 1 else merged[0]
        out.append(new_step)
    return out


def maybe_harden_search_clicks(plan, site_key: str, knowledge=None):
    """Add robust fallback selectors for brittle search-button click steps."""
    if not isinstance(plan, list):
        return plan

    hardened = []
    for step in plan:
        if not isinstance(step, dict) or step.get("action") != "click":
            hardened.append(step)
            continue

        selectors = _selector_candidates(step.get("selector"))
        selector_blob = " ".join(selectors).lower()
        looks_like_search = (
            "search-button" in selector_blob
            or "search" in selector_blob
            or "submit" in selector_blob
            or "検索" in selector_blob
        )
        if not looks_like_search:
            hardened.append(step)
            continue

        knowledge_search = []
        if isinstance(knowledge, dict):
            raw = knowledge.get("local_search_click_selectors", [])
            if isinstance(raw, list):
                knowledge_search = [s for s in raw if isinstance(s, str) and s.strip()]

        merged = []
        for value in knowledge_search + selectors + _service_search_click_fallbacks(site_key):
            if value not in merged:
                merged.append(value)
        merged = _maybe_append_bare_text_selectors(
            merged,
            [],
            allow=_allow_bare_text_fallback(),
        )
        new_step = dict(step)
        new_step["selector"] = merged if len(merged) > 1 else merged[0]
        hardened.append(new_step)
    return hardened


def maybe_harden_wait_steps(plan, site_key: str):
    """Add fallback wait selectors so one brittle node does not fail a run."""
    if not isinstance(plan, list):
        return plan
    out = []
    for step in plan:
        if not isinstance(step, dict) or step.get("action") != "wait":
            out.append(step)
            continue
        selectors = _selector_candidates(step.get("selector"))
        merged = []
        for selector in selectors + _service_wait_fallbacks(site_key):
            if selector not in merged:
                merged.append(selector)
        new_step = dict(step)
        new_step["selector"] = merged if len(merged) > 1 else merged[0]
        out.append(new_step)
    return out


def with_knowledge(
    plan,
    site_key,
    is_domestic,
    knowledge,
    vlm_hint=None,
    *,
    maybe_prepend_domain_toggle_fn: Optional[Callable[..., Any]] = None,
):
    """Apply knowledge-based enrichments to a plan."""
    out = _annotate_fill_roles(plan)
    out = maybe_prepend_modal_step(out, knowledge)
    out = _maybe_prepend_vlm_ui_steps(out, vlm_hint=vlm_hint, is_domestic=is_domestic)
    if callable(maybe_prepend_domain_toggle_fn):
        out = maybe_prepend_domain_toggle_fn(out, site_key, is_domestic, knowledge)
    out = maybe_enrich_wait_step(out, knowledge)
    out = _maybe_prioritize_fill_steps_from_knowledge(out, knowledge)
    out = _maybe_filter_failed_selectors(out, knowledge)
    out = maybe_harden_fill_steps(out, site_key)
    out = maybe_harden_search_clicks(out, site_key, knowledge)
    out = maybe_harden_wait_steps(out, site_key)
    return out
