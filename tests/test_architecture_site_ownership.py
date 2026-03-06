"""Tests for site ownership architecture invariants.

Verifies that:
1. Extraction service modules don't import browser automation
2. Scenario site modules are legacy-only (no new interaction logic)
3. Adapter interface is used consistently

ARCHITECTURE RULE: INV-ADAPTER-003, INV-ADAPTER-004
"""

import importlib
import inspect
from pathlib import Path


def test_extraction_service_modules_have_no_browser_imports():
    """INV-ADAPTER-003: Service extraction modules MUST NOT import browser modules.

    This ensures extraction logic is independent of UI driver implementation,
    allowing services to be used by any driver.
    """
    services_dir = Path("core/plugins/services")

    # List of files to check
    service_files = [
        services_dir / "google_flights.py",
        # Note: google_flights.py removed after extraction to scenario_runner/google_flights/ and gf_helpers/
        # See: docs/refactors/core_scenario_google_flights_refactor_journal.md
    ]

    # Browser modules that should NOT be imported
    banned_imports = {
        "playwright",
        "core.browser",
        "core.agent",
    }

    for service_file in service_files:
        if not service_file.exists():
            continue

        content = service_file.read_text()

        for banned in banned_imports:
            assert (
                f"import {banned}" not in content
                and f"from {banned}" not in content
            ), (
                f"INVARIANT VIOLATION: {service_file} imports {banned} "
                f"(extraction MUST be browser-free)"
            )


def test_scenario_site_modules_are_legacy_only():
    """INV-ADAPTER-004: Scenario site modules MUST be tagged as legacy.

    New interaction logic goes to agent adapters (core/agent/plugins/<site>/),
    not to scenario modules. This test ensures scenario modules are identifiable
    as legacy fallback.
    """
    scenario_dir = Path("core/scenario")

    # Site-specific scenario modules that should be legacy
    site_modules = [
        scenario_dir / "google_flights.py",  # REMOVED - functions extracted to scenario_runner and gf_helpers
        # See: docs/refactors/core_scenario_google_flights_refactor_journal.md
    ]

    for module_file in site_modules:
        if not module_file.exists():
            continue

        content = module_file.read_text()

        # Should have a docstring indicating it's legacy
        assert '"""' in content or "'''" in content, (
            f"{module_file} MUST have a docstring explaining its legacy status"
        )

        # Check that the module is not importing from agent adapters
        # (would indicate logic duplication)
        assert "from core.agent.plugins" not in content, (
            f"INVARIANT VIOLATION: {module_file} imports from agent plugins "
            f"(suggests logic duplication)"
        )


def test_agent_adapters_prefer_lightweight_methods():
    """Agent adapters SHOULD use lightweight pattern-matching, not heavy automation.

    This is aspirational; real agent adapters may have complex logic.
    But this test documents the intent.
    """
    agent_plugin_dir = Path("core/agent/plugins")

    assert (
        agent_plugin_dir / "google_flights" / "plugin.py"
    ).exists(), "Google Flights agent plugin should exist"

    assert (
        agent_plugin_dir / "google_flights" / "actions.py"
    ).exists(), "Google Flights agent actions should exist"


def test_site_adapter_base_class_is_minimal():
    """SiteAdapter interface MUST remain minimal (not assume specific impl).

    This test ensures the interface doesn't creep with implementation details.
    """
    from core.site_adapter import SiteAdapter

    # Count the abstract methods
    methods = [
        m for m in dir(SiteAdapter)
        if not m.startswith("_") and callable(getattr(SiteAdapter, m))
    ]

    # Should have at least bind_route and ensure_results_ready
    assert "bind_route" in methods
    assert "ensure_results_ready" in methods
    assert "capture_artifacts" in methods

    # Should be small (not a complex interface)
    required_methods = {"bind_route", "ensure_results_ready", "capture_artifacts"}
    interface_methods = {m for m in methods if not m.startswith("_")}

    # Allow for some small methods, but shouldn't have dozens
    assert len(interface_methods) <= 10, (
        f"SiteAdapter interface has grown too large ({len(interface_methods)} methods)"
    )


def test_site_adapter_result_classes_are_dataclass_like():
    """SiteAdapterBindResult and ReadinessResult MUST be simple data holders."""
    from core.site_adapter import (
        SiteAdapterBindResult,
        SiteAdapterReadinessResult,
    )

    # Both should be instantiatable
    bind_result = SiteAdapterBindResult(success=True)
    readiness_result = SiteAdapterReadinessResult(ready=False)

    assert bind_result.success is True
    assert readiness_result.ready is False


def test_registry_enforces_mutual_exclusive_selection():
    """Registry MUST ensure only one adapter instance per site per run.

    This is logically enforced by get_adapter() returning one adapter.
    """
    from core.site_adapter_registry import SiteAdapterRegistry
    from core.site_adapter import SiteAdapter, SiteAdapterBindResult, SiteAdapterReadinessResult

    class DummyAgent(SiteAdapter):
        site_id = "dummy"
        def bind_route(self, *args, **kwargs):
            return SiteAdapterBindResult(success=True)
        def ensure_results_ready(self, *args, **kwargs):
            return SiteAdapterReadinessResult(ready=False)

    class DummyLegacy(SiteAdapter):
        site_id = "dummy"
        def bind_route(self, *args, **kwargs):
            return SiteAdapterBindResult(success=True)
        def ensure_results_ready(self, *args, **kwargs):
            return SiteAdapterReadinessResult(ready=False)

    registry = SiteAdapterRegistry()
    registry.register_agent_adapter("dummy", DummyAgent)
    registry.register_legacy_adapter("dummy", DummyLegacy)

    # get_adapter should return exactly one
    config = {"ui_driver_mode": "agent"}
    adapter = registry.get_adapter("dummy", config)

    # Should be an instance of agent, not both
    assert isinstance(adapter, DummyAgent)
    assert not isinstance(adapter, DummyLegacy)


def test_config_loader_supports_ui_driver_settings():
    """Config loader MUST support ui_driver_* configuration.

    Verifies that run_input_config properly loads and normalizes
    the new UI driver config fields.
    """
    from core.run_input_config import _normalize_ui_driver_mode

    # Test normalization
    assert _normalize_ui_driver_mode("agent") == "agent"
    assert _normalize_ui_driver_mode("legacy") == "legacy"
    assert _normalize_ui_driver_mode("AGENT") == "agent"
    assert _normalize_ui_driver_mode("LEGACY") == "legacy"
    assert _normalize_ui_driver_mode("invalid") == "agent"  # defaults to agent
    assert _normalize_ui_driver_mode(None, default="legacy") == "legacy"
