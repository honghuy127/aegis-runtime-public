import importlib.util
from pathlib import Path

from tests.utils.dates import trip_dates


class _RouterStub:
    def get_event_summary(self):
        return {}


class _BrowserStub:
    def content(self):
        return "<html>fallback</html>"


def _load_run_agentic_impl_module():
    repo_root = Path(__file__).resolve().parents[1]
    impl_path = repo_root / "core" / "scenario_runner" / "run_agentic_scenario.py"
    spec = importlib.util.spec_from_file_location(
        "tests._run_agentic_impl_return_builder",
        str(impl_path),
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_scenario_return_callable_returns_html_and_emits_checkpoints(tmp_path):
    ras = _load_run_agentic_impl_module()
    build_callable = ras._build_scenario_return_callable
    depart, return_date = trip_dates(days_ahead_min=7, days_ahead_max=30, trip_min=4, trip_max=10)

    evidence_calls = []
    progress_calls = []
    route_state_calls = []

    def _write_evidence(stage, payload):
        evidence_calls.append((stage, payload))

    def _write_progress_snapshot(**kwargs):
        progress_calls.append(kwargs)

    def _build_route_fallback(**_kwargs):
        return {"route_bind_verdict": {}, "scope_verdicts": {}}

    def _build_extract_verdict(**_kwargs):
        return {"reason": "ok"}

    def _write_route_state_debug(*, run_id, site_key, payload):
        route_state_calls.append((run_id, site_key, payload))

    fn = build_callable(
        scenario_started_at=0.0,
        site_key="google_flights",
        scenario_run_id="run_123",
        router=_RouterStub(),
        url="https://example.test",
        origin="HND",
        dest="ITM",
        depart=depart,
        return_date=return_date,
        graph_stats=None,
        browser=_BrowserStub(),
        write_evidence_checkpoint_fn=_write_evidence,
        write_progress_snapshot_fn=_write_progress_snapshot,
        build_route_state_fallback_fn=_build_route_fallback,
        build_extract_verdict_fn=_build_extract_verdict,
        write_route_state_debug_fn=_write_route_state_debug,
        get_artifacts_dir_fn=lambda _run_id: Path(tmp_path),
    )

    html = fn(
        "<html>ok</html>",
        ready=True,
        reason="scenario_ready",
        scope_class="results",
        route_bound=True,
        route_support="strong",
    )

    assert html == "<html>ok</html>"
    assert evidence_calls and evidence_calls[0][0] == "after_results_ready_check"
    assert progress_calls and progress_calls[0]["stage"] == "scenario_return"
    assert route_state_calls and route_state_calls[0][0] == "run_123"
