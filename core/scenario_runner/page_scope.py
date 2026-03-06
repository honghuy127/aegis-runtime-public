from __future__ import annotations

from typing import Any, Dict, List, Optional

from storage.knowledge_store import record_failure


def normalize_page_class(value: str) -> str:
    """Normalize scope class labels used by LLM/VLM judges."""
    text = str(value or "").strip().lower()
    if text in {
        "flight_only",
        "flight_hotel_package",
        "garbage_page",
        "irrelevant_page",
        "unknown",
    }:
        return text
    return "unknown"


def is_non_flight_page_class(page_class: str) -> bool:
    """Return True when page class is non-flight or unusable for extraction."""
    return normalize_page_class(page_class) in {
        "flight_hotel_package",
        "garbage_page",
        "irrelevant_page",
    }


def scope_feedback_step(trace) -> Dict[str, Any]:
    """Pick one likely-wrong step from execution trace for knowledge feedback."""
    if not isinstance(trace, list):
        return {}
    product_tokens = ("hotel", "ホテル", "package", "map", "地図")
    soft_fill_roles = {"origin", "dest", "depart", "return"}
    # Highest priority: product/map-like click that succeeded.
    for item in reversed(trace):
        if not isinstance(item, dict):
            continue
        if item.get("action") != "click" or item.get("status") != "ok":
            continue
        selector = str(item.get("used_selector", "") or "")
        if any(token in selector.lower() for token in product_tokens):
            return item
    # Next: route/date fill soft-fail indicates likely unbound context.
    for item in reversed(trace):
        if not isinstance(item, dict):
            continue
        if item.get("action") == "fill" and item.get("status") == "soft_fail":
            if str(item.get("role", "")).strip().lower() in soft_fill_roles:
                return item
    # Fallback: last click/fill step.
    for item in reversed(trace):
        if not isinstance(item, dict):
            continue
        if item.get("action") in {"click", "fill"}:
            return item
    return {}


def page_class_to_trip_product(page_class: str) -> str:
    """Map normalized page class into coarse trip-product label."""
    normalized = normalize_page_class(page_class)
    if normalized in {"flight_only", "flight_hotel_package"}:
        return normalized
    return "unknown"


def apply_plugin_readiness_probe(
    *,
    ready: bool,
    route_bound,
    verify_status: str,
    verify_override_reason: str,
    scope_page_class: str,
    scope_trip_product: str,
    scope_sources: list,
    plugin_probe: Dict[str, Any],
) -> Dict[str, Any]:
    """Apply one plugin readiness probe with conservative fallback behavior."""
    # Local import to avoid heavy coupling at module import time
    from core.plugins.adapters.services_adapter import is_actionable_readiness_probe

    if not is_actionable_readiness_probe(plugin_probe):
        return {
            "used": False,
            "ready": bool(ready),
            "route_bound": route_bound,
            "verify_status": verify_status,
            "verify_override_reason": verify_override_reason,
            "scope_page_class": scope_page_class,
            "scope_trip_product": scope_trip_product,
            "scope_sources": list(scope_sources or []),
            "probe_page_class": "unknown",
        }

    probe_page_class = normalize_page_class(plugin_probe.get("page_class"))
    probe_trip_product = str(plugin_probe.get("trip_product", "") or "").strip().lower()
    if probe_trip_product not in {"flight_only", "flight_hotel_package", "unknown"}:
        probe_trip_product = "unknown"

    route_value = plugin_probe.get("route_bound")
    if not isinstance(route_value, bool):
        route_value = route_bound

    out_sources = list(scope_sources or [])
    if "plugin:readiness_probe" not in out_sources:
        out_sources.append("plugin:readiness_probe")
    if probe_page_class != "unknown":
        out_sources.append(f"plugin:{probe_page_class}")

    out_ready = bool(plugin_probe.get("ready", False))
    out_reason = str(plugin_probe.get("reason", "") or "").strip()
    return {
        "used": True,
        "ready": out_ready,
        "route_bound": route_value,
        "verify_status": "plugin_ready" if out_ready else "plugin_unready",
        "verify_override_reason": out_reason if not out_ready else verify_override_reason,
        "scope_page_class": probe_page_class if probe_page_class != "unknown" else scope_page_class,
        "scope_trip_product": (
            probe_trip_product
            if probe_trip_product != "unknown"
            else scope_trip_product
        ),
        "scope_sources": out_sources,
        "probe_page_class": probe_page_class,
    }


def resolve_page_scope_class(
    *,
    heuristic_class: str = "unknown",
    vlm_class: str = "unknown",
    llm_class: str = "unknown",
) -> str:
    """Resolve final page class from deterministic + VLM + LLM votes."""
    heuristic = normalize_page_class(heuristic_class)
    vlm = normalize_page_class(vlm_class)
    llm = normalize_page_class(llm_class)

    # Deterministic Google scope detector is high precision for map/hotel pages.
    if is_non_flight_page_class(heuristic):
        return heuristic

    non_flight_votes = [c for c in (vlm, llm) if is_non_flight_page_class(c)]
    flight_votes = [c for c in (vlm, llm) if c == "flight_only"]
    if heuristic == "flight_only":
        flight_votes.append("flight_only")
    if non_flight_votes and not flight_votes:
        # Preserve the first concrete class to keep reason specific.
        return non_flight_votes[0]
    if flight_votes and not non_flight_votes:
        return "flight_only"
    if heuristic == "flight_only" and not non_flight_votes:
        return "flight_only"
    return "unknown"


def should_block_ready_on_scope_conflict(
    *,
    heuristic_class: str = "unknown",
    llm_class: str = "unknown",
    resolved_class: str = "unknown",
    route_bound: Optional[bool] = None,
    route_support: str = "unknown",
    require_scope_not_irrelevant: bool = True,
) -> bool:
    """Return True when ready-state should be blocked due to unresolved scope conflict."""
    if not require_scope_not_irrelevant:
        return False
    llm_scope = normalize_page_class(llm_class)
    if llm_scope not in {"irrelevant_page", "garbage_page"}:
        return False
    if normalize_page_class(resolved_class) in {"irrelevant_page", "garbage_page"}:
        return True
    if route_bound is True and str(route_support or "").strip().lower() == "strong":
        return False
    heuristic_scope = normalize_page_class(heuristic_class)
    if heuristic_scope == "flight_only" and normalize_page_class(resolved_class) == "unknown":
        return True
    return False


def record_scope_feedback(
    *,
    site_key: str,
    page_class: str,
    step_trace,
    fallback_plan,
    user_id: Optional[str],
) -> (Dict[str, Any], List[str], str):
    """Persist one non-flight scope miss as selector/action feedback."""
    feedback_step = scope_feedback_step(step_trace)
    if not isinstance(feedback_step, dict):
        feedback_step = {}
    selectors = feedback_step.get("selectors")
    if isinstance(selectors, str):
        selector_list = [selectors]
    elif isinstance(selectors, list):
        selector_list = [s for s in selectors if isinstance(s, str) and s.strip()]
    else:
        selector_list = []

    used_selector = str(feedback_step.get("used_selector", "") or "").strip()
    if (
        used_selector
        and used_selector not in {"keyword_fill_recovery", "type_active_recovery"}
        and used_selector not in selector_list
    ):
        selector_list = [used_selector] + selector_list
    selector_list = selector_list[:8]

    action = str(feedback_step.get("action", "") or "").strip().lower()
    if not action:
        action = "click"
    reason = f"scope_guard_non_flight_{normalize_page_class(page_class)}"
    error_message = (
        f"Step failed action={action} selectors={repr(selector_list)}: {reason}"
    )
    record_failure(
        site_key,
        error_message=error_message,
        plan=fallback_plan if isinstance(fallback_plan, list) else None,
        user_id=user_id,
    )
    return feedback_step, selector_list, reason
