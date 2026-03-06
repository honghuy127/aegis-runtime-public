from core.scenario_runner.skyscanner.ui_actions import (
    _click_first_selector,
    _click_next_month,
    _skyscanner_dismiss_results_overlay,
    _skyscanner_date_openers,
    _skyscanner_fill_date_via_picker,
    _skyscanner_fill_and_commit_location,
    _skyscanner_search_click_selectors,
)
from tests.utils.dates import future_date, iso


class _DummyLocator:
    def __init__(self, visible: bool):
        self._visible = visible
        self.first = self

    def wait_for(self, state=None, timeout=None):  # noqa: ARG002
        if not self._visible:
            raise RuntimeError("not visible")
        return None

    def click(self, timeout=None):  # noqa: ARG002
        if not self._visible:
            raise RuntimeError("not clickable")
        return None


class _DummyKeyboard:
    def __init__(self):
        self.pressed = []

    def press(self, key):
        self.pressed.append(str(key))


class _DummyPage:
    def __init__(self, listbox_visible: bool, eval_result: dict):
        self._listbox_visible = listbox_visible
        self._eval_result = dict(eval_result)
        self.keyboard = _DummyKeyboard()
        self.current_value = ""

    def locator(self, selector):
        if "listbox" in str(selector):
            return _DummyLocator(self._listbox_visible)
        return _DummyLocator(False)

    def evaluate(self, _script, payload=None):  # noqa: ANN001
        if isinstance(payload, str):
            return self.current_value
        if isinstance(payload, dict) and "selector" in payload and "value" in payload:
            self.current_value = str(payload.get("value") or "")
            return True
        out = dict(self._eval_result)
        data = payload if isinstance(payload, dict) else {}
        out.setdefault("debug_value", data.get("value"))
        return out


class _DummyBrowser:
    def __init__(self, page):
        self.page = page
        self.fill_calls = []

    def fill(self, selector, value, timeout_ms=None):  # noqa: ARG002
        self.fill_calls.append((str(selector), str(value)))
        if hasattr(self.page, "current_value"):
            self.page.current_value = str(value)
        if "originInput" in str(selector) or "destinationInput" in str(selector):
            return None
        raise RuntimeError("selector_not_found")


def test_skyscanner_date_openers_prioritize_buttons_before_input_hints():
    openers = _skyscanner_date_openers(
        "depart",
        [
            "input[aria-label*='Departure']",
            "button:has-text('出発')",
        ],
    )
    assert openers[0] == "button[data-testid='depart-btn']"
    assert "input[aria-label*='Departure']" in openers
    assert openers.index("input[aria-label*='Departure']") > openers.index("button:has-text('出発')")


def test_skyscanner_fill_and_commit_location_succeeds_with_listbox_pick():
    page = _DummyPage(
        listbox_visible=True,
        eval_result={
            "ok": True,
            "reason": "suggestion_clicked",
            "option_index": 0,
            "option_score": 100,
            "option_aria": "東京 成田 (NRT) 日本",
            "option_id": "NRT",
        },
    )
    browser = _DummyBrowser(page)
    result = _skyscanner_fill_and_commit_location(
        browser=browser,
        role="origin",
        value="NRT",
        selectors=["input[name='originInput-search']"],
        timeout_ms=1500,
    )
    assert result.get("ok") is True
    assert result.get("reason") == "combobox_fill_success"
    assert result.get("selector_used") == "input[name='originInput-search']"


def test_skyscanner_fill_and_commit_location_fails_when_suggestion_absent():
    page = _DummyPage(
        listbox_visible=False,
        eval_result={"ok": False, "reason": "suggestion_not_found"},
    )
    browser = _DummyBrowser(page)
    result = _skyscanner_fill_and_commit_location(
        browser=browser,
        role="dest",
        value="HND",
        selectors=["input[name='destinationInput-search']"],
        timeout_ms=1500,
    )
    assert result.get("ok") is False
    assert result.get("reason") == "suggestion_not_found"
    assert "Enter" in page.keyboard.pressed


def test_skyscanner_fill_and_commit_location_rejects_iata_mismatch_pick():
    page = _DummyPage(
        listbox_visible=True,
        eval_result={
            "ok": True,
            "reason": "suggestion_clicked",
            "option_index": 0,
            "option_score": 3,
            "option_aria": "イギリス (UK) イギリス",
            "option_id": "UK",
        },
    )
    browser = _DummyBrowser(page)
    result = _skyscanner_fill_and_commit_location(
        browser=browser,
        role="origin",
        value="FUK",
        selectors=["input[name='originInput-search']"],
        timeout_ms=1500,
    )
    assert result.get("ok") is False
    assert result.get("reason") == "suggestion_mismatch_expected_iata"


def test_skyscanner_fill_and_commit_location_force_sets_iata_when_typed_drift_detected():
    class _Page:
        def __init__(self):
            self.keyboard = _DummyKeyboard()
            self.current_value = "ND"
            self.pick_calls = 0

        def locator(self, selector):
            if "listbox" in str(selector):
                return _DummyLocator(True)
            return _DummyLocator(False)

        def evaluate(self, script, payload=None):  # noqa: ANN001
            script_text = str(script or "")
            if isinstance(payload, str):
                return self.current_value
            if isinstance(payload, dict) and "selector" in payload and "value" in payload and "dispatchEvent" in script_text:
                self.current_value = str(payload.get("value") or "")
                return True
            if isinstance(payload, dict) and "role" in payload and "value" in payload:
                self.pick_calls += 1
                return {
                    "ok": True,
                    "reason": "suggestion_clicked",
                    "option_index": 0,
                    "option_score": 760,
                    "option_aria": "東京 羽田 (HND) 日本",
                    "option_id": "HND",
                }
            return {}

    browser = _DummyBrowser(_Page())
    result = _skyscanner_fill_and_commit_location(
        browser=browser,
        role="dest",
        value="HND",
        selectors=["input[name='destinationInput-search']"],
        timeout_ms=1500,
    )
    assert result.get("ok") is True
    assert result.get("reason") == "combobox_fill_success"
    evidence = dict(result.get("evidence") or {})
    assert evidence.get("typed_value_last") == "HND"


def test_skyscanner_search_click_selectors_drop_role_button_fanout():
    out = _skyscanner_search_click_selectors(
        [
            "[role='button']:has-text('Search flights')",
            "[role='button']:has-text('検索')",
            "button:has-text('検索')",
        ]
    )
    assert "button:has-text('検索')" in out
    assert not any(s.startswith("[role='button']") for s in out)


def test_click_next_month_uses_evaluate_fallback():
    class _Page:
        def locator(self, selector):  # noqa: ARG002
            return _DummyLocator(False)

        def evaluate(self, _script):
            return True

    assert _click_next_month(_Page()) is True


def test_click_first_selector_uses_dom_fallback_when_browser_click_fails():
    class _Page:
        def evaluate(self, _script, payload=None):  # noqa: ANN001
            return payload == {"selector": "button[data-testid='depart-btn']"}

    class _Browser:
        def __init__(self):
            self.page = _Page()
            self.calls = []

        def click(self, selector, timeout_ms=None):  # noqa: ARG002
            self.calls.append(str(selector))
            raise RuntimeError("playwright_click_failed")

    browser = _Browser()
    used = _click_first_selector(
        browser,
        [
            "button[data-testid='depart-btn']",
            "button:has-text('出発')",
        ],
        timeout_ms=900,
    )
    assert used == "button[data-testid='depart-btn']"
    assert browser.calls == ["button[data-testid='depart-btn']"]


def test_skyscanner_fill_date_via_picker_advances_month_before_day_click():
    class _Page:
        def __init__(self):
            self.keyboard = _DummyKeyboard()
            self.next_clicks = 0
            self.month_visible_probes = 0
            self.day_click_attempts = 0

        def locator(self, selector):
            text = str(selector or "")
            if "CustomCalendarContainer" in text:
                return _DummyLocator(True)
            if "来月" in text or "Next month" in text or "NextBtn" in text:
                page = self

                class _NextLoc(_DummyLocator):
                    def click(self_inner, timeout=None):  # noqa: ARG002
                        page.next_clicks += 1
                        return None

                return _NextLoc(True)
            return _DummyLocator(False)

        def wait_for_timeout(self, timeout_ms):  # noqa: ARG002
            return None

        def evaluate(self, _script, payload=None):  # noqa: ANN001
            data = dict(payload or {})
            if "day" in data:
                self.day_click_attempts += 1
                return {"ok": True, "reason": "day_clicked", "aria_label": "2026年5月2日"}
            if "month" in data and "year" in data:
                self.month_visible_probes += 1
                return self.month_visible_probes >= 2
            return False

    class _Browser:
        def __init__(self):
            self.page = _Page()
            self.click_calls = []

        def click(self, selector, timeout_ms=None):  # noqa: ARG002
            self.click_calls.append(str(selector))
            return None

    browser = _Browser()
    target_date = iso(future_date(days_ahead_min=45, days_ahead_max=90))
    result = _skyscanner_fill_date_via_picker(
        browser=browser,
        role="depart",
        date=target_date,
        timeout_ms=1500,
    )
    assert result.get("ok") is True
    assert browser.page.next_clicks >= 1
    assert browser.page.day_click_attempts == 1


def test_skyscanner_dismiss_results_overlay_noop_when_overlay_not_present():
    class _Page:
        def evaluate(self, _script, payload=None):  # noqa: ANN001, ARG002
            return {"overlay_present": False, "visible_dialog_count": 0}

        def wait_for_timeout(self, timeout_ms):  # noqa: ARG002
            return None

    class _Browser:
        def __init__(self):
            self.page = _Page()
            self.click_calls = []

        def click(self, selector, timeout_ms=None):  # noqa: ARG002
            self.click_calls.append(str(selector))
            return None

    browser = _Browser()
    out = _skyscanner_dismiss_results_overlay(browser=browser, timeout_ms=300, max_clicks=2)
    assert out.get("ok") is True
    assert out.get("reason") == "overlay_not_present"
    assert browser.click_calls == []


def test_skyscanner_dismiss_results_overlay_clicks_close_and_clears_overlay():
    class _Page:
        def __init__(self):
            self.probe_count = 0

        def evaluate(self, script, payload=None):  # noqa: ANN001, ARG002
            if "overlay_present" in str(script or ""):
                self.probe_count += 1
                if self.probe_count <= 1:
                    return {"overlay_present": True, "visible_dialog_count": 1}
                return {"overlay_present": False, "visible_dialog_count": 0}
            return False

        def wait_for_timeout(self, timeout_ms):  # noqa: ARG002
            return None

    class _Browser:
        def __init__(self):
            self.page = _Page()
            self.click_calls = []

        def click(self, selector, timeout_ms=None):  # noqa: ARG002
            self.click_calls.append(str(selector))
            if len(self.click_calls) == 1:
                return None
            raise RuntimeError("unexpected_extra_click")

    browser = _Browser()
    out = _skyscanner_dismiss_results_overlay(browser=browser, timeout_ms=300, max_clicks=2)
    assert out.get("ok") is True
    assert out.get("reason") == "overlay_dismissed"
    assert len(browser.click_calls) == 1
