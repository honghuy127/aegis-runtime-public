"""Skyscanner-specific scenario runner logic.

This module contains Skyscanner-specific plan generation, selector banks,
and readiness detection logic that was previously in core/scenario_runner.py.

Public API:
  - default_skyscanner_plan: Generate fallback plan for Skyscanner.
  - detect_skyscanner_interstitial_block: Detect Skyscanner captcha pages.
  - attempt_skyscanner_interstitial_grace: Handle transient captcha pages.
  - attempt_skyscanner_interstitial_fallback_reload: Fallback reload with optimized headers.
"""

from core.scenario_runner.skyscanner.plans import (
    default_skyscanner_plan,
)
from core.scenario_runner.skyscanner.interstitials import (
    detect_skyscanner_interstitial_block,
    attempt_skyscanner_interstitial_grace,
    attempt_skyscanner_interstitial_fallback_reload,
)
from core.scenario_runner.skyscanner.ui_actions import (
    _skyscanner_fill_date_via_picker,
    _skyscanner_fill_and_commit_location,
    _skyscanner_search_click_selectors,
    _skyscanner_dismiss_results_overlay,
    _ensure_skyscanner_flights_context,
)
from core.scenario_runner.skyscanner.url_binding import (
    _is_skyscanner_date_value_already_bound_from_url,
    _is_skyscanner_route_value_already_bound_from_url,
)

__all__ = [
    "default_skyscanner_plan",
    "detect_skyscanner_interstitial_block",
    "attempt_skyscanner_interstitial_grace",
    "attempt_skyscanner_interstitial_fallback_reload",
    "_skyscanner_fill_date_via_picker",
    "_skyscanner_fill_and_commit_location",
    "_skyscanner_search_click_selectors",
    "_skyscanner_dismiss_results_overlay",
    "_ensure_skyscanner_flights_context",
    "_is_skyscanner_route_value_already_bound_from_url",
    "_is_skyscanner_date_value_already_bound_from_url",
]
