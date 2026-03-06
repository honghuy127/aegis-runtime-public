"""Service runners for multi-service flight search orchestration.

This package provides pluggable service-specific implementations for handling
form interactions, verification, recovery, and plan generation across different
flight search websites (Google Flights, Skyscanner, etc.).

Architecture: Follows the SiteAdapter pattern with one-way dependency from
scenario_runner.py to service runners. Runners are stateless; state is managed
by the scenario orchestrator.

Key modules:
- base.py: Abstract ServiceRunner interface
- registry.py: Runtime registry for service runner dispatch
- google_flights.py: Google Flights implementation
- skyscanner.py: Skyscanner implementation
"""

from core.service_runners.base import ServiceRunner
from core.service_runners.registry import (
    register_service_runner,
    get_service_runner,
    list_registered_services,
    is_service_supported,
)
from core.service_runners.google_flights import GoogleFlightsRunner
from core.service_runners.skyscanner import SkyscannerRunner

# Auto-register built-in service runners
register_service_runner("google_flights", GoogleFlightsRunner)
register_service_runner("skyscanner", SkyscannerRunner)

__all__ = [
    "ServiceRunner",
    "GoogleFlightsRunner",
    "SkyscannerRunner",
    "register_service_runner",
    "get_service_runner",
    "list_registered_services",
    "is_service_supported",
]
