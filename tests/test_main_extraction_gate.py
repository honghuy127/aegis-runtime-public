import json
from pathlib import Path

import main


def test_scenario_preextract_gate_uses_evidence_checkpoint(monkeypatch, tmp_path: Path):
    run_id = "run_1"
    artifacts_dir = tmp_path / run_id / "artifacts"
    artifacts_dir.mkdir(parents=True)
    (artifacts_dir / "evidence_google_flights_state.json").write_text(
        json.dumps(
            {
                "checkpoints": {
                    "after_results_ready_check": {
                        "data": {
                            "readiness": {
                                "ready": False,
                                "override_reason": "date_fill_failure_calendar_not_open",
                            },
                            "route_bind": {
                                "route_bound": False,
                                "support": "none",
                            },
                            "scope_guard": {
                                "page_class": "unknown",
                            },
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "get_artifacts_dir", lambda rid: artifacts_dir)

    result = main._scenario_preextract_gate_result(run_id=run_id, service_key="google_flights")

    assert result is not None
    assert result["reason"] == "date_fill_failure_calendar_not_open"
    assert result["source"] == "scenario_guard"
    assert result["route_bound"] is False
    assert result["scenario_ready"] is False


def test_scenario_preextract_gate_falls_back_to_route_state_mismatch(monkeypatch, tmp_path: Path):
    run_id = "run_2"
    artifacts_dir = tmp_path / run_id / "artifacts"
    artifacts_dir.mkdir(parents=True)
    (artifacts_dir / "route_state_google_flights.json").write_text(
        json.dumps(
            {
                "route_bind_verdict": {
                    "route_bound": False,
                    "reason": "explicit_mismatch",
                    "support": "none",
                },
                "dest_is_placeholder": True,
                "date_picker_seen": True,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "get_artifacts_dir", lambda rid: artifacts_dir)

    result = main._scenario_preextract_gate_result(run_id=run_id, service_key="google_flights")

    assert result is not None
    assert result["reason"] == "google_route_context_unbound"
    assert result["source"] == "scenario_guard"
    assert result["route_bound"] is False


def test_scenario_preextract_gate_falls_back_to_google_scope_non_flight_unbound(
    monkeypatch, tmp_path: Path
):
    run_id = "run_2b"
    artifacts_dir = tmp_path / run_id / "artifacts"
    artifacts_dir.mkdir(parents=True)
    (artifacts_dir / "route_state_google_flights.json").write_text(
        json.dumps(
            {
                "route_bind_verdict": {
                    "route_bound": False,
                    "reason": "scope_non_flight_irrelevant_page",
                    "support": "weak",
                },
                "scope_verdicts": {
                    "final": "irrelevant_page",
                    "sources": ["verify:route_fill_mismatch", "heuristic:irrelevant_page"],
                },
                "dest_is_placeholder": True,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "get_artifacts_dir", lambda rid: artifacts_dir)

    result = main._scenario_preextract_gate_result(run_id=run_id, service_key="google_flights")

    assert result is not None
    assert result["reason"] == "google_route_context_unbound"
    assert result["source"] == "scenario_guard"
    assert result["route_bound"] is False


def test_scenario_preextract_gate_falls_back_to_retries_exhausted_unbound_route_state(
    monkeypatch, tmp_path: Path
):
    run_id = "run_retries_exhausted_unbound"
    artifacts_dir = tmp_path / run_id / "artifacts"
    artifacts_dir.mkdir(parents=True)
    (artifacts_dir / "route_state_google_flights.json").write_text(
        json.dumps(
            {
                "route_bind_verdict": {
                    "route_bound": False,
                    "reason": "retries_exhausted",
                    "support": "none",
                },
                "scenario_return_summary": {
                    "ready": False,
                    "reason": "retries_exhausted",
                    "scope_class": "unknown",
                },
                "scope_verdicts": {
                    "final": "unknown",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "get_artifacts_dir", lambda rid: artifacts_dir)

    result = main._scenario_preextract_gate_result(run_id=run_id, service_key="google_flights")

    assert result is not None
    assert result["reason"] == "google_route_context_unbound"
    assert result["source"] == "scenario_guard"
    assert result["route_bound"] is False
    assert result["scenario_ready"] is False
    assert result["route_bind_reason"] == "retries_exhausted"


def test_scenario_preextract_gate_falls_back_to_date_fill_failure_route_state(
    monkeypatch, tmp_path: Path
):
    run_id = "run_date_fill_failure"
    artifacts_dir = tmp_path / run_id / "artifacts"
    artifacts_dir.mkdir(parents=True)
    (artifacts_dir / "route_state_google_flights.json").write_text(
        json.dumps(
            {
                "route_bind_verdict": {
                    "route_bound": False,
                    "reason": "not_attempted",
                    "support": "none",
                },
                "scenario_return_summary": {
                    "ready": False,
                    "reason": "date_fill_failure_calendar_not_open",
                    "scope_class": "flight_only",
                },
                "scope_verdicts": {
                    "final": "flight_only",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "get_artifacts_dir", lambda rid: artifacts_dir)

    result = main._scenario_preextract_gate_result(run_id=run_id, service_key="google_flights")

    assert result is not None
    assert result["reason"] == "date_fill_failure_calendar_not_open"
    assert result["source"] == "scenario_guard"
    assert result["route_bound"] is False
    assert result["scenario_ready"] is False
    assert result["route_bind_reason"] == "not_attempted"


def test_scenario_preextract_gate_uses_scenario_last_error_for_blocked_interstitial(
    monkeypatch, tmp_path: Path
):
    run_id = "run_3"
    run_dir = tmp_path / run_id
    artifacts_dir = run_dir / "artifacts"
    artifacts_dir.mkdir(parents=True)
    (run_dir / "scenario_last_error.json").write_text(
        json.dumps(
            {
                "stage": "blocked_interstitial",
                "site_key": "skyscanner",
                "error": "blocked_interstitial_captcha",
                "blocked_interstitial": {
                    "page_kind": "interstitial",
                    "block_type": "captcha",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "get_artifacts_dir", lambda rid: artifacts_dir)
    monkeypatch.setattr(main, "get_run_dir", lambda rid: run_dir)

    result = main._scenario_preextract_gate_result(run_id=run_id, service_key="skyscanner")

    assert result is not None
    assert result["reason"] == "blocked_interstitial_captcha"
    assert result["source"] == "scenario_guard"
    assert result["route_bound"] is False
    assert result["scenario_ready"] is False
    assert result["scope_class"] == "interstitial"


def test_scenario_preextract_gate_v2_route_state_verdict_disabled_by_default(
    monkeypatch, tmp_path: Path
):
    run_id = "run_v2_disabled"
    artifacts_dir = tmp_path / run_id / "artifacts"
    artifacts_dir.mkdir(parents=True)
    (artifacts_dir / "route_state_google_flights.json").write_text(
        json.dumps(
            {
                "scenario_extract_verdict": {
                    "version": 1,
                    "service": "google_flights",
                    "source": "scenario_guard",
                    "non_actionable": True,
                    "reason": "google_route_context_unbound",
                    "route_bound": False,
                    "scenario_ready": False,
                    "scope_class": "irrelevant_page",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "get_artifacts_dir", lambda rid: artifacts_dir)
    monkeypatch.setattr(main, "get_threshold", lambda key, default=None: default)

    result = main._scenario_preextract_gate_result(run_id=run_id, service_key="google_flights")

    # Legacy path remains authoritative until v2 flag is enabled.
    assert result is None


def test_scenario_preextract_gate_route_state_summary_non_actionable_for_skyscanner(
    monkeypatch, tmp_path: Path
):
    run_id = "run_skyscanner_manual_non_actionable"
    artifacts_dir = tmp_path / run_id / "artifacts"
    artifacts_dir.mkdir(parents=True)
    (artifacts_dir / "route_state_skyscanner.json").write_text(
        json.dumps(
            {
                "route_bind_verdict": {
                    "route_bound": False,
                    "reason": "demo_mode_manual_target_closed",
                    "support": "none",
                },
                "scenario_return_summary": {
                    "ready": False,
                    "reason": "demo_mode_manual_target_closed",
                    "scope_class": "unknown",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "get_artifacts_dir", lambda rid: artifacts_dir)

    result = main._scenario_preextract_gate_result(run_id=run_id, service_key="skyscanner")

    assert result is not None
    assert result["reason"] == "demo_mode_manual_target_closed"
    assert result["source"] == "scenario_guard"
    assert result["route_bound"] is False
    assert result["scenario_ready"] is False
    assert result["route_bind_reason"] == "demo_mode_manual_target_closed"


def test_scenario_preextract_gate_demo_observation_complete_is_non_actionable(
    monkeypatch, tmp_path: Path
):
    run_id = "run_skyscanner_demo_observation"
    artifacts_dir = tmp_path / run_id / "artifacts"
    artifacts_dir.mkdir(parents=True)
    (artifacts_dir / "route_state_skyscanner.json").write_text(
        json.dumps(
            {
                "route_bind_verdict": {
                    "route_bound": False,
                    "reason": "demo_mode_observation_complete",
                    "support": "none",
                },
                "scenario_return_summary": {
                    "ready": True,
                    "reason": "demo_mode_observation_complete",
                    "scope_class": "unknown",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "get_artifacts_dir", lambda rid: artifacts_dir)

    result = main._scenario_preextract_gate_result(run_id=run_id, service_key="skyscanner")

    assert result is not None
    assert result["reason"] == "demo_mode_observation_complete"
    assert result["source"] == "scenario_guard"
    assert result["route_bound"] is False
    assert result["scenario_ready"] is False
    assert result["route_bind_reason"] == "demo_mode_observation_complete"


def test_scenario_preextract_gate_v2_non_actionable_fail_closed_for_skyscanner(
    monkeypatch, tmp_path: Path
):
    run_id = "run_skyscanner_v2_fail_closed"
    artifacts_dir = tmp_path / run_id / "artifacts"
    artifacts_dir.mkdir(parents=True)
    (artifacts_dir / "route_state_skyscanner.json").write_text(
        json.dumps(
            {
                "scenario_extract_verdict": {
                    "version": 1,
                    "service": "skyscanner",
                    "source": "scenario_guard",
                    "non_actionable": True,
                    "reason": "retries_exhausted",
                    "route_bound": False,
                    "scenario_ready": False,
                    "scope_class": "unknown",
                    "route_bind_reason": "retries_exhausted",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "get_artifacts_dir", lambda rid: artifacts_dir)
    monkeypatch.setattr(main, "get_threshold", lambda key, default=None: default)

    result = main._scenario_preextract_gate_result(run_id=run_id, service_key="skyscanner")

    assert result is not None
    assert result["reason"] == "retries_exhausted"
    assert result["source"] == "scenario_guard"
    assert result["route_bound"] is False
    assert result["scenario_ready"] is False


def test_scenario_preextract_gate_v2_route_state_verdict_enabled(monkeypatch, tmp_path: Path):
    run_id = "run_v2_enabled"
    artifacts_dir = tmp_path / run_id / "artifacts"
    artifacts_dir.mkdir(parents=True)
    (artifacts_dir / "route_state_google_flights.json").write_text(
        json.dumps(
            {
                "scenario_extract_verdict": {
                    "version": 1,
                    "service": "google_flights",
                    "source": "scenario_guard",
                    "non_actionable": True,
                    "reason": "google_route_context_unbound",
                    "route_bound": False,
                    "scenario_ready": False,
                    "scope_class": "irrelevant_page",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "get_artifacts_dir", lambda rid: artifacts_dir)

    def _mock_threshold(key, default=None):
        if key == "scenario_preextract_verdict_v2_enabled":
            return True
        if key == "scenario_preextract_verdict_v2_shadow_compare":
            return False
        return default

    monkeypatch.setattr(main, "get_threshold", _mock_threshold)

    result = main._scenario_preextract_gate_result(run_id=run_id, service_key="google_flights")

    assert result is not None
    assert result["reason"] == "google_route_context_unbound"
    assert result["source"] == "scenario_guard"
    assert result["route_bound"] is False
    assert result["scenario_ready"] is False
    assert result["scope_class"] == "irrelevant_page"
    assert result["route_bind_reason"] == ""


def test_scenario_preextract_gate_uses_evidence_checkpoint_for_deeplink_recovery_failfast(
    monkeypatch, tmp_path: Path
):
    run_id = "run_deeplink_failfast"
    artifacts_dir = tmp_path / run_id / "artifacts"
    artifacts_dir.mkdir(parents=True)
    (artifacts_dir / "evidence_google_flights_state.json").write_text(
        json.dumps(
            {
                "checkpoints": {
                    "after_results_ready_check": {
                        "data": {
                            "readiness": {
                                "ready": False,
                                "override_reason": "deeplink_page_state_recovery_unready_non_flight_scope_irrelevant_page",
                            },
                            "route_bind": {
                                "route_bound": False,
                                "support": "none",
                            },
                            "scope_guard": {
                                "page_class": "irrelevant_page",
                            },
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "get_artifacts_dir", lambda rid: artifacts_dir)

    result = main._scenario_preextract_gate_result(run_id=run_id, service_key="google_flights")

    assert result is not None
    assert result["source"] == "scenario_guard"
    assert result["reason"] == "deeplink_page_state_recovery_unready_non_flight_scope_irrelevant_page"
    assert result["scenario_ready"] is False
    assert result["route_bound"] is False
    assert result["scope_class"] == "irrelevant_page"


def test_scenario_success_extract_scope_guard_overrides_for_ready_bound_google(
    monkeypatch, tmp_path: Path
):
    run_id = "run_scope_override"
    artifacts_dir = tmp_path / run_id / "artifacts"
    artifacts_dir.mkdir(parents=True)
    (artifacts_dir / "route_state_google_flights.json").write_text(
        json.dumps(
            {
                "scenario_extract_verdict": {
                    "scenario_ready": True,
                    "route_bound": True,
                    "scope_class": "flight_only",
                },
                "route_bind_verdict": {
                    "support": "strong",
                    "reason": "route_bind_corroborated_local_fill",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "get_artifacts_dir", lambda rid: artifacts_dir)

    overrides = main._scenario_success_extract_scope_guard_overrides(
        run_id=run_id,
        service_key="google_flights",
    )

    assert overrides == {
        "FLIGHT_WATCHER_VLM_SCOPE_GUARD_ENABLED": "0",
        "FLIGHT_WATCHER_LLM_SCOPE_GUARD_ENABLED": "0",
    }


def test_scenario_success_extract_scope_guard_overrides_empty_for_unready(
    monkeypatch, tmp_path: Path
):
    run_id = "run_scope_override_unready"
    artifacts_dir = tmp_path / run_id / "artifacts"
    artifacts_dir.mkdir(parents=True)
    (artifacts_dir / "route_state_google_flights.json").write_text(
        json.dumps(
            {
                "scenario_extract_verdict": {
                    "scenario_ready": False,
                    "route_bound": True,
                    "scope_class": "flight_only",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "get_artifacts_dir", lambda rid: artifacts_dir)

    overrides = main._scenario_success_extract_scope_guard_overrides(
        run_id=run_id,
        service_key="google_flights",
    )

    assert overrides == {}


def test_scenario_success_google_deterministic_extract_fastpath_uses_plugin_on_ready_bound(
    monkeypatch, tmp_path: Path
):
    run_id = "run_fast_extract"
    artifacts_dir = tmp_path / run_id / "artifacts"
    artifacts_dir.mkdir(parents=True)
    (artifacts_dir / "route_state_google_flights.json").write_text(
        json.dumps(
            {
                "scenario_extract_verdict": {
                    "scenario_ready": True,
                    "route_bound": True,
                    "scope_class": "flight_only",
                    "route_bind_reason": "route_bind_corroborated_local_fill",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "get_artifacts_dir", lambda rid: artifacts_dir)
    monkeypatch.setattr(
        main,
        "extract_google_flights_price_from_html",
        lambda html, page_url=None: {
            "ok": True,
            "price": 10420,
            "currency": "JPY",
            "page_kind": "flights_results",
            "extraction_strategy": "google_flights_semantic_price_regex_v1",
            "evidence": {"candidate_count": 3},
        },
    )

    result = main._scenario_success_google_deterministic_extract_fastpath(
        run_id=run_id,
        service_key="google_flights",
        html="<html></html>",
        page_url="https://www.google.com/travel/flights?hl=en&gl=JP",
    )

    assert isinstance(result, dict)
    assert result["price"] == 10420.0
    assert result["source"] == "scenario_ready_plugin_fastpath"
    assert result["route_bound"] is True
    assert result["scope_class"] == "flight_only"


def test_scenario_success_google_deterministic_extract_fastpath_skips_non_results_page_kind(
    monkeypatch, tmp_path: Path
):
    run_id = "run_fast_extract_skip"
    artifacts_dir = tmp_path / run_id / "artifacts"
    artifacts_dir.mkdir(parents=True)
    (artifacts_dir / "route_state_google_flights.json").write_text(
        json.dumps(
            {
                "scenario_extract_verdict": {
                    "scenario_ready": True,
                    "route_bound": True,
                    "scope_class": "flight_only",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "get_artifacts_dir", lambda rid: artifacts_dir)
    monkeypatch.setattr(
        main,
        "extract_google_flights_price_from_html",
        lambda html, page_url=None: {
            "ok": True,
            "price": 1,
            "currency": "USD",
            "page_kind": "consent",
            "evidence": {},
        },
    )

    result = main._scenario_success_google_deterministic_extract_fastpath(
        run_id=run_id,
        service_key="google_flights",
        html="<html></html>",
        page_url="https://www.google.com/travel/flights?hl=en&gl=JP",
    )

    assert result is None


def test_should_salvage_extract_skips_deeplink_recovery_failfast_reason():
    result = {
        "price": None,
        "currency": None,
        "confidence": "low",
        "reason": "deeplink_page_state_recovery_unready_non_flight_scope_irrelevant_page",
        "source": "heuristic_html",
    }

    assert main._should_salvage_extract(result) is False
