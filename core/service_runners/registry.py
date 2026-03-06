"""Service runner registry for dispatching to service-specific implementations.

This module provides centralized registration and lookup of ServiceRunner instances.
It enables scenario_runner.py to delegate service-specific logic without hardcoding
dependencies.

Usage:
    from core.service_runners.registry import get_service_runner
    runner = get_service_runner("google_flights")
    plan = runner.get_default_plan(origin, dest, depart)
"""

from typing import Any, Dict, Optional
import logging

log = logging.getLogger(__name__)

# Registry: maps service_key -> (ServiceRunner class, cached instance)
_RUNNER_REGISTRY: Dict[str, tuple] = {}


def register_service_runner(service_key: str, runner_class: type) -> None:
    """Register a service runner class in the global registry.

    Args:
        service_key: Canonical service identifier (e.g., "google_flights")
        runner_class: Concrete ServiceRunner subclass
    """
    if service_key in _RUNNER_REGISTRY:
        log.warning("service_runner.registry.override service=%s", service_key)
    _RUNNER_REGISTRY[service_key] = (runner_class, None)  # (class, instance)
    log.debug("service_runner.registry.registered service=%s runner=%s", service_key, runner_class.__name__)


def get_service_runner(service_key: str) -> Optional[Any]:
    """Retrieve a service runner instance by service key.

    Returns a singleton instance (lazy-initialized) of the registered runner class.
    Returns None if service is not registered.

    Args:
        service_key: Service identifier (e.g., "google_flights", "skyscanner")

    Returns:
        ServiceRunner instance or None if not registered
    """
    entry = _RUNNER_REGISTRY.get(service_key)
    if entry is None:
        log.warning("service_runner.registry.not_found service=%s", service_key)
        return None

    runner_class, cached_instance = entry
    if cached_instance is not None:
        return cached_instance

    # Lazy-initialize singleton instance
    try:
        instance = runner_class()
        _RUNNER_REGISTRY[service_key] = (runner_class, instance)
        log.debug("service_runner.registry.instantiated service=%s", service_key)
        return instance
    except Exception as exc:
        log.error("service_runner.registry.instantiation_failed service=%s error=%s", service_key, exc)
        return None


def list_registered_services() -> list:
    """Return list of registered service keys."""
    return list(_RUNNER_REGISTRY.keys())


def is_service_supported(service_key: str) -> bool:
    """Check if a service has a registered runner."""
    return service_key in _RUNNER_REGISTRY
