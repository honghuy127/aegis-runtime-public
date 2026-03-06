from core.scenario_runner import _build_route_state_return_fallback_payload


def test_build_route_state_return_fallback_payload_marks_google_irrelevant_page_non_actionable():
    payload = _build_route_state_return_fallback_payload(
        run_id="run_x",
        site_key="google_flights",
        origin="HND",
        dest="ITM",
        depart="2026-03-01",
        return_date="2026-03-08",
        reason="deeplink_page_state_recovery_unready_non_flight_scope_irrelevant_page",
        ready=False,
        scope_class="irrelevant_page",
        route_bound=False,
        route_support="none",
    )

    route_verdict = payload["route_bind_verdict"]
    extract_verdict = payload["scenario_extract_verdict"]

    assert route_verdict["route_bound"] is False
    assert route_verdict["reason"] == "scope_non_flight_irrelevant_page"
    assert route_verdict["support"] == "none"

    assert extract_verdict["non_actionable"] is True
    assert extract_verdict["reason"] == "google_route_context_unbound"
    assert extract_verdict["route_bind_reason"] == "scope_non_flight_irrelevant_page"
    assert extract_verdict["scope_class"] == "irrelevant_page"
