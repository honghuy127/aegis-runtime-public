"""Threshold and runtime-option helpers for LLM adapters."""

import os
from typing import Dict

from utils.thresholds import get_threshold


def llm_mode() -> str:
    """Resolve LLM runtime mode (full|light), defaulting to full."""
    mode = os.getenv("FLIGHT_WATCHER_LLM_MODE", "full").strip().lower()
    return mode if mode in ("full", "light") else "full"


def threshold_int(key: str, default: int) -> int:
    """Read integer threshold with safe fallback."""
    try:
        return int(get_threshold(key, default))
    except Exception:
        return int(default)


def threshold_float(key: str, default: float) -> float:
    """Read float threshold with safe fallback."""
    try:
        return float(get_threshold(key, default))
    except Exception:
        return float(default)


def threshold_bool(key: str, default: bool) -> bool:
    """Read bool threshold with safe fallback."""
    raw = get_threshold(key, default)
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(raw, (int, float)):
        return bool(raw)
    return bool(default)


def llm_runtime_options(kind: str) -> Dict[str, float]:
    """Return num_ctx/num_predict/temperature for current LLM mode."""
    mode = llm_mode()
    if mode == "light":
        if kind == "planner":
            return {
                "num_ctx": threshold_int("llm_light_planner_num_ctx", 8192),
                "num_predict": threshold_int("llm_light_planner_num_predict", 256),
                "temperature": threshold_float("llm_light_temperature", 0.0),
            }
        return {
            "num_ctx": threshold_int("llm_light_coder_num_ctx", 6144),
            "num_predict": threshold_int("llm_light_coder_num_predict", 128),
            "temperature": threshold_float("llm_light_temperature", 0.0),
        }

    if kind == "planner":
        return {
            "num_ctx": threshold_int("llm_planner_num_ctx", 16384),
            "num_predict": threshold_int("llm_planner_num_predict", 768),
            "temperature": threshold_float("llm_temperature", 0.0),
        }
    return {
        "num_ctx": threshold_int("llm_coder_num_ctx", 12288),
        "num_predict": threshold_int("llm_coder_num_predict", 256),
        "temperature": threshold_float("llm_temperature", 0.0),
    }
