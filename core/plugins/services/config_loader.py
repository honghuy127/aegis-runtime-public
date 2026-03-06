"""Normalized service-plugin runtime config view over legacy services.yaml keys."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from core.services import all_service_keys
from core.services_config import DEFAULT_SERVICES_CONFIG_PATH, load_services_config


def load_service_plugin_config(
    path: str = str(DEFAULT_SERVICES_CONFIG_PATH),
) -> Dict[str, Any]:
    """Load legacy services config and expose a normalized plugin-oriented view."""
    loaded = load_services_config(path)
    enabled = [str(key).strip().lower() for key in loaded.get("enabled_services", []) if str(key).strip()]
    service_urls = loaded.get("service_urls", {}) if isinstance(loaded, dict) else {}
    service_hints = loaded.get("service_url_hints", {}) if isinstance(loaded, dict) else {}

    per_service: Dict[str, Dict[str, Any]] = {}
    for service_key in all_service_keys():
        hints = service_hints.get(service_key, {}) if isinstance(service_hints, dict) else {}
        per_service[service_key] = {
            "preferred_url": service_urls.get(service_key),
            "seed_hints": {
                "generic": list(hints.get("generic", []) or []),
                "domestic": list(hints.get("domestic", []) or []),
                "international": list(hints.get("international", []) or []),
                "package": list(hints.get("package", []) or []),
            },
        }

    return {
        "config_path": str(Path(path)),
        "enabled_service_keys": enabled,
        "per_service": per_service,
    }

