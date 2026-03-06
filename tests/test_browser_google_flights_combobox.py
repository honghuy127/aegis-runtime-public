from core.browser import BrowserSession
import core.browser.session as browser_mod


class _FakeInputLocator:
    def __init__(
        self,
        visible=True,
        *,
        value="",
        attrs=None,
        visible_raises=False,
        click_raises=False,
        force_click_raises=None,
    ):
        self._visible = visible
        self._value = str(value or "")
        self._attrs = dict(attrs or {})
        self._visible_raises = bool(visible_raises)
        self._click_raises = bool(click_raises)
        self._force_click_raises = (
            self._click_raises if force_click_raises is None else bool(force_click_raises)
        )
        self.filled = []
        self.clicks = 0
        self.wait_for_calls = []

    def is_visible(self, timeout=None):  # noqa: ARG002
        if self._visible_raises:
            raise TimeoutError("visibility probe timeout")
        return self._visible

    def wait_for(self, state=None, timeout=None):  # noqa: ARG002
        self.wait_for_calls.append((state, timeout))
        if not self._visible:
            raise TimeoutError("not visible")
        return True

    def click(self, timeout=None, no_wait_after=False, force=False):  # noqa: ARG002
        if not self._visible:
            raise TimeoutError("hidden locator")
        if force and self._force_click_raises:
            raise TimeoutError("force click failed")
        if (not force) and self._click_raises:
            raise TimeoutError("click failed")
        self.clicks += 1
        return True

    def fill(self, text, timeout=None):  # noqa: ARG002
        self._value = str(text or "")
        self.filled.append(text)

    def input_value(self, timeout=None):  # noqa: ARG002
        return self._value

    def get_attribute(self, name, timeout=None):  # noqa: ARG002
        if str(name or "") == "value":
            return self._value
        return self._attrs.get(str(name or ""))


class _FakeLocatorGroup:
    def __init__(self, count=0, first=None, locators=None):
        self._count = count
        self.first = first
        self._locators = list(locators or ([] if first is None else [first]))

    def count(self):
        return self._count

    def nth(self, idx):
        if 0 <= idx < len(self._locators):
            return self._locators[idx]
        return _FakeInputLocator(visible=False)


class _FakePage:
    def __init__(
        self,
        locator_map=None,
        verify_ok=True,
        scope_ok=True,
        focused_input=False,
        generic_resolve_index=None,
        commit_signal=None,
        reject_timeout_kw=False,
    ):
        self._locator_map = locator_map or {}
        self._verify_ok = bool(verify_ok)
        self._scope_ok = bool(scope_ok)
        self._focused_input = bool(focused_input)
        self._generic_resolve_index = generic_resolve_index
        self._reject_timeout_kw = bool(reject_timeout_kw)
        self._commit_signal = dict(
            commit_signal
            or {
                "active_expanded": "false",
                "listbox_visible": False,
                "exact_typed_match": False,
                "has_commit_signal": True,
            }
        )
        self.evaluate_calls = []
        self.keyboard_presses = []
        self.keyboard_types = []
        self.keyboard = type(
            "_Keyboard",
            (),
            {
                "press": lambda _self, key: self.keyboard_presses.append(key),
                "type": lambda _self, text, delay=0: self.keyboard_types.append((text, delay)),  # noqa: ARG005
            },
        )()

    def evaluate(self, script, arg=None, timeout=None):  # noqa: ARG002
        if self._reject_timeout_kw and timeout is not None:
            raise TypeError("evaluate() got an unexpected keyword argument 'timeout'")
        self.evaluate_calls.append((script, arg))
        if isinstance(script, str) and "e.tagName === 'INPUT'" in script:
            return self._focused_input
        if isinstance(arg, dict) and "verify_tokens" in arg:
            return self._generic_resolve_index
        if isinstance(arg, dict) and "typed_text" in arg:
            return dict(self._commit_signal)
        if isinstance(arg, dict) and "selector" in arg and isinstance(script, str) and "listboxVisible" in script:
            return {
                "opened": True,
                "expanded": "true",
                "activeExpanded": "true",
                "rootContainsActive": True,
                "listboxVisible": True,
                "tag": "input",
                "activeTag": "input",
            }
        if isinstance(arg, dict) and "selector" in arg:
            return self._scope_ok
        if isinstance(script, str) and "document.activeElement" in script:
            return self._verify_ok
        return False

    def locator(self, selector):
        return self._locator_map.get(selector, _FakeLocatorGroup(count=0, first=_FakeInputLocator(visible=False)))


class _FakeBrowserSession:
    def __init__(self, page):
        self.page = page
        self.action_timeout_ms = 1500
        self.click_calls = []

    def click(self, selector, timeout_ms=None, no_wait_after=False):  # noqa: ARG002
        self.click_calls.append(selector)
        # Activation succeeds, suggestion option clicks fail.
        if selector and "option" in selector:
            raise TimeoutError("option click timeout")
        return True


def test_fill_google_flights_combobox_skips_nonvisible_activation_selector(monkeypatch):
    monkeypatch.setattr(browser_mod.time, "sleep", lambda _s: None)

    dest_input = _FakeInputLocator(visible=True)
    page = _FakePage(
        locator_map={
            "[role='combobox'][aria-label*='Missing']": _FakeLocatorGroup(
                count=1, first=_FakeInputLocator(visible=False)
            ),
            "[role='combobox'][aria-label*='目的地']": _FakeLocatorGroup(
                count=1, first=_FakeInputLocator(visible=True)
            ),
            "input[aria-label*='目的地']": _FakeLocatorGroup(count=1, first=dest_input),
        },
        verify_ok=True,
        scope_ok=True,
        focused_input=False,
        commit_signal={
            "active_expanded": "false",
            "listbox_visible": False,
            "exact_typed_match": False,
            "has_commit_signal": True,
        },
    )
    session = _FakeBrowserSession(page)

    ok, selector_used = BrowserSession.fill_google_flights_combobox(  # noqa: SLF001
        session,
        activation_selectors=[
            "[role='combobox'][aria-label*='Missing']",
            "[role='combobox'][aria-label*='目的地']",
        ],
        input_selectors=["input[aria-label*='目的地']"],
        text="ITM",
        verify_tokens=["目的地", "ITM"],
        timeout_ms=1500,
    )

    assert ok is True
    assert selector_used == "[role='combobox'][aria-label*='目的地']"
    # The hidden/missing activation selector should be prefiltered instead of clicked.
    assert session.click_calls[0] == "[role='combobox'][aria-label*='目的地']"
    assert "[role='combobox'][aria-label*='Missing']" not in session.click_calls


def test_fill_google_flights_combobox_clicks_visible_duplicate_activation_candidate(monkeypatch):
    monkeypatch.setattr(browser_mod.time, "sleep", lambda _s: None)

    hidden_activation = _FakeInputLocator(visible=False)
    visible_activation = _FakeInputLocator(visible=True)
    origin_input = _FakeInputLocator(visible=True)
    page = _FakePage(
        locator_map={
            "[role='combobox'][aria-label*='Where from']": _FakeLocatorGroup(
                count=2,
                first=hidden_activation,
                locators=[hidden_activation, visible_activation],
            ),
            "input[aria-label*='Where from']": _FakeLocatorGroup(count=1, first=origin_input),
        },
        verify_ok=True,
        scope_ok=True,
        focused_input=False,
        commit_signal={
            "active_expanded": "false",
            "listbox_visible": False,
            "exact_typed_match": False,
            "has_commit_signal": True,
        },
    )
    session = _FakeBrowserSession(page)

    ok, selector_used = BrowserSession.fill_google_flights_combobox(  # noqa: SLF001
        session,
        activation_selectors=["[role='combobox'][aria-label*='Where from']"],
        input_selectors=["input[aria-label*='Where from']"],
        text="HND",
        verify_tokens=["Where from", "HND"],
        timeout_ms=1500,
    )

    debug = getattr(session, "_last_google_flights_combobox_debug", {}) or {}
    assert ok is True
    assert selector_used == "[role='combobox'][aria-label*='Where from']"
    assert hidden_activation.clicks == 0
    assert visible_activation.clicks == 1
    assert origin_input.filled == ["HND"]
    assert debug.get("activation_selector_index_used") == 1


def test_fill_google_flights_combobox_resolves_visible_activation_beyond_first_four(monkeypatch):
    monkeypatch.setattr(browser_mod.time, "sleep", lambda _s: None)

    clones = [_FakeInputLocator(visible=False) for _ in range(5)]
    live = _FakeInputLocator(visible=True)
    activation_locators = clones + [live]
    origin_input = _FakeInputLocator(visible=True)
    page = _FakePage(
        locator_map={
            "[role='combobox'][aria-label*='Where from']": _FakeLocatorGroup(
                count=len(activation_locators),
                first=activation_locators[0],
                locators=activation_locators,
            ),
            "input[aria-label*='Where from']": _FakeLocatorGroup(count=1, first=origin_input),
        },
        verify_ok=True,
        scope_ok=True,
        focused_input=False,
        commit_signal={
            "active_expanded": "false",
            "listbox_visible": False,
            "exact_typed_match": False,
            "has_commit_signal": True,
        },
    )
    session = _FakeBrowserSession(page)

    ok, selector_used = BrowserSession.fill_google_flights_combobox(  # noqa: SLF001
        session,
        activation_selectors=["[role='combobox'][aria-label*='Where from']"],
        input_selectors=["input[aria-label*='Where from']"],
        text="HND",
        verify_tokens=["Where from", "HND"],
        timeout_ms=1500,
    )

    debug = getattr(session, "_last_google_flights_combobox_debug", {}) or {}
    assert ok is True
    assert selector_used == "[role='combobox'][aria-label*='Where from']"
    assert live.clicks == 1
    assert debug.get("activation_selector_index_used") == 5


def test_fill_google_flights_combobox_skips_human_fallback_after_low_budget_fast_fail(monkeypatch):
    monkeypatch.setattr(browser_mod.time, "sleep", lambda _s: None)

    failing_activation = _FakeInputLocator(
        visible=True,
        visible_raises=True,  # make visibility prefilter "unknown" so click timeout is <=260ms
        click_raises=True,
        force_click_raises=True,
    )
    working_activation = _FakeInputLocator(visible=True, visible_raises=True)
    origin_input = _FakeInputLocator(visible=True)
    page = _FakePage(
        locator_map={
            "[role='combobox'][aria-label*='Broken origin']": _FakeLocatorGroup(
                count=1,
                first=failing_activation,
            ),
            "[role='combobox'][aria-label*='Where from']": _FakeLocatorGroup(
                count=1,
                first=working_activation,
            ),
            "input[aria-label*='Where from']": _FakeLocatorGroup(count=1, first=origin_input),
        },
        verify_ok=True,
        scope_ok=True,
        focused_input=False,
        commit_signal={
            "active_expanded": "false",
            "listbox_visible": False,
            "exact_typed_match": False,
            "has_commit_signal": True,
        },
    )
    session = _FakeBrowserSession(page)
    session.human_mimic = True

    ok, selector_used = BrowserSession.fill_google_flights_combobox(  # noqa: SLF001
        session,
        activation_selectors=[
            "[role='combobox'][aria-label*='Broken origin']",
            "[role='combobox'][aria-label*='Where from']",
        ],
        input_selectors=["input[aria-label*='Where from']"],
        text="HND",
        verify_tokens=["Where from", "HND"],
        timeout_ms=1500,
    )

    debug = getattr(session, "_last_google_flights_combobox_debug", {}) or {}
    assert ok is True
    assert selector_used == "[role='combobox'][aria-label*='Where from']"
    # The failing selector should not fall back into BrowserSession.click (human mimic path).
    assert "[role='combobox'][aria-label*='Broken origin']" not in session.click_calls
    assert working_activation.clicks == 1
    attempts = debug.get("activation_attempts") or []
    assert any(
        a.get("selector") == "[role='combobox'][aria-label*='Broken origin']"
        and a.get("mode") == "human_fallback"
        and a.get("reason") == "skipped_after_fast_fail"
        for a in attempts
    )


def test_fill_google_flights_combobox_accepts_semantic_prefilled_origin(monkeypatch):
    monkeypatch.setattr(browser_mod.time, "sleep", lambda _s: None)

    origin_input = _FakeInputLocator(visible=True, value="Tokyo")
    page = _FakePage(
        locator_map={
            "input[aria-label*='Where from']": _FakeLocatorGroup(count=1, first=origin_input),
            # Activation target intentionally unavailable to prove short-circuit.
            "[role='button'][aria-label*='Where from']": _FakeLocatorGroup(
                count=0, first=_FakeInputLocator(visible=False)
            ),
        },
        verify_ok=True,
        scope_ok=True,
        focused_input=False,
        commit_signal={
            "active_expanded": "false",
            "listbox_visible": False,
            "exact_typed_match": False,
            "has_commit_signal": True,
        },
    )
    session = _FakeBrowserSession(page)
    session.human_mimic = True

    ok, selector_used = BrowserSession.fill_google_flights_combobox(  # noqa: SLF001
        session,
        activation_selectors=["[role='button'][aria-label*='Where from']"],
        input_selectors=["input[aria-label*='Where from']"],
        text="HND",
        verify_tokens=["Where from", "HND", "Tokyo"],
        timeout_ms=1500,
    )

    debug = getattr(session, "_last_google_flights_combobox_debug", {}) or {}
    assert ok is True
    assert selector_used == "input[aria-label*='Where from']"
    assert session.click_calls == []
    assert origin_input.filled == []
    assert debug.get("prefilled_match") is True
    assert debug.get("prefilled_value") == "Tokyo"
    assert debug.get("input_source") == "prefilled_visible_input"
    assert any((item or {}).get("matched") for item in (debug.get("prefilled_probe") or []))


def test_fill_google_flights_combobox_prefilled_match_uses_token_beyond_initial_preview(monkeypatch):
    monkeypatch.setattr(browser_mod.time, "sleep", lambda _s: None)

    origin_input = _FakeInputLocator(visible=True, value="Tokyo")
    page = _FakePage(
        locator_map={
            "input[role='combobox'][aria-label='Where from?']": _FakeLocatorGroup(
                count=1,
                first=origin_input,
            ),
        },
        verify_ok=True,
        scope_ok=True,
        focused_input=False,
    )
    session = _FakeBrowserSession(page)

    verify_tokens = [
        "出発",
        "出発地",
        "出発空港",
        "Where from",
        "From",
        "Origin",
        "Departure airport",
        "羽田",
        "東京(羽田)",
        "東京",
        "東京（羽田）",
        "東京都",
        "TOKYO",  # beyond the previous 12-token cap
    ]
    ok, selector_used = BrowserSession.fill_google_flights_combobox(  # noqa: SLF001
        session,
        activation_selectors=["[role='button'][aria-label*='Where from']"],
        input_selectors=["input[role='combobox'][aria-label='Where from?']"],
        text="HND",
        verify_tokens=verify_tokens,
        timeout_ms=1500,
    )

    debug = getattr(session, "_last_google_flights_combobox_debug", {}) or {}
    assert ok is True
    assert selector_used == "input[role='combobox'][aria-label='Where from?']"
    assert debug.get("prefilled_match") is True
    assert debug.get("prefilled_match_token") in {"TOKYO", "Tokyo"}


def test_fill_google_flights_combobox_prefilled_match_rejects_generic_to_token(monkeypatch):
    monkeypatch.setattr(browser_mod.time, "sleep", lambda _s: None)

    dest_input = _FakeInputLocator(visible=True, value="Tokyo")
    page = _FakePage(
        locator_map={
            "input[role='combobox']": _FakeLocatorGroup(count=1, first=dest_input),
        },
        verify_ok=True,
        scope_ok=True,
        focused_input=False,
    )
    session = _FakeBrowserSession(page)
    session.human_mimic = True

    ok, selector_used = BrowserSession.fill_google_flights_combobox(  # noqa: SLF001
        session,
        activation_selectors=["[role='button'][aria-label*='Where to']"],
        input_selectors=["input[role='combobox']"],
        text="ITM",
        verify_tokens=["Where to", "To", "Destination", "ITM"],
        timeout_ms=1500,
    )

    debug = getattr(session, "_last_google_flights_combobox_debug", {}) or {}
    assert isinstance(ok, bool)
    assert isinstance(selector_used, str)
    assert debug.get("prefilled_match") is False
    assert debug.get("prefilled_match_token", "") in {"", None}
    # The probe should record the candidate and show no semantic match.
    assert any((item or {}).get("value") == "Tokyo" for item in (debug.get("prefilled_probe") or []))
    assert all(not (item or {}).get("matched") for item in (debug.get("prefilled_probe") or []))


def test_fill_google_flights_combobox_clears_focused_prefill_before_keyboard_type(monkeypatch):
    monkeypatch.setattr(browser_mod.time, "sleep", lambda _s: None)

    page = _FakePage(
        locator_map={},
        verify_ok=True,
        scope_ok=True,
        focused_input=True,  # Forces the `focused` input path (keyboard typing fallback)
        commit_signal={
            "active_expanded": "false",
            "listbox_visible": False,
            "exact_typed_match": False,
            "has_commit_signal": True,
        },
    )
    session = _FakeBrowserSession(page)

    ok, selector_used = BrowserSession.fill_google_flights_combobox(  # noqa: SLF001
        session,
        activation_selectors=["[role='combobox'][aria-label*='Where from']"],
        input_selectors=[],
        text="HND",
        verify_tokens=["Where from", "HND"],
        timeout_ms=1500,
    )

    assert ok is True
    assert selector_used == "[role='combobox'][aria-label*='Where from']"
    assert ("HND", 0) in page.keyboard_types
    # Clear-before-type is required to avoid appending to provider-prefilled values (e.g. Tokyo).
    assert "ControlOrMeta+A" in page.keyboard_presses
    assert "Backspace" in page.keyboard_presses


def test_fill_google_flights_combobox_rejects_ambiguous_generic_input_candidate(monkeypatch):
    monkeypatch.setattr(browser_mod.time, "sleep", lambda _s: None)

    generic_input = _FakeInputLocator(visible=True)
    page = _FakePage(
        locator_map={
            "input[role='combobox']": _FakeLocatorGroup(count=2, first=generic_input),
        },
        verify_ok=True,
        scope_ok=True,
        focused_input=False,
    )
    session = _FakeBrowserSession(page)

    ok, selector_used = BrowserSession.fill_google_flights_combobox(  # noqa: SLF001
        session,
        activation_selectors=["[role='combobox'][aria-label*='目的地']"],
        input_selectors=["input[role='combobox']"],
        text="ITM",
        verify_tokens=["ITM"],
        timeout_ms=1500,
    )

    assert ok is False
    assert selector_used == ""
    assert generic_input.filled == []


def test_fill_google_flights_combobox_records_deadline_activation_budget_failure(monkeypatch):
    monkeypatch.setattr(browser_mod.time, "sleep", lambda _s: None)

    page = _FakePage(
        locator_map={
            "[role='combobox'][aria-label*='Where from']": _FakeLocatorGroup(
                count=1, first=_FakeInputLocator(visible=True)
            ),
        },
        verify_ok=True,
        scope_ok=True,
        focused_input=False,
    )
    session = _FakeBrowserSession(page)

    ok, selector_used = BrowserSession.fill_google_flights_combobox(  # noqa: SLF001
        session,
        activation_selectors=["[role='combobox'][aria-label*='Where from']"],
        input_selectors=["input[aria-label*='Where from']"],
        text="HND",
        verify_tokens=["HND"],
        timeout_ms=200,
    )

    debug = getattr(session, "_last_google_flights_combobox_debug", {}) or {}
    assert ok is False
    assert selector_used == ""
    assert debug.get("failure_stage") == "deadline_activation_budget"
    assert isinstance(debug.get("failure_remaining_ms"), int)


def test_fill_google_flights_combobox_generic_fallback_requires_scope_check(monkeypatch):
    monkeypatch.setattr(browser_mod.time, "sleep", lambda _s: None)

    generic_input = _FakeInputLocator(visible=True)
    page = _FakePage(
        locator_map={
            "input[role='combobox']": _FakeLocatorGroup(count=1, first=generic_input),
        },
        verify_ok=True,   # activeElement contains typed token
        scope_ok=False,   # but activeElement is not inside activated container
        focused_input=False,
    )
    session = _FakeBrowserSession(page)

    ok, selector_used = BrowserSession.fill_google_flights_combobox(  # noqa: SLF001
        session,
        activation_selectors=["[role='combobox'][aria-label*='目的地']"],
        input_selectors=["input[role='combobox']"],
        text="ITM",
        verify_tokens=["ITM"],
        timeout_ms=1500,
    )

    assert generic_input.filled == ["ITM"]
    assert ok is False
    assert selector_used == ""


def test_fill_google_flights_combobox_resolves_ambiguous_generic_input_with_scoring(monkeypatch):
    monkeypatch.setattr(browser_mod.time, "sleep", lambda _s: None)

    wrong_input = _FakeInputLocator(visible=True)
    right_input = _FakeInputLocator(visible=True)
    page = _FakePage(
        locator_map={
            "input[role='combobox']": _FakeLocatorGroup(
                count=2,
                first=wrong_input,
                locators=[wrong_input, right_input],
            ),
        },
        verify_ok=True,
        scope_ok=True,
        focused_input=False,
        generic_resolve_index=1,
    )
    session = _FakeBrowserSession(page)

    ok, selector_used = BrowserSession.fill_google_flights_combobox(  # noqa: SLF001
        session,
        activation_selectors=["[role='combobox'][aria-label*='目的地']"],
        input_selectors=["input[role='combobox']"],
        text="ITM",
        verify_tokens=["目的地", "ITM"],
        timeout_ms=1500,
    )

    assert wrong_input.filled == []
    assert right_input.filled == ["ITM"]
    assert ok is True
    assert selector_used == "[role='combobox'][aria-label*='目的地']"


def test_fill_google_flights_combobox_resolves_shared_jsname_input_with_scoring(monkeypatch):
    monkeypatch.setattr(browser_mod.time, "sleep", lambda _s: None)

    wrong0 = _FakeInputLocator(visible=True)
    wrong1 = _FakeInputLocator(visible=True)
    right = _FakeInputLocator(visible=True)
    wrong3 = _FakeInputLocator(visible=True)
    page = _FakePage(
        locator_map={
            "input[jsname='yrriRe']": _FakeLocatorGroup(
                count=4,
                first=wrong0,
                locators=[wrong0, wrong1, right, wrong3],
            ),
        },
        verify_ok=True,
        scope_ok=True,
        focused_input=False,
        generic_resolve_index=2,
    )
    session = _FakeBrowserSession(page)

    ok, selector_used = BrowserSession.fill_google_flights_combobox(  # noqa: SLF001
        session,
        activation_selectors=["[role='combobox'][aria-label*='Where to']"],
        input_selectors=["input[jsname='yrriRe']"],
        text="ITM",
        verify_tokens=["Where to", "ITM"],
        timeout_ms=1500,
    )

    assert ok is True
    assert selector_used == "[role='combobox'][aria-label*='Where to']"
    assert right.filled == ["ITM"]
    assert sum(len(x.filled) for x in [wrong0, wrong1, right, wrong3]) == 1


def test_fill_google_flights_combobox_rejects_unconfirmed_draft_when_option_click_times_out(monkeypatch):
    monkeypatch.setattr(browser_mod.time, "sleep", lambda _s: None)

    dest_input = _FakeInputLocator(visible=True)
    page = _FakePage(
        locator_map={
            "input[aria-label*='目的地']": _FakeLocatorGroup(count=1, first=dest_input),
        },
        verify_ok=True,   # activeElement still contains "ITM"
        scope_ok=True,
        focused_input=False,
        commit_signal={
            "active_expanded": "true",
            "listbox_visible": True,
            "exact_typed_match": True,
            "has_commit_signal": False,
        },
    )
    session = _FakeBrowserSession(page)

    ok, selector_used = BrowserSession.fill_google_flights_combobox(  # noqa: SLF001
        session,
        activation_selectors=["[role='combobox'][aria-label*='目的地']"],
        input_selectors=["input[aria-label*='目的地']"],
        text="ITM",
        verify_tokens=["目的地", "ITM"],
        timeout_ms=1500,
    )

    assert dest_input.filled == ["ITM"]
    assert ok is False
    assert selector_used == ""


def test_fill_google_flights_combobox_rejects_placeholder_root_after_deadline_option_click(monkeypatch):
    monkeypatch.setattr(browser_mod.time, "sleep", lambda _s: None)

    dest_input = _FakeInputLocator(visible=True)
    page = _FakePage(
        locator_map={
            "input[aria-label*='目的地']": _FakeLocatorGroup(count=1, first=dest_input),
        },
        verify_ok=True,   # activeElement still contains "ITM"
        scope_ok=True,
        focused_input=False,
        commit_signal={
            "active_expanded": "false",
            "listbox_visible": False,
            "exact_typed_match": True,
            "root_placeholder_like": True,
            "has_commit_signal": False,
        },
    )
    session = _FakeBrowserSession(page)

    ok, selector_used = BrowserSession.fill_google_flights_combobox(  # noqa: SLF001
        session,
        activation_selectors=["[role='combobox'][aria-label*='目的地']"],
        input_selectors=["input[aria-label*='目的地']"],
        text="ITM",
        verify_tokens=["目的地", "ITM"],
        timeout_ms=1500,
    )

    assert dest_input.filled == ["ITM"]
    assert ok is False
    assert selector_used == ""


def test_fill_google_flights_combobox_commit_signal_probe_retries_without_timeout_kw(monkeypatch):
    monkeypatch.setattr(browser_mod.time, "sleep", lambda _s: None)

    dest_input = _FakeInputLocator(visible=True)
    page = _FakePage(
        locator_map={
            "input[aria-label*='目的地']": _FakeLocatorGroup(count=1, first=dest_input),
        },
        verify_ok=True,
        scope_ok=True,
        focused_input=False,
        reject_timeout_kw=True,
        commit_signal={
            "active_expanded": "false",
            "listbox_visible": False,
            "exact_typed_match": False,
            "has_commit_signal": True,
        },
    )
    session = _FakeBrowserSession(page)

    ok, selector_used = BrowserSession.fill_google_flights_combobox(  # noqa: SLF001
        session,
        activation_selectors=["[role='combobox'][aria-label*='目的地']"],
        input_selectors=["input[aria-label*='目的地']"],
        text="ITM",
        verify_tokens=["目的地", "ITM"],
        timeout_ms=1500,
    )

    assert dest_input.filled == ["ITM"]
    assert ok is True
    assert selector_used == "[role='combobox'][aria-label*='目的地']"


def test_fill_google_flights_combobox_generic_prefilled_requires_role_label_match(monkeypatch):
    monkeypatch.setattr(browser_mod.time, "sleep", lambda _s: None)

    origin_input = _FakeInputLocator(
        visible=True,
        value="Tokyo",
        attrs={"aria-label": "Where from? Tokyo HND"},
    )
    dest_input = _FakeInputLocator(
        visible=True,
        value="Osaka",
        attrs={"aria-label": "Where to? Osaka ITM"},
    )
    page = _FakePage(
        locator_map={
            # Role-specific selector misses (simulates UI drift), generic combobox remains.
            "input[aria-label*='出発地']": _FakeLocatorGroup(count=0, first=_FakeInputLocator(visible=False)),
            "input[role='combobox']": _FakeLocatorGroup(count=2, first=origin_input, locators=[origin_input, dest_input]),
        },
        verify_ok=False,
        scope_ok=False,
        focused_input=False,
    )
    session = _FakeBrowserSession(page)

    ok, _selector_used = BrowserSession.fill_google_flights_combobox(  # noqa: SLF001
        session,
        activation_selectors=["[role='button'][aria-label*='Where from']"],
        input_selectors=["input[aria-label*='出発地']"],
        text="HND",
        verify_tokens=["Where from", "From", "Origin", "Osaka", "OSA"],
        timeout_ms=1500,
    )

    debug = getattr(session, "_last_google_flights_combobox_debug", {}) or {}
    assert isinstance(ok, bool)
    assert debug.get("prefilled_match") is False
    probes = debug.get("prefilled_probe") or []
    # Generic destination input may match semantically, but must be rejected on role label mismatch.
    assert any((p or {}).get("value") == "Osaka" and (p or {}).get("role_hint_ok") is False for p in probes)


def test_fill_google_flights_combobox_uses_keyboard_fallback_when_option_click_times_out(monkeypatch):
    monkeypatch.setattr(browser_mod.time, "sleep", lambda _s: None)

    dest_input = _FakeInputLocator(visible=True)
    page = _FakePage(
        locator_map={
            "input[aria-label*='目的地']": _FakeLocatorGroup(count=1, first=dest_input),
        },
        verify_ok=True,
        scope_ok=True,
        focused_input=False,
        commit_signal={
            "active_expanded": "false",
            "listbox_visible": False,
            "exact_typed_match": False,
            "has_commit_signal": True,
        },
    )
    session = _FakeBrowserSession(page)

    ok, selector_used = BrowserSession.fill_google_flights_combobox(  # noqa: SLF001
        session,
        activation_selectors=["[role='combobox'][aria-label*='目的地']"],
        input_selectors=["input[aria-label*='目的地']"],
        text="ITM",
        verify_tokens=["目的地", "ITM"],
        timeout_ms=1500,
    )

    assert ok is True
    assert selector_used == "[role='combobox'][aria-label*='目的地']"
    assert "ArrowDown" in page.keyboard_presses
    assert "Enter" in page.keyboard_presses


def test_fill_google_flights_combobox_accepts_strong_semantic_commit_when_active_value_verify_fails(monkeypatch):
    monkeypatch.setattr(browser_mod.time, "sleep", lambda _s: None)

    dest_input = _FakeInputLocator(visible=True)
    page = _FakePage(
        locator_map={
            "input[aria-label*='目的地']": _FakeLocatorGroup(count=1, first=dest_input),
        },
        verify_ok=False,  # activeElement token check fails (localized label / focus drift)
        scope_ok=True,
        focused_input=False,
        commit_signal={
            "active_value": "大阪国際空港",
            "active_expanded": "false",
            "listbox_visible": False,
            "exact_typed_match": False,
            "root_placeholder_like": False,
            "root_text_preview": "目的地 大阪国際空港",
            "has_commit_signal": True,
        },
    )
    session = _FakeBrowserSession(page)

    ok, selector_used = BrowserSession.fill_google_flights_combobox(  # noqa: SLF001
        session,
        activation_selectors=["[role='combobox'][aria-label*='目的地']"],
        input_selectors=["input[aria-label*='目的地']"],
        text="ITM",
        verify_tokens=["目的地", "ITM"],
        timeout_ms=1500,
    )

    assert dest_input.filled == ["ITM"]
    assert ok is True
    assert selector_used == "[role='combobox'][aria-label*='目的地']"
