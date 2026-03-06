"""Google Flights adapter implementations - agent and legacy drivers.

Concrete implementations of SiteAdapter interface that wrap existing
agent engine and legacy scenario flow. Enables config-driven driver selection.

ARCHITECTURE USAGE:
- Agent driver: Uses GoogleFlightsPlugin + AgentEngine (core.agent.engine)
- Legacy driver: Uses legacy scenario helpers (core.scenario.google_flights)
- Extraction: Shared via core.plugins.services.google_flights (no driver awareness)
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from core.site_adapter import (
    SiteAdapter,
    SiteAdapterBindResult,
    SiteAdapterReadinessResult,
)


class GoogleFlightsAgentAdapter(SiteAdapter):
    """Agent-based UI driver for Google Flights using agentic framework.

    Wraps GoogleFlightsPlugin + AgentEngine for modern agentic execution.
    """

    site_id = "google_flights"

    def __init__(self):
        """Initialize agent adapter."""
        self._plugin = None
        self._engine = None
        self._last_html = None
        self._ready = False

    def bind_route(
        self,
        browser: Any,
        url: str,
        origin: str,
        dest: str,
        depart: str,
        return_date: Optional[str] = None,
        **kwargs,
    ) -> SiteAdapterBindResult:
        """Bind agent framework to browser session.

        Attempts to navigate URL and initialize agent engine for interaction.
        """
        try:
            # Navigate to URL
            browser.goto(url)
            self._last_html = browser.content()

            # Initialize plugin and engine
            from core.agent.engine import AgentEngine
            from core.agent.plugins.google_flights.plugin import GoogleFlightsPlugin
            from core.agent.plugins.base import RunContext

            self._plugin = GoogleFlightsPlugin()
            self._engine = AgentEngine(self._plugin, log=None)

            # Store context for later binding
            self._bind_context = RunContext(
                site_key=self.site_id,
                url=url,
                locale=kwargs.get("mimic_locale", "ja-JP"),
                region=kwargs.get("mimic_region", ""),
                currency=kwargs.get("mimic_currency", ""),
                is_domestic=kwargs.get("is_domestic", False),
                inputs={
                    "origin": origin,
                    "dest": dest,
                    "depart": depart,
                    "return_date": return_date or "",
                },
            )

            return SiteAdapterBindResult(success=True)

        except Exception as exc:
            return SiteAdapterBindResult(
                success=False,
                reason="agent_bind_failed",
                error_class=type(exc).__name__,
                error_message=str(exc)[:200],
            )

    def ensure_results_ready(
        self,
        browser: Any,
        html: Optional[str] = None,
    ) -> SiteAdapterReadinessResult:
        """Check if agent achieved readiness (results available).

        Runs agent engine turns and checks plugin readiness predicate.
        """
        if not self._engine or not self._plugin:
            return SiteAdapterReadinessResult(
                ready=False,
                reason="engine_not_initialized",
            )

        try:
            # Run up to 3 turns
            html_current = html or self._last_html or ""
            for turn in range(3):
                html_current, obs, _ = self._engine.run_once(
                    browser,
                    html_current,
                    self._bind_context,
                )
                self._last_html = html_current or self._last_html

                if self._plugin.readiness(obs, self._bind_context):
                    self._ready = True
                    return SiteAdapterReadinessResult(ready=True)

            return SiteAdapterReadinessResult(
                ready=False,
                reason="agent_turns_exhausted",
            )

        except Exception as exc:
            return SiteAdapterReadinessResult(
                ready=False,
                reason=f"agent_error_{type(exc).__name__}",
            )

    def capture_artifacts(self) -> Dict[str, Any]:
        """Capture agent artifacts (diagnost info)."""
        return {
            "last_html_available": self._last_html is not None,
            "ready": self._ready,
        }


class GoogleFlightsLegacyAdapter(SiteAdapter):
    """Legacy-based UI driver for Google Flights.

    Wraps existing scenario helpers from core/scenario/google_flights.py
    for fallback compatibility.
    """

    site_id = "google_flights"

    def __init__(self):
        """Initialize legacy adapter."""
        self._bound = False
        self._url = None
        self._last_html = None

    def bind_route(
        self,
        browser: Any,
        url: str,
        origin: str,
        dest: str,
        depart: str,
        return_date: Optional[str] = None,
        **kwargs,
    ) -> SiteAdapterBindResult:
        """Bind legacy scenario flow to browser session.

        Simply navigates URL (legacy interactions happen in ensure_results_ready).
        """
        try:
            browser.goto(url)
            self._last_html = browser.content()
            self._url = url
            self._bound = True

            return SiteAdapterBindResult(success=True)

        except Exception as exc:
            return SiteAdapterBindResult(
                success=False,
                reason="legacy_goto_failed",
                error_class=type(exc).__name__,
                error_message=str(exc)[:200],
            )

    def ensure_results_ready(
        self,
        browser: Any,
        html: Optional[str] = None,
    ) -> SiteAdapterReadinessResult:
        """Check if legacy flow achieved readiness.

        For legacy, this would invoke the existing scenario runner logic.
        For now, returns not-ready to defer to scenario_runner fallback path.
        """
        if not self._bound:
            return SiteAdapterReadinessResult(
                ready=False,
                reason="not_bound",
            )

        self._last_html = html or browser.content()

        # Legacy flow is orchestrated by scenario_runner itself
        # This adapter doesn't perform interactions; it's a shim
        return SiteAdapterReadinessResult(
            ready=False,
            reason="legacy_defers_to_scenario_runner",
        )

    def capture_artifacts(self) -> Dict[str, Any]:
        """Capture legacy artifacts."""
        return {
            "url": self._url,
            "last_html_available": self._last_html is not None,
        }
