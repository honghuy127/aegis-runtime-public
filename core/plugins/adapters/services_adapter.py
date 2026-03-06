"""ServicePlugin adapters over the existing core.services module."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from core import services as services_mod
from core.service_ui_profiles import get_service_ui_profile
from utils.logging import get_logger


log = get_logger(__name__)

_PAGE_CLASS_ENUM = {
    "flight_only",
    "flight_hotel_package",
    "garbage_page",
    "irrelevant_page",
    "unknown",
}
_TRIP_PRODUCT_ENUM = {"flight_only", "flight_hotel_package", "unknown"}


def _env_bool(name: str, default: bool) -> bool:
    """Parse boolean env variable with safe fallback."""
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    lowered = raw.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _normalize_page_class(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in _PAGE_CLASS_ENUM else "unknown"


def _normalize_trip_product(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in _TRIP_PRODUCT_ENUM else "unknown"


def plugin_strategy_enabled() -> bool:
    """Global plugin strategy switch with explicit emergency disable precedence.

    Precedence:
    1) FLIGHT_WATCHER_DISABLE_PLUGINS=true  -> disabled
    2) FLIGHT_WATCHER_PLUGIN_STRATEGY_ENABLED explicitly set -> respected
    3) default -> enabled
    """
    if _env_bool("FLIGHT_WATCHER_DISABLE_PLUGINS", False):
        return False

    raw_enabled = os.getenv("FLIGHT_WATCHER_PLUGIN_STRATEGY_ENABLED")
    if raw_enabled is not None:
        return _env_bool("FLIGHT_WATCHER_PLUGIN_STRATEGY_ENABLED", True)
    return True


def get_runtime_service_plugin(service_key: str):
    """Resolve runtime service plugin when plugin strategy is enabled.

    Returns None when feature switch is disabled, plugin is missing, or lookup fails.
    """
    if not plugin_strategy_enabled():
        return None
    try:
        from core.plugins.registry import get_service as get_service_plugin

        return get_service_plugin(service_key)
    except Exception:
        return None


def run_service_readiness_probe(
    service_key: str,
    *,
    html: str,
    screenshot_path: Optional[str] = None,
    inputs: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run service readiness probe through plugin router with tolerant fallback."""
    plugin = get_runtime_service_plugin(service_key)
    if plugin is None or not hasattr(plugin, "readiness_probe"):
        return {}
    try:
        raw = plugin.readiness_probe(
            html,
            screenshot_path=screenshot_path,
            inputs=inputs or {},
        )
    except Exception as exc:
        log.warning(
            "plugins.readiness_probe.failed service=%s error=%s",
            service_key,
            exc,
        )
        return {}
    if not isinstance(raw, dict) or not raw:
        return {}
    route_bound = raw.get("route_bound")
    if not isinstance(route_bound, bool):
        route_bound = None
    return {
        "ready": bool(raw.get("ready", False)),
        "page_class": _normalize_page_class(raw.get("page_class")),
        "trip_product": _normalize_trip_product(raw.get("trip_product")),
        "route_bound": route_bound,
        "reason": str(raw.get("reason", "") or "").strip(),
    }


def run_service_readiness_hints(
    service_key: str,
    *,
    inputs: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Fetch optional readiness hints from active service plugin."""
    plugin = get_runtime_service_plugin(service_key)
    if plugin is None or not hasattr(plugin, "readiness_hints"):
        return {}
    try:
        hints = plugin.readiness_hints(inputs=inputs or {})
    except Exception as exc:
        log.warning(
            "plugins.readiness_hints.failed service=%s error=%s",
            service_key,
            exc,
        )
        return {}
    return dict(hints) if isinstance(hints, dict) else {}


def run_service_scope_hints(
    service_key: str,
    *,
    inputs: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Fetch optional scope hints from active service plugin."""
    plugin = get_runtime_service_plugin(service_key)
    if plugin is None or not hasattr(plugin, "scope_hints"):
        return {}
    try:
        hints = plugin.scope_hints(inputs=inputs or {})
    except Exception as exc:
        log.warning(
            "plugins.scope_hints.failed service=%s error=%s",
            service_key,
            exc,
        )
        return {}
    return dict(hints) if isinstance(hints, dict) else {}


def is_actionable_readiness_probe(probe: Dict[str, Any]) -> bool:
    """Return True when probe has enough signal to override legacy readiness logic."""
    if not isinstance(probe, dict) or "ready" not in probe:
        return False
    page_class = _normalize_page_class(probe.get("page_class"))
    trip_product = _normalize_trip_product(probe.get("trip_product"))
    route_bound = probe.get("route_bound")
    reason = str(probe.get("reason", "") or "").strip().lower()
    if page_class != "unknown":
        return True
    if trip_product != "unknown":
        return True
    if isinstance(route_bound, bool):
        return True
    if reason and reason not in {"plugin_not_configured", "unknown"}:
        return True
    return False


@dataclass(frozen=True)
class ExistingServicePlugin:
    """Adapter that exposes `core.services` behavior via plugin contract."""

    key: str

    @property
    def service_key(self) -> str:
        return self.key

    @property
    def name(self) -> str:
        """Service display name."""
        return services_mod.service_name(self.key)

    @property
    def display_name(self) -> str:
        return self.name

    @property
    def default_url(self) -> str:
        return services_mod.default_service_url(self.key)

    @property
    def domains(self) -> List[str]:
        """Trusted domains from static service metadata."""
        return list(services_mod.SUPPORTED_SERVICES.get(self.key, {}).get("domains", []))

    @property
    def base_domains(self) -> List[str]:
        return list(services_mod._service_base_domains(self.key))

    @property
    def ui_profile_key(self) -> str:
        return self.key

    def url_candidates(
        self,
        preferred_url: Optional[str] = None,
        is_domestic: Optional[bool] = None,
        *,
        knowledge: Optional[Dict[str, Any]] = None,
        seed_hints: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        """Delegate candidate generation to existing URL ordering logic."""
        return services_mod.service_url_candidates(
            self.key,
            preferred_url=preferred_url,
            is_domestic=is_domestic,
            knowledge=knowledge,
            seed_hints=seed_hints,
        )

    def ui_profile(self) -> Optional[Dict[str, Any]]:
        """Back-compat alias for scenario profile hook."""
        return self.scenario_profile()

    def scenario_profile(self) -> Dict[str, Any]:
        """Default service scenario profile from merged service_ui_profiles."""
        return get_service_ui_profile(self.ui_profile_key)

    def readiness_probe(
        self,
        html: str,
        screenshot_path: Optional[str] = None,
        *,
        inputs: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Conservative default readiness probe (unknown => caller fallback)."""
        _ = (html, screenshot_path, inputs)
        return {
            "ready": False,
            "page_class": "unknown",
            "trip_product": "unknown",
            "route_bound": None,
            "reason": "plugin_not_configured",
        }

    def extraction_hints(
        self,
        html: str,
        screenshot_path: Optional[str] = None,
        *,
        inputs: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Optional extraction hints (default none)."""
        _ = (html, screenshot_path, inputs)
        return {}

    def readiness_hints(self, *, inputs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Optional scenario hints for readiness detection/waiting."""
        _ = inputs
        return {}

    def scope_hints(self, *, inputs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Optional scenario hints for scope classification."""
        _ = inputs
        return {}


def build_default_service_plugins() -> Dict[str, ExistingServicePlugin]:
    """Build adapter entries for all currently supported services."""
    out: Dict[str, ExistingServicePlugin] = {}
    for key in services_mod.all_service_keys():
        out[key] = ExistingServicePlugin(key=key)
    return out
