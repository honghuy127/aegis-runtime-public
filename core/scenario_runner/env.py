"""Scenario runner environment helpers."""

import os

from core.run_input_config import load_run_input_config
from utils.thresholds import get_threshold


def _env_bool(name: str, default: bool) -> bool:
    """Parse one boolean environment variable with fallback."""
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    """Parse one integer environment variable with fallback."""
    raw = os.getenv(name)
    if raw is None:
        return int(default)
    try:
        return int(raw.strip())
    except Exception:
        return int(default)


def _debug_exploration_mode() -> str:
    """Return optional debug exploration mode (e.g., 'super_deep')."""
    raw = str(os.getenv("FLIGHT_WATCHER_DEBUG_EXPLORATION_MODE", "") or "").strip().lower()
    if raw in {"super_deep", "super-deep", "superdeep", "ultra"}:
        return "super_deep"
    return ""


def _current_mimic_locale() -> str:
    """Resolve current runtime locale for locale-aware selector choices."""
    env_value = (os.getenv("FLIGHT_WATCHER_MIMIC_LOCALE") or "").strip()
    if env_value:
        return env_value
    try:
        cfg = load_run_input_config()
    except Exception:
        return ""
    value = cfg.get("mimic_locale")
    if isinstance(value, str):
        return value.strip()
    return ""


def _threshold_site_value(base_key: str, site_key: str, default):
    """Resolve one threshold with optional per-site override suffix."""
    site = (site_key or "").strip().lower()
    if site:
        site_key_name = f"{base_key}_{site}"
        marker = object()
        site_value = get_threshold(site_key_name, marker)
        if site_value is not marker:
            return site_value
    return get_threshold(base_key, default)
