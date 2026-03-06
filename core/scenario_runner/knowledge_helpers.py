"""Re-export knowledge helper functions used by the scenario runner.

This bridge provides a stable import surface for knowledge-related helpers so
the extracted `run_agentic_scenario` implementation can import them
explicitly while we move implementations out of the monolith.
"""

from __future__ import annotations

from core.scenario_runner.knowledge_impl import (
    format_knowledge_hints as _format_knowledge_hints,
    compose_global_knowledge_hint as _compose_global_knowledge_hint,
    compose_local_knowledge_hint as _compose_local_knowledge_hint,
    blocked_selectors_from_knowledge as _blocked_selectors_from_knowledge,
    fill_role_knowledge_key as _fill_role_knowledge_key,
    collect_plugin_readiness_hints as _collect_plugin_readiness_hints,
)

__all__ = [
    "_format_knowledge_hints",
    "_compose_global_knowledge_hint",
    "_compose_local_knowledge_hint",
    "_blocked_selectors_from_knowledge",
    "_fill_role_knowledge_key",
    "_collect_plugin_readiness_hints",
]

from typing import Any, Dict, List, Optional

from utils.knowledge_rules import get_knowledge_rule_tokens
from core.scenario_runner.google_flights.service_runner_bridge import (
    _build_click_selectors_for_tokens,
)
from core.scenario_runner import selector_utils as _sel_utils  # type: ignore


def format_knowledge_hints(knowledge: Any, key: str, limit: int = 6) -> str:
    values = knowledge.get(key, []) if isinstance(knowledge, dict) else []
    if not isinstance(values, list):
        return ""
    compact = [v for v in values if isinstance(v, str) and v.strip()][:limit]
    return " | ".join(compact)


def compose_global_knowledge_hint(knowledge: Any) -> str:
    if not isinstance(knowledge, dict):
        return ""
    chunks = []
    mapping = (
        ("global_selectors", "GlobalSelectors"),
        ("global_wait_selectors", "GlobalWait"),
        ("global_fill_origin_selectors", "GlobalOrigin"),
        ("global_fill_dest_selectors", "GlobalDest"),
        ("global_fill_depart_selectors", "GlobalDepart"),
        ("global_fill_return_selectors", "GlobalReturn"),
        ("global_search_click_selectors", "GlobalSearchClick"),
    )
    for key, label in mapping:
        part = format_knowledge_hints(knowledge, key, limit=4)
        if part:
            chunks.append(f"{label}: {part}")
    return " || ".join(chunks)


def compose_local_knowledge_hint(knowledge: Any) -> str:
    if not isinstance(knowledge, dict):
        return ""
    chunks = []
    mapping = (
        ("local_selectors", "LocalSelectors"),
        ("local_wait_selectors", "LocalWait"),
        ("local_fill_origin_selectors", "LocalOrigin"),
        ("local_fill_dest_selectors", "LocalDest"),
        ("local_fill_depart_selectors", "LocalDepart"),
        ("local_fill_return_selectors", "LocalReturn"),
        ("local_search_click_selectors", "LocalSearchClick"),
        ("local_modal_selectors", "LocalModal"),
        ("local_domestic_toggles", "LocalDomesticToggle"),
        ("local_international_toggles", "LocalIntlToggle"),
        ("local_domestic_url_hints", "LocalDomesticUrls"),
        ("local_international_url_hints", "LocalIntlUrls"),
    )
    for key, label in mapping:
        part = format_knowledge_hints(knowledge, key, limit=4)
        if part:
            chunks.append(f"{label}: {part}")
    blocked = format_knowledge_hints(knowledge, "local_failed_selectors", limit=4)
    if blocked:
        chunks.append(f"AvoidSelectors: {blocked}")
    suggested_turns = knowledge.get("suggested_turns")
    if isinstance(suggested_turns, int) and suggested_turns > 0:
        chunks.append(f"SuggestedTurns: {suggested_turns}")
    site_type = knowledge.get("site_type")
    if isinstance(site_type, str) and site_type:
        chunks.append(f"SiteType: {site_type}")
    return " || ".join(chunks)


def blocked_selectors_from_knowledge(knowledge: Any) -> List[str]:
    if not isinstance(knowledge, dict):
        return []
    blocked: List[str] = []
    for key in ("local_failed_selectors", "global_failed_selectors"):
        raw = knowledge.get(key, [])
        if isinstance(raw, list):
            blocked.extend([s for s in raw if isinstance(s, str) and s.strip()])
    return blocked


def fill_role_knowledge_key(role: str, *, local: bool) -> str:
    scope = "local" if local else "global"
    if role == "origin":
        return f"{scope}_fill_origin_selectors"
    if role == "dest":
        return f"{scope}_fill_dest_selectors"
    if role == "depart":
        return f"{scope}_fill_depart_selectors"
    if role == "return":
        return f"{scope}_fill_return_selectors"
    return ""


def collect_plugin_readiness_hints(*, site_key: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
    from core.plugins.adapters import services_adapter as _services_adapter
    from core import scenario_runner as _sr

    plugin_enabled_fn = getattr(_sr, "plugin_strategy_enabled", None)
    if not callable(plugin_enabled_fn):
        plugin_enabled_fn = _services_adapter.plugin_strategy_enabled

    run_hints_fn = getattr(_sr, "run_service_readiness_hints", None)
    if not callable(run_hints_fn):
        run_hints_fn = _services_adapter.run_service_readiness_hints

    if not plugin_enabled_fn():
        return {}
    hints = run_hints_fn(site_key, inputs=inputs)
    return dict(hints) if isinstance(hints, dict) else {}


def compose_local_hint_with_notes(local_knowledge_hint: str, planner_notes: List[str], trace_memory_hint: str) -> str:
    from core.scenario_runner.errors import planner_notes_hint, sanitize_runtime_note  # type: ignore

    notes_hint = planner_notes_hint(planner_notes)
    parts = []
    if local_knowledge_hint:
        parts.append(local_knowledge_hint)
    if notes_hint:
        parts.append(notes_hint)
    memory_hint = sanitize_runtime_note(trace_memory_hint, max_chars=240)
    if memory_hint:
        parts.append(memory_hint)
    return "\n".join(parts)
