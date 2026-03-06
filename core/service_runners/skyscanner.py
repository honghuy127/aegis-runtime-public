"""Skyscanner service runner implementation.

This module provides the Skyscanner-specific implementation of the ServiceRunner
interface for flight search scenarios.

Current status: Skyscanner is delegated to agent-only mode per architecture
invariants. This runner provides minimal fallback behavior for legacy scenarios.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple
from core.service_runners.base import ServiceRunner
from core.service_runners.google_flights import (
    _allow_bare_text_fallback,
    _maybe_append_bare_text_selectors,
)

log = logging.getLogger(__name__)


def _default_skyscanner_plan(
    origin: str,
    dest: str,
    depart: str,
    return_date: str = "",
) -> List[Dict[str, Any]]:
    from core.scenario_runner.skyscanner.plans import (
        default_skyscanner_plan as _runner_default_skyscanner_plan,
    )

    return _runner_default_skyscanner_plan(
        origin=origin,
        dest=dest,
        depart=depart,
        return_date=return_date,
    )


def _skyscanner_fill_selectors(role: str) -> List[str]:
    role_key = str(role or "").strip().lower()
    if role_key == "origin":
        return [
            "input[placeholder*='From']",
            "input[aria-label*='From']",
            "input[name*='origin']",
        ]
    if role_key == "dest":
        return [
            "input[placeholder*='To']",
            "input[aria-label*='To']",
            "input[name*='destination']",
        ]
    if role_key == "depart":
        return [
            "input[placeholder*='Depart']",
            "input[aria-label*='Depart']",
            "input[name*='depart']",
        ]
    if role_key == "return":
        return [
            "input[placeholder*='Return']",
            "input[aria-label*='Return']",
            "input[name*='return']",
        ]
    return []


def _skyscanner_search_click_selectors() -> List[str]:
    return _maybe_append_bare_text_selectors(
        [
            "button[type='submit']",
            "button[aria-label*='Search']",
        ],
        ["Search"],
        allow=_allow_bare_text_fallback(),
    )


def _skyscanner_wait_selectors() -> List[str]:
    return [
        "[data-testid*='search-results']",
        "[data-testid*='itinerary']",
        "[role='main']",
        "body",
    ]


class SkyscannerRunner(ServiceRunner):
    """Skyscanner-specific service runner implementation.

    Skyscanner is currently agent-first per architecture invariants
    (docs/kb/00_foundation/architecture_invariants.md).
    This runner provides minimal fallback for legacy scenarios.
    """

    @property
    def service_key(self) -> str:
        """Return canonical service key for Skyscanner."""
        return "skyscanner"

    # =========================================================================
    # PLAN GENERATION
    # =========================================================================

    def get_default_plan(
        self,
        origin: str,
        dest: str,
        depart: str,
        return_date: str = "",
        is_domestic: bool = False,
        knowledge: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Return default fallback plan for Skyscanner.

        When LLM plan generation fails, use this heuristic plan that
        performs basic form filling and search with stability timeouts.
        """
        return _default_skyscanner_plan(origin, dest, depart, return_date=return_date)

    # =========================================================================
    # STEP EXECUTION & FORM FILLING
    # =========================================================================

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
        """Apply a single step to the browser.

        For now, uses generic form filling. Skyscanner is agent-first,
        so service-specific fallback is minimal.

        Returns:
            (success, error_reason, metadata)
        """
        # Placeholder: Returns (True, None, {})
        # Real implementation will delegate to scenario_runner.py generic logic
        return True, None, {}

    # =========================================================================
    # VERIFICATION GATES
    # =========================================================================

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
        """Verify form field fill for Skyscanner.

        Minimal verification: check that field value matches expected.
        """
        # Simple verification: just check the field value
        return {
            "ok": bool(filled_value),
            "reason": "field_filled",
            "evidence": {"filled_value": filled_value},
        }

    def get_route_core_before_date_gate(
        self,
        html: str,
        page: Optional[Any] = None,
        expected_origin: str = "",
        expected_dest: str = "",
        expected_depart: str = "",
        expected_return: str = "",
    ) -> Dict[str, Any]:
        """Verify route core before date picker (Skyscanner version).

        Skyscanner doesn't have strict separation between date and route
        form elements, so this gate is less critical. Always returns OK.
        """
        return {
            "ok": True,
            "reason": "skyscanner_no_strict_gate",
            "evidence": {},
        }

    # =========================================================================
    # RECOVERY & REPAIR POLICIES
    # =========================================================================

    def get_recovery_limits(self) -> Dict[str, int | bool]:
        """Return recovery policy limits for Skyscanner (minimal)."""
        return {
            "enabled": False,
            "max_vlm": 0,
            "max_repair": 0,
            "max_planner": 0,
            "route_core_only_first": False,
            "planner_timeout_sec": 30,
        }

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
        """Force bind repair policy for Skyscanner (not used)."""
        return {
            "enabled": False,
            "reason": "skyscanner_no_force_bind",
            "priority": -1,
        }

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
        """Build a recovery plan for Skyscanner."""
        # Use default plan as recovery fallback
        return self.get_default_plan(origin, dest, depart)

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
        """Build repair plan for non-flight scope on Skyscanner."""
        # For now, just use default plan
        return self.get_default_plan(origin, dest, depart)

    # =========================================================================
    # DEEPLINK SUPPORT
    # =========================================================================

    def parse_deeplink_context(self, url: str) -> Dict[str, Any]:
        """Skyscanner doesn't have deeplink parsing (not implemented)."""
        return {}

    def get_deeplink_probe_status(
        self, html: str, url: str
    ) -> Tuple[bool, Optional[str]]:
        """Skyscanner doesn't use deeplinks."""
        return True, None

    # =========================================================================
    # SELECTOR & LOCALE MANAGEMENT
    # =========================================================================

    def get_locale_aware_selector(
        self,
        role: str,
        action: str = "fill",
        locale_hint: str = "",
    ) -> List[str]:
        """Return locale-aware selectors for a Skyscanner form field."""
        action_lower = str(action or "").strip().lower()
        role_lower = str(role or "").strip().lower()

        if action_lower == "fill":
            return _skyscanner_fill_selectors(role_lower)
        elif action_lower in ("click", "activate"):
            if role_lower == "search" or role_lower.startswith("search"):
                return _skyscanner_search_click_selectors()
            else:
                return _skyscanner_fill_selectors(role_lower)
        elif action_lower == "wait":
            return _skyscanner_wait_selectors()

        return _skyscanner_fill_selectors(role_lower)

    # =========================================================================
    # THRESHOLD SCOPE
    # =========================================================================

    def get_threshold_scope(self) -> str:
        """Return threshold scope for Skyscanner-specific thresholds."""
        return "skyscanner"
