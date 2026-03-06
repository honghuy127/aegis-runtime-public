"""Test observability of adapter fallback mechanism.

Ensures that when an adapter fallback occurs (agent→legacy), a structured
event is emitted to events.jsonl for monitoring and regression detection.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.site_adapter_registry import SiteAdapterRegistry, SiteAdapterBindResult
from core.site_adapter import SiteAdapter
from utils.run_episode import RunEpisode, emit_ui_driver_fallback_event, get_current_episode


class FakeFailingAgentAdapter(SiteAdapter):
    """Mock agent adapter that always fails binding."""

    def __init__(self):
        self.site_id = "test_site"

    def bind_route(self, **kwargs) -> SiteAdapterBindResult:
        """Always fail binding to trigger fallback."""
        return SiteAdapterBindResult(
            success=False,
            reason="agent_bind_failed",
            error_class="BindException",
        )


class FakeSuccessfulLegacyAdapter(SiteAdapter):
    """Mock legacy adapter that always succeeds binding."""

    def __init__(self):
        self.site_id = "test_site"

    def bind_route(self, **kwargs) -> SiteAdapterBindResult:
        """Always succeed binding."""
        return SiteAdapterBindResult(success=True, reason="success")


class TestAdapterFallbackObservability:
    """Tests for fallback event emission."""

    def test_fallback_event_emitted_when_agent_fails(self, tmp_path):
        """When agent adapter fails and fallback succeeds, event is emitted."""
        run_id = "test_run_001"

        with RunEpisode(run_id=run_id, base_dir=tmp_path) as episode:
            # Verify context is set
            assert get_current_episode() is episode

            # Create registry with test adapters
            registry = SiteAdapterRegistry()
            registry.register_agent_adapter("test_site", FakeFailingAgentAdapter)
            registry.register_legacy_adapter("test_site", FakeSuccessfulLegacyAdapter)

            # Create mock browser
            mock_browser = MagicMock()

            # Get the agent adapter instance (will fail when bound)
            config = {"ui_driver_mode": "agent", "ui_driver_fallback_to_legacy": True}
            adapter = registry.get_adapter("test_site", config)

            # Trigger fallback by attempting to bind
            result_adapter, result = registry.bind_with_fallback(
                adapter=adapter,
                browser=mock_browser,
                url="http://example.com",
                origin="AAA",
                dest="BBB",
                depart="2026-03-01",
                return_date="2026-03-08",
                config=config,
            )

            # Should have gotten legacy adapter due to fallback
            assert isinstance(result_adapter, FakeSuccessfulLegacyAdapter)
            assert result.success is True

        # Verify event was emitted to events.jsonl
        events_path = tmp_path / run_id / "events.jsonl"
        assert events_path.exists(), "events.jsonl should be created"

        # Parse events and find fallback event
        fallback_events = []
        with open(events_path, "r") as f:
            for line in f:
                event = json.loads(line.strip())
                if event.get("event_type") == "ui_driver_fallback":
                    fallback_events.append(event)

        # Should have exactly one fallback event
        assert len(fallback_events) == 1, f"Expected 1 fallback event, got {len(fallback_events)}"

        # Verify event structure and content
        event = fallback_events[0]
        assert event["event_type"] == "ui_driver_fallback"
        assert event["site_id"] == "test_site"
        assert event["from_driver"] == "agent"
        assert event["to_driver"] == "legacy"
        assert event["reason"] == "agent_bind_failed"
        assert "ts" in event  # timestamp auto-added
        assert "run_id" in event  # run_id auto-added
        assert "seq" in event  # sequence number auto-added

    def test_no_fallback_event_when_agent_succeeds(self, tmp_path):
        """When agent adapter succeeds, no fallback event is emitted."""

        class FakeSuccessfulAgentAdapter(SiteAdapter):
            def __init__(self):
                self.site_id = "test_site"

            def bind_route(self, **kwargs) -> SiteAdapterBindResult:
                return SiteAdapterBindResult(success=True, reason="success")

        run_id = "test_run_002"

        with RunEpisode(run_id=run_id, base_dir=tmp_path) as episode:
            registry = SiteAdapterRegistry()
            registry.register_agent_adapter("test_site", FakeSuccessfulAgentAdapter)
            registry.register_legacy_adapter("test_site", FakeSuccessfulLegacyAdapter)

            mock_browser = MagicMock()
            config = {"ui_driver_mode": "agent", "ui_driver_fallback_to_legacy": True}

            adapter = registry.get_adapter("test_site", config)
            result_adapter, result = registry.bind_with_fallback(
                adapter=adapter,
                browser=mock_browser,
                url="http://example.com",
                origin="AAA",
                dest="BBB",
                depart="2026-03-01",
                return_date="2026-03-08",
                config=config,
            )

            # Should have gotten agent adapter
            assert isinstance(result_adapter, FakeSuccessfulAgentAdapter)
            assert result.success is True

        # Verify NO fallback event was emitted
        events_path = tmp_path / run_id / "events.jsonl"
        if events_path.exists():
            with open(events_path, "r") as f:
                for line in f:
                    event = json.loads(line.strip())
                    assert (
                        event.get("event_type") != "ui_driver_fallback"
                    ), "Should not emit fallback event when agent succeeds"

    def test_no_fallback_event_when_fallback_disabled(self, tmp_path):
        """When fallback is disabled, no fallback event is emitted."""
        run_id = "test_run_003"

        with RunEpisode(run_id=run_id, base_dir=tmp_path) as episode:
            registry = SiteAdapterRegistry()
            registry.register_agent_adapter("test_site", FakeFailingAgentAdapter)
            registry.register_legacy_adapter("test_site", FakeSuccessfulLegacyAdapter)

            mock_browser = MagicMock()
            # Disable fallback
            config = {"ui_driver_mode": "agent", "ui_driver_fallback_to_legacy": False}

            adapter = registry.get_adapter("test_site", config)
            result_adapter, result = registry.bind_with_fallback(
                adapter=adapter,
                browser=mock_browser,
                url="http://example.com",
                origin="AAA",
                dest="BBB",
                depart="2026-03-01",
                return_date="2026-03-08",
                config=config,
            )

            # Should have gotten agent adapter (failure, no fallback)
            assert isinstance(result_adapter, FakeFailingAgentAdapter)
            assert result.success is False

        # Verify NO fallback event was emitted
        events_path = tmp_path / run_id / "events.jsonl"
        if events_path.exists():
            with open(events_path, "r") as f:
                for line in f:
                    event = json.loads(line.strip())
                    assert (
                        event.get("event_type") != "ui_driver_fallback"
                    ), "Should not emit fallback event when fallback is disabled"

    def test_fallback_event_without_active_episode(self):
        """Fallback event emission gracefully handles no active episode."""
        # Should not raise, just log
        emit_ui_driver_fallback_event(
            site_id="test_site",
            from_driver="agent",
            to_driver="legacy",
            reason="test",
        )
        # If we got here without exception, test passes


class TestRunEpisodeContext:
    """Tests for RunEpisode context management."""

    def test_current_episode_context_set_on_enter(self, tmp_path):
        """RunEpisode sets thread-local context on __enter__."""
        assert get_current_episode() is None, "Should start with no context"

        with RunEpisode(run_id="test_ctx", base_dir=tmp_path) as episode:
            assert get_current_episode() is episode, "Context should be set inside with block"

        assert get_current_episode() is None, "Context should be cleared on exit"

    def test_current_episode_context_cleared_on_exit(self, tmp_path):
        """RunEpisode clears thread-local context on __exit__."""
        episode = RunEpisode(run_id="test_ctx", base_dir=tmp_path)

        with episode:
            assert get_current_episode() is episode

        assert get_current_episode() is None, "Context should be cleared after exiting with block"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
