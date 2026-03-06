"""Selector probing, prioritization, and fallback helpers."""

# Re-export for convenience
from core.scenario_runner.selectors.probes import (
    _check_selector_visibility,
    _visible_selector_subset,
    _selector_probe_css_compatible,
    _compact_selector_dom_probe,
    _selector_blob,
    _contains_selector_word,
    _is_clickable_selector_candidate,
    _safe_click_first_match,
    _looks_non_fillable_selector_blob,
)
from core.scenario_runner.selectors.priority import (
    _fill_selector_priority,
    _prioritize_fill_selectors,
    _filter_blocked_selectors,
    _prepend_ranked_selectors,
    _reorder_search_selectors_for_locale,
)
from core.scenario_runner.selectors.fallbacks import (
    get_selector_hints,  # Re-export for test compatibility
    _selectors_look_search_submit,
    _selectors_look_domain_toggle,
    _selector_hints_overlay,
    _service_search_click_fallbacks,
    _service_fill_fallbacks,
    _service_wait_fallbacks,
)

__all__ = [
    # probes
    "_check_selector_visibility",
    "_visible_selector_subset",
    "_selector_probe_css_compatible",
    "_compact_selector_dom_probe",
    "_selector_blob",
    "_contains_selector_word",
    "_is_clickable_selector_candidate",
    "_safe_click_first_match",
    "_looks_non_fillable_selector_blob",
    # priority
    "_fill_selector_priority",
    "_prioritize_fill_selectors",
    "_filter_blocked_selectors",
    "_prepend_ranked_selectors",
    "_reorder_search_selectors_for_locale",
    # fallbacks
    "get_selector_hints",  # For test compatibility
    "_selectors_look_search_submit",
    "_selectors_look_domain_toggle",
    "_selector_hints_overlay",
    "_service_search_click_fallbacks",
    "_service_fill_fallbacks",
    "_service_wait_fallbacks",
]
