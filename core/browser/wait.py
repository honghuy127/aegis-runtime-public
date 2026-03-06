"""Element waiting and frame selection helpers for BrowserSession."""

import time
from urllib.parse import urlparse
from utils.logging import get_logger


log = get_logger(__name__)


class ElementWaitHelper:
    """Encapsulates element waiting and frame selection logic."""

    def __init__(self, browser_session):
        """
        Initialize element wait helper.

        Args:
            browser_session: Parent BrowserSession instance
        """
        self.session = browser_session

    def child_frames(self):
        """Return non-main frames for iframe fallback interactions."""
        if self.session.page is None:
            return []
        main = self.session.page.main_frame
        return [frame for frame in self.session.page.frames if frame != main]

    def candidate_frames(self):
        """Return ranked child frames for fallback attempts."""
        frames = self.child_frames()
        if not frames:
            return []
        page_host = ""
        try:
            page_host = (urlparse(self.session.page.url).hostname or "").lower()
        except Exception:
            page_host = ""

        bad_tokens = (
            "googleads",
            "doubleclick",
            "googletagmanager",
            "analytics",
            "adservice",
            "ladsp",
            "facebook",
        )
        good_tokens = ("flight", "air", "search", "trip", "booking", "reserve", "result")

        ranked = []
        for idx, frame in enumerate(frames):
            score = 0
            try:
                furl = (frame.url or "").lower()
                host = (urlparse(furl).hostname or "").lower()
            except Exception:
                furl = ""
                host = ""

            if page_host and host and host.endswith(page_host):
                score += 8
            if furl.startswith("about:blank"):
                score -= 3
            if any(token in furl for token in bad_tokens):
                score -= 6
            if any(token in furl for token in good_tokens):
                score += 3
            ranked.append((score, idx, frame))

        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [frame for _, _, frame in ranked[: self.session.MAX_FRAME_FALLBACKS]]

    def wait(self, selector: str, timeout_ms: int = None):
        """Block until the selector appears or timeout is reached."""
        if hasattr(
            self.session,
            "_assert_automation_allowed_during_manual_intervention",
        ):
            self.session._assert_automation_allowed_during_manual_intervention(
                "wait",
                str(selector or ""),
            )
        if hasattr(self.session, "_record_manual_automation_action"):
            self.session._record_manual_automation_action("wait", str(selector or ""))
        timeout = self.session.wait_timeout_ms if timeout_ms is None else int(timeout_ms)
        if timeout < 100:
            log.warning(
                "browser.wait.low_timeout selector=%s timeout_ms=%d (expected >= 100ms)",
                selector[:100] if selector else "",
                timeout,
            )

        deadline = self.session._start_deadline(timeout)
        deadline_ms = max(0, int((deadline - time.monotonic()) * 1000))
        log.info(
            "browser.wait.start selector=%s timeout_ms=%d deadline_ms=%d human_mimic=%s",
            selector[:100] if selector else "",
            timeout,
            deadline_ms,
            self.session.human_mimic,
        )
        try:
            self.session._ensure_deadline_not_exceeded(deadline, "wait")
            self.session._log_low_remaining_ms(
                action="wait.page",
                selector=selector,
                timeout_ms=timeout,
                deadline=deadline,
                attempt="page",
            )
            self.session.page.wait_for_selector(
                selector,
                timeout=self.session._remaining_timeout_ms(deadline),
                state="visible",
            )
        except Exception as page_exc:
            self.session._reraise_interrupt(page_exc)
            found = False
            last_exc = page_exc
            for frame in self.candidate_frames():
                try:
                    self.session._ensure_deadline_not_exceeded(deadline, "wait")
                    self.session._log_low_remaining_ms(
                        action="wait.frame",
                        selector=selector,
                        timeout_ms=timeout,
                        deadline=deadline,
                        attempt="frame",
                    )
                    frame.wait_for_selector(
                        selector,
                        timeout=self.session._remaining_timeout_ms(deadline),
                        state="visible",
                    )
                    found = True
                    break
                except Exception as frame_exc:
                    self.session._reraise_interrupt(frame_exc)
                    last_exc = frame_exc
                    continue
            if not found:
                raise last_exc
        self.session._sleep_action_delay()
