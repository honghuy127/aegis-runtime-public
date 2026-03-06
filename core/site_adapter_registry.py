"""Site adapter registry - manages UI driver selection and fallback logic.

ARCHITECTURE INVARIANT (INV-REGISTRY-001):
- Registry is the single source of truth for driver selection per site.
- Ensures agent-first default with safe fallback to legacy.
- No duplicate UIs are activated; selection is mutually exclusive.
- Config-driven (vs. hardcoded env vars) for operational flexibility.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from core.site_adapter import SiteAdapter, SiteAdapterBindResult


log = logging.getLogger(__name__)


class SiteAdapterRegistry:
    """Registry of site adapters for agent and legacy drivers.

    BEHAVIOR (INV-REGISTRY-002):
    - get_adapter() returns agent if config.ui_driver_mode=='agent' AND agent available
    - If agent missing or bind fails, fallback to legacy if config.ui_driver_fallback_to_legacy=true
    - If fallback disabled, raise clear error
    - Per-site overrides via config.ui_driver_overrides={'site_id': 'legacy'|'agent'}
    """

    def __init__(self):
        """Initialize registry with agent and legacy adapters."""
        self._agent_adapters: Dict[str, type[SiteAdapter]] = {}
        self._legacy_adapters: Dict[str, type[SiteAdapter]] = {}
        self._log_fallback_events = True

    def register_agent_adapter(self, site_id: str, adapter_class: type[SiteAdapter]) -> None:
        """Register an agent-mode adapter for a site."""
        self._agent_adapters[site_id] = adapter_class
        log.debug("siteadapter.register site=%s mode=agent", site_id)

    def register_legacy_adapter(self, site_id: str, adapter_class: type[SiteAdapter]) -> None:
        """Register a legacy-mode adapter for a site."""
        self._legacy_adapters[site_id] = adapter_class
        log.debug("siteadapter.register site=%s mode=legacy", site_id)

    def get_adapter(
        self,
        site_id: str,
        config: Dict[str, Any],
    ) -> SiteAdapter:
        """Get the selected UI driver adapter for a site.

        Selection logic:
        1. Check per-site override in config.ui_driver_overrides
        2. If no override, use config.ui_driver_mode (default: 'agent')
        3. Try to instantiate adapter
        4. If agent and bind fails at call time, fallback to legacy if enabled

        Args:
            site_id: Site identifier (e.g., 'google_flights')
            config: Run config dict with keys:
                - ui_driver_mode: 'agent' | 'legacy' (default: 'agent')
                - ui_driver_overrides: {'site_id': 'agent'|'legacy'} (default: {})
                - ui_driver_fallback_to_legacy: bool (default: True)

        Returns:
            Instantiated SiteAdapter ready to use.

        Raises:
            ValueError: If site_id not registered or selection fails with fallback disabled.

        INVARIANT (INV-REGISTRY-003):
        - If agent selected but not available, check fallback_to_legacy
        - If fallback enabled, silently switch to legacy on bind failure
        - If fallback disabled, raise with clear diagnostic
        """
        # 1. Resolve intended mode
        overrides = config.get("ui_driver_overrides") or {}
        intended_mode = overrides.get(site_id) or config.get("ui_driver_mode") or "agent"
        fallback_enabled = config.get("ui_driver_fallback_to_legacy", True)

        # 2. Try to get adapter for intended mode
        adapter_class = None
        if intended_mode == "agent":
            adapter_class = self._agent_adapters.get(site_id)
        elif intended_mode == "legacy":
            adapter_class = self._legacy_adapters.get(site_id)
        else:
            raise ValueError(f"Invalid ui_driver_mode: {intended_mode}")

        if adapter_class is not None:
            log.info(
                "siteadapter.selected site=%s mode=%s source=%s",
                site_id,
                intended_mode,
                "override" if site_id in overrides else "config",
            )
            return adapter_class()

        # 3. Adapter not available; attempt fallback
        fallback_class = None
        if intended_mode == "agent" and fallback_enabled:
            fallback_class = self._legacy_adapters.get(site_id)
            if fallback_class:
                log.warning(
                    "siteadapter.fallback site=%s from=agent to=legacy reason=agent_not_registered",
                    site_id,
                )
                return fallback_class()

        # 4. No adapter found
        raise ValueError(
            f"No UI driver adapter registered for site={site_id} mode={intended_mode} "
            f"(fallback_to_legacy={fallback_enabled})"
        )

    def bind_with_fallback(
        self,
        adapter: SiteAdapter,
        browser: Any,
        url: str,
        origin: str,
        dest: str,
        depart: str,
        return_date: Optional[str],
        config: Dict[str, Any],
    ) -> tuple[SiteAdapter, SiteAdapterBindResult]:
        """Attempt to bind adapter, with runtime fallback to legacy if configured.

        This is called at scenario start to bind the UI driver to the browser.
        If the selected adapter (e.g., agent) fails to bind, this may fallback
        to legacy if allowed by config.

        Args:
            adapter: Selected adapter instance
            browser: BrowserSession
            url, origin, dest, depart, return_date: Trip parameters
            config: Run config

        Returns:
            (final_adapter, bind_result)
            - final_adapter: The adapter that was bound (may differ if fallback occurred)
            - bind_result: Result of binding the final adapter

        INVARIANT (INV-REGISTRY-004):
        - If adapter.bind_route() fails and fallback enabled, try legacy
        - Only ONE adapter is bound; never run both
        - Fallback is logged as event: ui_driver.fallback
        """
        site_id = adapter.site_id
        result = adapter.bind_route(
            browser=browser,
            url=url,
            origin=origin,
            dest=dest,
            depart=depart,
            return_date=return_date,
        )

        if result.success:
            return adapter, result

        # Bind failed; attempt fallback
        fallback_enabled = config.get("ui_driver_fallback_to_legacy", True)
        if fallback_enabled and hasattr(adapter, "site_id"):
            # Try to get a different adapter (legacy if we're on agent)
            try:
                # Check if current adapter is agent-mode by isinstance check
                # (not identity, which would fail since we create new instances)
                is_agent_adapter = (
                    site_id in self._agent_adapters
                    and isinstance(adapter, self._agent_adapters[site_id])
                )
                if is_agent_adapter:
                    fallback_class = self._legacy_adapters.get(site_id)
                    if fallback_class:
                        fallback_adapter = fallback_class()
                        fallback_result = fallback_adapter.bind_route(
                            browser=browser,
                            url=url,
                            origin=origin,
                            dest=dest,
                            depart=depart,
                            return_date=return_date,
                        )
                        log.warning(
                            "ui_driver.fallback site=%s from=agent to=legacy "
                            "reason=%s error_class=%s",
                            site_id,
                            result.reason or "bind_failed",
                            result.error_class or "unknown",
                        )
                        # Emit structured fallback event for observability
                        from utils.run_episode import emit_ui_driver_fallback_event
                        emit_ui_driver_fallback_event(
                            site_id=site_id,
                            from_driver="agent",
                            to_driver="legacy",
                            reason=result.reason or "bind_failed",
                        )
                        return fallback_adapter, fallback_result
                    return adapter, SiteAdapterBindResult(
                        success=False,
                        reason="no_legacy_driver",
                        error_class=result.error_class,
                        error_message=result.error_message,
                        evidence={
                            "fallback_requested": True,
                            "site_id": site_id,
                            "prior_reason": result.reason or "bind_failed",
                        },
                    )
            except Exception as exc:
                log.exception("siteadapter.fallback_attempt_exception: %s", exc)

        return adapter, result


# Global registry instance
_global_registry = SiteAdapterRegistry()


def get_global_registry() -> SiteAdapterRegistry:
    """Get the global site adapter registry."""
    return _global_registry


def register_agent_adapter(site_id: str, adapter_class: type[SiteAdapter]) -> None:
    """Module-level function to register agent adapter."""
    _global_registry.register_agent_adapter(site_id, adapter_class)


def register_legacy_adapter(site_id: str, adapter_class: type[SiteAdapter]) -> None:
    """Module-level function to register legacy adapter."""
    _global_registry.register_legacy_adapter(site_id, adapter_class)


def get_adapter(site_id: str, config: Dict[str, Any]) -> SiteAdapter:
    """Module-level function to get adapter with config-driven selection."""
    return _global_registry.get_adapter(site_id, config)
