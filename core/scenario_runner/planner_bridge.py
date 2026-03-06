"""Planner bridge helpers for action plan generation and repair."""

from typing import Any, Dict, List, Optional, Tuple

from llm.code_model import generate_action_plan, repair_action_plan
from llm.model_router import ModelRouter
from utils.logging import get_logger

log = get_logger(__name__)


def _coerce_plan_bundle(payload):
    """Normalize plan payload into (steps, notes) for list/object responses."""
    if isinstance(payload, list):
        return payload, []
    if isinstance(payload, dict):
        steps = payload.get("steps")
        if not isinstance(steps, list):
            for key in ("plan", "actions"):
                candidate = payload.get(key)
                if isinstance(candidate, list):
                    steps = candidate
                    break
        notes = payload.get("notes")
        if isinstance(notes, str):
            notes = [notes]
        elif not isinstance(notes, list):
            notes = []
        return steps if isinstance(steps, list) else None, notes
    return None, []


def _apply_model_timeout(base_timeout, model):
    """Apply model-specific timeout multiplier."""
    # Import locally to avoid circular dependency
    from core.scenario_runner.timeouts import _apply_model_timeout as _timeout_impl
    return _timeout_impl(base_timeout, model)


def _get_model_timeout_multiplier(model):
    """Get model-specific timeout multiplier."""
    # Import locally to avoid circular dependency
    from core.scenario_runner.timeouts import _get_model_timeout_multiplier as _multiplier_impl
    return _multiplier_impl(model)


def _call_generate_action_plan_bundle(router=None, **kwargs):
    """Call planner with notes support; keep backward compatibility."""
    model = None
    reason = "default_plan"
    if isinstance(router, ModelRouter):
        model, reason = router.decide_model("plan", context={"turn": kwargs.get("turn_index", 0)})
        log.info("llm.route decision=plan model=%s reason=%s", model, reason)

    # Apply model-specific timeout multiplier
    base_timeout = kwargs.get("timeout_sec")
    if model is not None and base_timeout is not None:
        adjusted_timeout = _apply_model_timeout(base_timeout, model)
        multiplier = _get_model_timeout_multiplier(model)
        log.info(
            "llm.timeout model=%s base=%s adjusted=%s multiplier=%.2f",
            model, base_timeout, adjusted_timeout, multiplier
        )
        kwargs["timeout_sec"] = adjusted_timeout

    try:
        out = generate_action_plan(return_bundle=True, model=model, **kwargs)
    except TypeError as exc:
        if "unexpected keyword argument" not in str(exc):
            raise
        out = generate_action_plan(**kwargs)
    return _coerce_plan_bundle(out)


def _call_repair_action_plan_bundle(old_plan, html, router=None, **kwargs):
    """Call repair planner with notes support; keep backward compatibility."""
    model = None
    reason = "default_repair"
    if isinstance(router, ModelRouter):
        model, reason = router.decide_model("repair", context={"error": kwargs.get("trace_memory_hint", "")})
        log.info("llm.route decision=repair model=%s reason=%s", model, reason)

    # Apply model-specific timeout multiplier
    base_timeout = kwargs.get("timeout_sec")
    if model is not None and base_timeout is not None:
        adjusted_timeout = _apply_model_timeout(base_timeout, model)
        multiplier = _get_model_timeout_multiplier(model)
        log.info(
            "llm.timeout model=%s base=%s adjusted=%s multiplier=%.2f",
            model, base_timeout, adjusted_timeout, multiplier
        )
        kwargs["timeout_sec"] = adjusted_timeout

    try:
        out = repair_action_plan(old_plan, html, return_bundle=True, model=model, **kwargs)
    except TypeError as exc:
        if "unexpected keyword argument" not in str(exc):
            raise
        out = repair_action_plan(old_plan, html, **kwargs)
    return _coerce_plan_bundle(out)
