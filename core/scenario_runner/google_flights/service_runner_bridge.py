"""Bridge exports for Google Flights legacy functions.

This keeps orchestrator modules importing via ``core.scenario_runner.*`` paths while
legacy implementations remain in ``core.service_runners.google_flights`` during migration.
"""

from typing import Any, Dict, List, Optional, Tuple

from core.service_runners.google_flights import (
    _allow_bare_text_fallback,
    _build_click_selectors_for_tokens,
    _contains_any_token,
    _dedupe_selectors,
    _default_google_flights_plan,
    _env_bool,
    _env_int,
    _env_list,
    _google_date_done_selectors,
    _maybe_append_bare_text_selectors,
    _google_date_open_selector_hint_is_plausible,
    _google_date_tokens,
    _google_default_date_reference_year,
    _google_deeplink_page_state_recovery_policy,
    _google_deeplink_probe_status,
    _google_deeplink_recovery_plan,
    _google_display_locale_hint_from_browser,
    _google_display_locale_hint_from_url,
    _is_google_flights_deeplink,
    _google_flights_after_search_ready,
    _google_force_bind_dest_selectors,
    _google_force_bind_flights_tab_selectors,
    _google_force_bind_location_input_selectors,
    _google_form_text_looks_date_like,
    _google_form_text_looks_instructional_noise,
    _google_has_contextual_price_card,
    _google_has_iata_token,
    _google_has_results_shell_for_context,
    _google_missing_roles_from_reason,
    _google_non_flight_scope_repair_plan,
    _google_quick_page_class,
    _google_recovery_collab_limits_from_thresholds,
    _google_results_itinerary_matches_expected,
    _google_role_i18n_token_bank,
    _google_role_tokens,
    _google_route_activation_selectors,
    _google_route_alias_tokens,
    _google_route_context_matches,
    _google_route_core_only_recovery_plan,
    _google_route_fill_input_selector_hint_is_plausible,
    _google_route_reset_selectors,
    _google_search_selector_hint_is_plausible,
    _google_selector_locale_markers,
    _google_should_suppress_force_bind_after_date_failure,
    _google_step_trace_local_date_open_failure,
    _google_step_trace_route_fill_roles_ok,
    _label_click_selectors,
    _normalize_google_form_date_text,
    _parse_google_deeplink_context,
    _selector_candidates,
    _service_fill_activation_clicks,
    _service_fill_activation_keywords,
    _strip_nonvisible_html,
    _verification_confidence_rank,
)


def service_fill_fallbacks(site_key: str, role: str) -> List[str]:
    """Public bridge wrapper for service fill selector fallbacks."""
    from core.scenario_runner.selectors.fallbacks import _service_fill_fallbacks

    return _service_fill_fallbacks(site_key, role)


def service_search_click_fallbacks(site_key: str) -> List[str]:
    """Public bridge wrapper for service search click selector fallbacks."""
    from core.scenario_runner.selectors.fallbacks import _service_search_click_fallbacks

    return _service_search_click_fallbacks(site_key)


def service_wait_fallbacks(site_key: str) -> List[str]:
    """Public bridge wrapper for service wait selector fallbacks."""
    from core.scenario_runner.selectors.fallbacks import _service_wait_fallbacks

    return _service_wait_fallbacks(site_key)


def google_route_core_before_date_gate(
    *,
    html: str,
    page: Optional[Any] = None,
    expected_origin: str = "",
    expected_dest: str = "",
    expected_depart: str = "",
    expected_return: str = "",
) -> Dict[str, Any]:
    """Public bridge wrapper for Google route-core-before-date verification gate."""
    from core.scenario_runner.google_flights.route_bind import (
        _google_route_core_before_date_gate,
    )

    return _google_route_core_before_date_gate(
        html=html,
        page=page,
        expected_origin=expected_origin,
        expected_dest=expected_dest,
        expected_depart=expected_depart,
        expected_return=expected_return,
    )


def should_attempt_google_route_mismatch_reset(
    *,
    mismatch_detected: bool,
    enabled: bool,
    attempts: int,
    max_attempts: int,
) -> bool:
    """Public bridge wrapper for mismatch-reset policy gate."""
    from core.scenario_runner.google_flights.route_recovery import (
        should_attempt_google_route_mismatch_reset as _impl,
    )

    return _impl(
        mismatch_detected=mismatch_detected,
        enabled=enabled,
        attempts=attempts,
        max_attempts=max_attempts,
    )


def google_activate_route_form_recovery(
    browser,
    *,
    deeplink_url: str = "",
    locale_hint: str = "",
    action_timeout_ms: Optional[int] = None,
    settle_wait_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """Public bridge wrapper for route-form activation recovery."""
    from core.scenario_runner.google_flights.route_recovery import (
        google_activate_route_form_recovery as _impl,
    )

    return _impl(
        browser,
        locale_hint=locale_hint,
        action_timeout_ms=action_timeout_ms,
        settle_wait_ms=settle_wait_ms,
    )


def google_force_bind_repair_policy(
    *,
    service_key: str = "google_flights",
    enabled: bool,
    uses: int,
    max_per_attempt: int,
    verify_status: str,
    scope_class: str,
    observed_dest_raw: str,
    observed_origin_raw: str = "",
    expected_origin: str = "",
) -> Dict[str, Any]:
    """Public bridge wrapper for force-bind policy decision.

    This preserves the legacy call signature used by service runners while
    delegating to the canonical route_bind policy helper.
    """
    from core.scenario_runner.google_flights.route_bind import (
        _google_force_bind_repair_policy as _impl,
    )

    return _impl(
        service_key=service_key,
        enabled=enabled,
        uses=uses,
        max_per_attempt=max_per_attempt,
        verify_status=verify_status,
        scope_class=scope_class,
        observed_dest_raw=observed_dest_raw,
        observed_origin_raw=observed_origin_raw,
        expected_origin=expected_origin,
    )


__all__ = [
    "service_fill_fallbacks",
    "service_search_click_fallbacks",
    "service_wait_fallbacks",
    "google_route_core_before_date_gate",
    "should_attempt_google_route_mismatch_reset",
    "google_activate_route_form_recovery",
    "google_force_bind_repair_policy",
    "_build_click_selectors_for_tokens",
    "_allow_bare_text_fallback",
    "_contains_any_token",
    "_dedupe_selectors",
    "_default_google_flights_plan",
    "_env_bool",
    "_env_int",
    "_env_list",
    "_google_date_done_selectors",
    "_maybe_append_bare_text_selectors",
    "_google_date_open_selector_hint_is_plausible",
    "_google_date_tokens",
    "_google_default_date_reference_year",
    "_google_deeplink_page_state_recovery_policy",
    "_google_deeplink_probe_status",
    "_google_deeplink_recovery_plan",
    "_google_display_locale_hint_from_browser",
    "_google_display_locale_hint_from_url",
    "_is_google_flights_deeplink",
    "_google_flights_after_search_ready",
    "_google_force_bind_dest_selectors",
    "_google_force_bind_flights_tab_selectors",
    "_google_force_bind_location_input_selectors",
    "_google_form_text_looks_date_like",
    "_google_form_text_looks_instructional_noise",
    "_google_has_contextual_price_card",
    "_google_has_iata_token",
    "_google_has_results_shell_for_context",
    "_google_missing_roles_from_reason",
    "_google_non_flight_scope_repair_plan",
    "_google_quick_page_class",
    "_google_recovery_collab_limits_from_thresholds",
    "_google_results_itinerary_matches_expected",
    "_google_role_i18n_token_bank",
    "_google_role_tokens",
    "_google_route_activation_selectors",
    "_google_route_alias_tokens",
    "_google_route_context_matches",
    "_google_route_core_only_recovery_plan",
    "_google_route_fill_input_selector_hint_is_plausible",
    "_google_route_reset_selectors",
    "_google_search_selector_hint_is_plausible",
    "_google_selector_locale_markers",
    "_google_should_suppress_force_bind_after_date_failure",
    "_google_step_trace_local_date_open_failure",
    "_google_step_trace_route_fill_roles_ok",
    "_label_click_selectors",
    "_normalize_google_form_date_text",
    "_parse_google_deeplink_context",
    "_selector_candidates",
    "_service_fill_activation_clicks",
    "_service_fill_activation_keywords",
    "_strip_nonvisible_html",
    "_verification_confidence_rank",
]
