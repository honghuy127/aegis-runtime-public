"""Timeout propagation tests for scenario execution."""

from pathlib import Path
import importlib.util
from datetime import date, timedelta

import pytest
import llm.code_model as code_model
import llm.llm_client as llm_client

from core import scenario_runner as sr
from core.plugins.adapters import services_adapter
from core.service_runners import google_flights as gf
from core.scenario_runner import knowledge_helpers as knowledge_helpers_module
from core.scenario_runner.google_flights import ui_actions as gf_ui_actions
from core.scenario_runner import (
    _apply_plugin_readiness_probe,
    _detect_site_interstitial_block,
    _debug_exploration_mode,
    _local_programming_exception_reason,
    _google_search_commit_smart_escalation_skip_reason,
    _google_should_suppress_force_bind_after_date_failure,
    _google_step_trace_local_date_open_failure,
    _google_turn_fill_success_corroborates_route_bind,
    _should_run_vision_page_kind_probe,
    execute_plan,
)
from core.scenario_runner.readiness import has_skyscanner_price_signal
from core.scenario_runner.readiness import is_skyscanner_results_shell_incomplete
from core.scenario_runner.skyscanner import (
    attempt_skyscanner_interstitial_grace,
    attempt_skyscanner_interstitial_fallback_reload,
    detect_skyscanner_interstitial_block,
)
from core.scenario_runner.skyscanner.challenge_adapter import (
    attempt_skyscanner_last_resort_manual,
    validate_skyscanner_interstitial_clearance,
)
from core.scenario_runner.run_agentic.attempt_gate import run_attempt_precheck_and_interstitial_gate


def _load_run_agentic_impl_module():
    impl_path = Path("core/scenario_runner/run_agentic_scenario.py")
    spec = importlib.util.spec_from_file_location(
        "tests._run_agentic_impl_for_test",
        str(impl_path),
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _block_external_model_calls(monkeypatch):
    """Keep timeout tests deterministic even when local env enables VLM/LLM paths."""
    monkeypatch.setenv("FLIGHT_WATCHER_DISABLE_PLUGINS", "true")
    monkeypatch.setenv("FLIGHT_WATCHER_PLUGIN_STRATEGY_ENABLED", "false")

    def _unexpected(*args, **kwargs):  # noqa: ARG001
        raise AssertionError("Unexpected LLM/VLM call in tests/test_scenario_runner_timeouts.py")

    # Disable plugin strategy/probes directly (env flags may be ignored by some code paths).
    monkeypatch.setattr(sr, "plugin_strategy_enabled", lambda: False, raising=False)
    monkeypatch.setattr(sr, "run_service_readiness_hints", lambda *args, **kwargs: {}, raising=False)
    monkeypatch.setattr(sr, "run_service_readiness_probe", lambda *args, **kwargs: {}, raising=False)

    # Block scenario-runner model entry points (vision + planner/repair + scope guards).
    monkeypatch.setattr(sr, "analyze_page_ui_with_vlm", _unexpected, raising=False)
    monkeypatch.setattr(sr, "analyze_filled_route_with_vlm", _unexpected, raising=False)
    monkeypatch.setattr(sr, "assess_trip_product_scope_with_llm", _unexpected, raising=False)
    monkeypatch.setattr(sr, "generate_action_plan", _unexpected, raising=False)
    monkeypatch.setattr(sr, "repair_action_plan", _unexpected, raising=False)
    monkeypatch.setattr(sr, "_call_generate_action_plan_bundle", _unexpected, raising=False)
    monkeypatch.setattr(sr, "_call_repair_action_plan_bundle", _unexpected, raising=False)

    # Belt-and-suspenders: block lower-level LLM client calls in case a new path bypasses sr aliases.
    monkeypatch.setattr(llm_client, "call_llm", _unexpected, raising=False)
    monkeypatch.setattr(code_model, "call_llm", _unexpected, raising=False)


class _TimeoutBrowserStub:
    """Minimal browser stub that raises wall-clock timeout on fill."""

    def fill(self, selector, value, timeout_ms=None):  # noqa: ARG002
        raise TimeoutError("call timeout after 120s")

    def click(self, selector, timeout_ms=None):  # noqa: ARG002
        return None

    def wait(self, selector, timeout_ms=None):  # noqa: ARG002
        return None

    def type_active(self, value, timeout_ms=None):  # noqa: ARG002
        return None

    def activate_field_by_keywords(self, keywords, timeout_ms=None):  # noqa: ARG002
        return False

    def fill_by_keywords(self, keywords, value, timeout_ms=None):  # noqa: ARG002
        return False


def test_execute_plan_propagates_timeout_error():
    """Global wall-clock timeouts must abort immediately, not be retried per selector."""
    plan = [
        {
            "action": "fill",
            "selector": "input[name='origin']",
            "value": "HND",
        }
    ]
    with pytest.raises(TimeoutError):
        execute_plan(_TimeoutBrowserStub(), plan, site_key="google_flights")


def test_execute_plan_skyscanner_depart_fill_soft_fails_instead_of_raising():
    class _BrowserStub:
        def fill(self, selector, value, timeout_ms=None):  # noqa: ARG002
            raise RuntimeError("no_date_input_surface")

        def click(self, selector, timeout_ms=None):  # noqa: ARG002
            return None

        def wait(self, selector, timeout_ms=None):  # noqa: ARG002
            return None

        def type_active(self, value, timeout_ms=None):  # noqa: ARG002
            raise RuntimeError("no_active_typing_target")

        def activate_field_by_keywords(self, keywords, timeout_ms=None):  # noqa: ARG002
            return False

        def fill_by_keywords(self, keywords, value, timeout_ms=None):  # noqa: ARG002
            return False

        def content(self):
            return "<html><body></body></html>"

    plan = [
        {
            "action": "fill",
            "role": "depart",
            "selector": [
                "input[name*='depart']",
                "input[name*='outbound']",
                "input[placeholder*='Depart']",
            ],
            "value": "2026-05-02",
        }
    ]

    trace = execute_plan(_BrowserStub(), plan, site_key="skyscanner")
    assert isinstance(trace, list) and trace
    assert trace[0].get("status") == "soft_fail"
    assert trace[0].get("role") == "depart"


def test_execute_plan_skyscanner_skips_search_and_wait_after_local_date_failure(monkeypatch):
    class _BrowserStub:
        def __init__(self):
            self.click_calls = 0
            self.wait_calls = 0

        def fill(self, selector, value, timeout_ms=None):  # noqa: ARG002
            return None

        def click(self, selector, timeout_ms=None):  # noqa: ARG002
            self.click_calls += 1
            return None

        def wait(self, selector, timeout_ms=None):  # noqa: ARG002
            self.wait_calls += 1
            return None

        def type_active(self, value, timeout_ms=None):  # noqa: ARG002
            raise RuntimeError("no_active_typing_target")

        def activate_field_by_keywords(self, keywords, timeout_ms=None):  # noqa: ARG002
            return False

        def fill_by_keywords(self, keywords, value, timeout_ms=None):  # noqa: ARG002
            return False

        def content(self):
            return "<html><body></body></html>"

    monkeypatch.setattr(
        sr,
        "_skyscanner_fill_date_via_picker",
        lambda **kwargs: {  # noqa: ARG005
            "ok": False,
            "reason": "calendar_not_open",
            "selector_used": "",
            "evidence": {"calendar.failure_stage": "open"},
        },
    )

    plan = [
        {
            "action": "fill",
            "role": "depart",
            "selector": ["button[data-testid='depart-btn']"],
            "value": "2026-05-02",
        },
        {
            "action": "click",
            "selector": ["button:has-text('検索')"],
        },
        {
            "action": "wait",
            "selector": [
                "[data-testid*='search-results']",
                "[data-testid*='itinerary']",
                "[data-testid*='day-view']",
            ],
        },
    ]

    browser = _BrowserStub()
    trace = execute_plan(browser, plan, site_key="skyscanner")

    assert len(trace) == 3
    assert trace[0].get("status") == "soft_fail"
    assert trace[0].get("error") == "calendar_not_open"
    assert trace[1].get("status") == "soft_skip"
    assert trace[1].get("error") == "skip_search_after_local_date_fail"
    assert trace[2].get("status") == "soft_skip"
    assert trace[2].get("error") == "skip_wait_after_local_date_fail"
    assert browser.click_calls == 0
    assert browser.wait_calls == 0


def test_execute_plan_skyscanner_route_url_bound_soft_passes_origin_dest_fill_failures(monkeypatch):
    class _Page:
        url = "https://www.skyscanner.com/transport/flights/fuk/hnd/260502/260608/?rtn=1"

    class _BrowserStub:
        def __init__(self):
            self.page = _Page()

        def fill(self, selector, value, timeout_ms=None):  # noqa: ARG002
            raise RuntimeError("input_fill_failed")

        def click(self, selector, timeout_ms=None):  # noqa: ARG002
            return None

        def wait(self, selector, timeout_ms=None):  # noqa: ARG002
            return None

        def type_active(self, value, timeout_ms=None):  # noqa: ARG002
            raise RuntimeError("no_active_typing_target")

        def activate_field_by_keywords(self, keywords, timeout_ms=None):  # noqa: ARG002
            return False

        def fill_by_keywords(self, keywords, value, timeout_ms=None):  # noqa: ARG002
            return False

        def content(self):
            return "<html><body><main id='app-root'></main></body></html>"

    monkeypatch.setattr(
        sr,
        "_skyscanner_fill_and_commit_location",
        lambda **kwargs: {  # noqa: ARG005
            "ok": False,
            "reason": "input_fill_failed",
            "selector_used": "",
            "evidence": {},
        },
    )

    plan = [
        {
            "action": "fill",
            "role": "origin",
            "selector": ["input[name='originInput-search']"],
            "value": "FUK",
        },
        {
            "action": "fill",
            "role": "dest",
            "selector": ["input[name='destinationInput-search']"],
            "value": "HND",
        },
    ]

    trace = execute_plan(_BrowserStub(), plan, site_key="skyscanner")
    assert len(trace) == 2
    assert trace[0].get("status") == "ok"
    assert trace[1].get("status") == "ok"
    assert (trace[0].get("fill_commit") or {}).get("reason") == "route_url_already_bound"
    assert (trace[1].get("fill_commit") or {}).get("reason") == "route_url_already_bound"


def test_execute_plan_selector_fanout_is_capped_by_step_budget():
    class _BrowserStub:
        def __init__(self):
            self.fill_calls = []

        def fill(self, selector, value, timeout_ms=None):  # noqa: ARG002
            self.fill_calls.append(str(selector))
            raise RuntimeError("selector_not_found")

        def click(self, selector, timeout_ms=None):  # noqa: ARG002
            return None

        def wait(self, selector, timeout_ms=None):  # noqa: ARG002
            return None

        def type_active(self, value, timeout_ms=None):  # noqa: ARG002
            return None

        def activate_field_by_keywords(self, keywords, timeout_ms=None):  # noqa: ARG002
            return False

        def fill_by_keywords(self, keywords, value, timeout_ms=None):  # noqa: ARG002
            return False

        def content(self):
            return "<html><body></body></html>"

    browser = _BrowserStub()
    plan = [
        {
            "action": "fill",
            "role": "depart",
            "selector": [
                "s1",
                "s2",
                "s3",
                "s4",
                "s5",
                "s6",
                "s7",
                "s8",
            ],
            "value": "2026-05-02",
        }
    ]

    execute_plan(browser, plan, site_key="skyscanner")
    # Default caps (skyscanner): step=30000ms, per-selector=4000ms, reserve=3000ms => floor(27000/4000)=6
    assert len(browser.fill_calls) <= 6


def test_run_agentic_impl_exports_scope_guard_model_symbols():
    mod = _load_run_agentic_impl_module()
    assert callable(getattr(mod, "analyze_page_ui_with_vlm", None))
    assert callable(getattr(mod, "assess_trip_product_scope_with_llm", None))


def test_safe_click_first_match_skyscanner_does_not_expand_large_fallback_bank():
    class _BrowserStub:
        def __init__(self):
            self.click_calls = []

        def click(self, selector, timeout_ms=None):  # noqa: ARG002
            self.click_calls.append(str(selector))
            if str(selector) == "button:has-text('検索')":
                return None
            raise RuntimeError("not_clickable")

    browser = _BrowserStub()
    err, used = sr._safe_click_first_match(  # noqa: SLF001
        browser,
        [
            "[role='button']:has-text('Search flights')",
            "[role='button']:has-text('Search')",
            "button:has-text('検索')",
        ],
        timeout_ms=5000,
        require_clickable=True,
        site_key="skyscanner",
    )
    assert err is None
    assert used == "button:has-text('検索')"
    # Must stay within explicit selector list; no large fallback expansion.
    assert browser.click_calls == [
        "[role='button']:has-text('Search flights')",
        "[role='button']:has-text('Search')",
        "button:has-text('検索')",
    ]


def test_safe_click_first_match_skyscanner_human_mimic_prefers_visible_and_uses_longer_timeout():
    class _PageStub:
        def is_visible(self, selector, timeout=0):  # noqa: ARG002
            return str(selector) == "button:has-text('検索')"

    class _BrowserStub:
        def __init__(self):
            self.page = _PageStub()
            self.human_mimic = True
            self.min_action_delay_ms = 1000
            self.max_action_delay_ms = 5000
            self.click_calls = []

        def click(self, selector, timeout_ms=None):  # noqa: ARG002
            self.click_calls.append((str(selector), int(timeout_ms or 0)))
            if str(selector) == "button:has-text('検索')":
                return None
            raise RuntimeError("not_clickable")

    browser = _BrowserStub()
    err, used = sr._safe_click_first_match(  # noqa: SLF001
        browser,
        [
            "[role='button']:has-text('Search flights')",
            "[role='button']:has-text('Search')",
            "button:has-text('検索')",
        ],
        timeout_ms=5000,
        require_clickable=True,
        site_key="skyscanner",
    )
    assert err is None
    assert used == "button:has-text('検索')"
    assert browser.click_calls == [("button:has-text('検索')", 7200)]


def test_google_turn_fill_success_corroboration_requires_all_core_fill_roles():
    step_trace = [
        {"action": "fill", "role": "origin", "status": "ok"},
        {"action": "fill", "role": "dest", "status": "ok"},
        {"action": "fill", "role": "depart", "status": "ok"},
        {"action": "fill", "role": "return", "status": "ok"},
        {"action": "click", "status": "ok"},
    ]
    out = _google_turn_fill_success_corroborates_route_bind(step_trace)
    assert out["ok"] is True
    assert out["roles"] == {"origin": True, "dest": True, "depart": True, "return": True}


def test_google_turn_fill_success_corroboration_rejects_missing_return_fill():
    step_trace = [
        {"action": "fill", "role": "origin", "status": "ok"},
        {"action": "fill", "role": "dest", "status": "ok"},
        {"action": "fill", "role": "depart", "status": "ok"},
    ]
    out = _google_turn_fill_success_corroborates_route_bind(step_trace)
    assert out["ok"] is False
    assert out["roles"]["return"] is False


def test_is_results_ready_google_rejects_homepage_suggestions_false_positive():
    html = """
    <html><body>
      <div>Flights</div>
      <button aria-label="Search">Search</button>
      <div>Fukuoka FUK</div>
      <div>Tokyo HND</div>
      <div>Sat, May 2</div>
      <div>Mon, Jun 8</div>
      <div>Explore destinations</div>
      <div>from ¥10,420</div>
    </body></html>
    """
    assert (
        sr._is_results_ready(  # noqa: SLF001
            html,
            site_key="google_flights",
            origin="FUK",
            dest="HND",
            depart="2026-05-02",
            return_date="2026-06-08",
        )
        is False
    )


def test_is_results_ready_google_accepts_contextual_price_card():
    html = """
    <html><body>
      <div aria-label="FUK to HND Sat, May 2 Mon, Jun 8 from ¥10,420"></div>
      <div>Flights results</div>
    </body></html>
    """
    assert (
        sr._is_results_ready(  # noqa: SLF001
            html,
            site_key="google_flights",
            origin="FUK",
            dest="HND",
            depart="2026-05-02",
            return_date="2026-06-08",
        )
        is True
    )


def test_is_results_ready_google_accepts_results_shell_without_contextual_price_card():
    html = """
    <html><body>
      <main role="main">
        <h2>Search results</h2>
        <div role="alert">31 results returned.</div>
      </main>
      <input role="combobox" aria-label="Where from? Fukuoka FUK" value="Fukuoka" />
      <input role="combobox" aria-label="Where to? Tokyo HND" value="Tokyo" />
      <input aria-label="Departure" value="Sat, May 2" />
      <input aria-label="Return" value="Mon, Jun 8" />
      <div>¥10,420</div>
    </body></html>
    """
    assert (
        sr._is_results_ready(  # noqa: SLF001
            html,
            site_key="google_flights",
            origin="FUK",
            dest="HND",
            depart="2026-05-02",
            return_date="2026-06-08",
        )
        is True
    )
    ok, reason = sr._google_deeplink_probe_status(  # noqa: SLF001
        html,
        "https://www.google.com/travel/flights?hl=en&gl=JP#flt=FUK.HND.2026-05-02*HND.FUK.2026-06-08;c:JPY",
    )
    assert ok is True
    assert reason in {"results_shell_no_contextual_price_card", "ok"}


def test_is_results_ready_google_accepts_results_shell_before_price_tokens_render():
    html = """
    <html><body>
      <main role="main">
        <h2>Search results</h2>
        <div role="alert">31 results returned.</div>
      </main>
      <input role="combobox" aria-label="Where from? Fukuoka FUK" value="Fukuoka" />
      <input role="combobox" aria-label="Where to? Tokyo HND" value="Tokyo" />
      <input aria-label="Departure" value="Sat, May 2" />
      <input aria-label="Return" value="Mon, Jun 8" />
    </body></html>
    """
    assert (
        sr._is_results_ready(  # noqa: SLF001
            html,
            site_key="google_flights",
            origin="FUK",
            dest="HND",
            depart="2026-05-02",
            return_date="2026-06-08",
        )
        is True
    )


def test_is_results_ready_skyscanner_rejects_landing_false_positive():
    html = """
    <html><body>
      <div>Flights</div>
      <div>出発地</div>
      <div>目的地</div>
      <div>国、都市または空港</div>
      <div>from ¥10,420</div>
    </body></html>
    """
    assert sr._is_results_ready(html, site_key="skyscanner") is False  # noqa: SLF001


def test_is_results_ready_skyscanner_accepts_results_shell():
    html = """
    <html><body>
      <h1>Search results</h1>
      <div>Sort by: Cheapest</div>
      <div>Filters</div>
      <div>1 stop</div>
      <div>¥24,900</div>
    </body></html>
    """
    assert sr._is_results_ready(html, site_key="skyscanner") is True  # noqa: SLF001


def test_is_results_ready_skyscanner_rejects_transport_results_url_without_price_tokens():
    html = """
    <html><body>
      <main>Loading results shell</main>
    </body></html>
    """
    assert (
        sr._is_results_ready(  # noqa: SLF001
            html,
            site_key="skyscanner",
            page_url="https://www.skyscanner.com/transport/flights/fuk/hnd/260502/260608/",
        )
        is False
    )


def test_is_results_ready_skyscanner_rejects_flights_home_url_false_positive():
    html = """
    <html><body>
      <div>Sort by: Cheapest</div>
      <div>from ¥10,420</div>
      <div>出発地</div>
      <div>目的地</div>
    </body></html>
    """
    assert (
        sr._is_results_ready(  # noqa: SLF001
            html,
            site_key="skyscanner",
            page_url="https://www.skyscanner.com/flights",
        )
        is False
    )


def test_is_results_ready_skyscanner_rejects_hotels_url_even_with_price_and_results_tokens():
    html = """
    <html><body>
      <h1>Search results</h1>
      <div>Sort by: Cheapest</div>
      <div>Filters</div>
      <div>¥24,900</div>
    </body></html>
    """
    assert (
        sr._is_results_ready(  # noqa: SLF001
            html,
            site_key="skyscanner",
            page_url="https://www.skyscanner.com/hotels/search?adults=2",
        )
        is False
    )


def test_is_results_ready_skyscanner_rejects_hotels_tab_selected_false_positive():
    html = """
    <html><body>
      <a id="skhot" role="tab" aria-selected="true">ホテル</a>
      <a id="airli" role="tab" aria-selected="false">航空券</a>
      <div>Sort by: Cheapest</div>
      <div>Filters</div>
      <div>¥24,900</div>
    </body></html>
    """
    assert sr._is_results_ready(html, site_key="skyscanner") is False  # noqa: SLF001


def test_skyscanner_results_shell_incomplete_detects_script_shell():
    html = """
    <html><body>
      <script>
        window.__ctx = {"pageName":"day-view","flightSearch":{"originId":"FUK","destinationId":"HND"}};
      </script>
      <h1>福岡発の格安国内航空券</h1>
    </body></html>
    """
    assert (
        is_skyscanner_results_shell_incomplete(
            html,
            page_url="https://www.skyscanner.com/transport/flights/fuk/hnd/260502/260608/",
        )
        is True
    )


def test_skyscanner_results_shell_incomplete_detects_flights_dayview_blank_shell():
    html = """
    <html><body>
      <script>
        window.__ctx = {
          "pageName":"flights.dayview",
          "pageType":"flights:dayview",
          "searchParams":{"originId":"FUK","destinationId":"HND"}
        };
      </script>
      <h1>福岡発の格安国内航空券</h1>
    </body></html>
    """
    assert (
        is_skyscanner_results_shell_incomplete(
            html,
            page_url="https://www.skyscanner.com/transport/flights/fuk/hnd/260502/260608/",
        )
        is True
    )


def test_skyscanner_results_shell_incomplete_false_when_price_signal_present():
    html = """
    <html><body>
      <main>
        <div>Sort by: Cheapest</div>
        <div>¥24,900</div>
      </main>
      <script>
        window.__ctx = {"pageName":"day-view","flightSearch":{"originId":"FUK","destinationId":"HND"}};
      </script>
    </body></html>
    """
    assert (
        is_skyscanner_results_shell_incomplete(
            html,
            page_url="https://www.skyscanner.com/transport/flights/fuk/hnd/260502/260608/",
        )
        is False
    )


def test_skyscanner_date_value_already_bound_from_url():
    depart_dt = date.today() + timedelta(days=59)
    return_dt = depart_dt + timedelta(days=37)
    depart_iso = depart_dt.strftime("%Y-%m-%d")
    return_iso = return_dt.strftime("%Y-%m-%d")
    depart_yy = depart_dt.strftime("%y%m%d")
    return_yy = return_dt.strftime("%y%m%d")

    class _Page:
        url = f"https://www.skyscanner.com/transport/flights/fuk/hnd/{depart_yy}/{return_yy}/"

    class _Browser:
        page = _Page()

    assert (
        sr._is_skyscanner_date_value_already_bound_from_url(  # noqa: SLF001
            _Browser(),
            role="depart",
            value=depart_iso,
        )
        is True
    )
    assert (
        sr._is_skyscanner_date_value_already_bound_from_url(  # noqa: SLF001
            _Browser(),
            role="return",
            value=return_iso,
        )
        is True
    )
    assert (
        sr._is_skyscanner_date_value_already_bound_from_url(  # noqa: SLF001
            _Browser(),
            role="return",
            value=(return_dt + timedelta(days=1)).strftime("%Y-%m-%d"),
        )
        is False
    )


def test_execute_plan_skyscanner_skip_search_controls_on_results_url():
    class _Page:
        url = "https://www.skyscanner.com/transport/flights/fuk/hnd/260502/260608/?rtn=1"

    class _BrowserStub:
        def __init__(self):
            self.page = _Page()

        def click(self, selector, timeout_ms=None):  # noqa: ARG002
            raise AssertionError("click should be skipped on results url")

        def wait(self, selector, timeout_ms=None):  # noqa: ARG002
            return None

        def fill(self, selector, value, timeout_ms=None):  # noqa: ARG002
            return None

        def content(self):
            return "<html><body><main>results</main></body></html>"

    plan = [
        {
            "action": "wait",
            "selector": ["button:has-text('検索')", "button[aria-label*='Search']"],
        },
        {
            "action": "click",
            "selector": ["button:has-text('検索')", "button[aria-label*='Search']"],
        },
    ]
    trace = execute_plan(_BrowserStub(), plan, site_key="skyscanner")
    assert len(trace) == 2
    assert trace[0].get("status") == "soft_skip"
    assert trace[0].get("error") == "skip_search_controls_on_results_url"
    assert trace[1].get("status") == "soft_skip"
    assert trace[1].get("error") == "skip_search_controls_on_results_url"


def test_execute_plan_skyscanner_results_wait_not_skipped_on_results_url():
    class _Page:
        url = "https://www.skyscanner.com/transport/flights/fuk/hnd/260502/260608/?rtn=1"

    class _BrowserStub:
        def __init__(self):
            self.page = _Page()
            self.wait_calls = []

        def click(self, selector, timeout_ms=None):  # noqa: ARG002
            raise AssertionError("click is not expected in this test")

        def wait(self, selector, timeout_ms=None):  # noqa: ARG002
            self.wait_calls.append(str(selector))
            return None

        def fill(self, selector, value, timeout_ms=None):  # noqa: ARG002
            return None

        def content(self):
            return "<html><body><main>results</main></body></html>"

    plan = [
        {
            "action": "wait",
            "selector": [
                "[data-testid*='search-results']",
                "[data-testid*='day-view']",
                "main [role='main']",
            ],
        },
    ]
    browser = _BrowserStub()
    trace = execute_plan(browser, plan, site_key="skyscanner")
    assert len(trace) == 1
    assert trace[0].get("status") == "ok"
    assert trace[0].get("error") in {"", None}
    assert browser.wait_calls


def test_execute_plan_skyscanner_results_overlay_dismiss_probe_runs_on_results_url(monkeypatch):
    calls = []

    class _Page:
        url = "https://www.skyscanner.com/transport/flights/fuk/hnd/260502/260608/?rtn=1"

    class _BrowserStub:
        def __init__(self):
            self.page = _Page()

        def click(self, selector, timeout_ms=None):  # noqa: ARG002
            raise AssertionError("search click should still be skipped on results url")

        def wait(self, selector, timeout_ms=None):  # noqa: ARG002
            return None

        def fill(self, selector, value, timeout_ms=None):  # noqa: ARG002
            return None

    monkeypatch.setattr(
        sr,
        "_skyscanner_dismiss_results_overlay",
        lambda **kwargs: calls.append(dict(kwargs)) or {  # noqa: ARG005
            "ok": True,
            "reason": "overlay_not_present",
            "selector_used": "",
            "evidence": {},
        },
    )

    plan = [
        {
            "action": "wait",
            "selector": ["button:has-text('検索')", "button[aria-label*='Search']"],
        }
    ]
    trace = execute_plan(_BrowserStub(), plan, site_key="skyscanner")
    assert len(calls) == 1
    assert len(trace) == 1
    assert trace[0].get("status") == "soft_skip"
    assert trace[0].get("error") == "skip_search_controls_on_results_url"


def test_execute_plan_skyscanner_skips_steps_on_interstitial_surface():
    class _Page:
        url = "https://www.skyscanner.com/sttc/px/captcha-v2/index.html?url=Lw=="

    class _BrowserStub:
        def __init__(self):
            self.page = _Page()
            self.wait_calls = 0

        def click(self, selector, timeout_ms=None):  # noqa: ARG002
            raise AssertionError("click should be skipped on interstitial surface")

        def wait(self, selector, timeout_ms=None):  # noqa: ARG002
            self.wait_calls += 1
            raise AssertionError("wait should be skipped on interstitial surface")

        def fill(self, selector, value, timeout_ms=None):  # noqa: ARG002
            raise AssertionError("fill should be skipped on interstitial surface")

    plan = [
        {"action": "wait", "selector": ["[data-testid*='search-results']"]},
        {"action": "click", "selector": ["button:has-text('検索')"]},
    ]
    trace = execute_plan(_BrowserStub(), plan, site_key="skyscanner")
    assert len(trace) == 2
    assert trace[0].get("status") == "soft_skip"
    assert trace[0].get("error") == "skip_step_on_interstitial_surface"
    assert trace[1].get("status") == "soft_skip"
    assert trace[1].get("error") == "skip_step_on_interstitial_surface"


def test_execute_plan_skyscanner_hotels_context_recovers_before_fill(monkeypatch):
    class _Page:
        def __init__(self):
            self.url = "https://www.skyscanner.com/hotels/search?x=1"

    class _BrowserStub:
        def __init__(self):
            self.page = _Page()
            self.fill_calls = 0

        def click(self, selector, timeout_ms=None):  # noqa: ARG002
            return None

        def wait(self, selector, timeout_ms=None):  # noqa: ARG002
            return None

        def fill(self, selector, value, timeout_ms=None):  # noqa: ARG002
            self.fill_calls += 1
            return None

        def content(self):
            return "<html><body></body></html>"

    def _recover(browser, timeout_ms=0):  # noqa: ARG001
        browser.page.url = "https://www.skyscanner.com/flights"
        return {"ok": True, "reason": "rebound_to_flights", "selector_used": "a#airli"}

    monkeypatch.setattr(sr, "_ensure_skyscanner_flights_context", _recover)
    monkeypatch.setattr(
        sr,
        "_skyscanner_fill_and_commit_location",
        lambda **kwargs: {  # noqa: ARG005
            "ok": True,
            "reason": "combobox_fill_success",
            "selector_used": "input[name='originInput-search']",
            "evidence": {},
        },
    )

    plan = [
        {"action": "fill", "role": "origin", "selector": ["input[name='originInput-search']"], "value": "FUK"},
    ]
    browser = _BrowserStub()
    trace = execute_plan(browser, plan, site_key="skyscanner")
    assert len(trace) == 1
    assert trace[0].get("status") == "ok"
    assert browser.page.url.endswith("/flights")


def test_has_skyscanner_price_signal_rejects_script_only_cheapest_tokens():
    html = """
    <html><head>
      <script>
        window.__ctx = {"pageName":"day-view","label":"最安","note":"cheapest"};
      </script>
    </head><body>
      <main id="app-root"></main>
    </body></html>
    """
    assert has_skyscanner_price_signal(html) is False


def test_has_skyscanner_price_signal_accepts_visible_label_plus_amount():
    html = """
    <html><body>
      <main>
        <div>最安 ¥12,340</div>
        <div>Sort by: Cheapest</div>
      </main>
    </body></html>
    """
    assert has_skyscanner_price_signal(html) is True


def test_google_search_commit_requires_contextual_results_transition(monkeypatch):
    class _PageStub:
        def __init__(self):
            self.keyboard = self

        def evaluate(self, script):  # noqa: ARG002
            return False

        def press(self, key):  # noqa: ARG002
            return None

        def wait_for_timeout(self, ms):  # noqa: ARG002
            return None

    class _BrowserStub:
        def __init__(self):
            self.page = _PageStub()
            self.clicked = []

        def click(self, selector, timeout_ms=None, no_wait_after=False):  # noqa: ARG002
            self.clicked.append(selector)
            return None

        def wait(self, selector, timeout_ms=None):  # noqa: ARG002
            # Simulate weak selectors like [role='main'] always available.
            return None

        def content(self):
            return "<html><body><main role='main'>Flights</main><button aria-label='Search'>Search</button><div>from ¥10,420</div></body></html>"

    monkeypatch.setattr(sr, "_is_results_ready", lambda *args, **kwargs: False)
    monkeypatch.setattr(sr, "_google_deeplink_probe_status", lambda html, url: (False, "missing_contextual_price_card"))

    out = sr._google_search_and_commit(  # noqa: SLF001
        _BrowserStub(),
        selectors=["button[aria-label*='Search']"],
        timeout_ms=350,
        page_url="https://www.google.com/travel/flights?hl=en&gl=JP#flt=FUK.HND.2026-05-02*HND.FUK.2026-06-08;c:JPY",
        origin="FUK",
        dest="HND",
        depart="2026-05-02",
        return_date="2026-06-08",
    )

    assert out["ok"] is False
    assert out["error"] == "search_commit_no_results_transition"
    assert out["results_signal_found"] is False
    assert isinstance(out.get("probe_pre"), dict)
    assert isinstance(out.get("probe_post"), dict)
    assert "contextual_ready" in out["probe_pre"]
    assert "deeplink_probe_reason" in out["probe_post"]
    assert isinstance(out.get("elapsed_ms"), int)
    assert out.get("search_click_attempts") >= 0
    assert out.get("selector_candidates_count") >= 0
    assert out.get("results_candidates_count") >= 0
    assert "results_wait_timeout_ms" in out
    assert "post_click_ready_timeout_ms" in out


def test_google_search_commit_applies_contextual_wait_floor(monkeypatch):
    class _PageStub:
        def __init__(self):
            self.keyboard = self

        def evaluate(self, script):  # noqa: ARG002
            return False

        def press(self, key):  # noqa: ARG002
            return None

        def wait_for_timeout(self, ms):  # noqa: ARG002
            return None

    class _BrowserStub:
        def __init__(self):
            self.page = _PageStub()

        def click(self, selector, timeout_ms=None, no_wait_after=False):  # noqa: ARG002
            return None

        def wait(self, selector, timeout_ms=None):  # noqa: ARG002
            return None

        def content(self):
            return "<html><body><main role='main'>Flights</main><button aria-label='Search'>Search</button><div>from ¥10,420</div></body></html>"

    monkeypatch.setattr(sr, "_is_results_ready", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        sr,
        "_google_deeplink_probe_status",
        lambda html, url: (False, "missing_contextual_price_card"),
    )

    original_get_threshold = gf_ui_actions.get_threshold

    def _fake_get_threshold(key, default=None):
        if key == "browser_search_results_wait_timeout_ms":
            return 5000
        if key == "browser_search_results_contextual_min_wait_ms":
            return 2500
        return original_get_threshold(key, default)

    monkeypatch.setattr(gf_ui_actions, "get_threshold", _fake_get_threshold)

    out = sr._google_search_and_commit(  # noqa: SLF001
        _BrowserStub(),
        selectors=["button[aria-label*='Search']"],
        timeout_ms=350,
        page_url="https://www.google.com/travel/flights?hl=en&gl=JP#flt=FUK.HND.2026-05-02*HND.FUK.2026-06-08;c:JPY",
        origin="FUK",
        dest="HND",
        depart="2026-05-02",
        return_date="2026-06-08",
    )

    assert int(out.get("results_wait_timeout_ms") or 0) >= 2500


def test_google_search_commit_skips_click_when_pre_probe_already_ready(monkeypatch):
    class _PageStub:
        def __init__(self):
            self.keyboard = self
            self.url = "https://www.google.com/travel/flights/search?tfs=CBwQAhoeEgoyMDI2LTA1LTAyagcIARIDRlVLcgcIARIDSE5EGh4SCjIwMjYtMDYtMDhqBwgBEgNITkRyBwgBEgNGVUtAAUgBcAGCAQsI____________AZgBAQ&hl=en&gl=JP"

        def evaluate(self, script):  # noqa: ARG002
            return False

        def press(self, key):  # noqa: ARG002
            return None

        def wait_for_timeout(self, ms):  # noqa: ARG002
            return None

    class _BrowserStub:
        def __init__(self):
            self.page = _PageStub()
            self.clicked = []

        def click(self, selector, timeout_ms=None, no_wait_after=False):  # noqa: ARG002
            self.clicked.append(selector)
            raise RuntimeError("should_not_click_when_already_ready")

        def wait(self, selector, timeout_ms=None):  # noqa: ARG002
            return None

        def content(self):
            return """
            <html><body>
              <main role="main"><h2>Search results</h2><div role="alert">31 results returned.</div></main>
              <input role="combobox" aria-label="Where from? Fukuoka FUK" value="Fukuoka" />
              <input role="combobox" aria-label="Where to? Tokyo HND" value="Tokyo" />
              <input aria-label="Departure" value="Sat, May 2" />
              <input aria-label="Return" value="Mon, Jun 8" />
            </body></html>
            """

    # No contextual price card yet, but the Google results shell is valid.
    # Must be on /search page to qualify for optimization (not on form page).
    out = sr._google_search_and_commit(  # noqa: SLF001
        _BrowserStub(),
        selectors=["button[aria-label*='Search']"],
        timeout_ms=350,
        page_url="https://www.google.com/travel/flights/search?tfs=CBwQAhoeEgoyMDI2LTA1LTAyagcIARIDRlVLcgcIARIDSE5EGh4SCjIwMjYtMDYtMDhqBwgBEgNITkRyBwgBEgNGVUtAAUgBcAGCAQsI____________AZgBAQ&hl=en&gl=JP",
        origin="FUK",
        dest="HND",
        depart="2026-05-02",
        return_date="2026-06-08",
    )

    assert out["ok"] is True
    assert out["strategy"] == "already_ready_pre_click"
    assert out["results_signal_found"] is True
    assert (out.get("probe_pre") or {}).get("results_probe_ready") is True


def test_google_search_commit_requires_search_url_to_skip_click(monkeypatch):
    """Regression test: ensure we don't skip search click on form page even if HTML looks like results."""
    class _PageStub:
        def __init__(self):
            self.keyboard = self
            # Form page URL WITHOUT /search - this is the key difference
            self.url = "https://www.google.com/travel/flights?tfs=CBwQARoeEgoyMDI2LTA1LTAyagcIARIDRlVLcgcIARIDSE5EGh4SCjIwMjYtMDYtMDhqBwgBEgNITkRyBwgBEgNGVUtAAUgBcAGCAQsI____________AZgBAQ&tfu=KgIIAw&hl=en&gl=JP"

        def evaluate(self, script):  # noqa: ARG002
            return False

        def press(self, key):  # noqa: ARG002
            return None

        def wait_for_timeout(self, ms):  # noqa: ARG002
            return None

    class _BrowserStub:
        def __init__(self):
            self.page = _PageStub()
            self.clicked = False

        def click(self, selector, timeout_ms=None, no_wait_after=False):  # noqa: ARG002
            self.clicked = True
            return None

        def wait(self, selector, timeout_ms=None):  # noqa: ARG002
            return None

        def content(self):
            return """
            <html><body>
              <main role="main"><h2>Search results</h2><div role="alert">31 results returned.</div></main>
              <input role="combobox" aria-label="Where from? Fukuoka FUK" value="Fukuoka" />
              <input role="combobox" aria-label="Where to? Tokyo HND" value="Tokyo" />
              <input aria-label="Departure" value="Sat, May 2" />
              <input aria-label="Return" value="Mon, Jun 8" />
            </body></html>
            """

    # HTML looks like results, but URL is still form page (no /search).
    # We should NOT skip the search click in this case - must click to navigate to actual results.
    browser_stub = _BrowserStub()
    out = sr._google_search_and_commit(  # noqa: SLF001
        browser_stub,
        selectors=["button[aria-label*='Search']"],
        timeout_ms=350,
        page_url="https://www.google.com/travel/flights?tfs=CBwQARoeEgoyMDI2LTA1LTAyagcIARIDRlVLcgcIARIDSE5EGh4SCjIwMjYtMDYtMDhqBwgBEgNITkRyBwgBEgNGVUtAAUgBcAGCAQsI____________AZgBAQ&tfu=KgIIAw&hl=en&gl=JP",
        origin="FUK",
        dest="HND",
        depart="2026-05-02",
        return_date="2026-06-08",
    )

    # Verify we attempted to click the search button instead of skipping
    assert browser_stub.clicked is True
    assert out["strategy"] != "already_ready_pre_click"


def test_google_search_commit_non_deeplink_search_url_skips_deeplink_probe(monkeypatch):
    class _PageStub:
        def __init__(self):
            self.keyboard = self
            self.url = "https://www.google.com/travel/flights/search?tfs=CBwQAhoeEgoyMDI2LTA1LTAyagcIARIDRlVLcgcIARIDSE5E&hl=en&gl=JP"

        def evaluate(self, script):  # noqa: ARG002
            return False

        def press(self, key):  # noqa: ARG002
            return None

        def wait_for_timeout(self, ms):  # noqa: ARG002
            return None

    class _BrowserStub:
        def __init__(self):
            self.page = _PageStub()

        def click(self, selector, timeout_ms=None, no_wait_after=False):  # noqa: ARG002
            raise RuntimeError("should_not_click_when_already_ready")

        def wait(self, selector, timeout_ms=None):  # noqa: ARG002
            return None

        def content(self):
            return """
            <html><body>
              <main role="main"><h2>Search results</h2><div role="alert">31 results returned.</div></main>
              <input role="combobox" aria-label="Where from? Fukuoka FUK" value="Fukuoka" />
              <input role="combobox" aria-label="Where to? Tokyo HND" value="Tokyo" />
              <input aria-label="Departure" value="Sat, May 2" />
              <input aria-label="Return" value="Mon, Jun 8" />
            </body></html>
            """

    def _probe_should_not_run(html, url):  # noqa: ARG001
        raise AssertionError("deeplink probe should not run for non-deeplink URLs")

    monkeypatch.setattr(sr, "_google_deeplink_probe_status", _probe_should_not_run)

    out = sr._google_search_and_commit(  # noqa: SLF001
        _BrowserStub(),
        selectors=["button[aria-label*='Search']"],
        timeout_ms=350,
        page_url="https://www.google.com/travel/flights?hl=en&gl=JP",
        origin="FUK",
        dest="HND",
        depart="2026-05-02",
        return_date="2026-06-08",
    )

    assert out["ok"] is True
    assert out["strategy"] == "already_ready_pre_click"
    assert (out.get("probe_pre") or {}).get("deeplink_probe_reason", "") == ""


def test_google_search_commit_prefers_exact_visible_search_button(monkeypatch):
    class _LocatorNthStub:
        def __init__(self, page, selector, idx):
            self._page = page
            self._selector = selector
            self._idx = idx

        def click(self, timeout=None, no_wait_after=False):  # noqa: ARG002
            self._page.direct_clicks.append((self._selector, self._idx))
            return None

    class _LocatorStub:
        def __init__(self, page, selector):
            self._page = page
            self._selector = selector

        def nth(self, idx):
            return _LocatorNthStub(self._page, self._selector, idx)

    class _PageStub:
        def __init__(self):
            self.keyboard = self
            self.direct_clicks = []

        def evaluate(self, script, arg=None):  # noqa: ARG002
            if isinstance(arg, list):
                sel = str(arg[0] or "")
                if sel == "button[aria-label='Search']":
                    return [0]
                if sel == "button[aria-label*='Search']":
                    return [1]
                return []
            return False

        def press(self, key):  # noqa: ARG002
            return None

        def wait_for_timeout(self, ms):  # noqa: ARG002
            return None

        def locator(self, selector):
            return _LocatorStub(self, selector)

    class _BrowserStub:
        def __init__(self):
            self.page = _PageStub()
            self.clicked = []

        def click(self, selector, timeout_ms=None, no_wait_after=False):  # noqa: ARG002
            self.clicked.append(selector)
            raise RuntimeError("should_use_direct_visible_click")

        def wait(self, selector, timeout_ms=None):  # noqa: ARG002
            return None

        def content(self):
            return (
                "<html><body><main role='main'>Flights</main>"
                "<button aria-label='Search'>Search</button>"
                "<button aria-label='Done. Search for round trip flights'>Done</button>"
                "<div>from ¥10,420</div></body></html>"
            )

    monkeypatch.setattr(sr, "_is_results_ready", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        sr, "_google_deeplink_probe_status", lambda html, url: (False, "missing_contextual_price_card")
    )

    browser = _BrowserStub()
    out = sr._google_search_and_commit(  # noqa: SLF001
        browser,
        selectors=["button[aria-label*='Search']"],
        timeout_ms=350,
        page_url="https://www.google.com/travel/flights?hl=en&gl=JP#flt=FUK.HND.2026-05-02*HND.FUK.2026-06-08;c:JPY",
        origin="FUK",
        dest="HND",
        depart="2026-05-02",
        return_date="2026-06-08",
    )

    assert out["ok"] is False
    assert out["error"] == "search_commit_no_results_transition"
    assert out["selector_used"] == "button[aria-label='Search']"
    assert browser.page.direct_clicks == [("button[aria-label='Search']", 0)]


def test_google_search_commit_smart_escalation_skip_reason_for_repeated_local_click_deadline():
    step_trace = [
        {"action": "fill", "role": "origin", "status": "ok"},
        {"action": "fill", "role": "dest", "status": "ok"},
        {"action": "fill", "role": "depart", "status": "ok"},
        {"action": "fill", "role": "return", "status": "ok"},
        {
            "action": "click",
            "selectors": ["button[aria-label*='Search']"],
            "status": "soft_fail",
            "error": "action_deadline_exceeded_before_click",
        },
        {
            "action": "click",
            "selectors": ["button[aria-label*='Search']"],
            "status": "soft_fail",
            "error": "action_deadline_exceeded_before_click",
        },
    ]
    reason = _google_search_commit_smart_escalation_skip_reason(
        step_trace,
        error_message="results_not_ready_after_turn_limit attempt=1 turns=2",
    )
    assert reason == "google_search_commit_click_deadline"


def test_google_search_commit_uses_reload_on_google_error_surface(monkeypatch):
    class _LocatorNthStub:
        def __init__(self, page, selector, idx):
            self._page = page
            self._selector = selector
            self._idx = idx

        def click(self, timeout=None, no_wait_after=False):  # noqa: ARG002
            self._page.direct_clicks.append((self._selector, self._idx))
            self._page.reloaded = True
            return None

    class _LocatorStub:
        def __init__(self, page, selector):
            self._page = page
            self._selector = selector

        def nth(self, idx):
            return _LocatorNthStub(self._page, self._selector, idx)

    class _PageStub:
        def __init__(self):
            self.keyboard = self
            self.direct_clicks = []
            self.reloaded = False

        def evaluate(self, script, arg=None):  # noqa: ARG002
            if isinstance(arg, list):
                # Selector-based visibility probe path (native querySelectorAll semantics):
                # Playwright-only `:has-text(...)` selectors are invalid here and should
                # fail closed to exercise the CSS-safe reload visibility probe.
                if arg and isinstance(arg[0], str):
                    return []
                # CSS-safe visible button text probe path.
                if arg and isinstance(arg[0], list):
                    tokens = [str(t or "").lower() for t in (arg[0] or [])]
                    if (not self.reloaded) and any("reload" in t for t in tokens):
                        return 1
                    return 0
            return False

        def press(self, key):  # noqa: ARG002
            return None

        def wait_for_timeout(self, ms):  # noqa: ARG002
            return None

        def locator(self, selector):
            return _LocatorStub(self, selector)

    class _BrowserStub:
        def __init__(self):
            self.page = _PageStub()
            self.clicked = []

        def click(self, selector, timeout_ms=None, no_wait_after=False):  # noqa: ARG002
            self.clicked.append(selector)
            if "Reload" in str(selector):
                self.page.reloaded = True
                return None
            raise RuntimeError("unexpected_non_reload_click")

        def wait(self, selector, timeout_ms=None):  # noqa: ARG002
            return None

        def content(self):
            if not self.page.reloaded:
                return (
                    "<html><body><main role='main'><h2>Search results</h2>"
                    "<div role='alert'>No results returned.</div>"
                    "<p>Oops, something went wrong.</p>"
                    "<button>Reload</button></main></body></html>"
                )
            return (
                "<html><body>"
                "<div aria-label='FUK to HND Sat, May 2 Mon, Jun 8 from ¥10,420'></div>"
                "<div>Flights results</div>"
                "</body></html>"
            )

    monkeypatch.setattr(sr, "_google_deeplink_probe_status", lambda html, url: (False, "missing_result_or_price_token"))

    browser = _BrowserStub()
    out = sr._google_search_and_commit(  # noqa: SLF001
        browser,
        selectors=["button[aria-label*='Search']"],
        timeout_ms=500,
        page_url="https://www.google.com/travel/flights?hl=en&gl=JP#flt=FUK.HND.2026-05-02*HND.FUK.2026-06-08;c:JPY",
        origin="FUK",
        dest="HND",
        depart="2026-05-02",
        return_date="2026-06-08",
    )

    assert out["ok"] is True
    assert out["strategy"] == "reload_then_verify"
    assert "Reload" in (out.get("selector_used") or "")
    assert out["results_signal_found"] is True
    assert int((out.get("probe_pre") or {}).get("reload_button_visible_count") or 0) >= 1


def test_local_programming_exception_reason_detects_name_errors():
    assert _local_programming_exception_reason(NameError("name 'url' is not defined")) == "name_error"
    assert (
        _local_programming_exception_reason(UnboundLocalError("cannot access local variable 'x'"))
        == "unbound_local_error"
    )
    assert _local_programming_exception_reason(RuntimeError("calendar_not_open")) == ""


def test_detect_site_interstitial_block_skyscanner_captcha():
    html = """
    <html><head><title>Skyscanner</title></head>
    <body>
      <script src="/captcha.js"></script>
      <div id="px-captcha"></div>
      <h1>Are you a person or a robot?</h1>
      <div>Still having problems accessing the page?</div>
    </body></html>
    """
    out = detect_skyscanner_interstitial_block(html)
    assert out["reason"] == "blocked_interstitial_captcha"
    assert out["block_type"] == "captcha"
    assert out["evidence"]["html.length"] > 0
    assert "px-captcha" in out["evidence"]["ui.token_hits"]


def test_detect_site_interstitial_block_ignores_normal_skyscanner_html():
    html = "<html><body><main>Skyscanner flights search</main><input aria-label='From' /></body></html>"
    out = _detect_site_interstitial_block(html, "skyscanner")
    assert out == {}


def test_detect_skyscanner_interstitial_block_ignores_px_telemetry_on_normal_search_page():
    html = """
    <html><head><title>Skyscanner flights</title></head>
    <body>
      <script src="https://client.px-cloud.net/PXabc/main.min.js"></script>
      <iframe src="https://js.px-cloud.net/?t=d-sample-token" style="visibility:hidden"></iframe>
      <input id="originInput-input" name="originInput-search" aria-label="From" />
      <input id="destinationInput-input" name="destinationInput-search" aria-label="To" />
    </body></html>
    """
    out = detect_skyscanner_interstitial_block(html)
    assert out == {}


def test_detect_skyscanner_interstitial_block_ignores_lone_captcha_script_on_search_surface():
    html = """
    <html><head><title>Skyscanner flights</title></head>
    <body>
      <script src="/rf8vapwA/captcha.js"></script>
      <input id="originInput-input" name="originInput-search" aria-label="From" />
      <input id="destinationInput-input" name="destinationInput-search" aria-label="To" />
    </body></html>
    """
    out = detect_skyscanner_interstitial_block(html)
    assert out == {}


def test_vision_page_kind_probe_skips_route_and_date_mismatch_triggers():
    assert (
        _should_run_vision_page_kind_probe(
            enabled=True,
            trigger_reason="route_fill_mismatch",
            scope_class="unknown",
        )
        is False
    )
    assert (
        _should_run_vision_page_kind_probe(
            enabled=True,
            trigger_reason="date_fill_failure_calendar_not_open",
            scope_class="irrelevant_page",
        )
        is False
    )
    assert (
        _should_run_vision_page_kind_probe(
            enabled=True,
            trigger_reason="rebind_unready_non_flight_scope_irrelevant_page",
            scope_class="unknown",
        )
        is False
    )


def test_execute_plan_skips_optional_return_date_after_depart_date_failure(monkeypatch):
    class _BrowserStub:
        def content(self):
            return "<html><body></body></html>"

    calls = []

    def _fake_gf_set_date(*args, **kwargs):
        role = kwargs.get("role")
        calls.append(role)
        if role == "depart":
            return {
                "ok": False,
                "reason": "month_nav_exhausted",
                "selector_used": "[role='combobox'][aria-label*='出発']",
                "evidence": {"calendar.failure_stage": "month_header"},
            }
        raise AssertionError("return date fill should be skipped after depart failure")

    monkeypatch.setattr(sr, "_gf_set_date_impl", _fake_gf_set_date)

    plan = [
        {"action": "fill", "selector": ["[aria-label*='出発日']"], "value": "2026-03-01", "optional": True},
        {"action": "fill", "selector": ["[aria-label*='復路']"], "value": "2026-03-08", "optional": True},
    ]

    trace = execute_plan(_BrowserStub(), plan, site_key="google_flights")

    assert calls == ["depart"]
    assert any(
        isinstance(item, dict)
        and item.get("role") == "return"
        and item.get("status") == "soft_skip"
        and item.get("error") == "skip_return_after_depart_fail"
        for item in trace
    )


def test_google_step_trace_local_date_open_failure_detects_deterministic_pattern():
    step_trace = [
        {"action": "fill", "role": "origin", "status": "ok"},
        {"action": "fill", "role": "dest", "status": "ok"},
        {
            "action": "fill",
            "role": "depart",
            "status": "calendar_not_open",
            "evidence": {"calendar.failure_stage": "open"},
        },
    ]

    out = _google_step_trace_local_date_open_failure(step_trace)

    assert out["matched"] is True
    assert out["reason"] == "calendar_not_open_local_open_stage"
    assert out["role"] == "depart"
    assert out["route_fill_core_ok"] is True


def test_google_step_trace_local_date_open_failure_detects_month_nav_buttons_missing_after_open():
    step_trace = [
        {"action": "fill", "role": "origin", "status": "ok"},
        {"action": "fill", "role": "dest", "status": "ok"},
        {
            "action": "fill",
            "role": "depart",
            "status": "month_nav_buttons_not_found",
            "evidence": {"calendar.failure_stage": "month_nav_buttons_detection"},
        },
    ]

    out = _google_step_trace_local_date_open_failure(step_trace)

    assert out["matched"] is True
    assert out["reason"] == "month_nav_buttons_not_found_local_picker_stage"
    assert out["failure_stage"] == "month_nav_buttons_detection"
    assert out["route_fill_core_ok"] is True


def test_google_step_trace_local_date_open_failure_detects_date_verify_false_negative_with_active_match():
    step_trace = [
        {"action": "fill", "role": "origin", "status": "ok"},
        {"action": "fill", "role": "dest", "status": "ok"},
        {
            "action": "fill",
            "role": "depart",
            "status": "date_picker_unverified",
            "evidence": {
                "verify.close_method": "escape",
                "verify.active_matches_expected": True,
                "verify.active_value": "Sat, May 2",
            },
        },
    ]

    out = _google_step_trace_local_date_open_failure(step_trace)

    assert out["matched"] is True
    assert out["reason"] == "date_picker_unverified_local_verify_false_negative"
    assert out["failure_stage"] == "verify"
    assert out["route_fill_core_ok"] is True


def test_google_force_bind_suppression_after_local_date_open_failure_requires_route_fill_core_ok():
    no_core = _google_should_suppress_force_bind_after_date_failure(
        [
            {"action": "fill", "role": "origin", "status": "ok"},
            {
                "action": "fill",
                "role": "depart",
                "status": "calendar_not_open",
                "evidence": {"calendar.failure_stage": "open"},
            },
        ]
    )
    yes_core = _google_should_suppress_force_bind_after_date_failure(
        [
            {"action": "fill", "role": "origin", "status": "ok"},
            {"action": "fill", "role": "dest", "status": "ok"},
            {
                "action": "fill",
                "role": "depart",
                "status": "calendar_not_open",
                "evidence": {"calendar.failure_stage": "open"},
            },
        ]
    )

    assert no_core["use"] is False
    assert yes_core["use"] is True
    assert yes_core["reason"] == "recent_local_date_picker_failure_after_route_fill"


def test_debug_exploration_mode_super_deep_aliases(monkeypatch):
    monkeypatch.setenv("FLIGHT_WATCHER_DEBUG_EXPLORATION_MODE", "super-deep")
    assert _debug_exploration_mode() == "super_deep"
    monkeypatch.setenv("FLIGHT_WATCHER_DEBUG_EXPLORATION_MODE", "ultra")
    assert _debug_exploration_mode() == "super_deep"


def test_execute_plan_google_date_fill_prefers_display_lang_hint_for_date_open(monkeypatch):
    captured = {}

    class _PageStub:
        url = "https://www.google.com/travel/flights?hl=en&gl=JP"

    class _BrowserStub:
        def __init__(self):
            self.page = _PageStub()

        def content(self):
            return "<html><body><main></main></body></html>"

        def wait(self, selector, timeout_ms=None):  # noqa: ARG002
            return None

    def _fake_gf_set_date_impl(*args, **kwargs):  # noqa: ARG001
        captured.update(kwargs)
        return {"ok": False, "reason": "calendar_not_open", "selector_used": "", "evidence": {"calendar.failure_stage": "open"}}

    monkeypatch.setattr(sr, "_gf_set_date_impl", _fake_gf_set_date_impl)
    monkeypatch.setattr(sr, "_google_display_locale_hint_from_browser", lambda _browser: "en")

    trace = execute_plan(
        _BrowserStub(),
        [{"action": "fill", "selector": ["[role='combobox'][aria-label*='Depart']"], "value": "2026-03-01"}],
        site_key="google_flights",
        locale="ja-JP",
    )

    assert trace
    assert captured.get("locale_hint") == "en"


class _CaptureTimeoutBrowserStub:
    """Browser stub that records received selector timeout values."""

    def __init__(self):
        self.fill_timeout_calls = []
        self.fill_by_keywords_calls = 0
        self.activate_calls = 0
        self.type_active_calls = 0

    def fill(self, selector, value, timeout_ms=None):  # noqa: ARG002
        self.fill_timeout_calls.append(timeout_ms)
        raise RuntimeError("fill failed")

    def click(self, selector, timeout_ms=None):  # noqa: ARG002
        return None

    def wait(self, selector, timeout_ms=None):  # noqa: ARG002
        return None

    def type_active(self, value, timeout_ms=None):  # noqa: ARG002
        self.type_active_calls += 1
        return None

    def activate_field_by_keywords(self, keywords, timeout_ms=None):  # noqa: ARG002
        self.activate_calls += 1
        return False

    def fill_by_keywords(self, keywords, value, timeout_ms=None):  # noqa: ARG002
        self.fill_by_keywords_calls += 1
        return False

    def content(self):
        return "<html><body></body></html>"


def test_execute_plan_google_single_selector_uses_bounded_timeout():
    """Google fill actions should use short selector timeout even with one selector."""
    browser = _CaptureTimeoutBrowserStub()
    plan = [{"action": "fill", "selector": "input[name='origin']", "value": "HND"}]
    with pytest.raises(RuntimeError):
        execute_plan(browser, plan, site_key="google_flights")
    assert browser.fill_timeout_calls
    assert browser.fill_timeout_calls[0] == 1500


def test_google_fill_and_commit_location_fails_closed_when_dest_placeholder_persists(monkeypatch):
    class _FakeBrowser:
        def __init__(self):
            self.page = object()

        def fill_google_flights_combobox(self, **kwargs):  # noqa: ARG002
            return (True, "[role='combobox'][aria-label*='目的地']")

    monkeypatch.setattr(
        sr,
        "_extract_google_flights_form_state",
        lambda _page: {
            "dest_text": "",
            "dest_text_raw": "目的地を探索",
            "dest_is_placeholder": True,
            "confidence": "high",
        },
    )

    out = sr._google_fill_and_commit_location(
        _FakeBrowser(),
        role="dest",
        value="ITM",
        selectors=["[role='combobox'][aria-label*='目的地']"],
        locale_hint="ja-JP",
        timeout_ms=1500,
    )

    assert out["ok"] is False
    assert out["committed"] is False
    assert out["reason"] == "combobox_fill_unverified_dest_dest_placeholder"
    assert out["evidence"]["verify.postcheck_reason"] == "dest_placeholder"


def test_google_fill_and_commit_location_fails_closed_when_dest_placeholder_is_empty(monkeypatch):
    class _FakeBrowser:
        def __init__(self):
            self.page = object()

        def fill_google_flights_combobox(self, **kwargs):  # noqa: ARG002
            return (True, "[role='combobox'][aria-label*='Where to']")

    monkeypatch.setattr(
        sr,
        "_extract_google_flights_form_state",
        lambda _page: {
            "dest_text": "",
            "dest_text_raw": "",
            "dest_is_placeholder": True,
            "confidence": "high",
        },
    )

    out = sr._google_fill_and_commit_location(
        _FakeBrowser(),
        role="dest",
        value="HND",
        selectors=["[role='combobox'][aria-label*='Where to']"],
        timeout_ms=1500,
    )

    assert out["ok"] is False
    assert out["committed"] is False
    assert out["reason"] == "combobox_fill_unverified_dest_dest_placeholder"
    assert out["evidence"]["verify.postcheck_reason"] == "dest_placeholder"


def test_google_fill_and_commit_location_combobox_debug_evidence_on_combobox_fail():
    class _FakeBrowser:
        def __init__(self):
            self.page = object()
            self._last_google_flights_combobox_debug = {
                "failure_stage": "activation_failed",
                "failure_selector": "[role='combobox'][aria-label*='Where from']",
                "failure_remaining_ms": 420,
                "failure_reserve_ms": 180,
                "activation_selector_index_used": 2,
                "input_selector_used": "input[role='combobox'][aria-label*='Where from']",
                "generic_input_selector_used": True,
                "activation_visible_prefilter": {"[role='combobox'][aria-label*='Where from']": "hidden"},
                "activation_open_probe": {"opened": False, "source": "prefilter"},
                "prefilled_match": False,
                "prefilled_match_token": "HND",
                "prefilled_selector_used": "input[role='combobox']",
                "prefilled_value": "Tokyo",
                "keyboard_commit_attempted": True,
                "option_click_succeeded": False,
                "verify_ok": False,
                "commit_signal": {"active_value": "Tokyo", "has_commit_signal": False},
            }

        def fill_google_flights_combobox(self, **kwargs):  # noqa: ARG002
            return (False, "")

    out = sr._google_fill_and_commit_location(  # noqa: SLF001
        _FakeBrowser(),
        role="origin",
        value="HND",
        selectors=["[role='combobox'][aria-label*='Where from']"],
        locale_hint="en-US",
        timeout_ms=1200,
    )

    assert out["ok"] is False
    assert out["reason"] == "combobox_fill_failed"
    evidence = out.get("evidence", {})
    assert evidence.get("combobox.failure_stage") == "activation_failed"
    assert evidence.get("combobox.activation_selector_index_used") == 2
    assert evidence.get("combobox.input_selector_used")
    assert evidence.get("combobox.generic_input_selector_used") is True
    assert isinstance(evidence.get("combobox.activation_visible_prefilter"), dict)
    assert isinstance(evidence.get("combobox.activation_open_probe"), dict)
    assert evidence.get("combobox.keyboard_commit_attempted") is True


def test_google_fill_and_commit_location_combobox_verify_tokens_keep_cross_script_aliases(monkeypatch):
    captured = {}

    class _FakeBrowser:
        def __init__(self):
            self.page = object()

        def fill_google_flights_combobox(self, **kwargs):
            captured.update(kwargs)
            return (False, "")

    monkeypatch.setattr(
        sr,
        "get_airport_aliases_for_provider",
        lambda code, provider: {"HND", "東京", "TOKYO", "羽田"},  # noqa: ARG005
    )
    monkeypatch.setattr(
        sr,
        "prioritize_tokens",
        lambda tokens, locale_hint=None: ["羽田", "東京", "東京都", "東京(羽田)", "東京（羽田）", "HND", "TOKYO"],  # noqa: ARG005
    )

    out = sr._google_fill_and_commit_location(
        _FakeBrowser(),
        role="origin",
        value="HND",
        selectors=["[role='combobox'][aria-label*='Where from']"],
        locale_hint="ja-JP",
        timeout_ms=1500,
    )

    assert out["ok"] is False
    verify_tokens = list(captured.get("verify_tokens") or [])
    assert "東京" in verify_tokens
    assert "TOKYO" in verify_tokens


def test_google_fill_and_commit_location_applies_and_promotes_selector_hints(monkeypatch):
    captured = {}
    promoted = []

    class _FakePage:
        url = "https://www.google.com/travel/flights?hl=en&gl=JP"

    class _FakeBrowser:
        def __init__(self):
            self.page = _FakePage()
            self._last_google_flights_combobox_debug = {
                "activation_selector_used": "[role='combobox'][aria-label*='Where to']",
                "input_selector_used": "input[role='combobox'][aria-label*='Where to']",
                "generic_input_selector_used": False,
            }

        def fill_google_flights_combobox(self, **kwargs):
            captured.update(kwargs)
            return (True, "[role='combobox'][aria-label*='Where to']")

    monkeypatch.setattr(
        sr,
        "get_selector_hints",
        lambda **kwargs: (
            ["button[data-learned='dest-open']"]
            if kwargs.get("action") == "route_fill_activation"
            else ["input[aria-label*='Where to'][data-learned='dest-input']"]
        ),
    )
    monkeypatch.setattr(sr, "promote_selector_hint", lambda **kwargs: promoted.append(kwargs) or True)
    monkeypatch.setattr(
        sr,
        "_extract_google_flights_form_state",
        lambda _page: {
            "dest_text": "Osaka ITM",
            "dest_text_raw": "Osaka ITM",
            "dest_is_placeholder": False,
            "confidence": "high",
        },
    )
    monkeypatch.setattr(sr, "_google_form_value_matches_airport", lambda _value, _code: True)

    out = sr._google_fill_and_commit_location(
        _FakeBrowser(),
        role="dest",
        value="ITM",
        selectors=["[role='combobox'][aria-label*='Where to']"],
        locale_hint="ja-JP",
        timeout_ms=1500,
    )

    assert out["ok"] is True
    assert list(captured.get("activation_selectors") or [])[0] == "button[data-learned='dest-open']"
    assert (
        list(captured.get("input_selectors") or [])[0]
        == "input[aria-label*='Where to'][data-learned='dest-input']"
    )
    assert any(p.get("action") == "route_fill_activation" for p in promoted)
    assert any(p.get("action") == "route_fill_input" for p in promoted)


def test_profile_localized_list_interleaves_primary_and_secondary_locale():
    out_ja = sr._profile_localized_list(  # noqa: SLF001
        {"ja": ["JA1", "JA2"], "en": ["EN1", "EN2"]},
        prefer_ja=True,
    )
    out_en = sr._profile_localized_list(  # noqa: SLF001
        {"ja": ["JA1", "JA2"], "en": ["EN1", "EN2"]},
        prefer_ja=False,
    )

    assert out_ja[:4] == ["JA1", "EN1", "JA2", "EN2"]
    assert out_en[:2] == ["EN1", "EN2"]


def test_google_force_bind_location_input_selectors_keep_cross_locale_fallback_near_front(monkeypatch):
    monkeypatch.setattr(sr, "_current_mimic_locale", lambda: "ja-JP")
    monkeypatch.setattr(sr, "get_knowledge_rule_tokens", lambda _key: [])

    selectors = sr._google_force_bind_location_input_selectors("origin")  # noqa: SLF001
    head = selectors[:8]

    assert any("出発地" in s for s in head)
    assert any("Where from" in s or "From" in s for s in head)


def test_google_dest_placeholder_detector_does_not_flag_airport_name(monkeypatch):
    import utils.knowledge_rules
    import core.ui_tokens
    monkeypatch.setattr(utils.knowledge_rules, "get_tokens", lambda group, key: ["目的地を探索", "到着空港", "Arrival airport"])  # noqa: ARG005
    monkeypatch.setattr(core.ui_tokens, "prioritize_tokens", lambda tokens, locale_hint=None: list(tokens))  # noqa: ARG005

    assert sr._is_google_dest_placeholder("大阪国際空港") is False
    assert sr._is_google_dest_placeholder("到着空港") is True


def test_google_fill_and_commit_location_fails_closed_when_dest_mismatch_detected(monkeypatch):
    class _FakeBrowser:
        def __init__(self):
            self.page = object()

        def fill_google_flights_combobox(self, **kwargs):  # noqa: ARG002
            return (True, "[role='combobox'][aria-label*='目的地']")

    monkeypatch.setattr(
        sr,
        "_extract_google_flights_form_state",
        lambda _page: {
            "dest_text": "札幌",
            "dest_text_raw": "札幌",
            "dest_is_placeholder": False,
            "confidence": "high",
        },
    )

    out = sr._google_fill_and_commit_location(
        _FakeBrowser(),
        role="dest",
        value="ITM",
        selectors=["[role='combobox'][aria-label*='目的地']"],
        locale_hint="ja-JP",
        timeout_ms=1500,
    )

    assert out["ok"] is False
    assert out["committed"] is False
    assert out["reason"] == "combobox_fill_unverified_dest_dest_mismatch"
    assert out["evidence"]["verify.postcheck_reason"] == "dest_mismatch"


def test_google_fill_and_commit_location_reprobes_low_confidence_dest_placeholder_once(monkeypatch):
    class _FakePage:
        def __init__(self):
            self.waits = []

        def wait_for_timeout(self, timeout_ms):
            self.waits.append(int(timeout_ms))

    class _FakeBrowser:
        def __init__(self):
            self.page = _FakePage()

        def fill_google_flights_combobox(self, **kwargs):  # noqa: ARG002
            return (True, "[role='combobox'][aria-label*='目的地']")

    states = iter(
        [
            {
                "dest_text": "",
                "dest_text_raw": "目的地を探索",
                "dest_is_placeholder": True,
                "confidence": "low",
            },
            {
                "dest_text": "大阪国際空港",
                "dest_text_raw": "大阪国際空港",
                "dest_is_placeholder": False,
                "confidence": "medium",
            },
        ]
    )
    monkeypatch.setattr(sr, "_extract_google_flights_form_state", lambda _page: next(states))

    browser = _FakeBrowser()
    out = sr._google_fill_and_commit_location(
        browser,
        role="dest",
        value="ITM",
        selectors=["[role='combobox'][aria-label*='目的地']"],
        locale_hint="ja-JP",
        timeout_ms=1500,
    )

    assert out["ok"] is True
    assert out["committed"] is True
    assert browser.page.waits == [180]


def test_google_fill_and_commit_location_uses_alias_retry_after_placeholder_persists(monkeypatch):
    class _FakePage:
        def __init__(self):
            self.waits = []

        def wait_for_timeout(self, timeout_ms):
            self.waits.append(int(timeout_ms))

    class _FakeBrowser:
        def __init__(self):
            self.page = _FakePage()
            self.text_attempts = []

        def fill_google_flights_combobox(self, **kwargs):
            self.text_attempts.append(kwargs.get("text"))
            return (True, "[role='combobox'][aria-label*='目的地']")

    browser = _FakeBrowser()

    monkeypatch.setattr(sr, "get_airport_aliases_for_provider", lambda code, provider: {"ITM", "大阪", "大阪国際空港"})  # noqa: ARG005
    monkeypatch.setattr(
        sr,
        "prioritize_tokens",
        lambda tokens, locale_hint=None: (  # noqa: ARG005
            ["大阪国際空港", "大阪", "ITM"]
            if {"ITM", "大阪", "大阪国際空港"}.issubset(set(tokens or []))
            else list(tokens or [])
        ),
    )

    states = iter(
        [
            {
                "dest_text_raw": "目的地を探索",
                "dest_is_placeholder": True,
                "confidence": "low",
            },
            {
                "dest_text_raw": "目的地を探索",
                "dest_is_placeholder": True,
                "confidence": "low",
            },
            {
                "dest_text_raw": "大阪国際空港",
                "dest_is_placeholder": False,
                "confidence": "medium",
            },
        ]
    )
    monkeypatch.setattr(sr, "_extract_google_flights_form_state", lambda _page: next(states))

    out = sr._google_fill_and_commit_location(
        browser,
        role="dest",
        value="ITM",
        selectors=["[role='combobox'][aria-label*='目的地']"],
        locale_hint="ja-JP",
        timeout_ms=1500,
    )

    assert out["ok"] is True
    assert out["committed"] is True
    assert browser.text_attempts[:2] == ["ITM", "大阪国際空港"]
    assert browser.page.waits == [180, 220]


def test_google_fill_and_commit_location_uses_tab_finalize_after_placeholder_persists(monkeypatch):
    class _FakeKeyboard:
        def __init__(self):
            self.presses = []

        def press(self, key):
            self.presses.append(str(key))

    class _FakePage:
        def __init__(self):
            self.waits = []
            self.keyboard = _FakeKeyboard()

        def wait_for_timeout(self, timeout_ms):
            self.waits.append(int(timeout_ms))

    class _FakeBrowser:
        def __init__(self):
            self.page = _FakePage()
            self._last_google_flights_combobox_debug = {}

        def fill_google_flights_combobox(self, **kwargs):  # noqa: ARG002
            # Simulate helper reporting a keyboard/no-option commit that changed editor value.
            self._last_google_flights_combobox_debug = {
                "keyboard_commit_attempted": True,
                "option_click_succeeded": False,
                "verify_ok": True,
                "commit_signal": {
                    "active_value": "大阪",
                    "has_commit_signal": True,
                },
            }
            return (True, "[role='combobox'][aria-label*='目的地']")

    browser = _FakeBrowser()
    monkeypatch.setattr(sr, "get_airport_aliases_for_provider", lambda code, provider: {"ITM", "大阪"})  # noqa: ARG005
    monkeypatch.setattr(sr, "prioritize_tokens", lambda tokens, locale_hint=None: list(tokens or []))  # noqa: ARG005

    states = iter(
        [
            {
                "dest_text_raw": "目的地を探索",
                "dest_is_placeholder": True,
                "confidence": "low",
            },
            {
                "dest_text_raw": "目的地を探索",
                "dest_is_placeholder": True,
                "confidence": "low",
            },
            {
                "dest_text_raw": "大阪国際空港",
                "dest_is_placeholder": False,
                "confidence": "medium",
            },
        ]
    )
    monkeypatch.setattr(sr, "_extract_google_flights_form_state", lambda _page: next(states))

    out = sr._google_fill_and_commit_location(
        browser,
        role="dest",
        value="ITM",
        selectors=["[role='combobox'][aria-label*='目的地']"],
        locale_hint="ja-JP",
        timeout_ms=1500,
    )

    assert out["ok"] is True
    assert out["committed"] is True
    assert browser.page.keyboard.presses == ["Tab"]
    # 180ms low-confidence settle + 180ms tab-finalize probe
    assert browser.page.waits == [180, 180]


def test_google_fill_and_commit_location_accepts_results_itinerary_when_postcheck_low_confidence(monkeypatch):
    class _FakePage:
        def __init__(self):
            self.waits = []

        def wait_for_timeout(self, timeout_ms):
            self.waits.append(int(timeout_ms))

    class _FakeBrowser:
        def __init__(self):
            self.page = _FakePage()
            self._last_google_flights_combobox_debug = {
                "keyboard_commit_attempted": False,
                "option_click_succeeded": False,
                "verify_ok": True,
                "commit_signal": {},
            }

        def fill_google_flights_combobox(self, **kwargs):  # noqa: ARG002
            return (True, "[role='combobox'][aria-label*='目的地']")

        def content(self):
            return (
                '<html><body>'
                'data-travelimpactmodelwebsiteurl="https://www.travelimpactmodel.org/lookup/flight?itinerary=HND-ITM-NH-23-20260301"'
                '</body></html>'
            )

    monkeypatch.setattr(sr, "get_airport_aliases_for_provider", lambda code, provider: {"ITM"})  # noqa: ARG005
    monkeypatch.setattr(sr, "prioritize_tokens", lambda tokens, locale_hint=None: list(tokens or []))  # noqa: ARG005

    states = iter(
        [
            {
                "dest_text_raw": "目的地を探索",
                "dest_is_placeholder": True,
                "confidence": "low",
            },
            {
                "dest_text_raw": "目的地を探索",
                "dest_is_placeholder": True,
                "confidence": "low",
            },
        ]
    )
    monkeypatch.setattr(sr, "_extract_google_flights_form_state", lambda _page: next(states))

    out = sr._google_fill_and_commit_location(
        _FakeBrowser(),
        role="dest",
        value="ITM",
        selectors=["[role='combobox'][aria-label*='目的地']"],
        locale_hint="ja-JP",
        timeout_ms=1500,
        expected_origin="HND",
        expected_depart="2026-03-01",
    )

    assert out["ok"] is True
    assert out["committed"] is True
    assert out["reason"] == "combobox_fill_success"


def test_execute_plan_optional_required_fill_escalates_instead_of_soft_fail(monkeypatch):
    class _BrowserStub:
        def __init__(self):
            self.depart_attempted = 0

        def content(self):
            return "<html><body></body></html>"

        def wait(self, selector, timeout_ms=None):  # noqa: ARG002
            return None

        def click(self, selector, timeout_ms=None):  # noqa: ARG002
            return None

        def fill(self, selector, value, timeout_ms=None):  # noqa: ARG002
            self.depart_attempted += 1
            return None

        def activate_field_by_keywords(self, keywords, timeout_ms=None):  # noqa: ARG002
            return False

        def fill_by_keywords(self, keywords, value, timeout_ms=None):  # noqa: ARG002
            return False

        def type_active(self, value, timeout_ms=None):  # noqa: ARG002
            return None

    monkeypatch.setattr(
        sr,
        "_google_fill_and_commit_location",
        lambda *args, **kwargs: {
            "ok": False,
            "reason": "combobox_fill_unverified_dest_dest_placeholder",
            "selector_used": "",
            "committed": False,
            "evidence": {"verify.postcheck_reason": "dest_placeholder"},
        },
    )

    browser = _BrowserStub()
    plan = [
        {
            "action": "fill",
            "selector": ["[role='combobox'][aria-label*='目的地']"],
            "value": "ITM",
            "optional": True,
            "required_for_actionability": True,
        },
        {
            "action": "fill",
            "selector": ["[aria-label*='出発日']"],
            "value": "2026-03-01",
        },
    ]

    with pytest.raises(RuntimeError, match="combobox_fill_unverified_dest_dest_placeholder"):
        execute_plan(browser, plan, site_key="google_flights")
    # The destination failure should stop execution before date fill mutates the form.
    assert browser.depart_attempted == 0


def test_execute_plan_clamps_suspicious_low_selector_timeout(monkeypatch):
    """Very low selector timeout values (e.g., 25ms) must be clamped to safe minimum."""

    class _FakeBrowser:
        def __init__(self):
            self.timeout_calls = []

        def fill(self, selector, value, timeout_ms=None):  # noqa: ARG002
            self.timeout_calls.append(timeout_ms)
            if selector == "input[name='missing']":
                raise RuntimeError("missing")
            return None

    original_get_threshold = sr.get_threshold

    def _fake_get_threshold(key, default=None):
        if key in {
            "browser_action_selector_timeout_ms",
            "browser_action_selector_timeout_ms_google_flights",
        }:
            return 25
        if key in {
            "browser_selector_timeout_min_ms",
            "browser_selector_timeout_min_ms_google_flights",
        }:
            return 800
        if key == "browser_timeout_suspicious_low_ms":
            return 200
        return original_get_threshold(key, default)

    monkeypatch.setattr(sr, "get_threshold", _fake_get_threshold)

    browser = _FakeBrowser()
    plan = [
        {
            "action": "fill",
            "selector": ["input[name='missing']", "input[name='ok']"],
            "value": "ITM",
        }
    ]
    execute_plan(browser, plan, site_key="google_flights")
    assert browser.timeout_calls
    assert all((timeout or 0) >= 800 for timeout in browser.timeout_calls)


def test_execute_plan_optional_google_fill_skips_recovery_chain():
    """Optional fill soft-fail should not invoke expensive recovery fallbacks."""
    browser = _CaptureTimeoutBrowserStub()
    plan = [
        {
            "action": "fill",
            "selector": "input[name='origin']",
            "value": "HND",
            "optional": True,
        },
        {"action": "wait", "selector": "body"},
    ]
    execute_plan(browser, plan, site_key="google_flights")
    assert browser.fill_by_keywords_calls == 0
    assert browser.activate_calls == 0
    assert browser.type_active_calls == 0


def test_execute_plan_google_force_bind_origin_failure_skips_generic_recovery_chain():
    """Bounded Google force-bind commit failure must not trigger generic recovery selector spam."""

    class _ForceBindBrowserStub:
        def __init__(self):
            self.page = None
            self.activate_calls = 0
            self.fill_by_keywords_calls = 0
            self.type_active_calls = 0
            self.fill_google_calls = 0
            self.click_calls = 0

        def fill_google_flights_combobox(self, **kwargs):  # noqa: ARG002
            self.fill_google_calls += 1
            return (False, "")

        def click(self, selector, timeout_ms=None):  # noqa: ARG002
            self.click_calls += 1
            raise AssertionError("generic recovery click chain should be skipped")

        def fill(self, selector, value, timeout_ms=None):  # noqa: ARG002
            raise AssertionError("generic fill fallback should be skipped")

        def wait(self, selector, timeout_ms=None):  # noqa: ARG002
            return None

        def activate_field_by_keywords(self, keywords, timeout_ms=None):  # noqa: ARG002
            self.activate_calls += 1
            return False

        def fill_by_keywords(self, keywords, value, timeout_ms=None):  # noqa: ARG002
            self.fill_by_keywords_calls += 1
            return False

        def type_active(self, value, timeout_ms=None):  # noqa: ARG002
            self.type_active_calls += 1
            raise RuntimeError("no_active_typing_target")

        def content(self):
            return "<html><body></body></html>"

    browser = _ForceBindBrowserStub()
    plan = [
        {
            "action": "fill",
            "selector": ["[role='combobox'][aria-label*='出発地']"],
            "value": "HND",
            "force_bind_commit": True,
        }
    ]

    with pytest.raises(RuntimeError):
        execute_plan(browser, plan, site_key="google_flights")

    assert browser.fill_google_calls == 1
    assert browser.activate_calls == 0
    assert browser.fill_by_keywords_calls == 0
    assert browser.type_active_calls == 0


def test_execute_plan_google_route_fill_uses_bounded_combobox_helper_without_force_bind_flag():
    """Google origin/dest fills should use combobox helper even without force_bind_commit."""

    class _GoogleRouteFillBrowserStub:
        def __init__(self):
            self.fill_calls = 0
            self.combobox_calls = 0

        def fill_google_flights_combobox(self, **kwargs):  # noqa: ARG002
            self.combobox_calls += 1
            return (True, "[role='combobox'][aria-label*='目的地']")

        def fill(self, selector, value, timeout_ms=None):  # noqa: ARG002
            self.fill_calls += 1
            raise AssertionError("raw browser.fill should not be used for Google route combobox fills")

        def click(self, selector, timeout_ms=None):  # noqa: ARG002
            return None

        def wait(self, selector, timeout_ms=None):  # noqa: ARG002
            return None

        def activate_field_by_keywords(self, keywords, timeout_ms=None):  # noqa: ARG002
            return False

        def fill_by_keywords(self, keywords, value, timeout_ms=None):  # noqa: ARG002
            return False

        def type_active(self, value, timeout_ms=None):  # noqa: ARG002
            return None

        def content(self):
            return "<html><body></body></html>"

    browser = _GoogleRouteFillBrowserStub()
    plan = [
        {
            "action": "fill",
            "selector": ["[role='combobox'][aria-label*='目的地']"],
            "value": "ITM",
            "required_for_actionability": True,
        }
    ]

    execute_plan(browser, plan, site_key="google_flights")

    assert browser.combobox_calls == 1
    assert browser.fill_calls == 0


def test_google_force_bind_location_input_selectors_demote_generic_active_textboxes(monkeypatch):
    """Role-specific force-bind selectors must outrank generic combobox selectors."""

    monkeypatch.setattr(sr, "_current_mimic_locale", lambda: "ja-JP")
    monkeypatch.setattr(
        sr,
        "prioritize_tokens",
        lambda tokens, locale_hint=None: list(tokens or []),  # noqa: ARG005
    )
    monkeypatch.setattr(sr, "get_knowledge_rule_tokens", lambda key: ["目的地"] if "dest" in key else [])

    def _fake_profile_role_list(profile, key, role_key, prefer_ja=False):  # noqa: ARG001
        if key == "force_bind_location_input_selectors" and role_key == "dest":
            return ["input[aria-label*='目的地']"]
        if key == "active_textbox_selectors" and role_key == "dest":
            return ["input[role='combobox']", "input[aria-controls]"]
        return []

    monkeypatch.setattr(sr, "_profile_role_list", _fake_profile_role_list)

    selectors = sr._google_force_bind_location_input_selectors("dest")

    assert selectors
    assert "input[aria-label*='目的地']" in selectors[:3]
    assert "input[role='combobox']" in selectors
    assert selectors.index("input[aria-label*='目的地']") < selectors.index("input[role='combobox']")
    assert "input[role='combobox']" not in selectors[:3]


def test_google_force_bind_dest_selectors_include_bilingual_defaults(monkeypatch):
    monkeypatch.setattr(sr, "_current_mimic_locale", lambda: "en-US")
    monkeypatch.setattr(sr, "prioritize_tokens", lambda tokens, locale_hint=None: list(tokens or []))  # noqa: ARG005
    monkeypatch.setattr(sr, "_profile_localized_list", lambda cfg, prefer_ja=False: [])  # noqa: ARG005

    selectors = sr._google_force_bind_dest_selectors()

    assert any("Destination" in s for s in selectors)
    assert any("目的地" in s for s in selectors)
    assert any("[role='combobox']" in s for s in selectors)


class _DateBoundBrowserStub:
    """Browser stub where date fill fails but DOM already contains the requested date."""

    def __init__(self):
        self.fill_calls = 0

    def fill(self, selector, value, timeout_ms=None):  # noqa: ARG002
        self.fill_calls += 1
        raise RuntimeError("fill failed")

    def click(self, selector, timeout_ms=None):  # noqa: ARG002
        return None

    def wait(self, selector, timeout_ms=None):  # noqa: ARG002
        return None

    def type_active(self, value, timeout_ms=None):  # noqa: ARG002
        raise RuntimeError("no_active_typing_target")

    def activate_field_by_keywords(self, keywords, timeout_ms=None):  # noqa: ARG002
        return False

    def fill_by_keywords(self, keywords, value, timeout_ms=None):  # noqa: ARG002
        return False

    def content(self):
        return "<html><body><div>2026-03-01</div></body></html>"


def test_execute_plan_google_depart_fill_soft_pass_when_value_already_bound():
    """Mandatory depart fill should soft-pass if target date is already present in DOM."""
    browser = _DateBoundBrowserStub()
    plan = [{"action": "fill", "selector": "input[name='depart']", "value": "2026-03-01"}]
    execute_plan(browser, plan, site_key="google_flights")
    assert browser.fill_calls > 0


def test_plugin_readiness_probe_consumed_when_actionable():
    """Scenario readiness state should consume actionable plugin probe output."""
    out = _apply_plugin_readiness_probe(
        ready=False,
        route_bound=None,
        verify_status="not_attempted",
        verify_override_reason="",
        scope_page_class="unknown",
        scope_trip_product="unknown",
        scope_sources=[],
        plugin_probe={
            "ready": True,
            "page_class": "flight_only",
            "trip_product": "flight_only",
            "route_bound": True,
            "reason": "stub_ready",
        },
    )
    assert out["used"] is True
    assert out["ready"] is True
    assert out["verify_status"] == "plugin_ready"
    assert out["scope_page_class"] == "flight_only"
    assert out["scope_trip_product"] == "flight_only"
    assert "plugin:readiness_probe" in out["scope_sources"]


def test_plugin_readiness_hints_noop_when_plugins_disabled(monkeypatch):
    """Scenario runner should ignore plugin readiness hints when plugins are disabled."""
    monkeypatch.setenv("FLIGHT_WATCHER_DISABLE_PLUGINS", "true")
    monkeypatch.setenv("FLIGHT_WATCHER_PLUGIN_STRATEGY_ENABLED", "true")

    called = {"count": 0}

    def _hints(_site_key, *, inputs=None):  # noqa: ARG001
        called["count"] += 1
        return {"wait_selectors": ["[data-testid='results']"]}

    monkeypatch.setattr(sr, "run_service_readiness_hints", _hints)
    out = sr._collect_plugin_readiness_hints(
        site_key="google_flights",
        inputs={"site": "google_flights"},
    )
    assert out == {}
    assert called["count"] == 0


def test_knowledge_helpers_readiness_hints_honor_sr_runtime_overrides(monkeypatch):
    """Refactor compatibility: knowledge bridge should respect sr monkeypatches."""
    monkeypatch.setattr(services_adapter, "plugin_strategy_enabled", lambda: True)
    monkeypatch.setattr(
        services_adapter,
        "run_service_readiness_hints",
        lambda _site_key, *, inputs=None: {"source": "services_adapter"},  # noqa: ARG005
    )
    monkeypatch.setattr(sr, "plugin_strategy_enabled", lambda: True, raising=False)
    monkeypatch.setattr(
        sr,
        "run_service_readiness_hints",
        lambda _site_key, *, inputs=None: {"source": "scenario_runner"},  # noqa: ARG005
        raising=False,
    )

    out = knowledge_helpers_module.collect_plugin_readiness_hints(
        site_key="google_flights",
        inputs={"site": "google_flights"},
    )

    assert out == {"source": "scenario_runner"}


def test_run_agentic_scenario_wall_clock_cap_returns_latest_html(monkeypatch):
    """Scenario watchdog cap should stop early and return latest HTML without crashing."""

    class _FakePage:
        def wait_for_timeout(self, timeout_ms):  # noqa: ARG002
            return None

    class _FakeBrowserSession:
        def __init__(self, **kwargs):  # noqa: ARG002
            self.page = _FakePage()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ARG002
            return False

        def goto(self, url):  # noqa: ARG002
            return None

        def content(self):
            return "<html><body>stub</body></html>"

        def screenshot(self, path, full_page=True):  # noqa: ARG002
            return None

    original_get_threshold = sr.get_threshold

    def _fake_get_threshold(key, default=None):
        if key == "scenario_wall_clock_cap_sec":
            return 1
        if key == "scenario_evidence_dump_enabled":
            return False
        return original_get_threshold(key, default)

    monotonic_ticks = iter([0.0, 2.0, 2.0])
    monkeypatch.setattr(sr, "BrowserSession", _FakeBrowserSession)
    monkeypatch.setattr(sr, "get_threshold", _fake_get_threshold)
    monkeypatch.setattr(sr, "execute_plan", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected execute_plan")))  # noqa: E501
    monkeypatch.setattr(sr.time, "monotonic", lambda: next(monotonic_ticks, 2.0))

    depart_date = (date.today() + timedelta(days=1)).isoformat()
    return_date = (date.today() + timedelta(days=8)).isoformat()

    html = sr.run_agentic_scenario(
        url=f"https://www.google.com/travel/flights?hl=ja&gl=JP#flt=HND.ITM.{depart_date}",
        origin="HND",
        dest="ITM",
        depart=depart_date,
        return_date=return_date,
        trip_type="round_trip",
        is_domestic=True,
        site_key="google_flights",
    )
    assert "stub" in html


def test_run_agentic_scenario_applies_manual_intervention_runtime_env(monkeypatch):
    """BrowserSession kwargs should honor runtime env controls for manual intervention."""

    class _FakePage:
        def wait_for_timeout(self, timeout_ms):  # noqa: ARG002
            return None

    class _FakeBrowserSession:
        last_init = {}

        def __init__(self, **kwargs):
            type(self).last_init = dict(kwargs)
            self.page = _FakePage()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ARG002
            return False

        def goto(self, url):  # noqa: ARG002
            return None

        def content(self):
            return "<html><body>stub</body></html>"

        def screenshot(self, path, full_page=True):  # noqa: ARG002
            return None

    original_get_threshold = sr.get_threshold

    def _fake_get_threshold(key, default=None):
        if key == "scenario_wall_clock_cap_sec":
            return 1
        if key == "scenario_evidence_dump_enabled":
            return False
        return original_get_threshold(key, default)

    monkeypatch.setenv("FLIGHT_WATCHER_BROWSER_HEADLESS", "0")
    monkeypatch.setenv("FLIGHT_WATCHER_ALLOW_HUMAN_INTERVENTION", "1")
    monkeypatch.setenv("FLIGHT_WATCHER_MANUAL_INTERVENTION_TIMEOUT_SEC", "45")
    monotonic_ticks = iter([0.0, 2.0, 2.0])
    monkeypatch.setattr(sr, "BrowserSession", _FakeBrowserSession)
    monkeypatch.setattr(sr, "get_threshold", _fake_get_threshold)
    monkeypatch.setattr(
        sr,
        "execute_plan",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected execute_plan")),
    )
    monkeypatch.setattr(sr.time, "monotonic", lambda: next(monotonic_ticks, 2.0))

    depart_date = (date.today() + timedelta(days=2)).isoformat()
    return_date = (date.today() + timedelta(days=9)).isoformat()

    html = sr.run_agentic_scenario(
        url=f"https://www.google.com/travel/flights?hl=ja&gl=JP#flt=HND.ITM.{depart_date}",
        origin="HND",
        dest="ITM",
        depart=depart_date,
        return_date=return_date,
        trip_type="round_trip",
        is_domestic=True,
        site_key="google_flights",
    )

    assert "stub" in html
    assert _FakeBrowserSession.last_init.get("headless") is False
    assert _FakeBrowserSession.last_init.get("allow_human_intervention") is True
    assert _FakeBrowserSession.last_init.get("human_intervention_mode") == "assist"
    assert _FakeBrowserSession.last_init.get("last_resort_manual_when_disabled") is True
    assert _FakeBrowserSession.last_init.get("manual_intervention_timeout_sec") == 45


def test_run_agentic_scenario_applies_demo_human_intervention_mode(monkeypatch):
    class _FakePage:
        def wait_for_timeout(self, timeout_ms):  # noqa: ARG002
            return None

    class _FakeBrowserSession:
        last_init = {}

        def __init__(self, **kwargs):
            type(self).last_init = dict(kwargs)
            self.page = _FakePage()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ARG002
            return False

        def goto(self, url):  # noqa: ARG002
            return None

        def content(self):
            return "<html><body>stub</body></html>"

        def screenshot(self, path, full_page=True):  # noqa: ARG002
            return None

    original_get_threshold = sr.get_threshold

    def _fake_get_threshold(key, default=None):
        if key == "scenario_wall_clock_cap_sec":
            return 1
        if key == "scenario_evidence_dump_enabled":
            return False
        return original_get_threshold(key, default)

    monkeypatch.setenv("FLIGHT_WATCHER_BROWSER_HEADLESS", "0")
    monkeypatch.setenv("FLIGHT_WATCHER_ALLOW_HUMAN_INTERVENTION", "0")
    monkeypatch.setenv("FLIGHT_WATCHER_HUMAN_INTERVENTION_MODE", "demo")
    monotonic_ticks = iter([0.0, 2.0, 2.0])
    monkeypatch.setattr(sr, "BrowserSession", _FakeBrowserSession)
    monkeypatch.setattr(sr, "get_threshold", _fake_get_threshold)
    monkeypatch.setattr(
        sr,
        "execute_plan",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected execute_plan")),
    )
    monkeypatch.setattr(sr.time, "monotonic", lambda: next(monotonic_ticks, 2.0))

    depart_date = (date.today() + timedelta(days=2)).isoformat()
    return_date = (date.today() + timedelta(days=9)).isoformat()

    html = sr.run_agentic_scenario(
        url=f"https://www.google.com/travel/flights?hl=ja&gl=JP#flt=HND.ITM.{depart_date}",
        origin="HND",
        dest="ITM",
        depart=depart_date,
        return_date=return_date,
        trip_type="round_trip",
        is_domestic=True,
        site_key="google_flights",
    )

    assert "stub" in html
    assert _FakeBrowserSession.last_init.get("human_intervention_mode") == "demo"
    assert _FakeBrowserSession.last_init.get("allow_human_intervention") is True


def test_run_agentic_scenario_skyscanner_auto_overrides_headless_for_manual_recovery(monkeypatch):
    class _FakePage:
        def wait_for_timeout(self, timeout_ms):  # noqa: ARG002
            return None

    class _FakeBrowserSession:
        last_init = {}

        def __init__(self, **kwargs):
            type(self).last_init = dict(kwargs)
            self.page = _FakePage()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ARG002
            return False

        def goto(self, url):  # noqa: ARG002
            return None

        def content(self):
            return "<html><body>stub</body></html>"

        def screenshot(self, path, full_page=True):  # noqa: ARG002
            return None

    original_get_threshold = sr.get_threshold

    def _fake_get_threshold(key, default=None):
        if key == "scenario_wall_clock_cap_sec":
            return 1
        if key == "scenario_evidence_dump_enabled":
            return False
        return original_get_threshold(key, default)

    monkeypatch.delenv("FLIGHT_WATCHER_BROWSER_HEADLESS", raising=False)
    monkeypatch.setenv("FLIGHT_WATCHER_ALLOW_HUMAN_INTERVENTION", "0")
    monkeypatch.setenv("FLIGHT_WATCHER_HUMAN_INTERVENTION_MODE", "off")
    monkeypatch.setenv("FLIGHT_WATCHER_LAST_RESORT_HUMAN_INTERVENTION_WHEN_DISABLED", "1")
    monotonic_ticks = iter([0.0, 2.0, 2.0])
    monkeypatch.setattr(sr, "BrowserSession", _FakeBrowserSession)
    monkeypatch.setattr(sr, "get_threshold", _fake_get_threshold)
    monkeypatch.setattr(
        sr,
        "execute_plan",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected execute_plan")),
    )
    monkeypatch.setattr(sr.time, "monotonic", lambda: next(monotonic_ticks, 2.0))

    depart_date = (date.today() + timedelta(days=2)).isoformat()
    return_date = (date.today() + timedelta(days=9)).isoformat()

    html = sr.run_agentic_scenario(
        url="https://www.skyscanner.com/flights",
        origin="FUK",
        dest="HND",
        depart=depart_date,
        return_date=return_date,
        trip_type="round_trip",
        is_domestic=True,
        site_key="skyscanner",
    )

    assert "stub" in html
    assert _FakeBrowserSession.last_init.get("headless") is False
    assert _FakeBrowserSession.last_init.get("allow_human_intervention") is False
    assert _FakeBrowserSession.last_init.get("last_resort_manual_when_disabled") is True


def test_run_agentic_scenario_respects_explicit_headless_env_for_skyscanner(monkeypatch):
    class _FakePage:
        def wait_for_timeout(self, timeout_ms):  # noqa: ARG002
            return None

    class _FakeBrowserSession:
        last_init = {}

        def __init__(self, **kwargs):
            type(self).last_init = dict(kwargs)
            self.page = _FakePage()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ARG002
            return False

        def goto(self, url):  # noqa: ARG002
            return None

        def content(self):
            return "<html><body>stub</body></html>"

        def screenshot(self, path, full_page=True):  # noqa: ARG002
            return None

    original_get_threshold = sr.get_threshold

    def _fake_get_threshold(key, default=None):
        if key == "scenario_wall_clock_cap_sec":
            return 1
        if key == "scenario_evidence_dump_enabled":
            return False
        return original_get_threshold(key, default)

    monkeypatch.setenv("FLIGHT_WATCHER_BROWSER_HEADLESS", "1")
    monkeypatch.setenv("FLIGHT_WATCHER_ALLOW_HUMAN_INTERVENTION", "0")
    monkeypatch.setenv("FLIGHT_WATCHER_HUMAN_INTERVENTION_MODE", "off")
    monkeypatch.setenv("FLIGHT_WATCHER_LAST_RESORT_HUMAN_INTERVENTION_WHEN_DISABLED", "1")
    monotonic_ticks = iter([0.0, 2.0, 2.0])
    monkeypatch.setattr(sr, "BrowserSession", _FakeBrowserSession)
    monkeypatch.setattr(sr, "get_threshold", _fake_get_threshold)
    monkeypatch.setattr(
        sr,
        "execute_plan",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected execute_plan")),
    )
    monkeypatch.setattr(sr.time, "monotonic", lambda: next(monotonic_ticks, 2.0))

    depart_date = (date.today() + timedelta(days=2)).isoformat()
    return_date = (date.today() + timedelta(days=9)).isoformat()

    html = sr.run_agentic_scenario(
        url="https://www.skyscanner.com/flights",
        origin="FUK",
        dest="HND",
        depart=depart_date,
        return_date=return_date,
        trip_type="round_trip",
        is_domestic=True,
        site_key="skyscanner",
    )

    assert "stub" in html
    assert _FakeBrowserSession.last_init.get("headless") is True


def test_runtime_symbol_resolution_is_per_invocation_without_global_mutation():
    ras = _load_run_agentic_impl_module()

    class _LegacyStub:
        @staticmethod
        def _default_plan_for_service(*args, **kwargs):  # noqa: ARG004
            return ["patched-plan"]

    previous = ras._default_plan_for_service
    resolved = ras._resolve_runtime_symbol_overrides(_LegacyStub)
    assert resolved["_default_plan_for_service"]("google_flights", "A", "B", "2026-03-05") == ["patched-plan"]
    assert ras._default_plan_for_service is previous


def test_retry_bounds_contract_enforced():
    ras = _load_run_agentic_impl_module()
    assert ras._enforce_contract_retry_bounds(4, 5) == (2, 2)
    assert ras._enforce_contract_retry_bounds(2, 2) == (2, 2)
    assert ras._enforce_contract_retry_bounds(0, -1) == (1, 1)


def test_extracted_runner_avoids_direct_service_runner_imports():
    runner_path = Path("core/scenario_runner/run_agentic_scenario.py")
    text = runner_path.read_text(encoding="utf-8")
    assert "from core.service_runners.google_flights import" not in text


def test_run_agentic_scenario_skyscanner_captcha_returns_blocked_interstitial(monkeypatch):
    """Skyscanner captcha interstitial should fail fast with explicit blocked snapshot."""
    depart_date = (date.today() + timedelta(days=3)).isoformat()
    return_date = (date.today() + timedelta(days=10)).isoformat()

    class _FakePage:
        def wait_for_timeout(self, timeout_ms):  # noqa: ARG002
            return None

    class _FakeBrowserSession:
        def __init__(self, **kwargs):  # noqa: ARG002
            self.page = _FakePage()
            self._html = (
                "<html><head><title>Skyscanner</title><script src='/captcha.js'></script></head>"
                "<body><div id='px-captcha'></div><h1>Are you a person or a robot?</h1>"
                "<div>Still having problems accessing the page?</div></body></html>"
            )

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ARG002
            return False

        def goto(self, url):  # noqa: ARG002
            return None

        def content(self):
            return self._html

        def screenshot(self, path, full_page=True):  # noqa: ARG002
            return None

    original_get_threshold = sr.get_threshold
    snapshots = []

    def _fake_get_threshold(key, default=None):
        if key == "scenario_evidence_dump_enabled":
            return False
        return original_get_threshold(key, default)

    monkeypatch.setattr(sr, "BrowserSession", _FakeBrowserSession)
    monkeypatch.setattr(sr, "get_threshold", _fake_get_threshold)
    monkeypatch.setattr(sr, "_write_debug_snapshot", lambda payload, run_id: snapshots.append(dict(payload)))  # noqa: ARG005,E501
    monkeypatch.setattr(sr, "_write_html_snapshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(sr, "_write_image_snapshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        sr,
        "_default_plan_for_service",
        lambda *args, **kwargs: [
            {"action": "fill", "selector": ["input[name='origin']"], "value": "HND"},
            {"action": "fill", "selector": ["input[name='destination']"], "value": "ITM"},
            {"action": "fill", "selector": ["input[name='depart']"], "value": depart_date},
        ],
    )
    monkeypatch.setattr(sr, "_is_actionable_plan", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        sr,
        "execute_plan",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("execute_plan should not run")),
    )

    html = sr.run_agentic_scenario(
        url="https://www.skyscanner.com/flights",
        origin="HND",
        dest="ITM",
        depart=depart_date,
        return_date=return_date,
        trip_type="round_trip",
        is_domestic=True,
        site_key="skyscanner",
    )

    assert "px-captcha" in html
    assert any(s.get("stage") == "blocked_interstitial" for s in snapshots)
    assert any(s.get("error") == "blocked_interstitial_captcha" for s in snapshots)
    assert not any(s.get("stage") == "retries_exhausted" for s in snapshots)


def test_skyscanner_captcha_grace_helper_can_clear_transient_interstitial():
    class _FakePage:
        def __init__(self):
            self.wait_calls = []

        def wait_for_timeout(self, timeout_ms):
            self.wait_calls.append(timeout_ms)

    class _FakeBrowser:
        def __init__(self):
            self.page = _FakePage()
            self.grace_calls = 0
            self._htmls = [
                "<html><head><title>Skyscanner</title><script src='/captcha.js'></script></head>"
                "<body><div id='px-captcha'></div><h1>Are you a person or a robot?</h1></body></html>",
                "<html><body><main>Skyscanner flights</main><input aria-label='From'/></body></html>",
            ]

        def human_mimic_interstitial_grace(self, duration_ms=3500):  # noqa: ARG002
            self.grace_calls += 1

        def content(self):
            if len(self._htmls) > 1:
                return self._htmls.pop(0)
            return self._htmls[0]

    browser = _FakeBrowser()
    hard_block = detect_skyscanner_interstitial_block(browser.content())
    out = attempt_skyscanner_interstitial_grace(
        browser,
        hard_block=hard_block,
        human_mimic=True,
        grace_ms=2000,
    )

    assert out["used"] is True
    assert out["cleared"] is True
    assert browser.grace_calls == 1
    assert "px-captcha" not in out["html"]
    assert out["press_hold_probe_attempts"] == 0
    assert out["press_hold_executed"] is False


def test_skyscanner_captcha_grace_helper_skips_when_human_mimic_disabled():
    class _FakeBrowser:
        def content(self):
            return "<html></html>"

    out = attempt_skyscanner_interstitial_grace(
        _FakeBrowser(),
        hard_block={"reason": "blocked_interstitial_captcha"},
        human_mimic=False,
        grace_ms=2000,
    )
    assert out["used"] is False
    assert out["reason"] == "human_mimic_disabled"


def test_skyscanner_captcha_grace_helper_reports_press_hold_metadata():
    class _FakeBrowser:
        def __init__(self):
            self._last_interstitial_grace_meta = {}

        def human_mimic_interstitial_grace(self, duration_ms=3500):  # noqa: ARG002
            self._last_interstitial_grace_meta = {
                "press_hold_probe_attempts": 2,
                "press_hold_executed": True,
            }

        def content(self):
            return "<html><body><main>Skyscanner flights</main></body></html>"

    out = attempt_skyscanner_interstitial_grace(
        _FakeBrowser(),
        hard_block={"reason": "blocked_interstitial_captcha"},
        human_mimic=True,
        grace_ms=2000,
    )

    assert out["used"] is True
    assert out["cleared"] is True
    assert out["press_hold_probe_attempts"] == 2
    assert out["press_hold_executed"] is True


def test_skyscanner_captcha_grace_helper_propagates_probe_timeline():
    class _FakeBrowser:
        def __init__(self):
            self._last_interstitial_grace_meta = {}

        def human_mimic_interstitial_grace(self, duration_ms=3500):  # noqa: ARG002
            self._last_interstitial_grace_meta = {
                "press_hold_probe_attempts": 3,
                "press_hold_executed": False,
                "px_shell_nudged": True,
                "press_hold_probes": [
                    {"attempt": 1, "px_shell_present": True, "px_iframe_visible": 0, "executed": False},
                    {"attempt": 2, "px_shell_present": True, "px_iframe_visible": 0, "executed": False},
                ],
            }

        def content(self):
            return (
                "<html><head><script src='/captcha.js'></script></head><body>"
                "<div id='px-captcha'></div><h1>Are you a person or a robot?</h1>"
                "</body></html>"
            )

    out = attempt_skyscanner_interstitial_grace(
        _FakeBrowser(),
        hard_block={"reason": "blocked_interstitial_captcha"},
        human_mimic=True,
        grace_ms=2000,
    )

    assert out["used"] is True
    assert out["press_hold_probe_attempts"] == 3
    assert out["press_hold_executed"] is False
    assert out["px_shell_nudged"] is True
    assert isinstance(out["press_hold_probes"], list)
    assert out["press_hold_probes"][0]["attempt"] == 1


def test_skyscanner_captcha_grace_helper_attempts_early_manual_intervention():
    class _FakeBrowser:
        def __init__(self):
            self._last_interstitial_grace_meta = {}
            self._manual_calls = 0
            self._mimic_calls = 0
            self.allow_human_intervention = True
            self.page = type("_Page", (), {"is_closed": lambda self_inner: False})()

        def human_mimic_interstitial_grace(self, duration_ms=3500):  # noqa: ARG002
            self._mimic_calls += 1
            self._last_interstitial_grace_meta = {
                "press_hold_probe_attempts": 2,
                "press_hold_executed": False,
                "px_shell_nudged": True,
                "px_container_hold_attempted": True,
                "px_container_hold_executed": False,
            }

        def allow_manual_verification_intervention(self, reason="", wait_sec=None):  # noqa: ARG002
            self._manual_calls += 1
            return {"used": True, "reason": "manual_window_elapsed", "wait_sec": int(wait_sec or 0)}

        def content(self):
            if self._manual_calls == 0:
                return (
                    "<html><head><title>Skyscanner</title><script src='/captcha.js'></script></head><body>"
                    "<div id='px-captcha'></div><h1>Are you a person or a robot?</h1>"
                    "</body></html>"
                )
            return "<html><body><main>Skyscanner flights</main></body></html>"

    browser = _FakeBrowser()
    out = attempt_skyscanner_interstitial_grace(
        browser,
        hard_block={"reason": "blocked_interstitial_captcha"},
        human_mimic=True,
        grace_ms=2000,
    )

    assert out["used"] is True
    assert out["cleared"] is True
    assert out["manual_intervention"]["used"] is True
    assert out["manual_intervention"]["reason"] == "manual_window_elapsed"
    assert out["manual_intervention"]["wait_sec"] == 45
    assert out["manual_first_mode"] is True
    assert out["automation_paused_for_manual"] is True
    assert out["press_hold_probe_attempts"] == 0
    assert browser._mimic_calls == 0


def test_skyscanner_captcha_grace_helper_preserves_target_closed_reason_and_recovers():
    class _FakeBrowser:
        def __init__(self):
            self._last_interstitial_grace_meta = {}
            self._manual_calls = 0
            self._recovered = False
            self.allow_human_intervention = True
            outer = self

            class _Page:
                def is_closed(self_inner):
                    return not bool(outer._recovered)

            self.page = _Page()

        def human_mimic_interstitial_grace(self, duration_ms=3500):  # noqa: ARG002
            self._last_interstitial_grace_meta = {
                "press_hold_probe_attempts": 2,
                "press_hold_executed": False,
            }

        def allow_manual_verification_intervention(self, reason="", wait_sec=None):  # noqa: ARG002
            self._manual_calls += 1
            return {
                "used": True,
                "reason": "manual_intervention_target_closed",
                "error": "TargetClosedError",
                "wait_sec": int(wait_sec or 0),
            }

        def recover_page_after_target_closed(self, preferred_url=""):  # noqa: ARG002
            self._recovered = True
            return {"attempted": True, "recovered": True, "reason": "recovered"}

        def content(self):
            if self._manual_calls == 0:
                return (
                    "<html><head><title>Skyscanner</title><script src='/captcha.js'></script></head><body>"
                    "<div id='px-captcha'></div><h1>Are you a person or a robot?</h1>"
                    "</body></html>"
                )
            if not self._recovered:
                raise RuntimeError("TargetClosedError")
            return "<html><body><main>Skyscanner flights</main></body></html>"

    out = attempt_skyscanner_interstitial_grace(
        _FakeBrowser(),
        hard_block={"reason": "blocked_interstitial_captcha"},
        human_mimic=True,
        grace_ms=2000,
    )

    assert out["used"] is True
    assert out["cleared"] is True
    assert out["manual_intervention"]["reason"] == "manual_intervention_target_closed"
    assert out["manual_intervention"]["post_manual_page_recovery"]["recovered"] is True


def test_skyscanner_captcha_grace_helper_does_not_false_clear_on_closed_page_snapshot():
    class _FakeBrowser:
        def __init__(self):
            self._last_interstitial_grace_meta = {}
            self._manual_calls = 0
            self.allow_human_intervention = True

            class _ClosedPage:
                def is_closed(self_inner):
                    return True

            self.page = _ClosedPage()

        def human_mimic_interstitial_grace(self, duration_ms=3500):  # noqa: ARG002
            self._last_interstitial_grace_meta = {"press_hold_probe_attempts": 1}

        def allow_manual_verification_intervention(self, reason="", wait_sec=None):  # noqa: ARG002
            self._manual_calls += 1
            return {
                "used": True,
                "reason": "manual_intervention_target_closed",
                "error": "TargetClosedError",
                "wait_sec": int(wait_sec or 0),
            }

        def content(self):
            if self._manual_calls <= 0:
                return (
                    "<html><head><title>Skyscanner</title><script src='/captcha.js'></script></head><body>"
                    "<div id='px-captcha'></div><h1>Are you a person or a robot?</h1>"
                    "</body></html>"
                )
            raise RuntimeError("TargetClosedError")

    out = attempt_skyscanner_interstitial_grace(
        _FakeBrowser(),
        hard_block={"reason": "blocked_interstitial_captcha"},
        human_mimic=True,
        grace_ms=2000,
    )

    assert out["used"] is True
    assert out["cleared"] is False
    assert out["reason"] == "blocked_interstitial_captcha"


def test_skyscanner_captcha_grace_helper_blocks_on_captcha_url_without_tokens():
    class _FakeBrowser:
        def __init__(self):
            self._last_interstitial_grace_meta = {}
            self.allow_human_intervention = False
            self.page = type(
                "_Page",
                (),
                {
                    "url": "https://www.skyscanner.com/sttc/px/captcha-v2/index.html?url=L2ZsaWdodHM/",
                    "is_closed": lambda self_inner: False,
                },
            )()

        def human_mimic_interstitial_grace(self, duration_ms=3500):  # noqa: ARG002
            self._last_interstitial_grace_meta = {"press_hold_probe_attempts": 1}

        def content(self):
            return "<html><body><main>temporary challenge shell</main></body></html>"

    out = attempt_skyscanner_interstitial_grace(
        _FakeBrowser(),
        hard_block={"reason": "blocked_interstitial_captcha"},
        human_mimic=True,
        grace_ms=2000,
    )

    assert out["used"] is True
    assert out["cleared"] is False
    assert out["reason"] == "blocked_interstitial_captcha"


def test_skyscanner_captcha_grace_helper_reports_vision_guided_fields():
    class _FakeBrowser:
        def __init__(self):
            self._last_interstitial_grace_meta = {}

        def human_mimic_interstitial_grace(self, duration_ms=3500):  # noqa: ARG002
            self._last_interstitial_grace_meta = {
                "press_hold_probe_attempts": 1,
                "press_hold_executed": False,
                "vision_guided_press_attempted": True,
                "vision_guided_press_executed": True,
                "vision_guided_hint": {
                    "protector_label": "interstitial_press_hold",
                    "confidence": "high",
                    "target_bbox": [0.2, 0.2, 0.4, 0.2],
                },
            }

        def content(self):
            return "<html><body><main>Skyscanner flights</main></body></html>"

    out = attempt_skyscanner_interstitial_grace(
        _FakeBrowser(),
        hard_block={"reason": "blocked_interstitial_captcha"},
        human_mimic=True,
        grace_ms=2000,
    )

    assert out["used"] is True
    assert out["vision_guided_press_attempted"] is True
    assert out["vision_guided_press_executed"] is True
    assert out["vision_guided_hint"]["protector_label"] == "interstitial_press_hold"


def test_skyscanner_fallback_reload_skips_when_page_closed():
    class _ClosedPage:
        def is_closed(self):
            return True

    class _FakeBrowser:
        def __init__(self):
            self.page = _ClosedPage()

    out = attempt_skyscanner_interstitial_fallback_reload(
        _FakeBrowser(),
        "https://www.skyscanner.com/flights",
        grace_result={"used": True, "cleared": False},
        human_mimic=True,
        grace_ms_extended=8000,
        max_reload_attempts=3,
    )

    assert out["used"] is True
    assert out["cleared"] is False
    assert out["reason"] == "fallback_reload_page_closed"
    assert out["attempts"] == 0


def test_skyscanner_fallback_reload_stops_when_manual_unavailable():
    class _Page:
        def __init__(self):
            self.url = "https://www.skyscanner.com/sttc/px/captcha-v2/index.html?url=L2ZsaWdodHM/"
            self.headers = {}

        def is_closed(self):
            return False

        def set_extra_http_headers(self, headers):
            self.headers = dict(headers)

    class _FakeBrowser:
        def __init__(self):
            self.page = _Page()
            self.goto_calls = 0
            self._last_interstitial_grace_meta = {}

        def goto(self, url):  # noqa: ARG002
            self.goto_calls += 1

        def human_mimic_interstitial_grace(self, duration_ms=3500):  # noqa: ARG002
            self._last_interstitial_grace_meta = {
                "press_hold_probe_attempts": 1,
                "press_hold_executed": True,
                "press_hold_success": False,
                "press_hold_success_signal": "hold_too_short",
            }

        def allow_manual_verification_intervention(self, **kwargs):  # noqa: ARG002
            return {"used": False, "reason": "headless_mode", "wait_sec": 45}

        def content(self):
            return (
                "<html><head><script src='/captcha.js'></script></head>"
                "<body><div id='px-captcha'></div><main>blocked</main></body></html>"
            )

    browser = _FakeBrowser()
    out = attempt_skyscanner_interstitial_fallback_reload(
        browser,
        "https://www.skyscanner.com/flights",
        grace_result={"used": True, "cleared": False},
        human_mimic=True,
        grace_ms_extended=8000,
        max_reload_attempts=3,
    )

    assert out["used"] is True
    assert out["cleared"] is False
    assert out["reason"] == "fallback_manual_intervention_unavailable_headless_mode"
    assert browser.goto_calls == 2


def test_skyscanner_fallback_reload_runs_machine_retries_before_last_resort_manual_when_off_mode_headed():
    class _Page:
        def __init__(self):
            self.url = "https://www.skyscanner.com/sttc/px/captcha-v2/index.html?url=L2ZsaWdodHM/"
            self.headers = {}

        def is_closed(self):
            return False

        def set_extra_http_headers(self, headers):
            self.headers = dict(headers)

    class _FakeBrowser:
        def __init__(self):
            self.page = _Page()
            self.goto_calls = 0
            self.allow_human_intervention = False
            self.human_intervention_mode = "off"
            self.last_resort_manual_when_disabled = True
            self.headless = False

        def goto(self, url):  # noqa: ARG002
            self.goto_calls += 1

        def content(self):
            return (
                "<html><head><script src='/captcha.js'></script></head>"
                "<body><div id='px-captcha'></div><main>blocked</main></body></html>"
            )

    browser = _FakeBrowser()
    out = attempt_skyscanner_interstitial_fallback_reload(
        browser,
        "https://www.skyscanner.com/flights",
        grace_result={"used": True, "cleared": False},
        human_mimic=True,
        grace_ms_extended=8000,
        max_reload_attempts=3,
    )

    assert out["used"] is True
    assert out["attempted"] is True
    assert out["cleared"] is False
    assert out["reason"] in {"fallback_reload_failed", "blocked_interstitial_captcha"}
    assert browser.goto_calls >= 2


def test_skyscanner_fallback_reload_prefers_decoded_challenge_route_target():
    class _Page:
        def __init__(self):
            self.url = (
                "https://www.skyscanner.com/sttc/px/captcha-v2/index.html"
                "?url=L3RyYW5zcG9ydC9mbGlnaHRzL2Z1ay9obmQvMjYwNTAyLzI2MDYwOC8="
            )
            self.headers = {}

        def is_closed(self):
            return False

        def set_extra_http_headers(self, headers):
            self.headers = dict(headers)

    class _FakeBrowser:
        def __init__(self):
            self.page = _Page()
            self.goto_urls = []
            self._last_interstitial_grace_meta = {}

        def goto(self, url):
            self.goto_urls.append(str(url))

        def content(self):
            return (
                "<html><head><script src='/captcha.js'></script></head>"
                "<body><div id='px-captcha'></div><main>blocked</main></body></html>"
            )

    browser = _FakeBrowser()
    out = attempt_skyscanner_interstitial_fallback_reload(
        browser,
        "https://www.skyscanner.com/flights",
        grace_result={"used": True, "cleared": False},
        human_mimic=True,
        grace_ms_extended=2000,
        max_reload_attempts=1,
    )

    assert out["used"] is True
    assert browser.goto_urls
    assert browser.goto_urls[0].startswith("https://www.skyscanner.com/transport/flights/fuk/hnd/260502/260608/")
    assert out["reload_target_url"].startswith("https://www.skyscanner.com/transport/flights/fuk/hnd/260502/260608/")
    assert out["expected_route_url"].startswith("https://www.skyscanner.com/transport/flights/fuk/hnd/260502/260608/")


def test_skyscanner_fallback_reload_uses_fallback_url_when_decoded_target_is_root():
    class _Page:
        def __init__(self):
            self.url = "https://www.skyscanner.com/sttc/px/captcha-v2/index.html?url=Lw=="
            self.headers = {}

        def is_closed(self):
            return False

        def set_extra_http_headers(self, headers):
            self.headers = dict(headers)

    class _FakeBrowser:
        def __init__(self):
            self.page = _Page()
            self.goto_urls = []

        def goto(self, url):
            self.goto_urls.append(str(url))
            self.page.url = str(url)

        def content(self):
            return (
                "<html><head><script src='/captcha.js'></script></head>"
                "<body><div id='px-captcha'></div><main>blocked</main></body></html>"
            )

    browser = _FakeBrowser()
    out = attempt_skyscanner_interstitial_fallback_reload(
        browser,
        "https://www.skyscanner.com/flights",
        grace_result={"used": True, "cleared": False},
        human_mimic=True,
        grace_ms_extended=2000,
        max_reload_attempts=1,
    )

    assert out["used"] is True
    assert browser.goto_urls
    assert browser.goto_urls[0] == "https://www.skyscanner.com/flights"
    assert out["reload_target_url"] == "https://www.skyscanner.com/flights"


def test_skyscanner_fallback_reload_respects_success_html_predicate_gate():
    class _Page:
        def __init__(self):
            self.url = "https://www.skyscanner.com/transport/flights/fuk/hnd/260502/260608/"
            self.headers = {}

        def is_closed(self):
            return False

        def set_extra_http_headers(self, headers):
            self.headers = dict(headers)

    class _FakeBrowser:
        def __init__(self):
            self.page = _Page()
            self.goto_calls = 0
            self._last_interstitial_grace_meta = {}

        def goto(self, url):
            self.goto_calls += 1
            self.page.url = str(url)

        def human_mimic_interstitial_grace(self, duration_ms=0):  # noqa: ARG002
            self._last_interstitial_grace_meta = {}

        def content(self):
            return "<html><body><main>non_blocked_shell</main></body></html>"

    browser = _FakeBrowser()
    out = attempt_skyscanner_interstitial_fallback_reload(
        browser,
        "https://www.skyscanner.com/transport/flights/fuk/hnd/260502/260608/",
        grace_result={"used": True, "cleared": False},
        human_mimic=True,
        grace_ms_extended=1000,
        max_reload_attempts=2,
        allow_manual_escalation=False,
        success_html_predicate=lambda _html, _url: False,
    )

    assert out["used"] is True
    assert out["attempted"] is True
    assert out["cleared"] is False
    assert out["reason"] == "success_predicate_failed"
    assert browser.goto_calls >= 2


def test_validate_skyscanner_clearance_flags_reissue_after_unsuccessful_press_hold():
    class _Page:
        url = "https://www.skyscanner.com/flights"

        def is_closed(self):
            return False

        def wait_for_timeout(self, _ms):
            return None

    class _Browser:
        def __init__(self):
            self.page = _Page()
            self._probe_idx = 0

        def content(self):
            return "<html><body><main>Skyscanner flights</main></body></html>"

        def collect_runtime_diagnostics(self, **kwargs):  # noqa: ARG002
            self._probe_idx += 1
            if self._probe_idx == 1:
                signature = "sig_a"
            elif self._probe_idx == 2:
                signature = "sig_b"
            else:
                signature = "sig_c"
            return {
                "dom_probe": {
                    "cookie_enabled": True,
                    "cookie_probe_settable": True,
                    "px_iframe_count": 0,
                    "px_challenge_signature": signature,
                },
                "selector_probe": [{"selector": "#px-captcha", "count": 0, "visible": False}],
            }

    out = validate_skyscanner_interstitial_clearance(
        browser=_Browser(),
        html_text="<html><body><main>Skyscanner flights</main></body></html>",
        get_threshold_fn=lambda key, default=None: (
            2
            if key == "skyscanner_interstitial_clearance_checks"
            else 10
            if key == "skyscanner_interstitial_clearance_interval_ms"
            else 10
            if key == "skyscanner_interstitial_clearance_cooldown_probe_ms"
            else (default if default is not None else 0)
        ),
        grace_probe={"press_hold_executed": True, "press_hold_success": False},
    )

    assert out["cleared"] is False
    assert out["reason"] == "blocked_interstitial_reissued_after_manual"


def test_validate_skyscanner_clearance_rejects_route_context_loss_after_clear():
    class _Page:
        url = "https://www.skyscanner.com/flights?"

        def is_closed(self):
            return False

        def wait_for_timeout(self, _ms):
            return None

    class _Browser:
        def __init__(self):
            self.page = _Page()

        def content(self):
            return "<html><body><main>Skyscanner flights</main></body></html>"

        def collect_runtime_diagnostics(self, **kwargs):  # noqa: ARG002
            return {
                "dom_probe": {
                    "cookie_enabled": True,
                    "cookie_probe_settable": True,
                    "px_iframe_count": 0,
                    "px_challenge_signature": "",
                },
                "selector_probe": [{"selector": "#px-captcha", "count": 0, "visible": False}],
            }

    out = validate_skyscanner_interstitial_clearance(
        browser=_Browser(),
        html_text="<html><body><main>Skyscanner flights</main></body></html>",
        get_threshold_fn=lambda key, default=None: (
            1
            if key == "skyscanner_interstitial_clearance_checks"
            else 10
            if key == "skyscanner_interstitial_clearance_interval_ms"
            else 10
            if key == "skyscanner_interstitial_clearance_cooldown_probe_ms"
            else (default if default is not None else 0)
        ),
        grace_probe={
            "expected_route_url": "https://www.skyscanner.com/transport/flights/fuk/hnd/260502/260608/",
        },
    )

    assert out["cleared"] is False
    assert out["reason"] == "blocked_interstitial_route_context_lost"


def test_validate_skyscanner_clearance_fast_paths_on_results_route_surface():
    class _Page:
        url = "https://www.skyscanner.com/transport/flights/fuk/hnd/260502/260608/?rtn=1"

        def is_closed(self):
            return False

        def wait_for_timeout(self, _ms):
            return None

    class _Browser:
        def __init__(self):
            self.page = _Page()

        def content(self):
            return (
                "<html><head><title>Skyscanner</title><script src='/captcha.js'></script></head>"
                "<body><div id='px-captcha'></div></body></html>"
            )

        def collect_runtime_diagnostics(self, **kwargs):  # noqa: ARG002
            return {
                "dom_probe": {
                    "cookie_enabled": True,
                    "cookie_probe_settable": True,
                    "px_iframe_count": 1,
                    "px_iframe_visible_count": 0,
                    "px_challenge_signature": "|sig|1|0|",
                },
                "selector_probe": [
                    {"selector": "#px-captcha", "count": 0, "visible": False},
                    {"selector": "iframe[src*='px-cloud.net']", "count": 1, "visible": False},
                ],
                "network": {
                    "window": {
                        "failed_challenge_hosts_blocked_by_client": 0,
                        "failed_challenge_hosts": 0,
                    }
                },
            }

    out = validate_skyscanner_interstitial_clearance(
        browser=_Browser(),
        html_text=(
            "<html><head><title>Skyscanner</title><script src='/captcha.js'></script></head>"
            "<body><div id='px-captcha'></div></body></html>"
        ),
        get_threshold_fn=lambda key, default=None: (
            4
            if key == "skyscanner_interstitial_clearance_checks"
            else 1100
            if key == "skyscanner_interstitial_clearance_interval_ms"
            else 9000
            if key == "skyscanner_interstitial_clearance_cooldown_probe_ms"
            else (default if default is not None else 0)
        ),
        grace_probe={
            "expected_route_url": "https://www.skyscanner.com/transport/flights/fuk/hnd/260502/260608/?rtn=1",
        },
    )

    assert out["cleared"] is True
    assert out["reason"] == "route_ready_fast_path"
    assert isinstance(out.get("probes"), list)
    assert len(out.get("probes", [])) == 1


def test_skyscanner_last_resort_manual_never_marks_cleared_after_target_closed():
    class _Page:
        def __init__(self):
            self.url = "about:blank"

        def is_closed(self):
            return False

    class _Browser:
        def __init__(self):
            self.page = _Page()

        def allow_manual_verification_intervention(self, **kwargs):  # noqa: ARG002
            return {
                "used": True,
                "reason": "manual_intervention_target_closed",
                "automation_activity_during_manual": {"count": 1},
            }

        def content(self):
            return "<html><body>blank</body></html>"

    out = attempt_skyscanner_last_resort_manual(
        browser=_Browser(),
        grace_probe={"used": True, "cleared": False, "reason": "blocked_interstitial_captcha"},
        fallback_result={"used": True, "attempted": True, "cleared": False},
        get_threshold_fn=lambda key, default=None: default,
    )

    assert out["attempted"] is True
    assert out["cleared"] is False
    assert out["reason"] == "manual_intervention_target_closed"
    assert out["grace_probe"].get("cleared", False) is False


def test_skyscanner_last_resort_manual_runs_bounded_assist_rounds_and_clears():
    class _Page:
        def __init__(self):
            self.url = "https://www.skyscanner.com/flights"

        def is_closed(self):
            return False

    class _Browser:
        def __init__(self):
            self.page = _Page()
            self.manual_intervention_timeout_sec = 120
            self.calls = []
            self._content_calls = 0

        def allow_manual_verification_intervention(self, **kwargs):
            self.calls.append(dict(kwargs))
            return {
                "used": True,
                "reason": "manual_window_elapsed",
                "automation_activity_during_manual": {"count": 0},
            }

        def content(self):
            self._content_calls += 1
            if self._content_calls < 2:
                return (
                    "<html><head><title>Skyscanner</title><script src='/captcha.js'></script></head>"
                    "<body><div id='px-captcha'></div><h1>Are you a person or a robot?</h1></body></html>"
                )
            return "<html><body><main>Skyscanner flights</main></body></html>"

    browser = _Browser()
    out = attempt_skyscanner_last_resort_manual(
        browser=browser,
        grace_probe={"used": True, "cleared": False, "reason": "blocked_interstitial_captcha"},
        fallback_result={"used": True, "attempted": True, "cleared": False},
        get_threshold_fn=lambda key, default=None: (
            2
            if key == "skyscanner_last_resort_manual_rounds"
            else 45
            if key == "skyscanner_captcha_manual_wait_sec"
            else default
        ),
    )

    assert len(browser.calls) == 2
    assert all(call.get("force") is True for call in browser.calls)
    assert all(call.get("mode_override") == "assist" for call in browser.calls)
    assert all(int(call.get("wait_sec", 0) or 0) == 120 for call in browser.calls)
    assert out["cleared"] is True
    assert out["grace_probe"].get("cleared") is True
    assert out["grace_probe"].get("reason") == "cleared_after_last_resort_manual"


def test_skyscanner_last_resort_manual_accepts_results_route_despite_stale_captcha_html():
    class _Page:
        def __init__(self):
            self.url = "https://www.skyscanner.com/transport/flights/fuk/hnd/route-token/"

        def is_closed(self):
            return False

    class _Browser:
        def __init__(self):
            self.page = _Page()
            self.calls = 0

        def allow_manual_verification_intervention(self, **kwargs):  # noqa: ARG002
            self.calls += 1
            return {
                "used": True,
                "reason": "manual_challenge_cleared",
                "automation_activity_during_manual": {"count": 0},
            }

        def content(self):
            # Simulate a stale challenge snapshot that can remain buffered
            # briefly after redirect to route results.
            return (
                "<html><head><title>Skyscanner</title><script src='/captcha.js'></script></head>"
                "<body><div id='px-captcha'></div><h1>Are you a person or a robot?</h1></body></html>"
            )

        def collect_runtime_diagnostics(self, **kwargs):  # noqa: ARG002
            return {
                "dom_probe": {
                    "cookie_enabled": True,
                    "cookie_probe_settable": True,
                    "px_iframe_count": 1,
                    "px_iframe_visible_count": 0,
                    "px_challenge_signature": "|sig|1|0|",
                },
                "selector_probe": [
                    {"selector": "#px-captcha", "count": 0, "visible": False},
                    {"selector": "section[class*='resolve' i]", "count": 0, "visible": False},
                    {"selector": "iframe[src*='px-cloud.net']", "count": 1, "visible": False},
                ],
                "network": {"window": {"failed_challenge_hosts_blocked_by_client": 0, "failed_challenge_hosts": 0}},
            }

    browser = _Browser()
    out = attempt_skyscanner_last_resort_manual(
        browser=browser,
        grace_probe={"used": True, "cleared": False, "reason": "blocked_interstitial_captcha"},
        fallback_result={"used": True, "attempted": True, "cleared": False},
        get_threshold_fn=lambda key, default=None: (
            2
            if key == "skyscanner_last_resort_manual_rounds"
            else 45
            if key == "skyscanner_captcha_manual_wait_sec"
            else default
        ),
    )

    assert browser.calls == 1
    assert out["cleared"] is True
    assert out["grace_probe"].get("reason") == "cleared_after_last_resort_manual"


def test_attempt_gate_runs_limited_fallback_after_manual_no_effect_when_manual_enabled():
    class _FakeBrowser:
        allow_human_intervention = True
        human_intervention_mode = "demo"
        page = type(
            "_Page",
            (),
            {"url": "https://www.skyscanner.com/sttc/px/captcha-v2/index.html?url=L2ZsaWdodHM/"},
        )()

        def content(self):
            return (
                "<html><head><title>Skyscanner</title><script src='/captcha.js'></script></head>"
                "<body><div id='px-captcha'></div><h1>Are you a person or a robot?</h1></body></html>"
            )

    fallback_calls = {"count": 0}

    def _fallback_fn(*args, **kwargs):  # noqa: ARG001
        fallback_calls["count"] += 1
        return {"used": True, "attempted": True, "cleared": False, "reason": "fallback_reload_failed"}

    result = run_attempt_precheck_and_interstitial_gate(
        browser=_FakeBrowser(),
        site_key="skyscanner",
        url="https://www.skyscanner.com/flights",
        origin="NRT",
        dest="HAN",
        depart="2026-03-20",
        return_date=None,
        trip_type="one_way",
        is_domestic=False,
        max_transit=None,
        attempt=0,
        max_retries=2,
        max_turns=2,
        human_mimic=True,
        plan=[],
        last_error=None,
        scenario_run_id="test-run",
        wall_clock_cap_exhausted_fn=lambda: False,
        budget_almost_exhausted_fn=lambda: False,
        budget_remaining_sec_fn=lambda: 60.0,
        get_threshold_fn=lambda *args, **kwargs: 8000,  # noqa: ARG005
        detect_site_interstitial_block_fn=lambda html, site: {},  # noqa: ARG005
        attempt_skyscanner_interstitial_grace_fn=lambda *args, **kwargs: {  # noqa: ARG005
            "used": True,
            "cleared": False,
            "html": "<html><body>still blocked</body></html>",
            "reason": "blocked_interstitial_captcha",
            "manual_intervention": {
                "used": True,
                "reason": "manual_window_elapsed",
                "page_url_before": "https://www.skyscanner.com/sttc/px/captcha-v2/index.html?url=L2ZsaWdodHM/",
                "page_url_after": "https://www.skyscanner.com/sttc/px/captcha-v2/index.html?url=L2ZsaWdodHM/",
            },
        },
        attempt_skyscanner_interstitial_fallback_reload_fn=_fallback_fn,
        write_progress_snapshot_fn=lambda **kwargs: None,
        write_debug_snapshot_fn=lambda payload, **kwargs: None,  # noqa: ARG005
        write_html_snapshot_fn=lambda *args, **kwargs: None,
        write_image_snapshot_fn=lambda *args, **kwargs: None,
        write_json_artifact_snapshot_fn=lambda *args, **kwargs: None,
        scenario_return_fn=lambda *args, **kwargs: "blocked",
        logger=sr.log,
    )

    assert result["should_return"] is True
    assert fallback_calls["count"] == 0


def test_attempt_gate_runs_assist_follow_up_after_manual_no_effect():
    class _FakeBrowser:
        allow_human_intervention = True
        human_intervention_mode = "assist"
        page = type(
            "_Page",
            (),
            {"url": "https://www.skyscanner.com/sttc/px/captcha-v2/index.html?url=L2ZsaWdodHM/"},
        )()

        def content(self):
            return (
                "<html><head><title>Skyscanner</title><script src='/captcha.js'></script></head>"
                "<body><div id='px-captcha'></div><h1>Are you a person or a robot?</h1></body></html>"
            )

    fallback_calls = {"count": 0}

    def _fallback_fn(*args, **kwargs):  # noqa: ARG001
        fallback_calls["count"] += 1
        return {
            "used": True,
            "attempted": True,
            "cleared": False,
            "reason": "fallback_press_hold_unsuccessful",
        }

    result = run_attempt_precheck_and_interstitial_gate(
        browser=_FakeBrowser(),
        site_key="skyscanner",
        url="https://www.skyscanner.com/flights",
        origin="NRT",
        dest="HAN",
        depart="2026-03-20",
        return_date=None,
        trip_type="one_way",
        is_domestic=False,
        max_transit=None,
        attempt=0,
        max_retries=2,
        max_turns=2,
        human_mimic=True,
        plan=[],
        last_error=None,
        scenario_run_id="test-run",
        wall_clock_cap_exhausted_fn=lambda: False,
        budget_almost_exhausted_fn=lambda: False,
        budget_remaining_sec_fn=lambda: 60.0,
        get_threshold_fn=lambda *args, **kwargs: 8000,  # noqa: ARG005
        detect_site_interstitial_block_fn=lambda html, site: {},  # noqa: ARG005
        attempt_skyscanner_interstitial_grace_fn=lambda *args, **kwargs: {  # noqa: ARG005
            "used": True,
            "cleared": False,
            "html": "<html><body>still blocked</body></html>",
            "reason": "blocked_interstitial_captcha",
            "manual_intervention": {
                "used": True,
                "reason": "manual_window_elapsed",
                "page_url_before": "https://www.skyscanner.com/sttc/px/captcha-v2/index.html?url=L2ZsaWdodHM/",
                "page_url_after": "https://www.skyscanner.com/sttc/px/captcha-v2/index.html?url=L2ZsaWdodHM/",
            },
        },
        attempt_skyscanner_interstitial_fallback_reload_fn=_fallback_fn,
        write_progress_snapshot_fn=lambda **kwargs: None,
        write_debug_snapshot_fn=lambda payload, **kwargs: None,  # noqa: ARG005
        write_html_snapshot_fn=lambda *args, **kwargs: None,
        write_image_snapshot_fn=lambda *args, **kwargs: None,
        write_json_artifact_snapshot_fn=lambda *args, **kwargs: None,
        scenario_return_fn=lambda *args, **kwargs: kwargs.get("reason", "blocked"),
        logger=sr.log,
    )

    assert result["should_return"] is True
    assert result["result_html"] == "blocked_interstitial_reissued_after_manual"
    assert fallback_calls["count"] == 1


def test_attempt_gate_skips_fallback_when_manual_changed_page_state():
    class _FakeBrowser:
        allow_human_intervention = True
        page = type("_Page", (), {"url": "https://www.skyscanner.com/flights"})()

        def content(self):
            return (
                "<html><head><title>Skyscanner</title><script src='/captcha.js'></script></head>"
                "<body><div id='px-captcha'></div><h1>Are you a person or a robot?</h1></body></html>"
            )

    fallback_calls = {"count": 0}

    def _fallback_fn(*args, **kwargs):  # noqa: ARG001
        fallback_calls["count"] += 1
        return {"used": True, "attempted": True, "cleared": False, "reason": "fallback_reload_failed"}

    result = run_attempt_precheck_and_interstitial_gate(
        browser=_FakeBrowser(),
        site_key="skyscanner",
        url="https://www.skyscanner.com/flights",
        origin="NRT",
        dest="HAN",
        depart="2026-03-20",
        return_date=None,
        trip_type="one_way",
        is_domestic=False,
        max_transit=None,
        attempt=0,
        max_retries=2,
        max_turns=2,
        human_mimic=True,
        plan=[],
        last_error=None,
        scenario_run_id="test-run",
        wall_clock_cap_exhausted_fn=lambda: False,
        budget_almost_exhausted_fn=lambda: False,
        budget_remaining_sec_fn=lambda: 60.0,
        get_threshold_fn=lambda *args, **kwargs: 8000,  # noqa: ARG005
        detect_site_interstitial_block_fn=lambda html, site: {},  # noqa: ARG005
        attempt_skyscanner_interstitial_grace_fn=lambda *args, **kwargs: {  # noqa: ARG005
            "used": True,
            "cleared": False,
            "html": "<html><body>still blocked</body></html>",
            "reason": "blocked_interstitial_captcha",
            "manual_intervention": {
                "used": True,
                "reason": "manual_window_elapsed",
                "page_url_before": "https://www.skyscanner.com/sttc/px/captcha-v2/index.html?url=L2ZsaWdodHM/",
                "page_url_after": "https://www.skyscanner.com/flights",
            },
        },
        attempt_skyscanner_interstitial_fallback_reload_fn=_fallback_fn,
        write_progress_snapshot_fn=lambda **kwargs: None,
        write_debug_snapshot_fn=lambda payload, **kwargs: None,  # noqa: ARG005
        write_html_snapshot_fn=lambda *args, **kwargs: None,
        write_image_snapshot_fn=lambda *args, **kwargs: None,
        write_json_artifact_snapshot_fn=lambda *args, **kwargs: None,
        scenario_return_fn=lambda *args, **kwargs: "blocked",
        logger=sr.log,
    )

    assert result["should_return"] is True
    assert fallback_calls["count"] == 0


def test_attempt_gate_allows_limited_fallback_when_manual_intervention_target_closed_and_manual_disabled():
    class _FakeBrowser:
        allow_human_intervention = False

        def content(self):
            return (
                "<html><head><title>Skyscanner</title><script src='/captcha.js'></script></head>"
                "<body><div id='px-captcha'></div><h1>Are you a person or a robot?</h1></body></html>"
            )

    fallback_calls = {"count": 0, "max_reload_attempts": None}

    def _fallback_fn(*args, **kwargs):  # noqa: ARG001
        fallback_calls["count"] += 1
        fallback_calls["max_reload_attempts"] = kwargs.get("max_reload_attempts")
        return {"used": True, "attempted": True, "cleared": False, "reason": "fallback_reload_failed"}

    result = run_attempt_precheck_and_interstitial_gate(
        browser=_FakeBrowser(),
        site_key="skyscanner",
        url="https://www.skyscanner.com/flights",
        origin="NRT",
        dest="HAN",
        depart="2026-03-20",
        return_date=None,
        trip_type="one_way",
        is_domestic=False,
        max_transit=None,
        attempt=0,
        max_retries=2,
        max_turns=2,
        human_mimic=True,
        plan=[],
        last_error=None,
        scenario_run_id="test-run",
        wall_clock_cap_exhausted_fn=lambda: False,
        budget_almost_exhausted_fn=lambda: False,
        budget_remaining_sec_fn=lambda: 60.0,
        get_threshold_fn=lambda *args, **kwargs: 8000,  # noqa: ARG005
        detect_site_interstitial_block_fn=lambda html, site: {},  # noqa: ARG005
        attempt_skyscanner_interstitial_grace_fn=lambda *args, **kwargs: {  # noqa: ARG005
            "used": True,
            "cleared": False,
            "html": "<html><body>still blocked</body></html>",
            "reason": "blocked_interstitial_captcha",
            "manual_intervention": {
                "used": True,
                "reason": "manual_intervention_target_closed",
                "error": "TargetClosedError",
            },
        },
        attempt_skyscanner_interstitial_fallback_reload_fn=_fallback_fn,
        write_progress_snapshot_fn=lambda **kwargs: None,
        write_debug_snapshot_fn=lambda payload, **kwargs: None,  # noqa: ARG005
        write_html_snapshot_fn=lambda *args, **kwargs: None,
        write_image_snapshot_fn=lambda *args, **kwargs: None,
        write_json_artifact_snapshot_fn=lambda *args, **kwargs: None,
        scenario_return_fn=lambda *args, **kwargs: "blocked",
        logger=sr.log,
    )

    assert result["should_return"] is True
    assert fallback_calls["count"] == 0
    assert fallback_calls["max_reload_attempts"] is None


def test_attempt_gate_skips_follow_up_after_target_closed_when_manual_enabled():
    class _FakeBrowser:
        allow_human_intervention = True

        def content(self):
            return (
                "<html><head><title>Skyscanner</title><script src='/captcha.js'></script></head>"
                "<body><div id='px-captcha'></div><h1>Are you a person or a robot?</h1></body></html>"
            )

    fallback_calls = {"count": 0}

    def _fallback_fn(*args, **kwargs):  # noqa: ARG001
        fallback_calls["count"] += 1
        return {"used": True, "attempted": True, "cleared": False, "reason": "fallback_reload_failed"}

    result = run_attempt_precheck_and_interstitial_gate(
        browser=_FakeBrowser(),
        site_key="skyscanner",
        url="https://www.skyscanner.com/flights",
        origin="NRT",
        dest="HAN",
        depart="2026-03-20",
        return_date=None,
        trip_type="one_way",
        is_domestic=False,
        max_transit=None,
        attempt=0,
        max_retries=2,
        max_turns=2,
        human_mimic=True,
        plan=[],
        last_error=None,
        scenario_run_id="test-run",
        wall_clock_cap_exhausted_fn=lambda: False,
        budget_almost_exhausted_fn=lambda: False,
        budget_remaining_sec_fn=lambda: 60.0,
        get_threshold_fn=lambda *args, **kwargs: 8000,  # noqa: ARG005
        detect_site_interstitial_block_fn=lambda html, site: {},  # noqa: ARG005
        attempt_skyscanner_interstitial_grace_fn=lambda *args, **kwargs: {  # noqa: ARG005
            "used": True,
            "cleared": False,
            "html": "<html><body>still blocked</body></html>",
            "reason": "blocked_interstitial_captcha",
            "manual_intervention": {
                "used": True,
                "reason": "manual_intervention_target_closed",
                "error": "TargetClosedError",
            },
        },
        attempt_skyscanner_interstitial_fallback_reload_fn=_fallback_fn,
        write_progress_snapshot_fn=lambda **kwargs: None,
        write_debug_snapshot_fn=lambda payload, **kwargs: None,  # noqa: ARG005
        write_html_snapshot_fn=lambda *args, **kwargs: None,
        write_image_snapshot_fn=lambda *args, **kwargs: None,
        write_json_artifact_snapshot_fn=lambda *args, **kwargs: None,
        scenario_return_fn=lambda *args, **kwargs: "blocked",
        logger=sr.log,
    )

    assert result["should_return"] is True
    assert fallback_calls["count"] == 0


def test_attempt_gate_uses_last_resort_manual_when_manual_disabled():
    class _FakeBrowser:
        allow_human_intervention = False
        last_resort_manual_when_disabled = True

        def __init__(self):
            self._manual_calls = 0
            self.page = type("_Page", (), {"is_closed": lambda self_inner: False})()

        def allow_manual_verification_intervention(self, **kwargs):
            self._manual_calls += 1
            return {"used": True, "reason": "manual_window_elapsed", "wait_sec": int(kwargs.get("wait_sec", 0))}

        def content(self):
            if self._manual_calls <= 0:
                return (
                    "<html><head><title>Skyscanner</title><script src='/captcha.js'></script></head>"
                    "<body><div id='px-captcha'></div><h1>Are you a person or a robot?</h1></body></html>"
                )
            return "<html><body><main>Skyscanner flights</main></body></html>"

    fallback_calls = {"count": 0}

    def _fallback_fn(*args, **kwargs):  # noqa: ARG001
        fallback_calls["count"] += 1
        return {"used": True, "attempted": True, "cleared": False, "reason": "fallback_reload_failed"}

    result = run_attempt_precheck_and_interstitial_gate(
        browser=_FakeBrowser(),
        site_key="skyscanner",
        url="https://www.skyscanner.com/flights",
        origin="NRT",
        dest="HAN",
        depart="2026-03-20",
        return_date=None,
        trip_type="one_way",
        is_domestic=False,
        max_transit=None,
        attempt=0,
        max_retries=2,
        max_turns=2,
        human_mimic=True,
        plan=[],
        last_error=None,
        scenario_run_id="test-run",
        wall_clock_cap_exhausted_fn=lambda: False,
        budget_almost_exhausted_fn=lambda: False,
        budget_remaining_sec_fn=lambda: 60.0,
        get_threshold_fn=lambda *args, **kwargs: 45 if args and args[0] == "skyscanner_captcha_manual_wait_sec" else 8000,  # noqa: ARG005
        detect_site_interstitial_block_fn=lambda html, site: {},  # noqa: ARG005
        attempt_skyscanner_interstitial_grace_fn=lambda *args, **kwargs: {  # noqa: ARG005
            "used": True,
            "cleared": False,
            "html": "<html><body>still blocked</body></html>",
            "reason": "blocked_interstitial_captcha",
            "manual_intervention": {"used": False, "reason": "manual_intervention_disabled"},
        },
        attempt_skyscanner_interstitial_fallback_reload_fn=_fallback_fn,
        write_progress_snapshot_fn=lambda **kwargs: None,
        write_debug_snapshot_fn=lambda payload, **kwargs: None,  # noqa: ARG005
        write_html_snapshot_fn=lambda *args, **kwargs: None,
        write_image_snapshot_fn=lambda *args, **kwargs: None,
        write_json_artifact_snapshot_fn=lambda *args, **kwargs: None,
        scenario_return_fn=lambda *args, **kwargs: "blocked",
        logger=sr.log,
    )

    assert result["should_return"] is False
    assert fallback_calls["count"] == 1


def test_attempt_gate_reprobes_after_last_resort_manual_challenge_cleared():
    class _FakePage:
        url = "https://www.skyscanner.com/flights?"

        def __init__(self):
            self.wait_calls = 0

        def is_closed(self):
            return False

        def wait_for_timeout(self, _ms):
            self.wait_calls += 1

    class _FakeBrowser:
        allow_human_intervention = False
        last_resort_manual_when_disabled = True

        def __init__(self):
            self.page = _FakePage()
            self._manual_calls = 0
            self._content_calls = 0

        def allow_manual_verification_intervention(self, **kwargs):  # noqa: ARG002
            self._manual_calls += 1
            return {
                "used": True,
                "reason": "manual_challenge_cleared",
                "automation_activity_during_manual": {"count": 0, "counts": {}},
            }

        def content(self):
            self._content_calls += 1
            if self._content_calls <= 2:
                return (
                    "<html><head><title>Skyscanner</title><script src='/captcha.js'></script></head>"
                    "<body><div id='px-captcha'></div><h1>Are you a person or a robot?</h1></body></html>"
                )
            return "<html><body><main>Skyscanner flights</main></body></html>"

    browser = _FakeBrowser()
    result = run_attempt_precheck_and_interstitial_gate(
        browser=browser,
        site_key="skyscanner",
        url="https://www.skyscanner.com/flights",
        origin="NRT",
        dest="HAN",
        depart="2026-03-20",
        return_date=None,
        trip_type="one_way",
        is_domestic=False,
        max_transit=None,
        attempt=0,
        max_retries=2,
        max_turns=2,
        human_mimic=True,
        plan=[],
        last_error=None,
        scenario_run_id="test-run",
        wall_clock_cap_exhausted_fn=lambda: False,
        budget_almost_exhausted_fn=lambda: False,
        budget_remaining_sec_fn=lambda: 60.0,
        get_threshold_fn=lambda key, default=None: (
            45
            if key == "skyscanner_captcha_manual_wait_sec"
            else 300
            if key == "skyscanner_interstitial_clearance_interval_ms"
            else default
        ),
        detect_site_interstitial_block_fn=lambda html, site: {},  # noqa: ARG005
        attempt_skyscanner_interstitial_grace_fn=lambda *args, **kwargs: {  # noqa: ARG005
            "used": True,
            "cleared": False,
            "html": "<html><body>still blocked</body></html>",
            "reason": "blocked_interstitial_captcha",
            "manual_intervention": {"used": False, "reason": "manual_intervention_disabled"},
        },
        attempt_skyscanner_interstitial_fallback_reload_fn=lambda *args, **kwargs: {  # noqa: ARG005
            "used": True,
            "attempted": True,
            "cleared": False,
            "reason": "fallback_reload_failed",
        },
        write_progress_snapshot_fn=lambda **kwargs: None,
        write_debug_snapshot_fn=lambda payload, **kwargs: None,  # noqa: ARG005
        write_html_snapshot_fn=lambda *args, **kwargs: None,
        write_image_snapshot_fn=lambda *args, **kwargs: None,
        write_json_artifact_snapshot_fn=lambda *args, **kwargs: None,
        scenario_return_fn=lambda *args, **kwargs: "blocked",
        logger=sr.log,
    )

    assert result["should_return"] is False
    assert browser._manual_calls >= 1
    assert browser.page.wait_calls >= 1


def test_attempt_gate_uses_last_resort_manual_reason_for_terminal_classification():
    class _FakePage:
        url = "https://www.skyscanner.com/flights?"

        def is_closed(self):
            return False

        def wait_for_timeout(self, _ms):
            return None

    class _FakeBrowser:
        allow_human_intervention = False
        last_resort_manual_when_disabled = True

        def __init__(self):
            self.page = _FakePage()

        def allow_manual_verification_intervention(self, **kwargs):  # noqa: ARG002
            return {
                "used": True,
                "reason": "manual_intervention_target_closed",
                "error": "TargetClosedError",
                "automation_activity_during_manual": {"count": 0, "counts": {}},
            }

        def content(self):
            return (
                "<html><head><script src='/captcha.js'></script></head>"
                "<body><div id='px-captcha'></div><h1>Are you a person or a robot?</h1></body></html>"
            )

    result = run_attempt_precheck_and_interstitial_gate(
        browser=_FakeBrowser(),
        site_key="skyscanner",
        url="https://www.skyscanner.com/flights",
        origin="NRT",
        dest="HAN",
        depart="2026-03-20",
        return_date=None,
        trip_type="one_way",
        is_domestic=False,
        max_transit=None,
        attempt=0,
        max_retries=2,
        max_turns=2,
        human_mimic=True,
        plan=[],
        last_error=None,
        scenario_run_id="test-run",
        wall_clock_cap_exhausted_fn=lambda: False,
        budget_almost_exhausted_fn=lambda: False,
        budget_remaining_sec_fn=lambda: 60.0,
        get_threshold_fn=lambda key, default=None: (
            45
            if key == "skyscanner_captcha_manual_wait_sec"
            else default
        ),
        detect_site_interstitial_block_fn=lambda html, site: {},  # noqa: ARG005
        attempt_skyscanner_interstitial_grace_fn=lambda *args, **kwargs: {  # noqa: ARG005
            "used": True,
            "cleared": False,
            "html": "<html><body>still blocked</body></html>",
            "reason": "blocked_interstitial_captcha",
            "manual_intervention": {"used": False, "reason": "manual_intervention_disabled"},
        },
        attempt_skyscanner_interstitial_fallback_reload_fn=lambda *args, **kwargs: {  # noqa: ARG005
            "used": True,
            "attempted": True,
            "cleared": False,
            "reason": "fallback_reload_failed",
        },
        write_progress_snapshot_fn=lambda **kwargs: None,
        write_debug_snapshot_fn=lambda payload, **kwargs: None,  # noqa: ARG005
        write_html_snapshot_fn=lambda *args, **kwargs: None,
        write_image_snapshot_fn=lambda *args, **kwargs: None,
        write_json_artifact_snapshot_fn=lambda *args, **kwargs: None,
        scenario_return_fn=lambda _html, **kwargs: str(kwargs.get("reason", "")),
        logger=sr.log,
    )

    assert result["should_return"] is True
    assert result["result_html"] == "blocked_interstitial_manual_target_closed"


def test_attempt_gate_soft_stop_uses_safe_html_when_page_content_raises():
    class _FakeBrowser:
        def content(self):
            raise RuntimeError("TargetClosedError")

    result = run_attempt_precheck_and_interstitial_gate(
        browser=_FakeBrowser(),
        site_key="skyscanner",
        url="https://www.skyscanner.com/flights",
        origin="NRT",
        dest="HAN",
        depart="2026-03-20",
        return_date=None,
        trip_type="one_way",
        is_domestic=False,
        max_transit=None,
        attempt=0,
        max_retries=2,
        max_turns=2,
        human_mimic=True,
        plan=[],
        last_error=None,
        scenario_run_id="test-run",
        wall_clock_cap_exhausted_fn=lambda: True,
        budget_almost_exhausted_fn=lambda: False,
        budget_remaining_sec_fn=lambda: 60.0,
        get_threshold_fn=lambda key, default=None: default,
        detect_site_interstitial_block_fn=lambda html, site: {},  # noqa: ARG005
        attempt_skyscanner_interstitial_grace_fn=lambda *args, **kwargs: {},  # noqa: ARG005
        attempt_skyscanner_interstitial_fallback_reload_fn=lambda *args, **kwargs: {},  # noqa: ARG005
        write_progress_snapshot_fn=lambda **kwargs: None,
        write_debug_snapshot_fn=lambda payload, **kwargs: None,  # noqa: ARG005
        write_html_snapshot_fn=lambda *args, **kwargs: None,
        write_image_snapshot_fn=lambda *args, **kwargs: None,
        write_json_artifact_snapshot_fn=lambda *args, **kwargs: None,
        scenario_return_fn=lambda html, **kwargs: f"{kwargs.get('reason','')}:{len(str(html or ''))}",
        logger=sr.log,
    )

    assert result["should_return"] is True
    assert result["result_html"] == "scenario_wall_clock_cap:0"


def test_attempt_gate_rejects_false_clear_when_page_closed():
    class _ClosedPage:
        def is_closed(self):
            return True

    class _FakeBrowser:
        allow_human_intervention = True

        def __init__(self):
            self.page = _ClosedPage()

        def content(self):
            return (
                "<html><head><title>Skyscanner</title><script src='/captcha.js'></script></head>"
                "<body><div id='px-captcha'></div><h1>Are you a person or a robot?</h1></body></html>"
            )

    fallback_calls = {"count": 0}

    def _fallback_fn(*args, **kwargs):  # noqa: ARG001
        fallback_calls["count"] += 1
        return {"used": False, "attempted": False, "cleared": False, "reason": "fallback_not_run"}

    result = run_attempt_precheck_and_interstitial_gate(
        browser=_FakeBrowser(),
        site_key="skyscanner",
        url="https://www.skyscanner.com/flights",
        origin="NRT",
        dest="HAN",
        depart="2026-03-20",
        return_date=None,
        trip_type="one_way",
        is_domestic=False,
        max_transit=None,
        attempt=0,
        max_retries=2,
        max_turns=2,
        human_mimic=True,
        plan=[],
        last_error=None,
        scenario_run_id="test-run",
        wall_clock_cap_exhausted_fn=lambda: False,
        budget_almost_exhausted_fn=lambda: False,
        budget_remaining_sec_fn=lambda: 60.0,
        get_threshold_fn=lambda *args, **kwargs: 8000,  # noqa: ARG005
        detect_site_interstitial_block_fn=lambda html, site: {},  # noqa: ARG005
        attempt_skyscanner_interstitial_grace_fn=lambda *args, **kwargs: {  # noqa: ARG005
            "used": True,
            "cleared": True,
            "html": "",
            "reason": "cleared",
        },
        attempt_skyscanner_interstitial_fallback_reload_fn=_fallback_fn,
        write_progress_snapshot_fn=lambda **kwargs: None,
        write_debug_snapshot_fn=lambda payload, **kwargs: None,  # noqa: ARG005
        write_html_snapshot_fn=lambda *args, **kwargs: None,
        write_image_snapshot_fn=lambda *args, **kwargs: None,
        write_json_artifact_snapshot_fn=lambda *args, **kwargs: None,
        scenario_return_fn=lambda *args, **kwargs: "blocked",
        logger=sr.log,
    )

    assert result["should_return"] is True
    assert fallback_calls["count"] == 0


def test_attempt_gate_rejects_clear_when_px_selector_still_visible():
    class _Page:
        url = "https://www.skyscanner.com/flights"

        def is_closed(self):
            return False

        def wait_for_timeout(self, _ms):
            return None

    class _FakeBrowser:
        allow_human_intervention = True

        def __init__(self):
            self.page = _Page()

        def content(self):
            return (
                "<html><head><title>Skyscanner</title><script src='/captcha.js'></script></head>"
                "<body><div id='px-captcha'></div><h1>Are you a person or a robot?</h1></body></html>"
            )

        def collect_runtime_diagnostics(self, **kwargs):  # noqa: ARG002
            return {
                "dom_probe": {"cookie_enabled": True, "cookie_probe_settable": True},
                "selector_probe": [{"selector": "#px-captcha", "count": 1, "visible": True}],
            }

    result = run_attempt_precheck_and_interstitial_gate(
        browser=_FakeBrowser(),
        site_key="skyscanner",
        url="https://www.skyscanner.com/flights",
        origin="NRT",
        dest="HAN",
        depart="2026-03-20",
        return_date=None,
        trip_type="one_way",
        is_domestic=False,
        max_transit=None,
        attempt=0,
        max_retries=2,
        max_turns=2,
        human_mimic=True,
        plan=[],
        last_error=None,
        scenario_run_id="test-run",
        wall_clock_cap_exhausted_fn=lambda: False,
        budget_almost_exhausted_fn=lambda: False,
        budget_remaining_sec_fn=lambda: 60.0,
        get_threshold_fn=lambda key, default=None: (
            1
            if key == "skyscanner_interstitial_clearance_checks"
            else 10
            if key == "skyscanner_interstitial_clearance_interval_ms"
            else (default if default is not None else 8000)
        ),
        detect_site_interstitial_block_fn=lambda html, site: {},  # noqa: ARG005
        attempt_skyscanner_interstitial_grace_fn=lambda *args, **kwargs: {  # noqa: ARG005
            "used": True,
            "cleared": True,
            "html": "<html><body><main>Skyscanner flights</main></body></html>",
            "reason": "cleared",
        },
        attempt_skyscanner_interstitial_fallback_reload_fn=lambda *args, **kwargs: {
            "used": False,
            "attempted": False,
            "cleared": False,
            "reason": "not_used",
        },
        write_progress_snapshot_fn=lambda **kwargs: None,
        write_debug_snapshot_fn=lambda payload, **kwargs: None,  # noqa: ARG005
        write_html_snapshot_fn=lambda *args, **kwargs: None,
        write_image_snapshot_fn=lambda *args, **kwargs: None,
        write_json_artifact_snapshot_fn=lambda *args, **kwargs: None,
        scenario_return_fn=lambda *args, **kwargs: "blocked",
        logger=sr.log,
    )

    assert result["should_return"] is True


def test_attempt_gate_rejects_clear_when_cookie_runtime_is_disabled():
    class _Page:
        url = "https://www.skyscanner.com/flights"

        def is_closed(self):
            return False

        def wait_for_timeout(self, _ms):
            return None

    class _FakeBrowser:
        allow_human_intervention = True

        def __init__(self):
            self.page = _Page()

        def content(self):
            return (
                "<html><head><title>Skyscanner</title><script src='/captcha.js'></script></head>"
                "<body><div id='px-captcha'></div><h1>Are you a person or a robot?</h1></body></html>"
            )

        def collect_runtime_diagnostics(self, **kwargs):  # noqa: ARG002
            return {
                "dom_probe": {"cookie_enabled": False, "cookie_probe_settable": False},
                "selector_probe": [{"selector": "#px-captcha", "count": 0, "visible": False}],
            }

    result = run_attempt_precheck_and_interstitial_gate(
        browser=_FakeBrowser(),
        site_key="skyscanner",
        url="https://www.skyscanner.com/flights",
        origin="NRT",
        dest="HAN",
        depart="2026-03-20",
        return_date=None,
        trip_type="one_way",
        is_domestic=False,
        max_transit=None,
        attempt=0,
        max_retries=2,
        max_turns=2,
        human_mimic=True,
        plan=[],
        last_error=None,
        scenario_run_id="test-run",
        wall_clock_cap_exhausted_fn=lambda: False,
        budget_almost_exhausted_fn=lambda: False,
        budget_remaining_sec_fn=lambda: 60.0,
        get_threshold_fn=lambda key, default=None: (
            1
            if key == "skyscanner_interstitial_clearance_checks"
            else 10
            if key == "skyscanner_interstitial_clearance_interval_ms"
            else (default if default is not None else 8000)
        ),
        detect_site_interstitial_block_fn=lambda html, site: {},  # noqa: ARG005
        attempt_skyscanner_interstitial_grace_fn=lambda *args, **kwargs: {  # noqa: ARG005
            "used": True,
            "cleared": True,
            "html": "<html><body><main>Skyscanner flights</main></body></html>",
            "reason": "cleared",
        },
        attempt_skyscanner_interstitial_fallback_reload_fn=lambda *args, **kwargs: {
            "used": False,
            "attempted": False,
            "cleared": False,
            "reason": "not_used",
        },
        write_progress_snapshot_fn=lambda **kwargs: None,
        write_debug_snapshot_fn=lambda payload, **kwargs: None,  # noqa: ARG005
        write_html_snapshot_fn=lambda *args, **kwargs: None,
        write_image_snapshot_fn=lambda *args, **kwargs: None,
        write_json_artifact_snapshot_fn=lambda *args, **kwargs: None,
        scenario_return_fn=lambda *args, **kwargs: "blocked",
        logger=sr.log,
    )

    assert result["should_return"] is True


def test_attempt_gate_rejects_clear_when_challenge_scripts_blocked_by_client():
    class _Page:
        url = "https://www.skyscanner.com/flights"

        def is_closed(self):
            return False

        def wait_for_timeout(self, _ms):
            return None

    class _FakeBrowser:
        allow_human_intervention = True

        def __init__(self):
            self.page = _Page()

        def content(self):
            return (
                "<html><head><title>Skyscanner</title><script src='/captcha.js'></script></head>"
                "<body><div id='px-captcha'></div><h1>Are you a person or a robot?</h1></body></html>"
            )

        def collect_runtime_diagnostics(self, **kwargs):  # noqa: ARG002
            return {
                "dom_probe": {"cookie_enabled": True, "cookie_probe_settable": True},
                "selector_probe": [{"selector": "#px-captcha", "count": 0, "visible": False}],
                "network": {
                    "window": {
                        "failed_challenge_hosts_blocked_by_client": 2,
                        "failed_challenge_hosts": 2,
                    }
                },
            }

    result = run_attempt_precheck_and_interstitial_gate(
        browser=_FakeBrowser(),
        site_key="skyscanner",
        url="https://www.skyscanner.com/flights",
        origin="NRT",
        dest="HAN",
        depart="2026-03-20",
        return_date=None,
        trip_type="one_way",
        is_domestic=False,
        max_transit=None,
        attempt=0,
        max_retries=2,
        max_turns=2,
        human_mimic=True,
        plan=[],
        last_error=None,
        scenario_run_id="test-run",
        wall_clock_cap_exhausted_fn=lambda: False,
        budget_almost_exhausted_fn=lambda: False,
        budget_remaining_sec_fn=lambda: 60.0,
        get_threshold_fn=lambda key, default=None: (
            1
            if key == "skyscanner_interstitial_clearance_checks"
            else 10
            if key == "skyscanner_interstitial_clearance_interval_ms"
            else 10
            if key == "skyscanner_interstitial_clearance_cooldown_probe_ms"
            else (default if default is not None else 8000)
        ),
        detect_site_interstitial_block_fn=lambda html, site: {},  # noqa: ARG005
        attempt_skyscanner_interstitial_grace_fn=lambda *args, **kwargs: {  # noqa: ARG005
            "used": True,
            "cleared": True,
            "html": "<html><body><main>Skyscanner flights</main></body></html>",
            "reason": "cleared",
        },
        attempt_skyscanner_interstitial_fallback_reload_fn=lambda *args, **kwargs: {
            "used": False,
            "attempted": False,
            "cleared": False,
            "reason": "not_used",
        },
        write_progress_snapshot_fn=lambda **kwargs: None,
        write_debug_snapshot_fn=lambda payload, **kwargs: None,  # noqa: ARG005
        write_html_snapshot_fn=lambda *args, **kwargs: None,
        write_image_snapshot_fn=lambda *args, **kwargs: None,
        write_json_artifact_snapshot_fn=lambda *args, **kwargs: None,
        scenario_return_fn=lambda *args, **kwargs: kwargs.get("reason", "blocked"),
        logger=sr.log,
    )

    assert result["should_return"] is True
    assert result["result_html"] == "blocked_interstitial_challenge_script_blocked"


def test_attempt_gate_route_context_rearm_recovers_after_clearance_route_loss():
    class _Page:
        def __init__(self):
            self.url = "https://www.skyscanner.com/flights?"

        def is_closed(self):
            return False

        def wait_for_timeout(self, _ms):
            return None

    class _FakeBrowser:
        allow_human_intervention = True

        def __init__(self):
            self.page = _Page()
            self.goto_calls = []
            self._content_calls = 0

        def goto(self, url):
            self.goto_calls.append(str(url))
            self.page.url = str(url)

        def content(self):
            self._content_calls += 1
            if self._content_calls == 1:
                return (
                    "<html><head><script src='/captcha.js'></script></head>"
                    "<body><div id='px-captcha'></div><h1>Are you a person or a robot?</h1></body></html>"
                )
            return "<html><body><main>Skyscanner flights</main></body></html>"

        def collect_runtime_diagnostics(self, **kwargs):  # noqa: ARG002
            return {
                "dom_probe": {
                    "cookie_enabled": True,
                    "cookie_probe_settable": True,
                    "px_iframe_count": 0,
                    "px_challenge_signature": "",
                },
                "selector_probe": [{"selector": "#px-captcha", "count": 0, "visible": False}],
            }

    browser = _FakeBrowser()
    expected_route = "https://www.skyscanner.com/transport/flights/fuk/hnd/260502/260608/"
    depart = (date.today() + timedelta(days=30)).isoformat()
    return_date = (date.today() + timedelta(days=37)).isoformat()
    result = run_attempt_precheck_and_interstitial_gate(
        browser=browser,
        site_key="skyscanner",
        url="https://www.skyscanner.com/flights",
        origin="FUK",
        dest="HND",
        depart=depart,
        return_date=return_date,
        trip_type="round_trip",
        is_domestic=True,
        max_transit=None,
        attempt=0,
        max_retries=2,
        max_turns=2,
        human_mimic=True,
        plan=[],
        last_error=None,
        scenario_run_id="test-run",
        wall_clock_cap_exhausted_fn=lambda: False,
        budget_almost_exhausted_fn=lambda: False,
        budget_remaining_sec_fn=lambda: 60.0,
        get_threshold_fn=lambda key, default=None: (
            1
            if key == "skyscanner_interstitial_clearance_checks"
            else 10
            if key == "skyscanner_interstitial_clearance_interval_ms"
            else 10
            if key == "skyscanner_interstitial_clearance_cooldown_probe_ms"
            else (default if default is not None else 0)
        ),
        detect_site_interstitial_block_fn=lambda html, site: {  # noqa: ARG005
            "reason": "blocked_interstitial_captcha",
            "page_kind": "interstitial",
            "block_type": "captcha",
            "evidence": {"ui.token_hits": ["captcha-v2"], "html.length": 100},
        },
        attempt_skyscanner_interstitial_grace_fn=lambda *args, **kwargs: {  # noqa: ARG005
            "used": True,
            "cleared": True,
            "html": "<html><body><main>Skyscanner flights</main></body></html>",
            "reason": "manual_challenge_cleared_validated",
            "expected_route_url": expected_route,
        },
        attempt_skyscanner_interstitial_fallback_reload_fn=lambda *args, **kwargs: {  # noqa: ARG005
            "used": False,
            "attempted": False,
            "cleared": False,
            "reason": "not_used",
        },
        write_progress_snapshot_fn=lambda **kwargs: None,
        write_debug_snapshot_fn=lambda payload, **kwargs: None,  # noqa: ARG005
        write_html_snapshot_fn=lambda *args, **kwargs: None,
        write_image_snapshot_fn=lambda *args, **kwargs: None,
        write_json_artifact_snapshot_fn=lambda *args, **kwargs: None,
        scenario_return_fn=lambda _html, **kwargs: str(kwargs.get("reason", "")),
        logger=sr.log,
    )

    assert result["should_return"] is False
    assert browser.goto_calls
    assert browser.goto_calls[-1] == expected_route


def test_attempt_gate_blocks_on_skyscanner_captcha_url_without_tokens():
    class _Page:
        url = "https://www.skyscanner.com/sttc/px/captcha-v2/index.html?url=L2ZsaWdodHM/"

        def is_closed(self):
            return False

    class _FakeBrowser:
        allow_human_intervention = False

        def __init__(self):
            self.page = _Page()

        def content(self):
            return "<html><body><main>temporary challenge shell</main></body></html>"

    result = run_attempt_precheck_and_interstitial_gate(
        browser=_FakeBrowser(),
        site_key="skyscanner",
        url="https://www.skyscanner.com/flights",
        origin="NRT",
        dest="HAN",
        depart="2026-03-20",
        return_date=None,
        trip_type="one_way",
        is_domestic=False,
        max_transit=None,
        attempt=0,
        max_retries=2,
        max_turns=2,
        human_mimic=True,
        plan=[],
        last_error=None,
        scenario_run_id="test-run",
        wall_clock_cap_exhausted_fn=lambda: False,
        budget_almost_exhausted_fn=lambda: False,
        budget_remaining_sec_fn=lambda: 60.0,
        get_threshold_fn=lambda *args, **kwargs: 8000,  # noqa: ARG005
        detect_site_interstitial_block_fn=lambda html, site: {},  # noqa: ARG005
        attempt_skyscanner_interstitial_grace_fn=lambda *args, **kwargs: {  # noqa: ARG005
            "used": False,
            "cleared": False,
            "html": "",
            "reason": "not_used",
        },
        attempt_skyscanner_interstitial_fallback_reload_fn=lambda *args, **kwargs: {
            "used": False,
            "attempted": False,
            "cleared": False,
            "reason": "not_used",
        },
        write_progress_snapshot_fn=lambda **kwargs: None,
        write_debug_snapshot_fn=lambda payload, **kwargs: None,  # noqa: ARG005
        write_html_snapshot_fn=lambda *args, **kwargs: None,
        write_image_snapshot_fn=lambda *args, **kwargs: None,
        write_json_artifact_snapshot_fn=lambda *args, **kwargs: None,
        scenario_return_fn=lambda *args, **kwargs: "blocked",
        logger=sr.log,
    )

    assert result["should_return"] is True
    assert result["result_html"] == "blocked"


def test_safe_min_timeout_prevents_sub_minimum_values():
    """safe_min_timeout_ms must prevent any result below 300ms."""
    from core.browser import safe_min_timeout_ms

    # Test with various timeout values and caps
    # Logic: result = max(800, min(timeout_ms, cap_ms))
    test_cases = [
        (25, 600, 800),   # min(25, 600)=25 < 800, clamped to 800
        (100, 500, 800),  # min(100, 500)=100 < 800, clamped to 800
        (200, 400, 800),  # min(200, 400)=200 < 800, clamped to 800
        (500, 600, 800),  # min(500, 600)=500 < 800, clamped to 800
        (800, 900, 800),  # min(800, 900)=800 >= 800, returned as is
        (1200, 600, 800), # min(1200, 600)=600 < 800, clamped to 800
        (0, 100, 800),    # min(0, 100)=0 < 800, clamped to 800
        (800, 1000, 800), # min(800, 1000)=800 >= 800, returned as is
        (1500, 1200, 1200), # min(1500, 1200)=1200 >= 800, returned as is
    ]

    for timeout_ms, cap_ms, expected in test_cases:
        result = safe_min_timeout_ms(timeout_ms, cap_ms)
        assert result == expected, (
            f"safe_min_timeout_ms({timeout_ms}, {cap_ms}) = {result}, "
            f"expected {expected}"
        )


def test_google_flights_fill_commit_timeout_never_tiny():
    """Google Flights fill+commit with mocked thresholds must clamp to safe minimum."""
    import logging

    class _CaptureTimeoutBrowser:
        def __init__(self):
            self.click_timeouts = []
            self.fill_timeouts = []
            self.wait_timeouts = []

        def click(self, selector, timeout_ms=None):  # noqa: ARG002
            self.click_timeouts.append(timeout_ms)
            raise RuntimeError("click failed")

        def fill(self, selector, value, timeout_ms=None):  # noqa: ARG002
            self.fill_timeouts.append(timeout_ms)
            raise RuntimeError("fill failed")

        def wait(self, selector, timeout_ms=None):  # noqa: ARG002
            self.wait_timeouts.append(timeout_ms)
            raise RuntimeError("wait failed")

        def activate_field_by_keywords(self, *args, **kwargs):  # noqa: ARG002
            return False

        def fill_by_keywords(self, *args, **kwargs):  # noqa: ARG002
            return False

    # Create a simple mock logger
    logger = logging.getLogger("test")

    browser = _CaptureTimeoutBrowser()
    result = sr._google_fill_and_commit_location(
        browser,
        role="origin",
        value="HND",
        selectors=["[aria-label*='From']"],
        locale_hint="",
        timeout_ms=1000,  # Reasonable value
    )

    # Verify no timeout was < 300ms (all should be >= 800)
    all_timeouts = browser.click_timeouts + browser.fill_timeouts + browser.wait_timeouts
    for timeout in all_timeouts:
        if timeout is not None:
            assert timeout >= 300, f"Timeout {timeout}ms is below 300ms safety threshold"


def test_safe_min_timeout_preserves_reasonable_values():
    """safe_min_timeout_ms should pass through reasonable timeouts unchanged."""
    from core.browser import safe_min_timeout_ms

    # Reasonable timeouts should pass through
    assert safe_min_timeout_ms(2000, 3000) == 2000
    assert safe_min_timeout_ms(3000, 2000) == 2000
    assert safe_min_timeout_ms(1500, 1200) == 1200
    assert safe_min_timeout_ms(800, 900) == 800
    assert safe_min_timeout_ms(1000, 1000) == 1000


def test_execute_plan_search_click_block_fail_fast(monkeypatch):  # noqa: ARG001
    """Search click should fail fast when route verification blocks, without refill.

    When _assess_google_flights_fill_mismatch returns block=True, the plan execution
    should not attempt to refill the route or commit the search. The trace should
    indicate the block occurred.
    """

    class _BrowserStub:
        def locator(self, selector):  # noqa: ARG002
            class _L:  # noqa: N801
                def fill(self, value, timeout_ms=None):  # noqa: ARG002
                    return None

                def click(self, timeout_ms=None):  # noqa: ARG002
                    return None

                def wait(self, timeout_ms=None):  # noqa: ARG002
                    return None

            return _L()

        def content(self):
            return "<html><body></body></html>"

        def fill_google_flights_combobox(self, *args, **kwargs):  # noqa: ARG002
            """Stub: combobox fill is mocked out via monkeypatch."""
            return {"ok": False, "reason": "stub_combobox"}

    called = {"refill": 0, "search": 0}

    def _fake_refill(*args, **kwargs):  # noqa: ARG001
        called["refill"] += 1
        raise AssertionError("refill should not be called on block")

    def _fake_search_commit(*args, **kwargs):  # noqa: ARG001
        called["search"] += 1
        raise AssertionError("search commit should not run on block")

    monkeypatch.setattr(sr, "_google_refill_dest_on_mismatch_impl", _fake_refill)
    monkeypatch.setattr(sr, "_google_search_and_commit", _fake_search_commit)
    monkeypatch.setattr(sr, "_extract_google_flights_form_state", lambda *args, **kwargs: {})
    monkeypatch.setattr(sr, "_selectors_look_search_submit", lambda selectors: True)
    monkeypatch.setattr(
        sr,
        "_assess_google_flights_fill_mismatch",
        lambda **kwargs: {
            "block": True,
            "mismatches": ["dest"],
            "observed": {},
            "confidence": "low",
            "reason": "stub_block",
        },
    )

    plan = [
        {"action": "fill", "selector": "input[name='origin']", "value": "HND"},
        {"action": "fill", "selector": "input[name='dest']", "value": "ITM"},
        {"action": "fill", "selector": "input[name='depart']", "value": "2026-03-01"},
        {"action": "click", "selector": "button[type='submit']"},
    ]

    trace = execute_plan(_BrowserStub(), plan, site_key="google_flights")

    # Verify that no refill or search commit was attempted when route block=True
    assert called["refill"] == 0, "refill should not be called when route verification blocks"
    assert called["search"] == 0, "search should not be called when route verification blocks"

    # Verify that the trace contains items (indicating plan was executed partially)
    assert isinstance(trace, list), f"Expected trace to be list, got {type(trace)}"
    assert len(trace) > 0, "Expected trace to have entries indicating the block point"


def test_execute_plan_google_search_click_passes_defined_page_url(monkeypatch):
    class _BrowserStub:
        class _Page:
            url = "https://www.google.com/travel/flights?hl=en&gl=JP#flt=FUK.HND.2026-05-02*HND.FUK.2026-06-08;c:JPY"

        def __init__(self):
            self.page = self._Page()

        def fill(self, selector, value, timeout_ms=None):  # noqa: ARG002
            return None

        def click(self, selector, timeout_ms=None):  # noqa: ARG002
            return None

        def wait(self, selector, timeout_ms=None):  # noqa: ARG002
            return None

        def content(self):
            return "<html><body><main role='main'>Flights</main></body></html>"

    captured = {}

    def _fake_search_commit(*args, **kwargs):  # noqa: ARG001
        captured["page_url"] = kwargs.get("page_url")
        return {
            "ok": True,
            "strategy": "click_then_verify",
            "selector_used": "button[aria-label*='Search']",
            "results_signal_found": True,
            "post_click_wait_ms": 200,
            "error": "",
        }

    monkeypatch.setattr(sr, "_google_search_and_commit", _fake_search_commit)
    monkeypatch.setattr(
        sr,
        "_google_fill_and_commit_location",
        lambda *args, **kwargs: {  # noqa: ARG005
            "ok": True,
            "selector_used": "[role='combobox'][aria-label*='Where from']",
            "reason": "",
            "committed": True,
            "evidence": {},
        },
    )
    monkeypatch.setattr(sr, "_extract_google_flights_form_state", lambda *args, **kwargs: {})
    monkeypatch.setattr(sr, "_selectors_look_search_submit", lambda selectors: True)
    monkeypatch.setattr(
        sr,
        "_gf_set_date_impl",
        lambda *args, **kwargs: {  # noqa: ARG005
            "ok": True,
            "reason": "",
            "selector_used": "input[aria-label*='Departure']",
            "evidence": {
                "verify.close_method": "done_button",
                "calendar.close_scope": "calendar_root",
            },
        },
    )
    monkeypatch.setattr(
        sr,
        "_assess_google_flights_fill_mismatch",
        lambda **kwargs: {
            "block": False,
            "mismatches": [],
            "observed": {},
            "confidence": "low",
            "reason": "",
            "dest_committed": False,
            "dest_commit_reason": "",
            "suggestion_used": False,
        },
    )

    plan = [
        {"action": "fill", "selector": "input[name='origin']", "value": "FUK"},
        {"action": "fill", "selector": "input[name='dest']", "value": "HND"},
        {"action": "fill", "selector": "input[name='depart']", "value": "2026-05-02"},
        {"action": "click", "selector": "button[type='submit']"},
    ]

    expected_url = "https://www.google.com/travel/flights?hl=en&gl=JP#flt=FUK.HND.2026-05-02*HND.FUK.2026-06-08;c:JPY"
    trace = execute_plan(
        _BrowserStub(),
        plan,
        site_key="google_flights",
        evidence_ctx={"url": expected_url},
    )

    assert captured["page_url"] == expected_url
    assert any(isinstance(item, dict) and item.get("action") == "click" for item in trace)


def test_google_fill_mismatch_allows_low_conf_dest_placeholder_when_results_itinerary_matches():
    out = sr._assess_google_flights_fill_mismatch(  # noqa: SLF001
        form_state={
            "origin_text": "東京都",
            "dest_text": "",
            "dest_text_raw": "目的地を探索",
            "dest_is_placeholder": True,
            "depart_text": "3月1日(日)",
            "return_text": "3月8日(日)",
            "confidence": "low",
        },
        html=(
            '<div data-travelimpactmodelwebsiteurl="https://www.travelimpactmodel.org/lookup/flight?'
            'itinerary=HND-ITM-JL-139-20260301"></div>'
        ),
        expected_origin="HND",
        expected_dest="ITM",
        expected_depart="2026-03-01",
        expected_return="2026-03-08",
    )
    assert out["block"] is False
    assert out["reason"] == "match_results_itinerary_low_confidence"
    assert out["results_itinerary_match"] is True


def test_extract_google_form_state_ignores_passenger_to_text_for_dest():
    state = gf._extract_google_form_state_from_candidates(  # noqa: SLF001
        [
            {
                "label": "Remove child aged 2 to 11",
                "value": "",
                "text": "Remove child aged 2 to 11",
            },
            {
                "label": "Where to? Osaka ITM",
                "value": "",
                "text": "Where to? Osaka ITM",
            },
            {
                "label": "Where from? Tokyo HND",
                "value": "",
                "text": "Where from? Tokyo HND",
            },
            {
                "label": "Departure",
                "value": "Sun, Mar 1",
                "text": "Departure",
            },
        ]
    )
    assert state["dest_text_raw"] == "Where to? Osaka ITM"
    assert state["dest_text"] == "Where to? Osaka ITM"
    assert state["reason"] in {"fields_bound", "partial_fields_found"}


def test_extract_google_form_state_prefers_input_value_over_multi_airport_helper_text():
    state = gf._extract_google_form_state_from_candidates(  # noqa: SLF001
        [
            {
                "label": "From",
                "value": "",
                "text": "Select multiple airports Done Press the plus key to switch to multi-select mode.",
                "tag": "div",
                "role": "button",
                "input_like": False,
            },
            {
                "label": "Where from?",
                "value": "FUK",
                "text": "FUK",
                "tag": "input",
                "role": "combobox",
                "input_like": True,
            },
            {
                "label": "Where to? Tokyo HND",
                "value": "",
                "text": "Where to? Tokyo HND",
                "tag": "input",
                "role": "combobox",
                "input_like": True,
            },
            {
                "label": "Departure",
                "value": "Sat, May 2",
                "text": "Departure",
                "tag": "input",
                "role": "",
                "input_like": True,
            },
        ],
        current_url="https://www.google.com/travel/flights?hl=en&gl=JP#flt=FUK.HND.2026-05-02*HND.FUK.2026-06-08;c:JPY",
    )
    assert state["origin_text_raw"] == "FUK"
    assert "Select multiple airports" not in state["origin_text_raw"]
    assert state["origin_confidence"] in {"medium", "high"}


def test_extract_google_form_state_does_not_use_departure_date_as_origin():
    state = gf._extract_google_form_state_from_candidates(  # noqa: SLF001
        [
            {
                "label": "Departure",
                "value": "Sat, May 2",
                "text": "Departure",
                "tag": "input",
                "role": "",
                "input_like": True,
            },
            {
                "label": "Departure airport",
                "value": "FUK",
                "text": "FUK",
                "tag": "input",
                "role": "combobox",
                "input_like": True,
            },
            {
                "label": "Where to? Tokyo HND",
                "value": "",
                "text": "Where to? Tokyo HND",
                "tag": "input",
                "role": "combobox",
                "input_like": True,
            },
        ],
        current_url="https://www.google.com/travel/flights?hl=en&gl=JP#flt=FUK.HND.2026-05-02*HND.FUK.2026-06-08;c:JPY",
    )
    assert state["origin_text_raw"] == "FUK"
    assert state["origin_text_raw"] != "Sat, May 2"
    assert state["depart_text_raw"] == "Sat, May 2"


def test_google_form_value_matches_date_accepts_english_weekday_month_day_without_year():
    year = sr._google_default_date_reference_year()  # noqa: SLF001
    assert sr._google_form_value_matches_date("Sun, Mar 1", f"{year}-03-01") is True  # noqa: SLF001
    assert sr._google_form_value_matches_date("Sun, Mar 8", f"{year}-03-08") is True  # noqa: SLF001


def test_with_knowledge_preserves_google_fill_roles_under_cross_role_selector_knowledge():
    base = sr._default_google_flights_plan("FUK", "HND", "2026-05-02")  # noqa: SLF001
    # Simulate contaminated learned selector counts where dest knowledge includes origin-like selector.
    knowledge = {
        "local_fill_origin_selectors": ["input[aria-label*='Where from']"],
        "local_fill_dest_selectors": [
            "[role='button'][aria-label*='出発']",
            "input[aria-label*='Where to']",
        ],
        "global_fill_dest_selectors": [],
        "local_failed_selectors": [],
        "global_failed_selectors": [],
        "local_search_click_selectors": [],
        "local_wait_selectors": [],
        "global_wait_selectors": [],
    }
    enriched = sr._with_knowledge(base, "google_flights", True, knowledge, vlm_hint=None)  # noqa: SLF001
    fill_roles = [
        sr._infer_fill_role(step)  # noqa: SLF001
        for step in enriched
        if isinstance(step, dict) and step.get("action") == "fill"
    ]
    assert fill_roles[:3] == ["origin", "dest", "depart"]
    assert sr._is_actionable_plan(enriched, "round_trip", site_key="google_flights") is True  # noqa: SLF001


def test_google_fill_mismatch_keeps_blocking_wrong_return_even_with_results_itinerary():
    out = sr._assess_google_flights_fill_mismatch(  # noqa: SLF001
        form_state={
            "origin_text": "東京都",
            "dest_text": "",
            "dest_text_raw": "目的地を探索",
            "dest_is_placeholder": True,
            "depart_text": "3月1日(日)",
            "return_text": "4月15日(水)",
            "confidence": "low",
        },
        html=(
            '<div data-travelimpactmodelwebsiteurl="https://www.travelimpactmodel.org/lookup/flight?'
            'itinerary=HND-ITM-JL-139-20260301"></div>'
        ),
        expected_origin="HND",
        expected_dest="ITM",
        expected_depart="2026-03-01",
        expected_return="2026-03-08",
    )
    assert out["block"] is True
    assert out["reason"] == "mismatch_low_confidence_results_route"
    assert out["mismatches"] == ["return"]
    assert out["results_itinerary_match"] is True


def test_execute_plan_click_wall_clock_cap(monkeypatch):
    """Per-action click wall-clock cap should stop long-running steps."""

    class _ClickBrowser:
        def click(self, selector, timeout_ms=None):  # noqa: ARG002
            return None

        def fill(self, selector, value, timeout_ms=None):  # noqa: ARG002
            return None

        def wait(self, selector, timeout_ms=None):  # noqa: ARG002
            return None

    original_get_threshold = sr.get_threshold

    def _fake_get_threshold(key, default=None):
        if key in {
            "scenario_step_wall_clock_cap_ms_click",
            "scenario_step_wall_clock_cap_ms_default",
            "scenario_step_wall_clock_cap_ms",
        }:
            return 50
        return original_get_threshold(key, default)

    monotonic_ticks = iter([0.0, 0.0, 0.0, 0.1])
    monkeypatch.setattr(sr, "get_threshold", _fake_get_threshold)
    monkeypatch.setattr(sr.time, "monotonic", lambda: next(monotonic_ticks, 0.1))
    import core.scenario_runner.env as _sr_env
    monkeypatch.setattr(_sr_env, "get_threshold", _fake_get_threshold)
    import core.scenario_runner.timeouts as _sr_timeouts
    monkeypatch.setattr(_sr_timeouts.time, "monotonic", lambda: next(monotonic_ticks, 0.1))

    plan = [{"action": "click", "selector": "#search"}]
    with pytest.raises(RuntimeError):
        execute_plan(_ClickBrowser(), plan, site_key="google_flights")
