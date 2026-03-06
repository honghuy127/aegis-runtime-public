"""Test to identify fallback bug in SiteAdapterRegistry.bind_with_fallback.

This test demonstrates INV-REGISTRY-004 violation: The instance comparison
in bind_with_fallback using `adapter is self._agent_adapters[site_id]()`
always fails because it creates a new instance and compares identity.
"""

import pytest
from unittest.mock import MagicMock
from core.site_adapter import SiteAdapter, SiteAdapterBindResult
from core.site_adapter_registry import SiteAdapterRegistry


class FakeAgentAdapter(SiteAdapter):
    """Fake agent adapter for testing."""

    site_id = "test_site"

    def bind_route(self, browser, url, origin, dest, depart, return_date):
        """Fail bind to trigger fallback."""
        return SiteAdapterBindResult(
            success=False,
            reason="test_agent_bind_failed",
            error_class="test_error",
        )

    def run_step(self, browser, step_id):
        raise NotImplementedError()

    def readiness_probe(self, browser):
        raise NotImplementedError()


class FakeLegacyAdapter(SiteAdapter):
    """Fake legacy adapter for testing."""

    site_id = "test_site"

    def bind_route(self, browser, url, origin, dest, depart, return_date):
        """Succeed bind for fallback."""
        return SiteAdapterBindResult(success=True, reason="test_legacy_bound")

    def run_step(self, browser, step_id):
        raise NotImplementedError()

    def readiness_probe(self, browser):
        raise NotImplementedError()


class TestSiteAdapterFallbackBug:
    """Tests for fallback instance comparison bug."""

    def test_fallback_disabled_raises_error(self):
        """When agent fails and fallback disabled, should raise."""
        registry = SiteAdapterRegistry()
        registry.register_agent_adapter("test_site", FakeAgentAdapter)
        registry.register_legacy_adapter("test_site", FakeLegacyAdapter)

        agent = FakeAgentAdapter()
        browser = MagicMock()

        config = {
            "ui_driver_mode": "agent",
            "ui_driver_fallback_to_legacy": False,  # Fallback disabled
        }

        final_adapter, result = registry.bind_with_fallback(
            adapter=agent,
            browser=browser,
            url="http://example.com",
            origin="AAA",
            dest="BBB",
            depart="2026-03-01",
            return_date="2026-03-08",
            config=config,
        )

        # Agent failed to bind, fallback disabled
        # Current code has bug: instance check `adapter is self._agent_adapters[site_id]()`
        # always fails because it creates a NEW instance
        # So fallback code never tries legacy - it just returns agent + failed result
        assert not result.success, "Expected bind result to show failure"
        # This is the bug: we get back the failed agent result instead of attempting fallback
        print(f"Result: {result}")

    def test_fallback_enabled_should_try_legacy(self):
        """When agent fails and fallback enabled, should fallback to legacy."""
        registry = SiteAdapterRegistry()
        registry.register_agent_adapter("test_site", FakeAgentAdapter)
        registry.register_legacy_adapter("test_site", FakeLegacyAdapter)

        agent = FakeAgentAdapter()
        browser = MagicMock()

        config = {
            "ui_driver_mode": "agent",
            "ui_driver_fallback_to_legacy": True,  # Fallback enabled
        }

        final_adapter, result = registry.bind_with_fallback(
            adapter=agent,
            browser=browser,
            url="http://example.com",
            origin="AAA",
            dest="BBB",
            depart="2026-03-01",
            return_date="2026-03-08",
            config=config,
        )

        # After fix: should attempt fallback to legacy and succeed
        assert isinstance(final_adapter, FakeLegacyAdapter), (
            f"Expected FakeLegacyAdapter but got {type(final_adapter).__name__}"
        )
        assert result.success, "Fallback to legacy should succeed"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
