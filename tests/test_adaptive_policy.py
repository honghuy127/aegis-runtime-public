"""Tests for adaptive runtime policy self-healing behavior."""

from storage import adaptive_policy as ap


def test_record_service_outcome_updates_consecutive_counters(tmp_path, monkeypatch):
    """Outcome recorder should maintain consecutive success/failure streaks."""
    monkeypatch.setattr(ap, "STORE_PATH", tmp_path / "adaptive_policy.json")

    ap.record_service_outcome(
        site_key="google_flights",
        status="error",
        error="Scenario candidate timeout after 120s",
    )
    ap.record_service_outcome(
        site_key="google_flights",
        status="ok",
        result={"price": None, "reason": "heuristic_no_route_match"},
    )
    policy = ap.load_policy()
    state = policy["sites"]["google_flights"]
    assert state["failure_count"] == 1
    assert state["success_count"] == 1
    assert state["consecutive_failure"] == 0
    assert state["consecutive_success"] == 1
    assert state["heuristic_miss_count"] == 1


def test_recommend_runtime_profile_enables_escalation_after_repeated_misses(
    tmp_path,
    monkeypatch,
):
    """Repeated misses with healthy LLM should increase short escalation budgets."""
    monkeypatch.setattr(ap, "STORE_PATH", tmp_path / "adaptive_policy.json")
    monkeypatch.setattr(ap, "list_llm_metrics", lambda **kwargs: [])

    ap.record_service_outcome(
        site_key="google_flights",
        status="ok",
        result={"price": None, "reason": "heuristic_no_route_match"},
    )
    ap.record_service_outcome(
        site_key="google_flights",
        status="ok",
        result={"price": None, "reason": "llm_parse_failed"},
    )
    ap.record_service_outcome(
        site_key="google_flights",
        status="error",
        error="Initial action plan generation failed",
    )

    profile = ap.recommend_runtime_profile("google_flights", llm_mode="light")
    assert profile["light_try_llm_extract_on_heuristic_miss"] is True
    assert int(profile["llm_light_extract_timeout_sec"]) >= 30
    assert "enable_extract_escalation" in profile["reason"]


def test_recommend_runtime_profile_disables_planner_on_timeout_pressure(tmp_path, monkeypatch):
    """High timeout/circuit rate should disable expensive planner escalation."""
    monkeypatch.setattr(ap, "STORE_PATH", tmp_path / "adaptive_policy.json")
    monkeypatch.setattr(
        ap,
        "list_llm_metrics",
        lambda **kwargs: [
            {"status": "error", "category": "timeout"},
            {"status": "error", "category": "circuit_open"},
            {"status": "error", "category": "timeout"},
            {"status": "ok", "category": None},
        ],
    )

    profile = ap.recommend_runtime_profile("google_flights", llm_mode="light")
    planner_cap = int(
        ap.get_threshold("adaptive_high_timeout_pressure_light_planner_timeout_sec", 20)
    )
    assert profile["light_try_llm_plan_on_fast_plan_failure"] is False
    assert int(profile["llm_light_planner_timeout_sec"]) <= planner_cap
    assert "high_llm_timeout_pressure" in profile["reason"]
