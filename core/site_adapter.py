"""Site adapter interface - unified abstraction for UI driver selection.

Defines the minimal interface that all site-specific UI drivers MUST implement,
whether they use the agent framework or legacy scenario flow.

ARCHITECTURE INVARIANT (INV-ADAPTER-001):
- Each site has exactly ONE active UI driver per run (agent OR legacy, never both).
- Preferred default: agent driver (if available).
- Fallback: legacy driver (if enabled in config).
- Selection is made at bind time; cannot switch drivers mid-execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class SiteAdapterBindResult:
    """Result of attempting to bind a UI driver to a browser session."""

    success: bool
    """True if bind succeeded, False if bind failed or adapter unavailable."""

    reason: Optional[str] = None
    """Diagnostic reason if bind failed (e.g., 'agent_not_available', 'agent_bind_failed')."""

    error_class: Optional[str] = None
    """Exception class name if bind threw (e.g., 'AttributeError', 'TimeoutError')."""

    error_message: Optional[str] = None
    """Trimmed exception message (first 200 chars) for diagnostics."""

    evidence: Optional[Dict[str, Any]] = None
    """Optional structured evidence payload for bind failures/fallback decisions."""


@dataclass
class SiteAdapterReadinessResult:
    """Result of checking if UI interactions are complete."""

    ready: bool
    """True if search results are ready and extraction can proceed."""

    reason: Optional[str] = None
    """Diagnostic reason if not ready (e.g., 'form_pending', 'results_loading')."""

    evidence: Optional[Dict[str, Any]] = None
    """Optional structured evidence payload for readiness/extraction decisions."""


class SiteAdapter:
    """Minimal interface for site-specific UI drivers.

    INVARIANT (INV-ADAPTER-002):
    - MUST NOT import or call browser automation directly in __init__.
    - Site logic must be initialized on bind_route(), not in constructor.
    - Adapters MUST be stateless or have minimal state (url, origin, dest).
    - Every method MUST handle exceptions gracefully and return structured results.

    OWNERSHIP (INV-ADAPTER-003):
    - UI interaction: Agent adapter or legacy scenario
    - Extraction/parsing: core/plugins/services/<site>.py ONLY
    - Shared selectors/heuristics: utils/* or docs/kb/30_patterns/*
    """

    site_id: str = "unknown"
    """Unique site identifier (e.g., 'google_flights', 'skyscanner')."""

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
        """Bind UI driver to browser session and navigate to URL.

        Called once per scenario run, before any interactions.
        This is where the UI driver initializes and validates it can proceed.

        Args:
            browser: BrowserSession instance (has .goto(), .content(), etc.)
            url: URL to navigate to
            origin: Trip origin IATA code
            dest: Trip destination IATA code
            depart: Departure date (YYYY-MM-DD)
            return_date: Optional return date (YYYY-MM-DD)
            **kwargs: Additional driver-specific context (locale, region, etc.)

        Returns:
            SiteAdapterBindResult with success flag and diagnostic reason if failed.

        INVARIANT (INV-ADAPTER-004):
        - MUST return a result object (never raise to caller).
        - If browser.goto() fails, result.success = False, result.reason set.
        - If browser is unreachable, result.success = False (don't propagate).
        """
        raise NotImplementedError

    def ensure_results_ready(
        self,
        browser: Any,
        html: Optional[str] = None,
    ) -> SiteAdapterReadinessResult:
        """Check if search results are ready for extraction.

        Called during scenario execution to determine if form interactions
        are complete and results page is available.

        Args:
            browser: BrowserSession instance (optional, adapter may use stored state)
            html: Optional fresh HTML snapshot (adapter can use or fetch own)

        Returns:
            SiteAdapterReadinessResult with ready flag and reason.

        INVARIANT (INV-ADAPTER-005):
        - MUST NOT throw exceptions; return ready=False with reason instead.
        - MUST NOT attempt retries internally; use result.ready to signal need.
        """
        raise NotImplementedError

    def capture_artifacts(self) -> Dict[str, Any]:
        """Capture debug artifacts (screenshots, HTML snapshots, etc.).

        Called when scenario completes (success or failure) to gather evidence.

        Returns:
            Dict with optional fields:
            - html_path: str (path to saved HTML file)
            - screenshot_path: str (path to saved screenshot)
            - evidence: dict (custom diagnostic data)

        INVARIANT (INV-ADAPTER-006):
        - MUST NOT throw; return empty dict {} on errors.
        - Paths should be relative to storage/ root if possible.
        """
        return {}
