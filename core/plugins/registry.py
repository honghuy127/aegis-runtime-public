"""Simple plugin registries for service/extraction/provider adapters."""

from __future__ import annotations

from typing import Dict

from core.plugins.interfaces import ExtractionStrategy, LLMProvider, ServicePlugin


_SERVICE_REGISTRY: Dict[str, ServicePlugin] = {}
_EXTRACTION_REGISTRY: Dict[str, ExtractionStrategy] = {}
_PROVIDER_REGISTRY: Dict[str, LLMProvider] = {}


def _ensure_service_defaults() -> None:
    """Install default concrete service plugins once."""
    if not _SERVICE_REGISTRY:
        from core.plugins.services import build_default_service_plugins

        _SERVICE_REGISTRY.update(build_default_service_plugins())


def _ensure_extraction_defaults() -> None:
    """Install default extraction strategies once."""
    if not _EXTRACTION_REGISTRY:
        from core.plugins.adapters.extraction_adapter import (
            build_default_extraction_strategies,
        )

        _EXTRACTION_REGISTRY.update(build_default_extraction_strategies())


def _ensure_provider_defaults() -> None:
    """Install default LLM provider adapters once."""
    if not _PROVIDER_REGISTRY:
        from core.plugins.adapters.llm_provider_adapter import (
            build_default_provider_plugins,
        )

        _PROVIDER_REGISTRY.update(build_default_provider_plugins())


def register_service(key: str, plugin: ServicePlugin) -> None:
    """Register/override one service plugin."""
    _SERVICE_REGISTRY[(key or "").strip().lower()] = plugin


def register_strategy(key: str, plugin: ExtractionStrategy) -> None:
    """Register/override one extraction strategy plugin."""
    _EXTRACTION_REGISTRY[(key or "").strip().lower()] = plugin


def register_provider(key: str, plugin: LLMProvider) -> None:
    """Register/override one provider plugin."""
    _PROVIDER_REGISTRY[(key or "").strip().lower()] = plugin


def get_service(key: str) -> ServicePlugin:
    """Resolve one service plugin by key (must exist)."""
    _ensure_service_defaults()
    normalized = (key or "").strip().lower()
    if normalized not in _SERVICE_REGISTRY:
        raise KeyError(f"Unknown service plugin: {key}")
    return _SERVICE_REGISTRY[normalized]


def get_strategy(key: str) -> ExtractionStrategy:
    """Resolve one extraction strategy plugin by key (must exist)."""
    _ensure_extraction_defaults()
    normalized = (key or "").strip().lower()
    if normalized not in _EXTRACTION_REGISTRY:
        raise KeyError(f"Unknown extraction strategy plugin: {key}")
    return _EXTRACTION_REGISTRY[normalized]


def get_provider(key: str = "default") -> LLMProvider:
    """Resolve one provider plugin by key (must exist)."""
    _ensure_provider_defaults()
    normalized = (key or "").strip().lower() or "default"
    if normalized not in _PROVIDER_REGISTRY:
        raise KeyError(f"Unknown provider plugin: {key}")
    return _PROVIDER_REGISTRY[normalized]


def get_plugin(kind: str, key: str):
    """Generic accessor for small incremental migrations."""
    normalized_kind = (kind or "").strip().lower()
    if normalized_kind == "service":
        return get_service(key)
    if normalized_kind in {"strategy", "extraction"}:
        return get_strategy(key)
    if normalized_kind in {"provider", "llm_provider"}:
        return get_provider(key)
    raise KeyError(f"Unknown plugin kind: {kind}")


def list_service_plugins() -> Dict[str, ServicePlugin]:
    """Return shallow copy of service plugin registry."""
    _ensure_service_defaults()
    return dict(_SERVICE_REGISTRY)


def list_strategy_plugins() -> Dict[str, ExtractionStrategy]:
    """Return shallow copy of extraction strategy registry."""
    _ensure_extraction_defaults()
    return dict(_EXTRACTION_REGISTRY)


def list_provider_plugins() -> Dict[str, LLMProvider]:
    """Return shallow copy of provider registry."""
    _ensure_provider_defaults()
    return dict(_PROVIDER_REGISTRY)
