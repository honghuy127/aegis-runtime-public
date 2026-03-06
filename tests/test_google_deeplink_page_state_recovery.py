from pathlib import Path

from core import scenario_runner as sr


class _BrowserStub:
    def __init__(self, html: str):
        self._html = html

    def content(self):
        return self._html


def _deeplink_url():
    return sr.build_google_flights_deeplink(  # type: ignore[attr-defined]
        {
            "origin": "HND",
            "dest": "ITM",
            "depart": "2026-03-01",
            "return_date": "2026-03-08",
            "trip_type": "round_trip",
        },
        {
            "mimic_locale": "ja-JP",
            "mimic_region": "JP",
            "mimic_currency": "JPY",
        },
    )


def test_phase3_fixture_probe_triggers_irrelevant_page_recovery_and_continues_once():
    fixture = Path("tests/fixtures/google_flights/non_flight_scope_irrelevant_page_sample.html")
    html = fixture.read_text(encoding="utf-8")
    url = _deeplink_url()

    ready, reason = sr._google_deeplink_probe_status(html, url)  # noqa: SLF001
    assert ready is False
    assert reason == "non_flight_scope_irrelevant_page"

    browser = _BrowserStub(html)
    calls = {"recovery": 0, "rebind": 0}

    def _recovery_hook(_browser, **kwargs):  # noqa: ARG001
        calls["recovery"] += 1
        return {"ok": True, "reason": "activated_route_form", "html": html}

    def _rebind_hook(_browser, **kwargs):  # noqa: ARG001
        calls["rebind"] += 1
        return False, "rebind_unready_non_flight_scope_irrelevant_page", html

    out = sr._attempt_google_deeplink_page_state_recovery(  # noqa: SLF001
        browser,
        trigger_reason=reason,
        url=url,
        origin="HND",
        dest="ITM",
        depart="2026-03-01",
        return_date="2026-03-08",
        trip_type="round_trip",
        enabled=True,
        uses=0,
        max_extra_actions=1,
        recovery_hook=_recovery_hook,
        rebind_hook=_rebind_hook,
    )

    assert out["used"] is True
    assert out["fail_fast"] is False
    assert out["ready"] is False
    assert out["reason"] == "deeplink_page_state_recovery_unready_non_flight_scope_irrelevant_page"
    assert out["rebind_reason"] == "rebind_unready_non_flight_scope_irrelevant_page"
    assert calls == {"recovery": 1, "rebind": 1}


def test_phase3_recovery_hard_cap_prevents_second_attempt():
    browser = _BrowserStub("<html></html>")
    out = sr._attempt_google_deeplink_page_state_recovery(  # noqa: SLF001
        browser,
        trigger_reason="non_flight_scope_irrelevant_page",
        url=_deeplink_url(),
        origin="HND",
        dest="ITM",
        depart="2026-03-01",
        return_date="2026-03-08",
        trip_type="round_trip",
        enabled=True,
        uses=1,
        max_extra_actions=1,
    )
    assert out["used"] is False
    assert out["uses"] == 1


def test_phase3_recovery_disabled_or_noneligible_is_noop():
    browser = _BrowserStub("<html></html>")
    assert sr._should_attempt_google_deeplink_page_state_recovery(  # noqa: SLF001
        trigger_reason="non_flight_scope_irrelevant_page",
        enabled=False,
        uses=0,
        max_extra_actions=1,
    ) is False
    assert sr._should_attempt_google_deeplink_page_state_recovery(  # noqa: SLF001
        trigger_reason="missing_contextual_price_card",
        enabled=True,
        uses=0,
        max_extra_actions=1,
    ) is False
    out = sr._attempt_google_deeplink_page_state_recovery(  # noqa: SLF001
        browser,
        trigger_reason="missing_contextual_price_card",
        url=_deeplink_url(),
        origin="HND",
        dest="ITM",
        depart="2026-03-01",
        return_date="2026-03-08",
        trip_type="round_trip",
        enabled=True,
        uses=0,
        max_extra_actions=1,
    )
    assert out["used"] is False


def test_google_deeplink_quick_rebind_limits_search_clicks_and_prefers_visible(monkeypatch):
    class _Page:
        def __init__(self):
            self.visible = {"#search-visible"}

        def wait_for_timeout(self, _ms):
            return None

        def is_visible(self, selector, timeout=0):  # noqa: ARG002
            return selector in self.visible

    class _QuickRebindBrowser:
        def __init__(self):
            self.page = _Page()
            self.clicked = []

        def fill_by_keywords(self, _keywords, _value, timeout_ms=None):  # noqa: ARG002
            return True

        def content(self):
            return "<html>stub</html>"

        def click(self, selector, timeout_ms=None):  # noqa: ARG002
            self.clicked.append(selector)
            if selector != "#search-visible":
                raise RuntimeError("unexpected selector")

    browser = _QuickRebindBrowser()
    selectors = [
        "#search-hidden-1",
        "#search-hidden-2",
        "#search-hidden-3",
        "#search-hidden-4",
        "#search-visible",
        "#search-hidden-5",
    ]
    probe_calls = {"count": 0}

    def _mock_probe(_html, _url):  # noqa: ARG001
        probe_calls["count"] += 1
        if probe_calls["count"] == 1:
            return False, "missing_contextual_price_card"
        return True, "ok"

    def _mock_threshold(key, default=None):
        if key == "google_flights_quick_rebind_search_click_max_selectors":
            return 2
        if key == "google_flights_quick_rebind_search_visibility_probe_ms":
            return 0
        if key == "google_flights_quick_rebind_settle_timeout_ms":
            return 10
        if key == "google_flights_quick_rebind_step_pause_ms":
            return 0
        return default

    monkeypatch.setattr(
        sr,
        "_service_search_click_fallbacks",
        lambda _site, **_kwargs: selectors,
    )
    monkeypatch.setattr(sr, "promote_selector_hint", lambda **_kwargs: True)
    monkeypatch.setattr(sr, "_google_deeplink_probe_status", _mock_probe)
    monkeypatch.setattr(sr, "get_threshold", _mock_threshold)

    ok, reason, _html = sr._google_deeplink_quick_rebind(  # noqa: SLF001
        browser,
        url=_deeplink_url(),
        origin="HND",
        dest="ITM",
        depart="2026-03-01",
        return_date="2026-03-08",
        trip_type="round_trip",
    )

    assert ok is True
    assert reason == "rebind_ready"
    assert browser.clicked == ["#search-visible"]
    assert probe_calls["count"] >= 2


def test_google_deeplink_quick_rebind_promotes_verified_search_selector(monkeypatch):
    class _Page:
        def wait_for_timeout(self, _ms):
            return None

        def is_visible(self, selector, timeout=0):  # noqa: ARG002
            return selector == "button[aria-label*='Search']"

    class _Browser:
        def __init__(self):
            self.page = _Page()
            self.clicked = []

        def fill_by_keywords(self, _keywords, _value, timeout_ms=None):  # noqa: ARG002
            return True

        def content(self):
            return "<html>stub</html>"

        def click(self, selector, timeout_ms=None):  # noqa: ARG002
            self.clicked.append(selector)
            return None

    browser = _Browser()
    probe_calls = {"count": 0}
    promoted = []

    def _mock_probe(_html, _url):  # noqa: ARG001
        probe_calls["count"] += 1
        return (probe_calls["count"] >= 2), ("ok" if probe_calls["count"] >= 2 else "missing_contextual_price_card")

    monkeypatch.setattr(sr, "_service_search_click_fallbacks", lambda _site, **_kwargs: ["button[aria-label*='Search']"])
    monkeypatch.setattr(sr, "_google_deeplink_probe_status", _mock_probe)
    monkeypatch.setattr(
        sr,
        "promote_selector_hint",
        lambda **kwargs: promoted.append(kwargs) or True,
    )
    monkeypatch.setattr(
        sr,
        "get_threshold",
        lambda key, default=None: (
            1 if key == "google_flights_quick_rebind_search_click_max_selectors"
            else 0 if key in {"google_flights_quick_rebind_search_visibility_probe_ms", "google_flights_quick_rebind_step_pause_ms"}
            else 10 if key == "google_flights_quick_rebind_settle_timeout_ms"
            else default
        ),
    )

    ok, reason, _ = sr._google_deeplink_quick_rebind(  # noqa: SLF001
        browser,
        url=_deeplink_url(),
        origin="HND",
        dest="ITM",
        depart="2026-03-01",
        return_date="2026-03-08",
        trip_type="round_trip",
    )

    assert ok is True
    assert reason == "rebind_ready"
    assert browser.clicked == ["button[aria-label*='Search']"]
    assert promoted
    assert promoted[0]["action"] == "quick_rebind_search"
    assert promoted[0]["selector"] == "button[aria-label*='Search']"
