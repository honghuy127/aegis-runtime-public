from contextlib import contextmanager

import main


@contextmanager
def _noop_call_timeout(_timeout_sec: int):
    yield


def test_foreign_timeout_not_relabelled(monkeypatch):
    monkeypatch.setenv("FLIGHT_WATCHER_SCENARIO_HARD_TIMEOUT_ENABLED", "1")
    monkeypatch.setattr(main, "_call_timeout", _noop_call_timeout)

    def _raise_foreign(**_kwargs):
        raise TimeoutError("browser_action_timeout")

    monkeypatch.setattr(main, "run_agentic_scenario", _raise_foreign)

    try:
        main._run_agentic_scenario_with_timeout(timeout_sec=120, url="", origin="", dest="", depart="")
        assert False, "expected foreign timeout"
    except RuntimeError as exc:
        assert "foreign_timeout" in str(exc)
        assert "Scenario candidate timeout" not in str(exc)
    except TimeoutError as exc:
        assert "Scenario candidate timeout" not in str(exc)


def test_hard_timeout_relabels_candidate_timeout(monkeypatch):
    monkeypatch.setenv("FLIGHT_WATCHER_SCENARIO_HARD_TIMEOUT_ENABLED", "1")

    @contextmanager
    def _hard_timeout(_timeout_sec: int):
        raise main.ScenarioHardTimeout("scenario_hard_timeout_after_1s")
        yield

    monkeypatch.setattr(main, "_call_timeout", _hard_timeout)

    def _noop(**_kwargs):
        return ""

    monkeypatch.setattr(main, "run_agentic_scenario", _noop)

    try:
        main._run_agentic_scenario_with_timeout(timeout_sec=10, url="", origin="", dest="", depart="")
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "Scenario candidate timeout after 10s" in str(exc)