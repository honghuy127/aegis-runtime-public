"""Base service runner abstraction for flight search orchestration.

This module defines the ServiceRunner interface that all service-specific
implementations (Google Flights, Skyscanner, etc.) must conform to.

Each runner encapsulates service-specific logic for:
- Form filling and interaction strategies
- Verification gates and validation rules
- Recovery policies and repair plans
- Deeplink parsing (if supported)
- Locale-aware selector management

Architecture: This follows the SiteAdapter pattern
(docs/kb/00_foundation/architecture_invariants.md § M).
Runners are stateless service logic; browser session is owned by caller.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple
import time


class ServiceRunner(ABC):
    """Abstract base class for service-specific scenario orchestration.

    Each concrete implementation (GoogleFlightsRunner, SkyscannerRunner, etc.)
    provides service-specific behavior for form interaction, verification,
    recovery, and plan generation.

    Core guarantee: Runners are stateless and thread-safe. All state
    is passed via arguments; no runner instance state is persisted.
    """

    @property
    @abstractmethod
    def service_key(self) -> str:
        """Return the canonical service key (e.g., 'google_flights', 'skyscanner')."""
        pass

    # =========================================================================
    # PLAN GENERATION
    # =========================================================================

    @abstractmethod
    def get_default_plan(
        self,
        origin: str,
        dest: str,
        depart: str,
        is_domestic: bool = False,
        knowledge: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Return a heuristic fallback plan when LLM plan generation fails.

        Args:
            origin: IATA code for origin airport
            dest: IATA code for destination airport
            depart: ISO date (YYYY-MM-DD) for departure
            is_domestic: Whether this is a domestic flight
            knowledge: Optional prior knowledge hints

        Returns:
            List of action steps compatible with execute_plan()
            [{"action": "fill", "selector": [...], "value": origin}, ...]
        """
        pass

    # =========================================================================
    # STEP EXECUTION & FORM FILLING
    # =========================================================================

    @abstractmethod
    def apply_step(
        self,
        browser: Any,
        step: Dict[str, Any],
        *,
        site_key: str,
        timeout_ms: Optional[int] = None,
        deadline: Optional[float] = None,
        step_index: int = -1,
        attempt: int = 0,
        turn: int = 0,
        evidence_ctx: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, Optional[str], Dict[str, Any]]:
        """Apply a single step to the browser (fill, click, wait, etc.).

        This is the primary extension point for service-specific form interaction.
        Implementations should handle the step action and return success/failure.

        Args:
            browser: BrowserSession instance
            step: Single action step from plan
                {"action": "fill|click|wait|wait_msec", "selector": [...], ...}
            site_key: Service identifier (e.g., "google_flights")
            timeout_ms: Per-action timeout in milliseconds
            deadline: Absolute deadline (monotonic time) for this step
            step_index: Index of this step in the execution plan
            attempt: Scenario retry attempt number (0-indexed)
            turn: Plan generation turn within attempt (0-indexed)
            evidence_ctx: Evidence collection context

        Returns:
            (success: bool, error_reason: Optional[str], metadata: Dict)
            - success: True if step completed without issue
            - error_reason: Reason code if step failed (e.g., "selector_not_found")
            - metadata: Step-specific metadata (fill_commit, used_selector, etc.)
        """
        pass

    # =========================================================================
    # VERIFICATION GATES
    # =========================================================================

    @abstractmethod
    def verify_after_fill(
        self,
        browser: Any,
        filled_role: str,
        filled_value: str,
        *,
        expected_origin: str = "",
        expected_dest: str = "",
        expected_depart: str = "",
        expected_return: str = "",
        html: str = "",
        page: Optional[Any] = None,
        locale_hint: str = "",
    ) -> Dict[str, Any]:
        """Verify that a form field was filled correctly.

        Service-specific verification logic. For example, Google Flights
        verifies that airline combobox selections are visible; Skyscanner may
        just check that the input field contains the expected value.

        Args:
            browser: BrowserSession instance
            filled_role: Field role that was filled ("origin", "dest", "depart", "return")
            filled_value: Value that was filled
            expected_origin: Expected origin for context
            expected_dest: Expected destination for context
            expected_depart: Expected departure date for context
            expected_return: Expected return date for context
            html: Current visible HTML (for probe-based verification)
            page: Playwright Page object (if available)
            locale_hint: Locale hint for selector/token selection

        Returns:
            {
                "ok": bool,
                "reason": str,  # e.g., "combobox_confirmed", "selector_not_visible"
                "evidence": {...},  # Detailed verification metadata
            }
        """
        pass

    @abstractmethod
    def get_route_core_before_date_gate(
        self,
        html: str,
        page: Optional[Any] = None,
        expected_origin: str = "",
        expected_dest: str = "",
        expected_depart: str = "",
        expected_return: str = "",
    ) -> Dict[str, Any]:
        """Verify origin+dest route binding before attempting date picker.

        Phase A invariant: in deeplink recovery mode, date picker interactions
        are blocked until origin and destination are verifiably rebound.

        Args:
            html: Visible page HTML
            page: Playwright Page object (if available)
            expected_origin: Expected origin IATA code
            expected_dest: Expected destination IATA code
            expected_depart: Expected departure date
            expected_return: Expected return date

        Returns:
            {
                "ok": bool,
                "reason": str,
                "evidence": {...},
                "probe": {...},  # Service-specific probe results
            }
        """
        pass

    # =========================================================================
    # RECOVERY & REPAIR POLICIES
    # =========================================================================

    @abstractmethod
    def get_recovery_limits(self) -> Dict[str, int | bool]:
        """Return bounded recovery policy limits from thresholds.

        Returns:
            {
                "enabled": bool,
                "max_vlm": int,  # max VLM page-kind probe calls
                "max_repair": int,  # max repair policy calls
                "max_planner": int,  # max planner re-invocations
                "route_core_only_first": bool,  # phase B gate
                "planner_timeout_sec": int,
            }
        """
        pass

    @abstractmethod
    def get_force_bind_repair_policy(
        self,
        *,
        enabled: bool,
        uses: int,
        max_per_attempt: int,
        verify_status: str,
        scope_class: str,
        observed_dest_raw: str,
        observed_origin_raw: str = "",
        expected_origin: str = "",
    ) -> Dict[str, Any]:
        """Build forced rebind repair policy for route mismatch recovery.

        Returns:
            {
                "enabled": bool,
                "reason": str,
                "priority": int,
            }
        """
        pass

    @abstractmethod
    def build_recovery_plan(
        self,
        origin: str,
        dest: str,
        depart: str,
        return_date: str = "",
        trip_type: str = "one_way",
        missing_roles: Optional[set] = None,
        soft_fail_fills: bool = True,
    ) -> List[Dict[str, Any]]:
        """Build a recovery plan when primary plan fails.

        Args:
            origin: IATA code for origin
            dest: IATA code for destination
            depart: ISO date for departure
            return_date: ISO date for return (if round trip)
            trip_type: "one_way" or "round_trip"
            missing_roles: Set of form roles that failed to fill
            soft_fail_fills: Whether to use soft-fail fill steps

        Returns:
            List of recovery plan steps compatible with execute_plan()
        """
        pass

    @abstractmethod
    def build_non_flight_scope_repair_plan(
        self,
        origin: str,
        dest: str,
        depart: str,
        return_date: str = "",
        trip_type: str = "one_way",
        is_domestic: bool = False,
        scope_class: str = "unknown",
        vlm_hint: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Build repair plan when page is in non-flight scope (hotel, car, etc).

        Forces flight product/mode context before form fills.

        Returns:
            List of repair plan steps
        """
        pass

    # =========================================================================
    # DEEPLINK SUPPORT (if applicable)
    # =========================================================================

    def parse_deeplink_context(self, url: str) -> Dict[str, Any]:
        """Parse route/date context from service deeplink URL.

        Default: empty dict (service may not support deeplinks).
        Override in service-specific runners (Google Flights does).

        Returns:
            {
                "origin": str,
                "dest": str,
                "depart": str,
                "return_date": str,
            }
        """
        return {}

    def get_deeplink_probe_status(
        self, html: str, url: str
    ) -> Tuple[bool, Optional[str]]:
        """Check if deeplink probe is ready for binding verification.

        Default: (True, None) — no service-specific deeplink logic.

        Returns:
            (ready: bool, reason: Optional[str])
        """
        return True, None

    # =========================================================================
    # SELECTOR & LOCALE MANAGEMENT
    # =========================================================================

    @abstractmethod
    def get_locale_aware_selector(
        self,
        role: str,
        action: str = "fill",
        locale_hint: str = "",
    ) -> List[str]:
        """Return locale-aware selectors for a form field role.

        Args:
            role: Field role ("origin", "dest", "depart", "return", "search_button")
            action: Interaction type ("fill", "click", "wait")
            locale_hint: Locale hint (e.g., "ja-JP", "en-US")

        Returns:
            List of CSS/aria selectors in priority order
        """
        pass

    # =========================================================================
    # THRESHOLD SCOPE
    # =========================================================================

    def get_threshold_scope(self) -> str:
        """Return threshold scope for service-specific threshold lookups.

        Example: "google_flights" to query "<key>_google_flights" thresholds.

        Default: return self.service_key
        """
        return self.service_key
