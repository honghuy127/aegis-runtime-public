"""Tests for scenario plan input rebinding logic."""

import core.scenario_runner as sr
import core.service_runners.google_flights

from core.scenario_runner import (
    _default_plan_for_service,
    _google_route_fill_smart_escalation_skip_reason,
    _google_force_bind_location_input_selectors,
    _google_route_activation_selectors,
    _infer_fill_role,
    _is_actionable_plan,
    _reconcile_fill_plan_roles_and_values,
    _retarget_plan_inputs,
    _service_fill_fallbacks,
    _service_fill_activation_keywords,
    _google_route_fill_input_selector_hint_is_plausible,
    _google_origin_looks_unbound,
)


def test_retarget_plan_rewrites_fill_values():
    """Cached fill values should be rebound to current trip inputs."""
    plan = [
        {
            "action": "fill",
            "selector": ["input[aria-label*='Where from']"],
            "value": "OLD",
        },
        {
            "action": "fill",
            "selector": ["input[aria-label*='Where to']"],
            "value": "OLD",
        },
        {
            "action": "fill",
            "selector": ["input[aria-label*='Departure']"],
            "value": "2000-01-01",
        },
    ]

    rebound = _retarget_plan_inputs(
        plan=plan,
        origin="HND",
        dest="ITM",
        depart="2099-03-01",
        return_date=None,
        trip_type="one_way",
    )

    assert rebound[0]["value"] == "HND"
    assert rebound[1]["value"] == "ITM"
    assert rebound[2]["value"] == "2099-03-01"


def test_retarget_plan_adds_return_step_for_round_trip_when_missing():
    """Round-trip should inject return fill step if plan has none."""
    plan = [
        {
            "action": "fill",
            "selector": ["input[aria-label*='Departure']"],
            "value": "2000-01-01",
        },
        {"action": "click", "selector": ["text=Search"]},
    ]

    rebound = _retarget_plan_inputs(
        plan=plan,
        origin="HND",
        dest="ITM",
        depart="2099-03-01",
        return_date="2099-03-08",
        trip_type="round_trip",
    )

    return_steps = [s for s in rebound if s.get("action") == "fill" and "Return" in " ".join(s.get("selector", []))]
    assert return_steps
    assert return_steps[0]["value"] == "2099-03-08"


def test_retarget_plan_drops_return_step_for_one_way():
    """One-way should remove existing return-date fill steps."""
    plan = [
        {
            "action": "fill",
            "selector": ["input[aria-label*='Return']"],
            "value": "2099-03-08",
        },
        {"action": "wait", "selector": ["body"]},
    ]

    rebound = _retarget_plan_inputs(
        plan=plan,
        origin="HND",
        dest="ITM",
        depart="2099-03-01",
        return_date=None,
        trip_type="one_way",
    )

    assert all("Return" not in " ".join(s.get("selector", [])) for s in rebound)


def test_retarget_plan_drops_irrelevant_contact_fill_steps():
    """Repair plans should not keep contact/auth form fill steps."""
    plan = [
        {"action": "fill", "selector": ["input[name='email']"], "value": "me@example.com"},
        {"action": "fill", "selector": ["input[placeholder='Full Name']"], "value": "Foo Bar"},
        {"action": "click", "selector": ["button[type='submit']"]},
    ]

    rebound = _retarget_plan_inputs(
        plan=plan,
        origin="HND",
        dest="ITM",
        depart="2099-03-01",
        return_date="2099-03-08",
        trip_type="round_trip",
    )

    selectors_blob = " ".join(" ".join(s.get("selector", [])) for s in rebound)
    assert "email" not in selectors_blob.lower()
    assert "full name" not in selectors_blob.lower()


def test_retarget_plan_keeps_unknown_fill_when_value_is_trip_input():
    """Unknown selectors can remain if value is a canonical trip input value."""
    plan = [
        {"action": "fill", "selector": ["input[name='q']"], "value": "HND"},
        {"action": "click", "selector": ["button[type='submit']"]},
    ]

    rebound = _retarget_plan_inputs(
        plan=plan,
        origin="HND",
        dest="ITM",
        depart="2099-03-01",
        return_date=None,
        trip_type="one_way",
    )

    fills = [s for s in rebound if s.get("action") == "fill"]
    assert fills
    assert fills[0]["value"] == "HND"


def test_infer_fill_role_treats_airport_code_selector_as_origin():
    """Airport code hidden/input fields should map to origin/dest, not depart date."""
    step = {
        "action": "fill",
        "selector": ["input[name='outwardDepartureAirportCode']"],
        "value": "HND",
    }
    assert _infer_fill_role(step) == "origin"


def test_google_activation_keywords_keep_ja_and_en_for_non_ja_locale(monkeypatch):
    monkeypatch.setenv("FLIGHT_WATCHER_MIMIC_LOCALE", "fr-FR")
    out = _service_fill_activation_keywords("google_flights", "dest")
    assert "destination" in out
    assert "目的地" in out
    assert out.index("destination") < out.index("目的地")


def test_google_activation_keywords_keep_en_backup_for_ja_locale(monkeypatch):
    monkeypatch.setenv("FLIGHT_WATCHER_MIMIC_LOCALE", "ja-JP")
    out = _service_fill_activation_keywords("google_flights", "dest")
    assert "目的地" in out
    assert "destination" in out
    assert out.index("目的地") < out.index("destination")


def test_infer_fill_role_treats_japanese_return_selector_as_return():
    step = {
        "action": "fill",
        "selector": ["input[aria-label*='帰り']"],
        "value": "2099-03-08",
    }
    assert _infer_fill_role(step) == "return"


def test_infer_fill_role_treats_japanese_depart_date_selector_as_depart():
    step = {
        "action": "fill",
        "selector": ["input[aria-label*='出発日']"],
        "value": "2099-03-01",
    }
    assert _infer_fill_role(step) == "depart"


def test_infer_fill_role_prefers_date_semantics_over_airport_code_tokens():
    step = {
        "action": "fill",
        "selector": [
            "input[aria-label*='Departure airport']",
            "input[aria-label*='Departure date']",
        ],
        "value": "2099-03-01",
    }
    assert _infer_fill_role(step) == "depart"


def test_default_google_plan_remains_actionable_after_selector_token_sorting():
    plan = _default_plan_for_service(
        "google_flights",
        origin="HND",
        dest="ITM",
        depart="2099-03-01",
        is_domestic=True,
        knowledge={},
    )
    assert _is_actionable_plan(plan, "round_trip", site_key="google_flights") is True


def test_google_route_fill_input_selector_hint_plausibility_rejects_generic_combobox():
    assert _google_route_fill_input_selector_hint_is_plausible("dest", "input[role='combobox']") is False
    assert _google_route_fill_input_selector_hint_is_plausible(
        "dest", "input[role='combobox'][aria-label='Where to?']"
    ) is True


def test_google_origin_looks_unbound_treats_expected_city_alias_as_bound():
    assert _google_origin_looks_unbound("Tokyo", expected_origin="HND") is False
    assert _google_origin_looks_unbound("Japan", expected_origin="HND") is True


def test_infer_fill_role_uses_profile_semantic_tokens_for_custom_date_label(monkeypatch):
    def _fake_profile(site_key):  # noqa: ARG001
        return {
            "semantic_role_tokens": {
                "depart": {
                    "selector_en": ["Journey start date"],
                }
            }
        }

    monkeypatch.setattr(core.service_runners.google_flights, "get_service_ui_profile", _fake_profile)
    step = {
        "action": "fill",
        "selector": ["input[aria-label*='Journey start date']"],
        "value": "2099-03-01",
    }
    assert _infer_fill_role(step) == "depart"


def test_google_route_activation_selectors_filter_value_labeled_chip_selectors():
    selectors = _google_route_activation_selectors(
        role="origin",
        value="HND",
        plan_selectors=[
            "[role='button'][aria-label*='HND']",
            "[role='tab'][aria-label*='HND']",
            "[role='combobox'][aria-label*='出発地']",
        ],
    )
    assert selectors
    assert "[role='combobox'][aria-label*='出発地']" in selectors
    assert not any("HND" in s for s in selectors)


def test_google_route_activation_selectors_filter_ambiguous_origin_departure_label():
    selectors = _google_route_activation_selectors(
        role="origin",
        value="HND",
        plan_selectors=[
            "[role='combobox'][aria-label*='出発']",
            "[role='combobox'][aria-label*='出発地']",
        ],
    )
    assert "[role='combobox'][aria-label*='出発地']" in selectors
    assert "[role='combobox'][aria-label*='出発']" not in selectors


def test_google_route_activation_selectors_filter_multi_city_add_controls():
    selectors = _google_route_activation_selectors(
        role="dest",
        value="ITM",
        plan_selectors=[
            "button:has-text('+')",
            "[role='button'][aria-label*='Add flight']",
            "[role='combobox'][aria-label*='Where to']",
        ],
    )
    assert "[role='combobox'][aria-label*='Where to']" in selectors
    assert not any("Add flight" in s or "has-text('+')" in s for s in selectors)


def test_google_route_activation_selectors_prioritize_containers_over_inputs():
    selectors = _google_route_activation_selectors(
        role="origin",
        value="HND",
        plan_selectors=[
            "input[aria-label*='出発地']",
            "[role='combobox'][aria-label*='出発地']",
            "[role='button'][aria-label*='From']",
            "label:has-text('From')",
        ],
    )
    # Container/button activators should be ranked before input/label fallbacks.
    assert selectors.index("[role='combobox'][aria-label*='出発地']") < selectors.index("input[aria-label*='出発地']")
    assert selectors.index("[role='button'][aria-label*='From']") < selectors.index("input[aria-label*='出発地']")


def test_google_route_activation_selectors_prioritize_exact_where_from_over_broad_ja_variants():
    selectors = _google_route_activation_selectors(
        role="origin",
        value="HND",
        plan_selectors=[
            "[role='combobox'][aria-label*='出発地']",
            "[role='combobox'][aria-label*='出発空港']",
            "[role='combobox'][aria-label='Where from?']",
            "[role='combobox'][aria-label*='Origin']",
        ],
    )
    assert selectors.index("[role='combobox'][aria-label='Where from?']") < selectors.index(
        "[role='combobox'][aria-label*='出発地']"
    )
    assert selectors.index("[role='combobox'][aria-label='Where from?']") < selectors.index(
        "[role='combobox'][aria-label*='出発空港']"
    )


def test_google_route_activation_selectors_seed_exact_where_to_combobox_when_plan_lacks_it():
    selectors = _google_route_activation_selectors(
        role="dest",
        value="ITM",
        plan_selectors=[
            "[role='button'][aria-label*='Where to']",
            "[role='combobox'][aria-label*='Destination']",
            "[role='button'][aria-label*='To']",
            "input[aria-label*='目的地']",
        ],
    )
    assert "[role='combobox'][aria-label*='Where to']" in selectors[:5]


def test_google_force_bind_location_input_selectors_include_exact_en_placeholders():
    origin_selectors = _google_force_bind_location_input_selectors("origin")
    dest_selectors = _google_force_bind_location_input_selectors("dest")

    assert any("aria-label='Where from?'" in s or "aria-label^='Where from?'" in s for s in origin_selectors)
    assert any("aria-label='Where to?'" in s or "aria-label^='Where to?'" in s for s in dest_selectors)


def test_google_fill_fallbacks_keep_bilingual_selectors_with_locale_order(monkeypatch):
    monkeypatch.setenv("FLIGHT_WATCHER_MIMIC_LOCALE", "en-US")
    selectors = _service_fill_fallbacks("google_flights", "dest")
    assert selectors
    assert any("Destination" in s or "Where to" in s or "To" in s for s in selectors)
    assert any("目的地" in s or "到着地" in s for s in selectors)
    # EN-preferring locale should put EN selector near the front while keeping JA fallback.
    head = selectors[:6]
    assert any("Destination" in s or "Where to" in s or "To" in s for s in head)


def test_google_route_fill_smart_escalation_skip_reason_uses_combobox_failure_stage():
    class _BrowserStub:
        _last_google_flights_combobox_debug = {"failure_stage": "deadline_activation_budget"}

    out = _google_route_fill_smart_escalation_skip_reason(
        [],
        error_message=(
            "Step failed action=fill role=origin selectors=['x']: combobox_fill_failed"
        ),
        browser=_BrowserStub(),
    )

    assert out == "google_route_fill_deadline_activation_budget"


def test_google_route_fill_smart_escalation_skip_reason_uses_latest_step_trace_when_error_wrapped():
    class _BrowserStub:
        _last_google_flights_combobox_debug = {"failure_stage": "deadline_activation_budget"}

    step_trace = [
        {
            "action": "fill",
            "role": "origin",
            "status": "hard_fail",
            "required_for_actionability": True,
            "evidence": {"combobox.failure_stage": "deadline_activation_budget"},
            "fill_commit": {
                "ok": False,
                "reason": "combobox_fill_failed",
                # Simulate a path where nested evidence was not copied into fill_commit.
            },
        }
    ]
    out = _google_route_fill_smart_escalation_skip_reason(
        step_trace,
        error_message="Repeated failure without DOM change; aborting early",
        browser=_BrowserStub(),
    )
    assert out == "google_route_fill_deadline_activation_budget"


def test_google_route_fill_smart_escalation_skip_reason_uses_browser_debug_with_step_trace_only():
    class _BrowserStub:
        _last_google_flights_combobox_debug = {"failure_stage": "deadline_activation_check"}

    step_trace = [
        {
            "action": "fill",
            "role": "dest",
            "required_for_actionability": True,
            "fill_commit": {
                "ok": False,
                "reason": "combobox_fill_failed",
                "evidence": {},
            },
        }
    ]
    out = _google_route_fill_smart_escalation_skip_reason(
        step_trace,
        error_message="planner wrapper message without step signature",
        browser=_BrowserStub(),
    )
    assert out == "google_route_fill_deadline_activation_check"


def test_google_route_fill_smart_escalation_skip_reason_ignores_unverified_postcheck_failures():
    step_trace = [
        {
            "action": "fill",
            "role": "dest",
            "required_for_actionability": True,
            "fill_commit": {
                "ok": False,
                "reason": "combobox_fill_unverified_dest_dest_placeholder",
                "evidence": {"verify.postcheck_reason": "dest_placeholder"},
            },
        }
    ]
    out = _google_route_fill_smart_escalation_skip_reason(step_trace)
    assert out == ""


def test_google_route_fill_smart_escalation_skip_reason_skips_activation_failed_without_required_flag():
    step_trace = [
        {
            "action": "fill",
            "role": "dest",
            "required_for_actionability": False,
            "fill_commit": {
                "ok": False,
                "reason": "combobox_fill_failed",
                "evidence": {"combobox.failure_stage": "activation_failed"},
            },
        }
    ]
    out = _google_route_fill_smart_escalation_skip_reason(step_trace)
    assert out == "google_route_fill_activation_failed"


def test_google_route_fill_smart_escalation_skip_reason_skips_verified_postcheck_helper_contamination():
    class _BrowserStub:
        _last_google_flights_combobox_debug = {"verify_ok": True}

    step_trace = [
        {
            "action": "fill",
            "role": "origin",
            "required_for_actionability": True,
            "fill_commit": {
                "ok": False,
                "reason": "combobox_fill_unverified_origin_origin_mismatch",
                "evidence": {
                    "verify.postcheck_reason": "origin_mismatch",
                    "verify.observed_origin": (
                        "Select multiple airportsDonePress the plus key to switch to multi-select mode."
                    ),
                },
            },
        }
    ]
    out = _google_route_fill_smart_escalation_skip_reason(step_trace, browser=_BrowserStub())
    assert out == "google_route_fill_postcheck_helper_contamination"


def test_google_route_fill_smart_escalation_skip_reason_skips_verified_postcheck_cross_field_date():
    class _BrowserStub:
        _last_google_flights_combobox_debug = {"verify_ok": True}

    step_trace = [
        {
            "action": "fill",
            "role": "origin",
            "required_for_actionability": True,
            "fill_commit": {
                "ok": False,
                "reason": "combobox_fill_unverified_origin_origin_mismatch",
                "evidence": {
                    "verify.postcheck_reason": "origin_mismatch",
                    "verify.observed_origin": "Sat, May 2",
                    "verify.postcheck_observed_kind": "date_value_cross_field",
                },
            },
        }
    ]
    out = _google_route_fill_smart_escalation_skip_reason(step_trace, browser=_BrowserStub())
    assert out == "google_route_fill_postcheck_cross_field_date"


def test_reconcile_fill_plan_roles_and_values_repairs_mixed_selector_value_conflict():
    plan = [
        {
            "action": "fill",
            "selector": ["[aria-label*='From']"],
            "value": "HND",
        },
        {
            "action": "fill",
            # Mixed selectors drifted toward origin, but value is destination.
            "selector": ["[role='button'][aria-label*='出発']", "[aria-label*='To']", "[aria-label*='目的地']"],
            "value": "ITM",
        },
    ]
    out = _reconcile_fill_plan_roles_and_values(
        plan,
        site_key="google_flights",
        origin="HND",
        dest="ITM",
        depart="2026-03-01",
        return_date="2026-03-08",
        trip_type="round_trip",
    )
    assert isinstance(out, list)
    assert out[1]["value"] == "ITM"
    selectors = out[1]["selector"]
    selector_list = selectors if isinstance(selectors, list) else [selectors]
    assert any("Where to" in s or "目的地" in s or "到着地" in s for s in selector_list[:8])
