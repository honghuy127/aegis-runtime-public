from core import scenario_runner as sr
from core import site_recovery_dispatch as srd
from core import scenario_recovery_collab as src


def test_google_route_core_only_recovery_plan_strips_date_fills_and_keeps_submit():
    plan = [
        {"action": "fill", "selector": ["input[aria-label*='From']"], "value": "HND"},
        {"action": "fill", "selector": ["input[aria-label*='To']"], "value": "ITM"},
        {"action": "fill", "selector": ["input[aria-label*='Departure']"], "value": "2026-03-01"},
        {"action": "fill", "selector": ["input[aria-label*='Return']"], "value": "2026-03-08"},
        {"action": "click", "selector": ["button[aria-label*='検索']"]},
        {"action": "wait", "selector": [".results"]},
    ]

    out = sr._google_route_core_only_recovery_plan(  # noqa: SLF001
        plan,
        origin="HND",
        dest="ITM",
    )

    roles = [
        sr._infer_fill_role(step)  # noqa: SLF001
        for step in out
        if isinstance(step, dict) and step.get("action") == "fill"
    ]
    assert roles == ["origin", "dest"]
    assert any(
        isinstance(step, dict)
        and step.get("action") == "click"
        for step in out
    )
    assert any(
        isinstance(step, dict)
        and step.get("action") == "wait"
        for step in out
    )


def test_google_recovery_collab_limits_are_bounded_defaults(monkeypatch):
    monkeypatch.setattr(sr, "get_threshold", lambda key, default=None: default)
    out = sr._google_recovery_collab_limits_from_thresholds()  # noqa: SLF001
    assert out["enabled"] is True
    assert out["max_vlm"] == 1
    assert out["max_repair"] == 1
    assert out["max_planner"] == 1
    assert out["route_core_only_first"] is True


def test_site_recovery_collab_limits_dispatch_disables_unknown_site():
    out = srd.collab_limits_from_thresholds(  # noqa: SLF001
        "example_site",
        google_limits_fn=lambda: {"enabled": True},
    )
    assert out["enabled"] is False
    assert out["max_vlm"] == 0
    assert out["max_repair"] == 0
    assert out["max_planner"] == 0


def test_site_recovery_trigger_reason_dispatch_matches_google_phase_a():
    assert srd.collab_trigger_reason("google_flights") == "route_core_before_date_fill_unverified"  # noqa: SLF001
    assert srd.should_attempt_recovery_collab_after_date_failure(  # noqa: SLF001
        site_key="google_flights",
        recovery_mode=True,
        date_failure_reason="route_core_before_date_fill_unverified",
    )
    assert not srd.should_attempt_recovery_collab_after_date_failure(  # noqa: SLF001
        site_key="example_site",
        recovery_mode=True,
        date_failure_reason="route_core_before_date_fill_unverified",
    )


def test_recovery_collab_caps_skip_vlm_repair_and_planner_calls():
    calls = {"vlm": 0, "repair": 0, "planner": 0}
    base_plan = {"steps": [{"action": "fill", "selector": ["x"], "value": "HND"}]}

    deps = {
        "log": type("L", (), {"warning": lambda *a, **k: None, "info": lambda *a, **k: None, "debug": lambda *a, **k: None})(),
        "is_valid_plan": lambda plan: isinstance(plan, dict) and isinstance(plan.get("steps"), list),
        "run_vision_probe": lambda **kwargs: calls.__setitem__("vlm", calls["vlm"] + 1) or {},
        "apply_vision_hints": lambda payload: False,
        "refresh_html": lambda: "<html/>",
        "reprobe_route_core": lambda html: {"ok": False, "reason": "still_unbound"},
        "make_base_plan": lambda failed_plan, vision_hint_payload: base_plan,
        "compose_local_hint_with_notes": lambda *args: "",
        "call_repair_plan": lambda **kwargs: calls.__setitem__("repair", calls["repair"] + 1) or (None, []),
        "call_generate_plan": lambda **kwargs: calls.__setitem__("planner", calls["planner"] + 1) or (None, []),
        "postprocess_plan": lambda plan: plan,
    }
    env = {
        "site_key": "google_flights",
        "recovery_mode": True,
        "limits": {
            "enabled": True,
            "max_vlm": 0,
            "max_repair": 0,
            "max_planner": 0,
            "route_core_only_first": True,
            "planner_timeout_sec": 45,
        },
        "usage": {"vlm": 0, "repair": 0, "planner": 0},
        "trigger_reason": "route_core_before_date_fill_unverified",
    }

    plan, notes = src.try_recovery_collab_followup(
        current_html="<html/>",
        failed_plan={"steps": []},
        route_core_failure=None,
        turn_index=0,
        env=env,
        deps=deps,
    )

    assert plan == base_plan
    assert calls == {"vlm": 0, "repair": 0, "planner": 0}
    assert any("DeterministicFallback" in note for note in notes)


def test_recovery_collab_respects_usage_caps_when_limits_allow_calls():
    calls = {"repair": 0, "planner": 0}
    base_plan = {"steps": [{"action": "wait", "selector": [".results"]}]}

    deps = {
        "log": type("L", (), {"warning": lambda *a, **k: None, "info": lambda *a, **k: None, "debug": lambda *a, **k: None})(),
        "is_valid_plan": lambda plan: isinstance(plan, dict) and isinstance(plan.get("steps"), list),
        "run_vision_probe": lambda **kwargs: {},
        "apply_vision_hints": lambda payload: False,
        "refresh_html": lambda: "<html/>",
        "reprobe_route_core": lambda html: {"ok": False, "reason": "still_unbound"},
        "make_base_plan": lambda failed_plan, vision_hint_payload: base_plan,
        "compose_local_hint_with_notes": lambda *args: "",
        "call_repair_plan": lambda **kwargs: calls.__setitem__("repair", calls["repair"] + 1) or (None, []),
        "call_generate_plan": lambda **kwargs: calls.__setitem__("planner", calls["planner"] + 1) or (None, []),
        "postprocess_plan": lambda plan: plan,
    }
    env = {
        "site_key": "google_flights",
        "recovery_mode": True,
        "limits": {
            "enabled": True,
            "max_vlm": 1,
            "max_repair": 1,
            "max_planner": 1,
            "route_core_only_first": True,
            "planner_timeout_sec": 45,
        },
        "usage": {"vlm": 1, "repair": 1, "planner": 1},
        "trigger_reason": "route_core_before_date_fill_unverified",
    }

    plan, _notes = src.try_recovery_collab_followup(
        current_html="<html/>",
        failed_plan={"steps": []},
        route_core_failure=None,
        turn_index=0,
        env=env,
        deps=deps,
    )

    assert plan == base_plan
    assert calls == {"repair": 0, "planner": 0}
