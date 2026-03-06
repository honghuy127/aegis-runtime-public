"""Tests for site adapter registry - verifies UI driver selection logic.

Tests the architecture rule that enforces exactly ONE UI driver per run,
with config-driven selection and safe fallback.

ARCHITECTURE RULE: INV-ADAPTER-001, INV-ADAPTER-002, INV-ADAPTER-005
"""

import pytest

from core.site_adapter import SiteAdapter, SiteAdapterBindResult, SiteAdapterReadinessResult
from core.site_adapter_registry import SiteAdapterRegistry


class MockAgentAdapter(SiteAdapter):
    """Mock agent adapter for testing."""

    site_id = "test_site"

    def bind_route(self, browser, url, origin, dest, depart, return_date=None, **kwargs):
        """Mock bind that succeeds by default."""
        return SiteAdapterBindResult(success=True)

    def ensure_results_ready(self, browser, html=None):
        return SiteAdapterReadinessResult(ready=False)


class MockAgentAdapterFailsOnBind(SiteAdapter):
    """Mock agent adapter that fails to bind."""

    site_id = "test_site"

    def bind_route(self, browser, url, origin, dest, depart, return_date=None, **kwargs):
        """Mock bind that fails."""
        return SiteAdapterBindResult(
            success=False,
            reason="agent_timeout",
            error_class="TimeoutError",
            error_message="Browser timeout"
        )

    def ensure_results_ready(self, browser, html=None):
        return SiteAdapterReadinessResult(ready=False)


class MockLegacyAdapter(SiteAdapter):
    """Mock legacy adapter for testing."""

    site_id = "test_site"

    def bind_route(self, browser, url, origin, dest, depart, return_date=None, **kwargs):
        """Mock bind that succeeds."""
        return SiteAdapterBindResult(success=True)

    def ensure_results_ready(self, browser, html=None):
        return SiteAdapterReadinessResult(ready=False)


class TestSiteAdapterRegistry:
    """Tests for site adapter registry."""

    def test_adapter_selection_prefers_agent_when_available(self):
        """INV-ADAPTER-002: Registry MUST prefer agent driver when available."""
        registry = SiteAdapterRegistry()
        registry.register_agent_adapter("test_site", MockAgentAdapter)
        registry.register_legacy_adapter("test_site", MockLegacyAdapter)

        config = {
            "ui_driver_mode": "agent",
            "ui_driver_fallback_to_legacy": True,
        }

        adapter = registry.get_adapter("test_site", config)
        assert isinstance(adapter, MockAgentAdapter)

    def test_adapter_explicit_legacy_mode(self):
        """Registry MUST respect explicit legacy mode selection."""
        registry = SiteAdapterRegistry()
        registry.register_agent_adapter("test_site", MockAgentAdapter)
        registry.register_legacy_adapter("test_site", MockLegacyAdapter)

        config = {
            "ui_driver_mode": "legacy",
            "ui_driver_fallback_to_legacy": True,
        }

        adapter = registry.get_adapter("test_site", config)
        assert isinstance(adapter, MockLegacyAdapter)

    def test_adapter_config_overrides_mode(self):
        """INV-ADAPTER-002: Per-site overrides MUST take precedence over default mode."""
        registry = SiteAdapterRegistry()
        registry.register_agent_adapter("test_site", MockAgentAdapter)
        registry.register_legacy_adapter("test_site", MockLegacyAdapter)

        config = {
            "ui_driver_mode": "agent",
            "ui_driver_overrides": {"test_site": "legacy"},
            "ui_driver_fallback_to_legacy": True,
        }

        adapter = registry.get_adapter("test_site", config)
        assert isinstance(adapter, MockLegacyAdapter)

    def test_adapter_fallback_to_legacy_when_agent_missing(self):
        """INV-ADAPTER-002: Registry MUST fallback to legacy if agent unavailable and fallback enabled."""
        registry = SiteAdapterRegistry()
        registry.register_legacy_adapter("test_site", MockLegacyAdapter)

        config = {
            "ui_driver_mode": "agent",
            "ui_driver_fallback_to_legacy": True,
        }

        adapter = registry.get_adapter("test_site", config)
        assert isinstance(adapter, MockLegacyAdapter)

    def test_adapter_fallback_disabled_raises_error(self):
        """INV-ADAPTER-002: Registry MUST raise when adapter unavailable and fallback disabled."""
        registry = SiteAdapterRegistry()
        registry.register_legacy_adapter("test_site", MockLegacyAdapter)

        config = {
            "ui_driver_mode": "agent",
            "ui_driver_fallback_to_legacy": False,
        }

        with pytest.raises(ValueError, match="No UI driver adapter registered"):
            registry.get_adapter("test_site", config)

    def test_adapter_unknown_site_raises_error(self):
        """Registry MUST raise for completely unknown sites."""
        registry = SiteAdapterRegistry()

        config = {"ui_driver_mode": "agent"}

        with pytest.raises(ValueError, match="No UI driver adapter registered"):
            registry.get_adapter("unknown_site", config)

    def test_adapter_invalid_mode_raises_error(self):
        """Registry MUST raise for invalid ui_driver_mode values."""
        registry = SiteAdapterRegistry()
        registry.register_agent_adapter("test_site", MockAgentAdapter)

        config = {"ui_driver_mode": "invalid_mode"}

        with pytest.raises(ValueError, match="Invalid ui_driver_mode"):
            registry.get_adapter("test_site", config)

    def test_bind_with_fallback_succeeds_on_first_try(self):
        """bind_with_fallback MUST return adapter unchanged if bind succeeds."""
        registry = SiteAdapterRegistry()
        adapter = MockAgentAdapter()

        config = {"ui_driver_fallback_to_legacy": True}

        final_adapter, result = registry.bind_with_fallback(
            adapter=adapter,
            browser=None,
            url="http://test",
            origin="NRT",
            dest="HND",
            depart="2026-03-01",
            return_date=None,
            config=config,
        )

        assert final_adapter is adapter
        assert result.success

    def test_bind_with_fallback_when_disabled(self):
        """bind_with_fallback MUST NOT fallback if fallback_to_legacy=False."""
        registry = SiteAdapterRegistry()
        registry.register_legacy_adapter("test_site", MockLegacyAdapter)

        adapter = MockAgentAdapterFailsOnBind()

        config = {"ui_driver_fallback_to_legacy": False}

        final_adapter, result = registry.bind_with_fallback(
            adapter=adapter,
            browser=None,
            url="http://test",
            origin="NRT",
            dest="HND",
            depart="2026-03-01",
            return_date=None,
            config=config,
        )

        # Still returns original adapter, but result shows failure
        assert not result.success

    def test_module_level_functions_work(self):
        """Module-level helper functions MUST work correctly."""
        from core.site_adapter_registry import (
            register_agent_adapter,
            register_legacy_adapter,
            get_adapter,
            get_global_registry,
        )

        # Get the global registry and register
        registry = get_global_registry()

        # Verify registration works
        register_agent_adapter("test_site", MockAgentAdapter)

        config = {"ui_driver_mode": "agent"}
        adapter = get_adapter("test_site", config)

        assert isinstance(adapter, MockAgentAdapter)


class TestSiteAdapterBindResult:
    """Tests for SiteAdapterBindResult dataclass."""

    def test_bind_result_success(self):
        """SiteAdapterBindResult MUST support success state."""
        result = SiteAdapterBindResult(success=True)
        assert result.success
        assert result.reason is None
        assert result.error_class is None

    def test_bind_result_failure_with_diagnostics(self):
        """SiteAdapterBindResult MUST support failure with diagnostics."""
        result = SiteAdapterBindResult(
            success=False,
            reason="timeout",
            error_class="TimeoutError",
            error_message="Timeout after 30s",
        )
        assert not result.success
        assert result.reason == "timeout"
        assert result.error_class == "TimeoutError"


class TestSiteAdapterReadinessResult:
    """Tests for SiteAdapterReadinessResult dataclass."""

    def test_readiness_result_ready(self):
        """SiteAdapterReadinessResult MUST support ready state."""
        result = SiteAdapterReadinessResult(ready=True)
        assert result.ready
        assert result.reason is None

    def test_readiness_result_not_ready_with_reason(self):
        """SiteAdapterReadinessResult MUST support not-ready state with reason."""
        result = SiteAdapterReadinessResult(ready=False, reason="form_pending")
        assert not result.ready
        assert result.reason == "form_pending"
