"""Bootstrap helpers for extracted run_agentic_scenario orchestration."""

from typing import Any, Callable, Dict, Mapping, Tuple

RUNTIME_PATCHABLE_SYMBOLS = (
    "get_threshold",
    "time",
    "BrowserSession",
    "execute_plan",
    "get_plan",
    "get_plan_notes",
    "save_plan",
    "_default_plan_for_service",
    "_is_actionable_plan",
    "_call_generate_action_plan_bundle",
    "_call_repair_action_plan_bundle",
    "_step_trace_memory_hint",
)


def resolve_runtime_symbol_overrides(
    legacy_module: Any,
    *,
    base_symbols: Mapping[str, Any],
    step_trace_memory_hint_fallback: Any = None,
) -> Dict[str, Any]:
    """Resolve runtime-patchable symbols for one invocation without global mutation."""
    resolved: Dict[str, Any] = {}
    for name in RUNTIME_PATCHABLE_SYMBOLS:
        if hasattr(legacy_module, name):
            resolved[name] = getattr(legacy_module, name)
        else:
            resolved[name] = base_symbols.get(name)
    if resolved.get("_step_trace_memory_hint") is None:
        resolved["_step_trace_memory_hint"] = step_trace_memory_hint_fallback
    return resolved


def resolve_retry_turn_defaults(
    legacy_module: Any,
    get_threshold_fn: Callable[[str, Any], Any],
) -> Tuple[int, int]:
    """Resolve default retries/turns from legacy constants or thresholds."""
    if hasattr(legacy_module, "DEFAULT_SCENARIO_MAX_RETRIES"):
        default_scenario_max_retries = int(getattr(legacy_module, "DEFAULT_SCENARIO_MAX_RETRIES"))
    else:
        try:
            default_scenario_max_retries = int(get_threshold_fn("scenario_max_retries", 2))
        except Exception:
            default_scenario_max_retries = 2

    if hasattr(legacy_module, "DEFAULT_SCENARIO_MAX_TURNS"):
        default_scenario_max_turns = int(getattr(legacy_module, "DEFAULT_SCENARIO_MAX_TURNS"))
    else:
        try:
            default_scenario_max_turns = int(get_threshold_fn("scenario_max_turns", 2))
        except Exception:
            default_scenario_max_turns = 2
    return default_scenario_max_retries, default_scenario_max_turns


def enforce_contract_retry_bounds(max_retries: int, max_turns: int) -> Tuple[int, int]:
    """Enforce scenario runner hard contract: max 2 attempts and max 2 turns."""
    return max(1, min(int(max_retries), 2)), max(1, min(int(max_turns), 2))
