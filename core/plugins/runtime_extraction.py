"""Feature-flagged plugin extraction router with conservative fallback semantics."""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, Optional

from core.plugins.adapters.services_adapter import (
    get_runtime_service_plugin,
    plugin_strategy_enabled,
)
from core.plugins.extraction.accept import accept_candidate
from core.plugins.extraction.normalize import normalize_plugin_candidate
from core.plugins.extraction.schemas import (
    CONFIDENCE_VALUES,
    PAGE_CLASS_VALUES,
    TRIP_PRODUCT_VALUES,
)
from core.plugins.registry import get_strategy as get_strategy_plugin
from utils.logging import get_logger
from utils.thresholds import get_threshold


log = get_logger(__name__)


def _env_bool(name: str, default: bool) -> bool:
    """Parse one boolean env var with fallback."""
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _resolve_strategy_key() -> str:
    """Resolve extraction strategy key from env with threshold fallback."""
    return str(
        os.getenv(
            "FLIGHT_WATCHER_EXTRACT_STRATEGY_KEY",
            get_threshold("extract_strategy_plugin_key", "html_llm"),
        )
    ).strip().lower() or "html_llm"


def _sanitize_extraction_hints(
    hints: Dict[str, Any],
    *,
    site: str,
) -> Dict[str, Any]:
    """Keep only service-local extraction hints; drop mismatched service payloads."""
    if not isinstance(hints, dict) or not hints:
        return {}
    site_key = str(site or "").strip().lower()
    hinted_service = (
        str(
            hints.get("service_key")
            or hints.get("service")
            or hints.get("site")
            or ""
        )
        .strip()
        .lower()
    )
    if hinted_service and site_key and hinted_service != site_key:
        return {}
    return dict(hints)


def _has_invalid_enum_drift(raw: Any) -> bool:
    """Detect explicit enum drift in strategy outputs for fail-closed routing."""
    if not isinstance(raw, dict):
        return False

    confidence = str(raw.get("confidence", "") or "").strip().lower()
    if confidence and confidence not in CONFIDENCE_VALUES:
        return True

    page_class = str(raw.get("page_class", "") or "").strip().lower()
    if page_class and page_class not in PAGE_CLASS_VALUES:
        return True

    trip_product = str(raw.get("trip_product", "") or "").strip().lower()
    if trip_product and trip_product not in TRIP_PRODUCT_VALUES:
        return True

    return False


def run_plugin_extraction_router(
    *,
    html: str,
    site: str,
    task: str,
    origin: Optional[str],
    dest: Optional[str],
    depart: Optional[str],
    return_date: Optional[str],
    trip_type: Optional[str],
    is_domestic: Optional[bool],
    screenshot_path: Optional[str],
    page_url: Optional[str],
    existing_scope_guard_fn: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    thresholds_getter: Optional[Callable[[str, Any], Any]] = None,
    finalize_output_fn: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Run one plugin extraction strategy and return normalized candidate.

    Returns {} when plugin extraction routing is disabled, unknown, or uncertain.
    """
    if not plugin_strategy_enabled():
        return {}
    # Backward-compatible override: explicit false disables router.
    # Default is enabled once plugin strategy is active.
    if not _env_bool("FLIGHT_WATCHER_PLUGIN_EXTRACT_ROUTER_ENABLED", True):
        return {}

    strategy_key = _resolve_strategy_key()
    try:
        extraction_strategy = get_strategy_plugin(strategy_key)
    except Exception:
        return {}

    service_plugin = get_runtime_service_plugin(site)
    extraction_hints: Dict[str, Any] = {}
    if service_plugin is not None and hasattr(service_plugin, "extraction_hints"):
        try:
            hints = service_plugin.extraction_hints(
                html,
                screenshot_path=screenshot_path,
                inputs={
                    "site": site,
                    "task": task,
                    "origin": origin or "",
                    "dest": dest or "",
                    "depart": depart or "",
                    "return_date": return_date or "",
                    "trip_type": trip_type or "",
                    "is_domestic": is_domestic,
                    "page_url": page_url or "",
                },
            )
            if isinstance(hints, dict):
                extraction_hints = _sanitize_extraction_hints(hints, site=site)
        except Exception as exc:
            log.warning(
                "plugins.extraction_hints.failed site=%s error=%s",
                site,
                exc,
            )

    payload = {
        "site": site,
        "task": task,
        "origin": origin,
        "dest": dest,
        "depart": depart,
        "return_date": return_date,
        "trip_type": trip_type,
        "is_domestic": is_domestic,
        "page_url": page_url,
        "strategy_key": strategy_key,
        "service_plugin": service_plugin,
        "extraction_hints": extraction_hints,
    }
    try:
        raw = extraction_strategy.extract(
            html=html,
            screenshot_path=screenshot_path,
            context=payload,
        )
    except Exception as exc:
        log.warning(
            "plugins.extraction_router.failed site=%s strategy=%s error=%s",
            site,
            strategy_key,
            exc,
        )
        return {}
    if _has_invalid_enum_drift(raw):
        log.info(
            "plugins.extraction_router.rejected_invalid_enums site=%s strategy=%s",
            site,
            strategy_key,
        )
        return {}
    normalized = normalize_plugin_candidate(
        raw,
        strategy_key=strategy_key,
        source_default=f"plugin_{strategy_key}",
    )
    accepted, out, reason = accept_candidate(
        normalized,
        html=html,
        site_key=site,
        existing_scope_guard_fn=existing_scope_guard_fn,
        thresholds_getter=thresholds_getter,
        strategy_key=strategy_key,
    )
    if not accepted:
        log.info(
            "plugins.extraction_router.rejected site=%s strategy=%s reason=%s",
            site,
            strategy_key,
            reason,
        )
        return {}
    if callable(finalize_output_fn):
        try:
            finalized = finalize_output_fn(out)
        except Exception as exc:
            log.warning(
                "plugins.extraction_router.finalize_failed site=%s strategy=%s error=%s",
                site,
                strategy_key,
                exc,
            )
            return {}
        return finalized if isinstance(finalized, dict) else {}
    return out if isinstance(out, dict) else {}
