"""Skyscanner-specific plan generation and presets."""

from typing import Any, Dict, List
from utils.thresholds import get_threshold
from core.scenario_runner.google_flights.service_runner_bridge import (
    _maybe_append_bare_text_selectors,
    _allow_bare_text_fallback,
)


def default_skyscanner_plan(
    origin: str,
    dest: str,
    depart: str,
    return_date: str = "",
) -> List[Dict[str, Any]]:
    """Return a heuristic fallback plan for Skyscanner with activation stability.

    Args:
        origin: Origin airport code or name.
        dest: Destination airport code or name.
        depart: Departure date string.

    Returns:
        List of action steps (fill, click, wait) to search for flights.
    """
    search_selectors = _maybe_append_bare_text_selectors(
        [
            "button:has-text('検索')",
            "button[aria-label*='検索']",
            "button[aria-label*='Search']",
            "button[data-testid*='search']",
            "button[type='submit']",
        ],
        ["検索", "Search"],
        allow=_allow_bare_text_fallback(),
    )
    origin_fill_selectors = [
        "input#originInput-input",
        "input[name='originInput-search']",
        "input[id='originInput-input'][role='combobox']",
        "input[placeholder*='From']",
        "input[aria-label*='From']",
        "input[placeholder*='Leaving']",
        "input[aria-label*='Leaving']",
        "input[placeholder*='出発']",
        "input[aria-label*='出発']",
        "input[name*='origin']",
        "input[name*='from']",
        "input[id*='origin']",
        "[data-testid*='origin'] input",
    ]
    dest_fill_selectors = [
        "input#destinationInput-input",
        "input[name='destinationInput-search']",
        "input[id='destinationInput-input'][role='combobox']",
        "input[placeholder*='To']",
        "input[aria-label*='To']",
        "input[placeholder*='Going']",
        "input[aria-label*='Going']",
        "input[placeholder*='目的地']",
        "input[aria-label*='目的地']",
        "input[name*='destination']",
        "input[name*='to']",
        "input[id*='destination']",
        "[data-testid*='destination'] input",
    ]
    depart_fill_selectors = [
        "button[data-testid='depart-btn']",
        "[data-testid='depart-btn'] button",
        "button:has(span:has-text('出発'))",
        "button:has-text('出発')",
        "button[aria-label*='Depart']",
        "button[aria-label*='出発']",
    ]
    return_fill_selectors = [
        "button[data-testid='return-btn']",
        "[data-testid='return-btn'] button",
        "button:has(span:has-text('復路'))",
        "button:has-text('復路')",
        "button[aria-label*='Return']",
        "button[aria-label*='復路']",
    ]
    # Activation visibility timeout: wait for search controls to be ready
    activation_visibility_timeout = int(
        get_threshold("skyscanner_activation_visibility_timeout_ms", 3000)
    )
    # Post-activation wait: allow results to begin rendering
    post_activation_wait = int(
        get_threshold("skyscanner_post_activation_wait_ms", 5000)
    )
    # Results readiness: full results page visibility
    results_readiness_timeout = int(
        get_threshold("skyscanner_results_readiness_timeout_ms", 8000)
    )

    plan: List[Dict[str, Any]] = [
        {
            "action": "fill",
            "selector": origin_fill_selectors,
            "value": origin,
            "role": "origin",
            "metadata": {"purpose": "origin_fill"},
        },
        {
            "action": "fill",
            "selector": dest_fill_selectors,
            "value": dest,
            "role": "dest",
            "metadata": {"purpose": "destination_fill"},
        },
        {
            "action": "fill",
            "selector": depart_fill_selectors,
            "value": depart,
            "role": "depart",
            "metadata": {"purpose": "depart_fill"},
        },
        # Phase 4: Visibility wait before search activation
        {
            "action": "wait",
            "selector": search_selectors,
            "timeout_ms": activation_visibility_timeout,
            "metadata": {
                "purpose": "ensure_search_button_visible_phase4",
                "retry_on_fail": False,
            },
        },
        # Phase 4: Activate search
        {
            "action": "click",
            "selector": search_selectors,
        },
        # Phase 4: Post-activation pause for results to begin rendering
        {
            "action": "wait_msec",
            "duration_ms": post_activation_wait,
            "metadata": {"purpose": "render_settle_phase4"},
        },
        # Phase 4: Wait for results readiness with longer timeout
        {
            "action": "wait",
            "selector": [
                "[data-testid*='search-results']",
                "[data-testid*='itinerary']",
                "[data-testid*='day-view']",
                "main [role='main']",
                "[role='main']",
                "body",
            ],
            "timeout_ms": results_readiness_timeout,
            "metadata": {
                "purpose": "results_readiness_phase4",
                "retry_label": "skyscanner_results_not_loaded",
            },
        },
    ]

    if str(return_date or "").strip():
        plan.insert(
            3,
            {
                "action": "fill",
                "selector": return_fill_selectors,
                "value": str(return_date),
                "role": "return",
                "optional": True,
                "metadata": {"purpose": "return_fill"},
            },
        )

    return plan
