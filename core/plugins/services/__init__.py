"""Concrete service plugins (delegating to legacy core.services logic)."""

from __future__ import annotations

from typing import Dict

from core.plugins.interfaces import ServicePlugin
from core.plugins.services.google_flights import GoogleFlightsServicePlugin
from core.plugins.services.skyscanner import SkyscannerServicePlugin


def build_default_service_plugins() -> Dict[str, ServicePlugin]:
    """Build default concrete service plugins for all supported services."""
    plugins = [
        GoogleFlightsServicePlugin(),
        SkyscannerServicePlugin(),
    ]
    return {plugin.service_key: plugin for plugin in plugins}
