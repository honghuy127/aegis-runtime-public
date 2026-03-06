from core.scenario_runner import (
    _build_route_state_scenario_extract_verdict,
    _google_reconcile_ready_route_bound_consistency,
)


def test_route_state_scenario_extract_verdict_google_mismatch_non_actionable():
    payload = _build_route_state_scenario_extract_verdict(
        site_key="google_flights",
        route_bind_verdict={
            "route_bound": False,
            "reason": "explicit_mismatch",
            "support": "none",
            "mismatch_fields": ["dest"],
        },
        scope_final="unknown",
        ready=False,
    )

    assert payload["version"] == 1
    assert payload["service"] == "google_flights"
    assert payload["source"] == "scenario_guard"
    assert payload["non_actionable"] is True
    assert payload["reason"] == "google_route_context_unbound"
    assert payload["route_bound"] is False
    assert payload["scenario_ready"] is False
    assert payload["route_bind_reason"] == "explicit_mismatch"
    assert payload["route_bind_support"] == "none"
    assert payload["mismatch_fields"] == ["dest"]


def test_route_state_scenario_extract_verdict_non_google_is_observational_only():
    payload = _build_route_state_scenario_extract_verdict(
        site_key="skyscanner",
        route_bind_verdict={"route_bound": False, "reason": "explicit_mismatch", "support": "none"},
        scope_final="irrelevant_page",
        ready=False,
    )

    assert payload["service"] == "skyscanner"
    assert payload["non_actionable"] is False
    assert payload["reason"] == ""
    assert payload["route_bound"] is False
    assert payload["scenario_ready"] is False


def test_route_state_scenario_extract_verdict_non_google_manual_reason_non_actionable():
    payload = _build_route_state_scenario_extract_verdict(
        site_key="skyscanner",
        route_bind_verdict={"route_bound": False, "reason": "manual_mode", "support": "none"},
        scope_final="unknown",
        ready=False,
        scenario_reason="demo_mode_manual_target_closed",
    )

    assert payload["service"] == "skyscanner"
    assert payload["non_actionable"] is True
    assert payload["reason"] == "demo_mode_manual_target_closed"
    assert payload["route_bound"] is False
    assert payload["scenario_ready"] is False


def test_route_state_scenario_extract_verdict_google_retries_exhausted_unbound_non_actionable():
    payload = _build_route_state_scenario_extract_verdict(
        site_key="google_flights",
        route_bind_verdict={
            "route_bound": False,
            "reason": "retries_exhausted",
            "support": "none",
        },
        scope_final="unknown",
        ready=False,
    )

    assert payload["service"] == "google_flights"
    assert payload["non_actionable"] is True
    assert payload["reason"] == "google_route_context_unbound"
    assert payload["route_bound"] is False
    assert payload["scenario_ready"] is False
    assert payload["route_bind_reason"] == "retries_exhausted"


def test_route_state_scenario_extract_verdict_non_google_retries_exhausted_unbound_non_actionable():
    payload = _build_route_state_scenario_extract_verdict(
        site_key="skyscanner",
        route_bind_verdict={
            "route_bound": False,
            "reason": "retries_exhausted",
            "support": "none",
        },
        scope_final="unknown",
        ready=False,
    )

    assert payload["service"] == "skyscanner"
    assert payload["non_actionable"] is True
    assert payload["reason"] == "retries_exhausted"
    assert payload["route_bound"] is False
    assert payload["scenario_ready"] is False
    assert payload["route_bind_reason"] == "retries_exhausted"


def test_google_reconcile_ready_route_bound_consistency_clamps_ready_when_unverified():
    out = _google_reconcile_ready_route_bound_consistency(
        ready=True,
        route_bound=False,
        verify_status="not_attempted",
        verify_override_reason="",
        scope_page_class="flight_only",
    )
    assert out["changed"] is True
    assert out["ready"] is False
    assert out["verify_status"] == "not_verified"
    assert out["verify_override_reason"] == "route_bind_not_verified"


def test_google_reconcile_ready_route_bound_consistency_keeps_noncontradictory_state():
    out = _google_reconcile_ready_route_bound_consistency(
        ready=True,
        route_bound=True,
        verify_status="bound",
        verify_override_reason="",
        scope_page_class="flight_only",
    )
    assert out["changed"] is False
    assert out["ready"] is True


def test_route_state_scenario_extract_verdict_google_date_fill_failure_non_actionable():
    payload = _build_route_state_scenario_extract_verdict(
        site_key="google_flights",
        route_bind_verdict={
            "route_bound": False,
            "reason": "not_attempted",
            "support": "none",
        },
        scope_final="flight_only",
        ready=False,
        scenario_reason="date_fill_failure_calendar_not_open",
    )

    assert payload["service"] == "google_flights"
    assert payload["non_actionable"] is True
    assert payload["reason"] == "date_fill_failure_calendar_not_open"
    assert payload["route_bound"] is False
    assert payload["scenario_ready"] is False
    assert payload["route_bind_reason"] == "not_attempted"
