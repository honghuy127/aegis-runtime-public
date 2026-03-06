from core import scenario_runner as sr


def test_google_route_core_before_date_gate_blocks_irrelevant_uncommitted_dest():
    html = """
    <html><body>
      <input aria-label="出発地" value="東京都">
      <input aria-label="目的地" value="目的地を探索">
      <input aria-label="出発日" value="3月1日(日)">
      <input aria-label="復路" value="3月8日(日)">
      <a href="/travel/explore">Explore</a>
    </body></html>
    """
    out = sr._google_route_core_before_date_gate(  # noqa: SLF001
        html=html,
        expected_origin="HND",
        expected_dest="ITM",
        expected_depart="2026-03-01",
        expected_return="2026-03-08",
    )
    assert out["ok"] is False
    assert out["reason"] in {
        "route_core_dest_uncommitted",
        "route_core_dest_mismatch",
        "scope_non_flight_irrelevant_page",
    }
    assert "verify.route_core_observed_dest" in out["evidence"]


def test_google_route_core_before_date_gate_accepts_bound_route_core():
    html = """
    <html><body>
      <input aria-label="出発地" value="東京 (HND)">
      <input aria-label="目的地" value="大阪 (ITM)">
      <input aria-label="出発日" value="2026-03-01">
      <input aria-label="復路" value="2026-03-08">
    </body></html>
    """
    out = sr._google_route_core_before_date_gate(  # noqa: SLF001
        html=html,
        expected_origin="HND",
        expected_dest="ITM",
        expected_depart="2026-03-01",
        expected_return="2026-03-08",
    )
    assert out["ok"] is True
    assert out["reason"] == "route_core_verified"


def test_google_route_core_before_date_gate_accepts_results_itinerary_when_chip_placeholder():
    html = """
    <html><body>
      <input aria-label="出発地" value="東京都">
      <input aria-label="目的地" value="目的地を探索">
      <input aria-label="出発日" value="3月1日(日)">
      <input aria-label="復路" value="4月15日(水)">
      <div data-travelimpactmodelwebsiteurl="https://www.travelimpactmodel.org/lookup/flight?itinerary=HND-ITM-JL-139-20260301"></div>
    </body></html>
    """
    out = sr._google_route_core_before_date_gate(  # noqa: SLF001
        html=html,
        expected_origin="HND",
        expected_dest="ITM",
        expected_depart="2026-03-01",
        expected_return="2026-03-08",
    )
    assert out["ok"] is True
    assert out["reason"] == "route_core_verified_results_itinerary"
    assert out["evidence"]["verify.route_core_results_itinerary_match"] is True


def test_google_route_core_before_date_gate_accepts_live_dom_when_html_probe_is_stale(monkeypatch):
    html = """
    <html><body>
      <input aria-label="Where from?" value="Tokyo">
      <input role="combobox" aria-label="Where to? " value="">
      <input aria-label="Departure" value="Sun, Mar 1">
      <input aria-label="Return" value="Sun, Mar 8">
    </body></html>
    """

    monkeypatch.setattr(
        sr,
        "_extract_google_flights_form_state",
        lambda _page: {
            "origin_text": "Tokyo",
            "dest_text": "Osaka",
            "origin_text_raw": "Tokyo",
            "dest_text_raw": "Where to? Osaka ITM",
            "confidence": "high",
            "reason": "dom_probe_ok",
        },
    )

    out = sr._google_route_core_before_date_gate(  # noqa: SLF001
        html=html,
        page=object(),
        expected_origin="HND",
        expected_dest="ITM",
        expected_depart="2026-03-01",
        expected_return="2026-03-08",
    )
    assert out["ok"] is True
    assert out["reason"] == "route_core_verified_live_dom_form"
    assert out["evidence"]["verify.route_core_live_probe_used"] is True
    assert out["evidence"]["verify.route_core_live_dest_ok"] is True


class _PlanBrowserStub:
    def __init__(self):
        self.page = self

    def content(self):
        return "<html><body><main>travel/explore</main></body></html>"

    def fill(self, selector, value, timeout_ms=None):  # noqa: ARG002
        return None

    def click(self, selector, timeout_ms=None):  # noqa: ARG002
        return None

    def wait(self, selector, timeout_ms=None):  # noqa: ARG002
        return None

    def wait_for_timeout(self, ms):  # noqa: ARG002
        return None


def test_execute_plan_google_recovery_blocks_date_fill_until_route_core_bound(monkeypatch):
    browser = _PlanBrowserStub()
    plan = [
        {"action": "fill", "selector": ["input[aria-label*='From']"], "value": "HND"},
        {"action": "fill", "selector": ["input[aria-label*='To']"], "value": "ITM"},
        {"action": "fill", "selector": ["input[aria-label*='Departure']"], "value": "2026-03-01"},
        {"action": "fill", "selector": ["input[aria-label*='Return']"], "value": "2026-03-08", "optional": True},
    ]

    def _gate(**kwargs):  # noqa: ARG001
        return {
            "ok": False,
            "reason": "route_core_dest_uncommitted",
            "evidence": {"verify.route_core_observed_dest": "目的地を探索"},
        }

    def _unexpected_gf_set_date(*args, **kwargs):  # noqa: ARG001
        raise AssertionError("gf_set_date should not run when route-core gate blocks depart")

    monkeypatch.setattr(sr, "_google_route_core_before_date_gate", _gate)
    monkeypatch.setattr(sr, "_gf_set_date_impl", _unexpected_gf_set_date)

    trace = sr.execute_plan(
        browser,
        plan,
        site_key="google_flights",
        evidence_ctx={"google_recovery_route_core_gate_enabled": True},
    )

    assert any(
        isinstance(item, dict)
        and item.get("role") == "depart"
        and item.get("status") == "route_core_before_date_fill_unverified"
        for item in trace
    )
    assert any(
        isinstance(item, dict)
        and item.get("role") == "return"
        and item.get("status") == "soft_skip"
        and item.get("error") == "skip_return_after_depart_fail"
        for item in trace
    )
