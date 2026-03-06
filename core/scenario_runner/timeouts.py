from __future__ import annotations

from typing import Optional


def get_model_timeout_multiplier(model: Optional[str]) -> float:
    """Get timeout multiplier for a given model.

    Maps model names to timeout multipliers:
    - Fast/lightweight models: 0.5x
    - Standard models: 1.0x
    - Comprehensive/powerful models: 1.5x
    """
    if model is None:
        return 1.0

    model_lower = model.lower() if isinstance(model, str) else ""

    fast_models = {"gpt-3.5", "gpt-35-turbo", "llama-2", "mistral"}
    if any(fast in model_lower for fast in fast_models):
        return 0.5

    comprehensive_models = {"gpt-4", "claude-3-opus", "claude-opus"}
    if any(comp in model_lower for comp in comprehensive_models):
        return 1.5

    return 1.0


def apply_model_timeout(timeout_sec: Optional[int], model: Optional[str]) -> Optional[int]:
    """Apply model-specific timeout multiplier to base timeout."""
    if timeout_sec is None:
        return None

    multiplier = get_model_timeout_multiplier(model)
    adjusted = int(timeout_sec * multiplier)

    # Ensure minimum 1 second timeout
    return max(1, adjusted)
"""Scenario runner timeout helpers."""

import time
from typing import Optional

from core.browser import apply_selector_timeout_strategy
from utils.logging import get_logger
from utils.thresholds import get_threshold

from core.scenario_runner.env import _threshold_site_value

log = get_logger("core.scenario_runner")


def _get_model_timeout_multiplier(model: Optional[str]) -> float:
    """Get timeout multiplier for a given model.

    Maps model names to timeout multipliers:
    - Fast/lightweight models: 0.5x (half standard timeout)
    - Standard models: 1.0x (standard timeout)
    - Comprehensive/powerful models: 1.5x (1.5x standard timeout)

    Args:
        model: Model name (e.g., "gpt-4", "claude-3", etc.), or None for default

    Returns:
        Timeout multiplier as float (>0.0)
    """
    if model is None:
        return 1.0

    model_lower = model.lower() if isinstance(model, str) else ""

    # Fast models: 0.5x timeout
    fast_models = {"gpt-3.5", "gpt-35-turbo", "llama-2", "mistral"}
    if any(fast in model_lower for fast in fast_models):
        return 0.5

    # Comprehensive models: 1.5x timeout
    comprehensive_models = {"gpt-4", "claude-3-opus", "claude-opus"}
    if any(comp in model_lower for comp in comprehensive_models):
        return 1.5

    # Default: 1.0x
    return 1.0


def _apply_model_timeout(timeout_sec: Optional[int], model: Optional[str]) -> Optional[int]:
    """Apply model-specific timeout multiplier to base timeout.

    Args:
        timeout_sec: Base timeout in seconds (or None for no timeout)
        model: Selected model name

    Returns:
        Adjusted timeout in seconds (or None if input was None)
    """
    if timeout_sec is None:
        return None

    multiplier = _get_model_timeout_multiplier(model)
    adjusted = int(timeout_sec * multiplier)

    # Ensure minimum 1 second timeout
    return max(1, adjusted)


def _normalize_selector_timeout_ms(
    timeout_ms: Optional[int],
    *,
    site_key: str = "",
    action: str = "",
) -> Optional[int]:
    """Normalize per-selector timeout to safe millisecond budget."""
    if timeout_ms is None:
        return None
    try:
        requested_timeout_ms = int(timeout_ms)
    except Exception:
        return None

    min_timeout_ms = max(
        1,
        int(
            _threshold_site_value(
                "browser_selector_timeout_min_ms",
                site_key,
                int(get_threshold("browser_selector_timeout_min_ms", 800)),
            )
        ),
    )
    suspicious_low_ms = max(1, int(get_threshold("browser_timeout_suspicious_low_ms", 200)))
    effective_timeout_ms = max(min_timeout_ms, requested_timeout_ms)

    if requested_timeout_ms < suspicious_low_ms:
        log.warning(
            "scenario.timeout.suspicious_low site=%s action=%s requested_ms=%s effective_ms=%s min_ms=%s",
            site_key,
            action or "unknown",
            requested_timeout_ms,
            effective_timeout_ms,
            min_timeout_ms,
        )
    return effective_timeout_ms


def _optional_click_timeout_ms(site_key: str) -> int:
    """Resolve optional click timeout via centralized selector strategy."""
    return apply_selector_timeout_strategy(
        base_timeout_ms="browser_optional_click_timeout_ms",
        action_type="action",
        site_key=site_key,
        is_optional_click=True,
    )


def _optional_toggle_timeout_ms(site_key: str) -> int:
    """Backward-compatible wrapper for optional toggle click timeout."""
    return _optional_click_timeout_ms(site_key)


def _wall_clock_cap_reached(
    *,
    started_at: float,
    cap_sec: int,
    now: Optional[float] = None,
) -> bool:
    """Return True when elapsed wall-clock time exceeds cap (0 disables cap)."""
    try:
        cap_value = int(cap_sec)
    except Exception:
        cap_value = 0
    if cap_value <= 0:
        return False
    current = time.monotonic() if now is None else float(now)
    return (current - float(started_at)) >= float(cap_value)
