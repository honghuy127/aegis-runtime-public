import time

from core.browser import _human_mimic_chromium_user_agent, _human_mimic_stealth_init_script
from core.browser import BrowserSession


def test_human_mimic_stealth_init_script_includes_core_shims():
    script = _human_mimic_stealth_init_script("ja-JP")

    assert "webdriver" in script
    assert "Navigator.prototype" in script
    assert "chrome.runtime" in script
    assert "userAgentData" in script
    assert "platform" in script
    assert "vendor" in script
    assert "permissions.query" in script
    assert "notifications" in script
    assert "ja-JP" in script
    assert '"ja"' in script


def test_human_mimic_stealth_init_script_deduplicates_languages():
    script = _human_mimic_stealth_init_script("en-US")

    # The generated language list should not repeat en-US or en.
    assert script.count("en-US") == 1
    assert script.count('"en"') == 1


def test_human_mimic_chromium_user_agent_is_non_headless():
    ua = _human_mimic_chromium_user_agent()
    assert "Chrome/" in ua
    assert "Headless" not in ua
    assert "Macintosh" in ua


def test_route_filter_allows_verification_challenge_resources():
    class _Request:
        resource_type = "image"
        url = "https://client.px-cloud.net/captcha/bg.png"

    class _Route:
        def __init__(self):
            self.request = _Request()
            self.aborted = False
            self.continued = False

        def abort(self):
            self.aborted = True

        def continue_(self):
            self.continued = True

    route = _Route()
    BrowserSession._route_filter(route)  # noqa: SLF001
    assert route.continued is True
    assert route.aborted is False


def test_route_filter_still_blocks_generic_tracker_hosts():
    class _Request:
        resource_type = "script"
        url = "https://www.google-analytics.com/analytics.js"

    class _Route:
        def __init__(self):
            self.request = _Request()
            self.aborted = False
            self.continued = False

        def abort(self):
            self.aborted = True

        def continue_(self):
            self.continued = True

    route = _Route()
    BrowserSession._route_filter(route)  # noqa: SLF001
    assert route.aborted is True
    assert route.continued is False


def test_unexpected_aux_page_policy_closes_random_domain_tabs():
    close_random = BrowserSession._should_close_unexpected_page(  # noqa: SLF001
        candidate_url="https://random-news.example.org/article",
        primary_url="https://www.skyscanner.com/flights",
        expected_new_pages=0,
    )
    keep_same_site = BrowserSession._should_close_unexpected_page(  # noqa: SLF001
        candidate_url="https://www.skyscanner.jp/flights",
        primary_url="https://www.skyscanner.com/flights",
        expected_new_pages=0,
    )

    assert close_random is True
    assert keep_same_site is False


def test_rebind_live_page_filters_out_off_site_tabs():
    class _Page:
        def __init__(self, url, closed=False):
            self.url = str(url)
            self._closed = bool(closed)
            self.front_count = 0

        def is_closed(self):
            return self._closed

        def bring_to_front(self):
            self.front_count += 1

    class _Context:
        def __init__(self, pages):
            self.pages = list(pages)

    class _Session:
        def __init__(self):
            self.page = _Page(
                "https://www.skyscanner.com/sttc/px/captcha-v2/index.html",
                closed=True,
            )
            random_live = _Page("https://random-news.example.org/article", closed=False)
            same_site_live = _Page("https://www.skyscanner.jp/flights", closed=False)
            self.context = _Context([random_live, same_site_live])

    session = _Session()
    out = BrowserSession._rebind_live_page_after_target_closed(session)  # noqa: SLF001

    assert out["recovered"] is True
    assert out["reason"] == "rebound_live_page"
    assert out["final_url"] == "https://www.skyscanner.jp/flights"


def test_rebind_live_page_rejects_only_off_site_tabs():
    class _Page:
        def __init__(self, url, closed=False):
            self.url = str(url)
            self._closed = bool(closed)

        def is_closed(self):
            return self._closed

        def bring_to_front(self):
            return None

    class _Context:
        def __init__(self, pages):
            self.pages = list(pages)

    class _Session:
        def __init__(self):
            self.page = _Page(
                "https://www.skyscanner.com/sttc/px/captcha-v2/index.html",
                closed=True,
            )
            self.context = _Context([_Page("https://ads.example.net/offers", closed=False)])

    out = BrowserSession._rebind_live_page_after_target_closed(_Session())  # noqa: SLF001

    assert out["recovered"] is False
    assert out["reason"] == "no_live_page_same_site"


def test_network_snapshot_reports_challenge_blocked_by_client_metrics():
    now_ms = int(time.time() * 1000)

    class _Session:
        def __init__(self):
            self._network_activity = {
                "started_ms": now_ms - 5000,
                "requests": 0,
                "responses": 0,
                "failed": 2,
                "status_buckets": {},
                "resource_types": {},
                "domains": {"client.px-cloud.net": 2},
                "events": [
                    {
                        "t_ms": now_ms - 600,
                        "kind": "failed",
                        "host": "client.px-cloud.net",
                        "error": "net::ERR_BLOCKED_BY_CLIENT",
                    },
                    {
                        "t_ms": now_ms - 500,
                        "kind": "failed",
                        "host": "analytics.example.com",
                        "error": "net::ERR_BLOCKED_BY_CLIENT",
                    },
                ],
            }

    out = BrowserSession.get_network_activity_snapshot(_Session(), window_sec=20)  # noqa: SLF001
    window = dict((out or {}).get("window", {}) or {})
    assert int(window.get("failed_blocked_by_client", 0) or 0) == 2
    assert int(window.get("failed_challenge_hosts", 0) or 0) == 1
    assert int(window.get("failed_challenge_hosts_blocked_by_client", 0) or 0) == 1


def test_random_human_user_agent_filters_mobile_candidate(monkeypatch):
    class _FakeUA:
        @property
        def random(self):
            return (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 18_3_2 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                "CriOS/135.0.7049.53 Mobile/15E148 Safari/604.1"
            )

    monkeypatch.setattr("core.browser.session._FakeUserAgent", _FakeUA)

    ua = BrowserSession._random_human_user_agent()  # noqa: SLF001
    assert "mobile" not in ua.lower()
    assert "iphone" not in ua.lower()


def test_random_human_user_agent_rejects_stale_desktop_candidate(monkeypatch):
    class _FakeUA:
        @property
        def random(self):
            return (
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/114.0.0.0 Safari/537.36"
            )

    monkeypatch.setattr("core.browser.session._FakeUserAgent", _FakeUA)

    ua = BrowserSession._random_human_user_agent()  # noqa: SLF001
    assert "chrome/" in ua.lower()
    assert "chrome/114." not in ua.lower()


def test_human_mimic_press_and_hold_challenge_executes_bounded_hold(monkeypatch):
    class _Mouse:
        def __init__(self):
            self.events = []

        def move(self, x, y, steps=1):  # noqa: ARG002
            self.events.append(("move", round(x, 1), round(y, 1)))

        def down(self):
            self.events.append(("down",))

        def up(self):
            self.events.append(("up",))

    class _Page:
        def __init__(self):
            self.mouse = _Mouse()
            self.waits = []

        def evaluate(self, script, arg=None):  # noqa: ARG002
            assert "press & hold" in script.lower()
            return {"x": 500, "y": 400, "w": 220, "h": 72, "text": "press & hold"}

        def wait_for_timeout(self, timeout_ms):
            self.waits.append(int(timeout_ms))

    class _Session:
        def __init__(self):
            self.page = _Page()
            self.human_mimic = True

    monkeypatch.setattr("core.browser.session.random.randint", lambda a, b: a)

    session = _Session()
    ok = BrowserSession.human_mimic_press_and_hold_challenge(session, max_hold_ms=1200)  # noqa: SLF001

    assert ok is True
    assert ("down",) in session.page.mouse.events
    assert ("up",) in session.page.mouse.events
    # Includes the hold duration and pre/post settle waits.
    assert any(ms == 1200 for ms in session.page.waits)


def test_human_mimic_press_and_hold_challenge_px_shell_uses_cursor_approach(monkeypatch):
    class _Mouse:
        def __init__(self):
            self.events = []

        def move(self, x, y, steps=1):  # noqa: ARG002
            self.events.append(("move", round(x, 1), round(y, 1)))

        def down(self):
            self.events.append(("down",))

        def up(self):
            self.events.append(("up",))

    class _Page:
        def __init__(self):
            self.mouse = _Mouse()
            self.waits = []

        def evaluate(self, script, arg=None):  # noqa: ARG002
            assert "px-captcha" in str(script)
            return {
                "x": 500,
                "y": 420,
                "w": 310,
                "h": 100,
                "text": "px-captcha-shell",
                "kind": "px_shell",
            }

        def wait_for_timeout(self, timeout_ms):
            self.waits.append(int(timeout_ms))

    class _Session:
        def __init__(self):
            self.page = _Page()
            self.human_mimic = True

    monkeypatch.setattr("core.browser.session.random.randint", lambda a, b: a)
    monkeypatch.setattr("core.browser.verification_challenges.random.randint", lambda a, b: a)
    monkeypatch.setattr("core.browser.verification_challenges.random.random", lambda: 0.0)
    monkeypatch.setattr("core.browser.verification_challenges.random.uniform", lambda a, b: a)

    session = _Session()
    ok = BrowserSession.human_mimic_press_and_hold_challenge(session, max_hold_ms=10_000)  # noqa: SLF001

    assert ok is True
    assert ("down",) in session.page.mouse.events
    assert ("up",) in session.page.mouse.events
    move_count = sum(1 for event in session.page.mouse.events if event and event[0] == "move")
    assert move_count >= 3


def test_manual_intervention_returns_rich_skip_metadata_for_headless():
    class _Page:
        url = "https://www.skyscanner.com/flights"

    class _Session:
        def __init__(self):
            self.allow_human_intervention = True
            self.headless = True
            self.page = _Page()
            self.manual_intervention_timeout_sec = 120

    out = BrowserSession.allow_manual_verification_intervention(  # noqa: SLF001
        _Session(),
        reason="skyscanner_interstitial_grace_blocked",
        wait_sec=45,
    )

    assert out["used"] is False
    assert out["reason"] == "headless_mode"
    assert out["wait_sec"] == 45
    assert out["requested_reason"] == "skyscanner_interstitial_grace_blocked"
    assert out["allow_human_intervention"] is True
    assert out["headless"] is True
    assert out["page_available"] is True
    assert out["page_url_before"] == "https://www.skyscanner.com/flights"


def test_manual_intervention_force_last_resort_works_when_disabled():
    class _Page:
        def __init__(self):
            self.url = "https://www.skyscanner.com/flights"
            self.wait_calls = []

        def title(self):
            return "Skyscanner"

        def bring_to_front(self):
            return None

        def wait_for_timeout(self, timeout_ms):
            self.wait_calls.append(int(timeout_ms))

    class _Session:
        def __init__(self):
            self.allow_human_intervention = False
            self.last_resort_manual_when_disabled = True
            self.headless = False
            self.page = _Page()
            self.manual_intervention_timeout_sec = 120

    session = _Session()
    out = BrowserSession.allow_manual_verification_intervention(  # noqa: SLF001
        session,
        reason="skyscanner_interstitial_last_resort_when_manual_disabled",
        wait_sec=12,
        force=True,
    )

    assert out["used"] is True
    assert out["force_requested"] is True
    assert out["force_last_resort"] is True
    assert out["reason"] == "manual_window_elapsed"
    assert sum(session.page.wait_calls) == 12000
    assert len(session.page.wait_calls) >= 1


def test_manual_intervention_returns_rich_success_metadata(monkeypatch):
    class _Page:
        def __init__(self):
            self.url = "https://www.skyscanner.com/flights"
            self.wait_calls = []
            self._eval_calls = 0

        def title(self):
            return "Skyscanner"

        def bring_to_front(self):
            return None

        def wait_for_timeout(self, timeout_ms):
            self.wait_calls.append(int(timeout_ms))

        def evaluate(self, script, payload=None):  # noqa: ARG002
            self._eval_calls += 1
            if self._eval_calls == 1:
                return {"enabled": True, "token": "tok", "cursor": 0, "started_at_ms": 1000}
            return {
                "enabled": True,
                "event_count": 2,
                "dropped_events": 0,
                "event_counts": {"click": 1, "wheel": 1},
                "events": [
                    {"t": 1001, "type": "click", "x": 123, "y": 234, "target": "button#search"},
                    {"t": 1200, "type": "wheel", "dx": 0, "dy": 180, "target": "div.results"},
                ],
                "captured_ms": 199,
                "started_at_ms": 1001,
                "ended_at_ms": 1200,
            }

    class _Session:
        def __init__(self):
            self.allow_human_intervention = True
            self.headless = False
            self.page = _Page()
            self.manual_intervention_timeout_sec = 120
            self._manual_events = []
            self.manual_intervention_event_hook = lambda payload: self._manual_events.append(dict(payload))

    session = _Session()
    out = BrowserSession.allow_manual_verification_intervention(  # noqa: SLF001
        session,
        reason="skyscanner_interstitial_retry_1",
        wait_sec=15,
    )

    assert out["used"] is True
    assert out["reason"] == "manual_window_elapsed"
    assert out["wait_sec"] == 15
    assert out["requested_reason"] == "skyscanner_interstitial_retry_1"
    assert out["allow_human_intervention"] is True
    assert out["headless"] is False
    assert out["page_available"] is True
    assert out["brought_to_front"] is True
    assert out["elapsed_ms"] >= 0
    assert out["page_url_before"] == "https://www.skyscanner.com/flights"
    assert out["page_url_after"] == "https://www.skyscanner.com/flights"
    assert sum(session.page.wait_calls) == 15000
    assert len(session.page.wait_calls) >= 1
    assert out["ui_action_capture"]["enabled"] is True
    assert out["ui_action_capture"]["event_count"] == 2
    assert out["ui_action_capture"]["event_counts"]["wheel"] == 1
    assert any(evt.get("stage") == "start" for evt in session._manual_events)
    assert any(evt.get("stage") == "heartbeat" for evt in session._manual_events)
    assert any(evt.get("stage") == "done" for evt in session._manual_events)


def test_manual_intervention_reports_target_closed_metadata():
    class TargetClosedError(Exception):
        pass

    class _Page:
        def __init__(self):
            self.url = "https://www.skyscanner.com/flights"

        def title(self):
            return "Skyscanner"

        def bring_to_front(self):
            return None

        def wait_for_timeout(self, timeout_ms):  # noqa: ARG002
            raise TargetClosedError("Target page, context or browser has been closed")

        def is_closed(self):
            return True

    class _Session:
        def __init__(self):
            self.allow_human_intervention = True
            self.headless = False
            self.page = _Page()
            self.manual_intervention_timeout_sec = 120

    out = BrowserSession.allow_manual_verification_intervention(  # noqa: SLF001
        _Session(),
        reason="skyscanner_interstitial_grace_blocked",
        wait_sec=20,
    )

    assert out["used"] is True
    assert out["reason"] == "manual_intervention_target_closed"
    assert out["error"] == "TargetClosedError"
    assert out["page_available_after"] is True
    assert out["page_closed_after"] is True
    assert out["page_url_after"] == "https://www.skyscanner.com/flights"


def test_manual_intervention_target_closed_uses_last_heartbeat_capture_fallback():
    class TargetClosedError(Exception):
        pass

    class _Page:
        def __init__(self):
            self.url = "https://www.skyscanner.com/flights"
            self._eval_calls = 0

        def title(self):
            return "Skyscanner"

        def bring_to_front(self):
            return None

        def evaluate(self, script, payload=None):  # noqa: ARG002
            text = str(script or "")
            # Captcha-surface probes expect booleans; keep it disabled in this test.
            if "__fwManualCapture" not in text:
                return False
            self._eval_calls += 1
            if self._eval_calls == 1:
                return {"enabled": True, "token": "tok", "cursor": 0, "started_at_ms": 1000}
            if self._eval_calls == 2:
                return {
                    "enabled": True,
                    "event_count": 4,
                    "dropped_events": 0,
                    "event_counts": {"iframe_added": 1, "pointerdown": 1, "pointerup": 1, "click": 1},
                    "events": [],
                }
            raise TargetClosedError("Target page, context or browser has been closed")

        def wait_for_timeout(self, timeout_ms):  # noqa: ARG002
            raise TargetClosedError("Target page, context or browser has been closed")

        def is_closed(self):
            return True

    class _Session:
        def __init__(self):
            self.allow_human_intervention = True
            self.human_intervention_mode = "demo"
            self.headless = False
            self.page = _Page()
            self.manual_intervention_timeout_sec = 120

    out = BrowserSession.allow_manual_verification_intervention(  # noqa: SLF001
        _Session(),
        reason="scenario_demo_mode_attempt_1_turn_1",
        wait_sec=20,
    )

    assert out["used"] is True
    assert out["reason"] == "manual_intervention_target_closed"
    assert out["ui_action_capture"]["enabled"] is True
    assert out["ui_action_capture"]["event_count"] == 4
    assert out["ui_action_capture"]["fallback_from_heartbeat"] is True


def test_manual_intervention_demo_target_closed_high_signal_is_observation_complete():
    class TargetClosedError(Exception):
        pass

    class _Page:
        def __init__(self):
            self.url = "https://www.google.com/travel/flights?hl=en&gl=JP"
            self._eval_calls = 0

        def title(self):
            return "Google Flights"

        def bring_to_front(self):
            return None

        def evaluate(self, script, payload=None):  # noqa: ARG002
            text = str(script or "")
            if "__fwManualCapture" not in text:
                return False
            self._eval_calls += 1
            if self._eval_calls == 1:
                return {"enabled": True, "token": "tok", "cursor": 0, "started_at_ms": 1000}
            if self._eval_calls == 2:
                return {
                    "enabled": True,
                    "event_count": 180,
                    "dropped_events": 0,
                    "event_counts": {
                        "click": 30,
                        "keydown": 35,
                        "input": 28,
                        "scroll": 20,
                        "focusin": 15,
                        "focusout": 15,
                    },
                    "events": [],
                }
            raise TargetClosedError("Target page, context or browser has been closed")

        def wait_for_timeout(self, timeout_ms):  # noqa: ARG002
            self.url = (
                "https://www.google.com/travel/flights/search?"
                "tfs=CBwQAhoeEgoyMDI2LTA1LTAyagcIARIDRlVLcgcIARIDSE5E"
            )
            raise TargetClosedError("Target page, context or browser has been closed")

        def is_closed(self):
            return True

    class _Session:
        def __init__(self):
            self.allow_human_intervention = True
            self.human_intervention_mode = "demo"
            self.headless = False
            self.page = _Page()
            self.manual_intervention_timeout_sec = 120

    out = BrowserSession.allow_manual_verification_intervention(  # noqa: SLF001
        _Session(),
        reason="scenario_demo_mode_attempt_1_turn_1",
        wait_sec=20,
    )

    assert out["used"] is True
    assert out["reason"] == "manual_observation_complete_target_closed"
    assert out["observation_complete"] is True
    assert out["ui_action_capture"]["fallback_from_heartbeat"] is True
    assert out["ui_action_capture"]["signal_quality"] == "direct"
    assert out["ui_action_capture"]["direct_event_count"] >= 20


def test_manual_intervention_demo_target_closed_on_captcha_stays_target_closed():
    class TargetClosedError(Exception):
        pass

    class _Page:
        def __init__(self):
            self.url = (
                "https://www.skyscanner.com/sttc/px/captcha-v2/index.html"
                "?url=L2ZsaWdodHM/&uuid=abc123&vid=def456"
            )
            self._eval_calls = 0

        def title(self):
            return "Skyscanner"

        def bring_to_front(self):
            return None

        def evaluate(self, script, payload=None):  # noqa: ARG002
            text = str(script or "")
            if "__fwManualCapture" not in text:
                return True
            self._eval_calls += 1
            if self._eval_calls == 1:
                return {"enabled": True, "token": "tok", "cursor": 0, "started_at_ms": 1000}
            if self._eval_calls == 2:
                return {
                    "enabled": True,
                    "event_count": 240,
                    "dropped_events": 0,
                    "event_counts": {"click": 40, "keydown": 30, "input": 20},
                    "events": [],
                }
            raise TargetClosedError("Target page, context or browser has been closed")

        def wait_for_timeout(self, timeout_ms):  # noqa: ARG002
            raise TargetClosedError("Target page, context or browser has been closed")

        def is_closed(self):
            return True

    class _Session:
        def __init__(self):
            self.allow_human_intervention = True
            self.human_intervention_mode = "demo"
            self.headless = False
            self.page = _Page()
            self.manual_intervention_timeout_sec = 120

    out = BrowserSession.allow_manual_verification_intervention(  # noqa: SLF001
        _Session(),
        reason="scenario_demo_mode_attempt_1_turn_1",
        wait_sec=20,
    )

    assert out["used"] is True
    assert out["reason"] == "manual_intervention_target_closed"
    assert out["observation_complete"] is False


def test_manual_intervention_reports_keyboard_interrupt_metadata():
    class _Page:
        def __init__(self):
            self.url = "https://www.skyscanner.com/flights"

        def title(self):
            return "Skyscanner"

        def bring_to_front(self):
            return None

        def wait_for_timeout(self, timeout_ms):  # noqa: ARG002
            raise KeyboardInterrupt()

        def is_closed(self):
            return False

    class _Session:
        def __init__(self):
            self.allow_human_intervention = True
            self.human_intervention_mode = "demo"
            self.headless = False
            self.page = _Page()
            self.manual_intervention_timeout_sec = 120

    out = BrowserSession.allow_manual_verification_intervention(  # noqa: SLF001
        _Session(),
        reason="scenario_demo_mode_attempt_1_turn_1",
        wait_sec=20,
    )

    assert out["used"] is True
    assert out["reason"] == "manual_intervention_interrupted"
    assert out["error"] == "KeyboardInterrupt"
    assert out["human_intervention_mode"] == "demo"
    assert out["demo_mode"] is True


def test_manual_intervention_tracks_captcha_challenge_signature_churn():
    class _Page:
        def __init__(self):
            self.url = (
                "https://www.skyscanner.com/sttc/px/captcha-v2/index.html"
                "?url=L2ZsaWdodHM/&uuid=abc123456789&vid=def123456789"
            )
            self.wait_calls = []
            self._capture_calls = 0
            self._fp_calls = 0

        def title(self):
            return "Skyscanner"

        def bring_to_front(self):
            return None

        def wait_for_timeout(self, timeout_ms):
            self.wait_calls.append(int(timeout_ms))

        def evaluate(self, script, payload=None):  # noqa: ARG002
            text = str(script or "")
            if "__fwManualCapture" in text:
                self._capture_calls += 1
                if self._capture_calls == 1:
                    return {"enabled": True, "token": "tok", "cursor": 0, "started_at_ms": 1000}
                return {
                    "enabled": True,
                    "event_count": 1,
                    "dropped_events": 0,
                    "event_counts": {"iframe_attr_changed": 1},
                    "events": [],
                    "captured_ms": 0,
                    "started_at_ms": 1001,
                    "ended_at_ms": 1001,
                }
            if "challenge_signature" in text and "frame_src_signature" in text:
                self._fp_calls += 1
                signature = "sig-A" if self._fp_calls < 3 else "sig-B"
                return {
                    "iframe_count": 1,
                    "iframe_visible_count": 0,
                    "token_prefix": "",
                    "token_len": 0,
                    "dataframe_token_prefix": "",
                    "captcha_uuid_prefix": "abc123456789",
                    "captcha_vid_prefix": "def123456789",
                    "captcha_identifier_prefix": "abc123456789",
                    "captcha_script_key": "rf8vapwA",
                    "frame_src_signature": "srcsig",
                    "container_signature": "contsig",
                    "challenge_signature": signature,
                }
            return False

    class _Session:
        def __init__(self):
            self.allow_human_intervention = True
            self.human_intervention_mode = "demo"
            self.headless = False
            self.page = _Page()
            self.manual_intervention_timeout_sec = 120
            self._manual_events = []
            self.manual_intervention_event_hook = lambda payload: self._manual_events.append(dict(payload))

    session = _Session()
    out = BrowserSession.allow_manual_verification_intervention(  # noqa: SLF001
        session,
        reason="scenario_demo_mode_attempt_1_turn_1",
        wait_sec=10,
    )

    assert out["used"] is True
    assert out["reason"] == "manual_window_elapsed"
    assert out["captcha_challenge_change_count"] >= 1
    assert len(out["captcha_challenge_signatures_seen"]) >= 2
    assert out["captcha_challenge_signatures_seen"][0] == "sig-A"
    heartbeat_events = [evt for evt in session._manual_events if evt.get("stage") == "heartbeat"]
    assert heartbeat_events
    assert any(str(evt.get("captcha_challenge_signature_prefix", "")) for evt in heartbeat_events)


def test_manual_intervention_uses_html_fallback_tokens_and_emits_proxy_signal():
    class _Page:
        def __init__(self):
            self.url = (
                "https://www.skyscanner.com/sttc/px/captcha-v2/index.html"
                "?url=L2ZsaWdodHM/&uuid=abc123456789&vid=def123456789"
            )
            self.wait_calls = []
            self._capture_calls = 0
            self._token_idx = 0

        def title(self):
            return "Skyscanner"

        def bring_to_front(self):
            return None

        def wait_for_timeout(self, timeout_ms):
            self.wait_calls.append(int(timeout_ms))

        def content(self):
            self._token_idx += 1
            token = "tok-a" if self._token_idx < 3 else "tok-b"
            return (
                "<html><body><section class='identifier'>abc123456789</section>"
                f"<div id='px-captcha'><iframe token=\"{token}\" title='Human verification challenge'></iframe></div>"
                "<iframe dataframetoken=\"d-probe-token\"></iframe>"
                "</body></html>"
            )

        def evaluate(self, script, payload=None):  # noqa: ARG002
            text = str(script or "")
            if "__fwManualCapture" in text:
                self._capture_calls += 1
                if self._capture_calls == 1:
                    return {"enabled": True, "token": "tok", "cursor": 0, "started_at_ms": 1000}
                return {
                    "enabled": True,
                    "event_count": 1,
                    "dropped_events": 0,
                    "event_counts": {"active_element_changed": 1},
                    "events": [],
                    "captured_ms": 0,
                    "started_at_ms": 1001,
                    "ended_at_ms": 1001,
                }
            if "challenge_signature" in text and "frame_src_signature" in text:
                return {
                    "iframe_count": 0,
                    "iframe_visible_count": 0,
                    "token_prefix": "",
                    "token_len": 0,
                    "dataframe_token_prefix": "",
                    "challenge_signature": "",
                }
            return False

    class _Session:
        def __init__(self):
            self.allow_human_intervention = True
            self.human_intervention_mode = "demo"
            self.headless = False
            self.page = _Page()
            self.manual_intervention_timeout_sec = 120
            self._manual_events = []
            self.manual_intervention_event_hook = lambda payload: self._manual_events.append(dict(payload))

    session = _Session()
    out = BrowserSession.allow_manual_verification_intervention(  # noqa: SLF001
        session,
        reason="scenario_demo_mode_attempt_1_turn_1",
        wait_sec=10,
    )

    assert out["used"] is True
    assert out["reason"] == "manual_window_elapsed"
    assert out["captcha_token_source"] == "html_fallback"
    assert out["captcha_challenge_change_count"] >= 1
    assert out["captcha_last_probe"]["token_source"] == "html_fallback"
    assert out["ui_action_capture"]["signal_quality"] == "proxy_only"
    assert any(
        evt.get("stage") == "human_interaction_proxy_detected"
        for evt in session._manual_events
    )


def test_manual_intervention_recovers_target_closed_and_completes_window():
    class TargetClosedError(Exception):
        pass

    class _Page:
        def __init__(self, should_fail=False):
            self.url = "https://www.skyscanner.com/flights"
            self.should_fail = should_fail
            self.wait_calls = []

        def title(self):
            return "Skyscanner"

        def bring_to_front(self):
            return None

        def wait_for_timeout(self, timeout_ms):
            self.wait_calls.append(int(timeout_ms))
            if self.should_fail:
                self.should_fail = False
                raise TargetClosedError("Target page, context or browser has been closed")

        def is_closed(self):
            return False

    class _Session:
        def __init__(self):
            self.allow_human_intervention = True
            self.human_intervention_mode = "off"
            self.last_resort_manual_when_disabled = False
            self.headless = False
            self.page = _Page(should_fail=True)
            self.manual_intervention_timeout_sec = 120
            self._recovery_calls = 0

        def recover_page_after_target_closed(self, preferred_url=""):  # noqa: ARG002
            self._recovery_calls += 1
            self.page = _Page(should_fail=False)
            return {"attempted": True, "recovered": True, "reason": "recovered"}

    session = _Session()
    out = BrowserSession.allow_manual_verification_intervention(  # noqa: SLF001
        session,
        reason="skyscanner_interstitial_grace_blocked",
        wait_sec=10,
    )

    assert out["used"] is True
    assert out["reason"] == "manual_window_elapsed"
    assert out["recovery_attempts"] >= 1
    assert session._recovery_calls >= 1


def test_manual_intervention_assist_mode_skips_target_closed_auto_recovery():
    class TargetClosedError(Exception):
        pass

    class _Page:
        def __init__(self):
            self.url = "https://www.skyscanner.com/flights"

        def title(self):
            return "Skyscanner"

        def bring_to_front(self):
            return None

        def wait_for_timeout(self, timeout_ms):  # noqa: ARG002
            raise TargetClosedError("Target page, context or browser has been closed")

        def is_closed(self):
            return False

    class _Session:
        def __init__(self):
            self.allow_human_intervention = True
            self.human_intervention_mode = "assist"
            self.last_resort_manual_when_disabled = False
            self.headless = False
            self.page = _Page()
            self.manual_intervention_timeout_sec = 120
            self._recovery_calls = 0

        def recover_page_after_target_closed(self, preferred_url=""):  # noqa: ARG002
            self._recovery_calls += 1
            return {"attempted": True, "recovered": True, "reason": "recovered"}

    session = _Session()
    out = BrowserSession.allow_manual_verification_intervention(  # noqa: SLF001
        session,
        reason="skyscanner_interstitial_grace_blocked",
        wait_sec=10,
    )

    assert out["used"] is True
    assert out["reason"] == "manual_intervention_target_closed"
    assert out["recovery_attempts"] == 0
    assert session._recovery_calls == 0


def test_manual_intervention_force_last_resort_skips_target_closed_auto_recovery():
    class TargetClosedError(Exception):
        pass

    class _Page:
        def __init__(self):
            self.url = "https://www.skyscanner.com/sttc/px/captcha-v2/index.html"

        def title(self):
            return "Skyscanner"

        def bring_to_front(self):
            return None

        def wait_for_timeout(self, timeout_ms):  # noqa: ARG002
            raise TargetClosedError("Target page, context or browser has been closed")

        def is_closed(self):
            return False

    class _Session:
        def __init__(self):
            self.allow_human_intervention = False
            self.human_intervention_mode = "off"
            self.last_resort_manual_when_disabled = True
            self.headless = False
            self.page = _Page()
            self.manual_intervention_timeout_sec = 120
            self._recovery_calls = 0

        def recover_page_after_target_closed(self, preferred_url=""):  # noqa: ARG002
            self._recovery_calls += 1
            return {"attempted": True, "recovered": True, "reason": "recovered"}

    session = _Session()
    out = BrowserSession.allow_manual_verification_intervention(  # noqa: SLF001
        session,
        reason="skyscanner_interstitial_last_resort_when_manual_disabled",
        wait_sec=10,
        force=True,
    )

    assert out["used"] is True
    assert out["reason"] == "manual_intervention_target_closed"
    assert out["recovery_attempts"] == 0
    assert session._recovery_calls == 0


def test_manual_intervention_assist_mode_rebinds_live_page_after_target_closed():
    class TargetClosedError(Exception):
        pass

    class _ClosedPage:
        def __init__(self):
            self.url = "https://www.skyscanner.com/sttc/px/captcha-v2/index.html"
            self._raised = False

        def title(self):
            return "Skyscanner"

        def bring_to_front(self):
            return None

        def wait_for_timeout(self, timeout_ms):  # noqa: ARG002
            if not self._raised:
                self._raised = True
                raise TargetClosedError("Target page, context or browser has been closed")
            return None

        def is_closed(self):
            return True

    class _LivePage:
        def __init__(self):
            self.url = "https://www.skyscanner.com/flights"

        def title(self):
            return "Skyscanner"

        def bring_to_front(self):
            return None

        def wait_for_timeout(self, timeout_ms):  # noqa: ARG002
            return None

        def is_closed(self):
            return False

    class _Context:
        def __init__(self, live_page):
            self.pages = [live_page]

    class _Session:
        def __init__(self):
            self.allow_human_intervention = True
            self.human_intervention_mode = "assist"
            self.last_resort_manual_when_disabled = False
            self.headless = False
            self.page = _ClosedPage()
            self.live_page = _LivePage()
            self.context = _Context(self.live_page)
            self.manual_intervention_timeout_sec = 120
            self._recovery_calls = 0

        def recover_page_after_target_closed(self, preferred_url=""):  # noqa: ARG002
            self._recovery_calls += 1
            return {"attempted": True, "recovered": True, "reason": "recovered"}

    session = _Session()
    out = BrowserSession.allow_manual_verification_intervention(  # noqa: SLF001
        session,
        reason="skyscanner_interstitial_grace_blocked",
        wait_sec=10,
    )

    assert out["used"] is True
    assert out["reason"] == "manual_challenge_cleared"
    assert out["recovery_attempts"] >= 1
    assert out["recovery_events"][0]["reason"] == "rebound_live_page"
    assert out["page_url_after"] == "https://www.skyscanner.com/flights"
    assert session._recovery_calls == 0


def test_manual_intervention_demo_mode_does_not_extend_on_passive_captcha_churn():
    class _Page:
        def __init__(self):
            self.url = (
                "https://www.skyscanner.com/sttc/px/captcha-v2/index.html"
                "?url=L2ZsaWdodHM/&uuid=u123&vid=v123"
            )
            self._tick = 0

        def title(self):
            return "Skyscanner"

        def bring_to_front(self):
            return None

        def wait_for_timeout(self, timeout_ms):  # noqa: ARG002
            self._tick += 1

        def is_closed(self):
            return False

        def content(self):
            tokens = [
                "a" * 128,
                "b" * 128,
                "c" * 128,
                "d" * 128,
                "e" * 128,
            ]
            token = tokens[min(self._tick, len(tokens) - 1)]
            return (
                "<html><body><div id='px-captcha'><iframe "
                f"token=\"{token}\" dataframetoken=\"d-token-1234567890\" "
                "title='Human verification challenge'></iframe></div></body></html>"
            )

    class _Session:
        def __init__(self):
            self.allow_human_intervention = True
            self.human_intervention_mode = "demo"
            self.last_resort_manual_when_disabled = False
            self.headless = False
            self.page = _Page()
            self.manual_intervention_timeout_sec = 120
            self._manual_events = []
            self.manual_intervention_event_hook = (
                lambda payload, *_args: self._manual_events.append(dict(payload))
            )

    session = _Session()
    out = BrowserSession.allow_manual_verification_intervention(  # noqa: SLF001
        session,
        reason="scenario_demo_mode_attempt_1_turn_1",
        wait_sec=10,
    )

    assert out["used"] is True
    assert out["reason"] == "manual_window_elapsed"
    extend_events = [evt for evt in session._manual_events if evt.get("stage") == "extend"]
    assert len(extend_events) == 0
    assert out["captcha_token_change_count"] >= 2


def test_manual_intervention_assist_mode_extends_on_captcha_reissue_signals():
    class _Page:
        def __init__(self):
            self.url = (
                "https://www.skyscanner.com/sttc/px/captcha-v2/index.html"
                "?url=L2ZsaWdodHM/&uuid=u123&vid=v123"
            )
            self._tick = 0

        def title(self):
            return "Skyscanner"

        def bring_to_front(self):
            return None

        def wait_for_timeout(self, timeout_ms):  # noqa: ARG002
            self._tick += 1

        def is_closed(self):
            return False

        def content(self):
            tokens = [
                "a" * 128,
                "b" * 128,
                "c" * 128,
                "d" * 128,
                "e" * 128,
            ]
            token = tokens[min(self._tick, len(tokens) - 1)]
            return (
                "<html><body><div id='px-captcha'><iframe "
                f"token=\"{token}\" dataframetoken=\"d-token-1234567890\" "
                "title='Human verification challenge'></iframe></div></body></html>"
            )

    class _Session:
        def __init__(self):
            self.allow_human_intervention = True
            self.human_intervention_mode = "assist"
            self.last_resort_manual_when_disabled = False
            self.headless = False
            self.page = _Page()
            self.manual_intervention_timeout_sec = 120
            self._manual_events = []
            self.manual_intervention_event_hook = (
                lambda payload, *_args: self._manual_events.append(dict(payload))
            )

    session = _Session()
    out = BrowserSession.allow_manual_verification_intervention(  # noqa: SLF001
        session,
        reason="skyscanner_interstitial_retry_1",
        wait_sec=10,
    )

    assert out["used"] is True
    assert out["reason"] == "manual_window_elapsed"
    extend_events = [evt for evt in session._manual_events if evt.get("stage") == "extend"]
    assert len(extend_events) >= 1
    assert out["captcha_token_change_count"] >= 2


def test_manual_intervention_demo_mode_does_not_exit_on_clearance():
    class _Page:
        def __init__(self):
            self.url = (
                "https://www.skyscanner.com/sttc/px/captcha-v2/index.html"
                "?url=L2ZsaWdodHM/&uuid=u123&vid=v123"
            )
            self._tick = 0

        def title(self):
            return "Skyscanner"

        def bring_to_front(self):
            return None

        def wait_for_timeout(self, timeout_ms):  # noqa: ARG002
            self._tick += 1
            if self._tick >= 1:
                self.url = "https://www.skyscanner.com/flights"

        def is_closed(self):
            return False

        def evaluate(self, script):  # noqa: ARG002
            return {}

        def content(self):
            return "<html></html>"

    class _Session:
        def __init__(self):
            self.allow_human_intervention = True
            self.human_intervention_mode = "demo"
            self.last_resort_manual_when_disabled = False
            self.headless = False
            self.page = _Page()
            self.manual_intervention_timeout_sec = 120

    session = _Session()
    out = BrowserSession.allow_manual_verification_intervention(  # noqa: SLF001
        session,
        reason="scenario_demo_mode_attempt_1_turn_1",
        wait_sec=10,
    )

    assert out["used"] is True
    assert out["reason"] == "manual_window_elapsed"
    assert out["challenge_cleared_during_window"] is True
    assert out["page_url_after"] == "https://www.skyscanner.com/flights"


def test_recover_page_after_target_closed_rebinds_and_navigates(monkeypatch):
    class _Page:
        def __init__(self, closed=False, url="about:blank"):
            self._closed = bool(closed)
            self.url = url
            self.goto_calls = []

        def is_closed(self):
            return self._closed

        def goto(self, url, wait_until=None, timeout=None):  # noqa: ARG002
            self.goto_calls.append(str(url))
            self.url = str(url)
            self._closed = False

    class _Context:
        def __init__(self):
            self.pages = [_Page(closed=True, url="https://www.skyscanner.com/sttc/px/captcha-v2/index.html")]

        def new_page(self):
            page = _Page(closed=False, url="about:blank")
            self.pages.append(page)
            return page

    class _Session:
        def __init__(self):
            self.context = _Context()
            self.page = self.context.pages[0]
            self.block_heavy_resources = False
            self.goto_timeout_ms = 15000
            self.goto_commit_timeout_ms = 9000
            self.action_timeout_ms = 8000
            self.wait_timeout_ms = 8000
            self.human_mimic = True

        def _route_filter(self, route):  # noqa: ARG002
            return None

        def _sleep_action_delay(self):
            return None

        def _human_scan_page(self):
            return None

        def goto(self, url):
            return BrowserSession.goto(self, url)  # noqa: SLF001

    out = BrowserSession.recover_page_after_target_closed(  # noqa: SLF001
        _Session(),
        preferred_url="https://www.skyscanner.com/flights",
    )

    assert out["attempted"] is True
    assert out["recovered"] is True
    assert out["reason"] == "recovered"
    assert out["opened_new_page"] is True
    assert out["final_url"] == "https://www.skyscanner.com/flights"


def test_human_mimic_interstitial_grace_retries_press_hold_probe_when_ui_appears_late(monkeypatch):
    class _Mouse:
        def move(self, x, y, steps=1):  # noqa: ARG002
            return None

        def down(self):
            return None

        def up(self):
            return None

        def wheel(self, dx, dy):  # noqa: ARG002
            return None

    class _Page:
        def __init__(self):
            self.mouse = _Mouse()
            self._eval_calls = 0
            self.waits = []
            self.viewport_size = {"width": 1366, "height": 900}

        def evaluate(self, script, arg=None):  # noqa: ARG002
            if "press & hold" in str(script).lower():
                self._eval_calls += 1
                if self._eval_calls < 2:
                    return None  # challenge not rendered yet
                return {"x": 400, "y": 300, "w": 200, "h": 70, "text": "press & hold"}
            return None

        def wait_for_timeout(self, timeout_ms):
            self.waits.append(int(timeout_ms))

    class _Session:
        def __init__(self):
            self.page = _Page()
            self.human_mimic = True
            self._last_interstitial_grace_meta = {}

        def human_mimic_press_and_hold_challenge(self, max_hold_ms=1800):  # noqa: ARG002
            return BrowserSession.human_mimic_press_and_hold_challenge(self, max_hold_ms=max_hold_ms)  # noqa: SLF001

    monkeypatch.setattr("core.browser.session.random.randint", lambda a, b: a)
    monkeypatch.setattr("core.browser.session.random.random", lambda: 0.0)
    monkeypatch.setattr("core.browser.verification_challenges.random.randint", lambda a, b: a)
    monkeypatch.setattr("core.browser.verification_challenges.random.random", lambda: 0.0)

    session = _Session()
    BrowserSession.human_mimic_interstitial_grace(session, duration_ms=2200)  # noqa: SLF001

    assert session._last_interstitial_grace_meta["press_hold_probe_attempts"] >= 2
    assert session._last_interstitial_grace_meta["press_hold_executed"] is True


def test_human_mimic_press_and_hold_challenge_uses_px_container_fallback(monkeypatch):
    class _Mouse:
        def __init__(self):
            self.events = []

        def move(self, x, y, steps=1):  # noqa: ARG002
            self.events.append(("move", round(x, 1), round(y, 1)))

        def down(self):
            self.events.append(("down",))

        def up(self):
            self.events.append(("up",))

    class _Page:
        def __init__(self):
            self.mouse = _Mouse()
            self.waits = []

        def evaluate(self, script, arg=None):  # noqa: ARG002
            s = str(script).lower()
            assert "press & hold" in s
            assert "px-captcha" in s
            return {"x": 420, "y": 360, "w": 310, "h": 102, "text": "px-captcha", "kind": "px_container"}

        def wait_for_timeout(self, timeout_ms):
            self.waits.append(int(timeout_ms))

    class _Session:
        def __init__(self):
            self.page = _Page()
            self.human_mimic = True

    monkeypatch.setattr("core.browser.session.random.randint", lambda a, b: a)

    session = _Session()
    ok = BrowserSession.human_mimic_press_and_hold_challenge(session, max_hold_ms=900)  # noqa: SLF001

    assert ok is True
    assert ("down",) in session.page.mouse.events
    assert ("up",) in session.page.mouse.events


def test_human_mimic_interstitial_grace_skips_pointer_choreography_when_px_shell_present(monkeypatch):
    class _Mouse:
        def __init__(self):
            self.moves = 0

        def move(self, x, y, steps=1):  # noqa: ARG002
            self.moves += 1

        def down(self):
            return None

        def up(self):
            return None

        def wheel(self, dx, dy):  # noqa: ARG002
            return None

    class _Page:
        def __init__(self):
            self.mouse = _Mouse()
            self.viewport_size = {"width": 1366, "height": 900}
            self._press_probe_calls = 0

        def evaluate(self, script, arg=None):  # noqa: ARG002
            s = str(script).lower()
            if "press & hold" in s:
                self._press_probe_calls += 1
                if self._press_probe_calls == 1:
                    return None
                return {"x": 420, "y": 360, "w": 310, "h": 102, "text": "px-captcha", "kind": "px_container"}
            if "px-captcha" in s and "person or a robot" in s:
                return True
            return None

        def wait_for_timeout(self, timeout_ms):  # noqa: ARG002
            return None

    class _Session:
        def __init__(self):
            self.page = _Page()
            self.human_mimic = True
            self._last_interstitial_grace_meta = {}

        def human_mimic_press_and_hold_challenge(self, max_hold_ms=1800):  # noqa: ARG002
            return BrowserSession.human_mimic_press_and_hold_challenge(self, max_hold_ms=max_hold_ms)  # noqa: SLF001

    monkeypatch.setattr("core.browser.session.random.randint", lambda a, b: a)
    monkeypatch.setattr("core.browser.session.random.random", lambda: 0.0)
    monkeypatch.setattr("core.browser.verification_challenges.random.randint", lambda a, b: a)
    monkeypatch.setattr("core.browser.verification_challenges.random.random", lambda: 0.0)

    session = _Session()
    BrowserSession.human_mimic_interstitial_grace(session, duration_ms=2200)  # noqa: SLF001

    assert session.page.mouse.moves == 1  # only the actual press-hold targeting move, no choreography moves
    assert session._last_interstitial_grace_meta["press_hold_executed"] is True


def test_human_mimic_interstitial_grace_uses_long_hold_budget_when_px_iframe_visible(monkeypatch):
    class _Mouse:
        def move(self, x, y, steps=1):  # noqa: ARG002
            return None

        def down(self):
            return None

        def up(self):
            return None

        def wheel(self, dx, dy):  # noqa: ARG002
            return None

    class _Page:
        def __init__(self):
            self.mouse = _Mouse()
            self.viewport_size = {"width": 1366, "height": 900}
            self.url = "https://www.skyscanner.com/sttc/px/captcha-v2/index.html"
            self.waits = []

        def evaluate(self, script, arg=None):  # noqa: ARG002
            s = str(script)
            s_low = s.lower()
            if "px_iframe_visible" in s and "hidden_human_iframe" in s:
                return {
                    "px_shell_present": False,
                    "px_root_visible": False,
                    "px_iframe_total": 1,
                    "px_iframe_visible": 1,
                    "hidden_human_iframe": False,
                    "press_hold_text_visible": False,
                    "loader_dots_visible": False,
                }
            if "px-captcha" in s_low and "person or a robot" in s_low:
                return False
            return None

        def wait_for_timeout(self, timeout_ms):
            self.waits.append(int(timeout_ms))

    class _Session:
        def __init__(self):
            self.page = _Page()
            self.human_mimic = True
            self._last_interstitial_grace_meta = {}
    hold_ms_seen = []

    monkeypatch.setattr("core.browser.session.random.randint", lambda a, b: a)
    monkeypatch.setattr("core.browser.session.random.random", lambda: 0.0)
    monkeypatch.setattr("core.browser.verification_challenges.random.randint", lambda a, b: a)
    monkeypatch.setattr("core.browser.verification_challenges.random.random", lambda: 0.0)
    monkeypatch.setattr(
        "core.browser.verification_challenges.VerificationChallengeHelper.human_mimic_press_and_hold_challenge",
        lambda self, max_hold_ms=1800: hold_ms_seen.append(int(max_hold_ms)) or True,
    )

    session = _Session()
    BrowserSession.human_mimic_interstitial_grace(session, duration_ms=14000)  # noqa: SLF001

    assert hold_ms_seen
    assert max(hold_ms_seen) >= 9000
    assert session._last_interstitial_grace_meta["press_hold_executed"] is True


def test_human_mimic_interstitial_grace_nudges_px_shell_when_iframe_hidden(monkeypatch):
    class _Mouse:
        def __init__(self):
            self.events = []

        def move(self, x, y, steps=1):  # noqa: ARG002
            self.events.append(("move", round(x, 1), round(y, 1)))

        def down(self):
            self.events.append(("down",))

        def up(self):
            self.events.append(("up",))

        def wheel(self, dx, dy):  # noqa: ARG002
            self.events.append(("wheel", dx, dy))

    class _Page:
        def __init__(self):
            self.mouse = _Mouse()
            self.viewport_size = {"width": 1366, "height": 900}
            self.waits = []
            self._press_probe_calls = 0

        def evaluate(self, script, arg=None):  # noqa: ARG002
            s = str(script)
            s_low = s.lower()
            if "px_iframe_visible" in s and "hidden_human_iframe" in s:
                return {
                    "px_shell_present": True,
                    "px_root_visible": True,
                    "px_iframe_total": 1,
                    "px_iframe_visible": 0,
                    "hidden_human_iframe": True,
                }
            if "press & hold" in s_low:
                self._press_probe_calls += 1
                return None
            if "person or a robot" in s_low and "px-captcha" in s_low:
                return True
            if "return { x: rect.x + (rect.width / 2)" in s:
                return {"x": 420, "y": 360}
            return None

        def wait_for_timeout(self, timeout_ms):
            self.waits.append(int(timeout_ms))

    class _Session:
        def __init__(self):
            self.page = _Page()
            self.human_mimic = True
            self._last_interstitial_grace_meta = {}

        def human_mimic_press_and_hold_challenge(self, max_hold_ms=1800):  # noqa: ARG002
            return False

    monkeypatch.setattr("core.browser.session.random.randint", lambda a, b: a)
    monkeypatch.setattr("core.browser.session.random.random", lambda: 0.0)

    session = _Session()
    BrowserSession.human_mimic_interstitial_grace(session, duration_ms=2200)  # noqa: SLF001

    assert session._last_interstitial_grace_meta["px_shell_nudged"] is True
    assert ("down",) in session.page.mouse.events
    assert ("up",) in session.page.mouse.events


def test_human_mimic_interstitial_grace_nudges_when_iframe_non_visible_without_hidden_flag(monkeypatch):
    class _Mouse:
        def __init__(self):
            self.events = []

        def move(self, x, y, steps=1):  # noqa: ARG002
            self.events.append(("move", round(x, 1), round(y, 1)))

        def down(self):
            self.events.append(("down",))

        def up(self):
            self.events.append(("up",))

        def wheel(self, dx, dy):  # noqa: ARG002
            self.events.append(("wheel", dx, dy))

    class _Page:
        def __init__(self):
            self.mouse = _Mouse()
            self.viewport_size = {"width": 1366, "height": 900}
            self.waits = []

        def evaluate(self, script, arg=None):  # noqa: ARG002
            s = str(script)
            s_low = s.lower()
            if "px_iframe_visible" in s and "hidden_human_iframe" in s:
                return {
                    "px_shell_present": True,
                    "px_root_visible": True,
                    "px_iframe_total": 1,
                    "px_iframe_visible": 0,
                    "hidden_human_iframe": False,
                }
            if "press & hold" in s_low:
                return None
            if "person or a robot" in s_low and "px-captcha" in s_low:
                return True
            if "f.style.display = \"block\"" in s:
                return None
            if "return { x: rect.x + (rect.width / 2)" in s:
                return {"x": 420, "y": 360}
            return None

        def wait_for_timeout(self, timeout_ms):
            self.waits.append(int(timeout_ms))

    class _Session:
        def __init__(self):
            self.page = _Page()
            self.human_mimic = True
            self._last_interstitial_grace_meta = {}

        def human_mimic_press_and_hold_challenge(self, max_hold_ms=1800):  # noqa: ARG002
            return False

    monkeypatch.setattr("core.browser.session.random.randint", lambda a, b: a)
    monkeypatch.setattr("core.browser.session.random.random", lambda: 0.0)

    session = _Session()
    BrowserSession.human_mimic_interstitial_grace(session, duration_ms=2200)  # noqa: SLF001

    assert session._last_interstitial_grace_meta["px_shell_nudged"] is True
    assert ("down",) in session.page.mouse.events
    assert ("up",) in session.page.mouse.events


def test_human_mimic_interstitial_grace_escalates_with_px_container_hold(monkeypatch):
    class _Mouse:
        def __init__(self):
            self.events = []

        def move(self, x, y, steps=1):  # noqa: ARG002
            self.events.append(("move", round(x, 1), round(y, 1)))

        def down(self):
            self.events.append(("down",))

        def up(self):
            self.events.append(("up",))

        def wheel(self, dx, dy):  # noqa: ARG002
            self.events.append(("wheel", dx, dy))

    class _Page:
        def __init__(self):
            self.mouse = _Mouse()
            self.viewport_size = {"width": 1366, "height": 900}
            self.waits = []

        def evaluate(self, script, arg=None):  # noqa: ARG002
            s = str(script)
            s_low = s.lower()
            if "px_iframe_visible" in s and "hidden_human_iframe" in s:
                return {
                    "px_shell_present": True,
                    "px_root_visible": True,
                    "px_iframe_total": 1,
                    "px_iframe_visible": 0,
                    "hidden_human_iframe": False,
                }
            if "press & hold" in s_low:
                return None
            if "person or a robot" in s_low and "px-captcha" in s_low:
                return True
            if "return { x: rect.x + (rect.width / 2)" in s:
                return {"x": 420, "y": 360}
            return None

        def wait_for_timeout(self, timeout_ms):
            self.waits.append(int(timeout_ms))

    class _Session:
        def __init__(self):
            self.page = _Page()
            self.human_mimic = True
            self._last_interstitial_grace_meta = {}

        def human_mimic_press_and_hold_challenge(self, max_hold_ms=1800):  # noqa: ARG002
            return False

    monkeypatch.setattr("core.browser.session.random.randint", lambda a, b: a)
    monkeypatch.setattr("core.browser.session.random.random", lambda: 0.0)

    session = _Session()
    BrowserSession.human_mimic_interstitial_grace(session, duration_ms=2200)  # noqa: SLF001

    assert session._last_interstitial_grace_meta["px_container_hold_attempted"] is True
    assert session._last_interstitial_grace_meta["px_container_hold_executed"] is True
    assert ("down",) in session.page.mouse.events
    assert ("up",) in session.page.mouse.events


def test_human_mimic_interstitial_grace_uses_vision_guided_bbox_press(monkeypatch):
    class _Mouse:
        def __init__(self):
            self.events = []

        def move(self, x, y, steps=1):  # noqa: ARG002
            self.events.append(("move", round(x, 1), round(y, 1)))

        def down(self):
            self.events.append(("down",))

        def up(self):
            self.events.append(("up",))

        def wheel(self, dx, dy):  # noqa: ARG002
            self.events.append(("wheel", dx, dy))

    class _Page:
        def __init__(self):
            self.mouse = _Mouse()
            self.viewport_size = {"width": 1366, "height": 900}
            self.waits = []

        def evaluate(self, script, arg=None):  # noqa: ARG002
            s = str(script)
            s_low = s.lower()
            if "px_iframe_visible" in s and "hidden_human_iframe" in s:
                return {
                    "px_shell_present": True,
                    "px_root_visible": True,
                    "px_iframe_total": 1,
                    "px_iframe_visible": 0,
                    "hidden_human_iframe": False,
                }
            if "press & hold" in s_low:
                return None
            if "person or a robot" in s_low and "px-captcha" in s_low:
                return True
            if "return { x: rect.x + (rect.width / 2)" in s:
                return {"x": 420, "y": 360}
            return None

        def screenshot(self, **kwargs):  # noqa: ARG002
            return b"fake_png"

        def content(self):
            return "<html><body>px-captcha</body></html>"

        def wait_for_timeout(self, timeout_ms):
            self.waits.append(int(timeout_ms))

    class _Session:
        def __init__(self):
            self.page = _Page()
            self.human_mimic = True
            self._last_interstitial_grace_meta = {}

        def human_mimic_press_and_hold_challenge(self, max_hold_ms=1800):  # noqa: ARG002
            return False

    monkeypatch.setattr("core.browser.session.random.randint", lambda a, b: a)
    monkeypatch.setattr("core.browser.session.random.random", lambda: 0.0)
    monkeypatch.setattr(
        "core.browser.verification_challenges._extract_verification_action_with_vision_light",
        lambda screenshot_b64, html_hint="": {
            "protector_label": "interstitial_press_hold",
            "solution": "bbox hold",
            "target_bbox": [0.2, 0.2, 0.4, 0.2],
            "confidence": "high",
        },
    )

    session = _Session()
    BrowserSession.human_mimic_interstitial_grace(session, duration_ms=2200)  # noqa: SLF001

    assert session._last_interstitial_grace_meta["vision_guided_press_attempted"] is True
    assert session._last_interstitial_grace_meta["vision_guided_press_executed"] is True
