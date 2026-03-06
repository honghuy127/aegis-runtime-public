"""Page navigation and inspection helpers for BrowserSession."""

import time
from utils.logging import get_logger


log = get_logger(__name__)


class PageInteractionHelper:
    """Encapsulates page navigation, inspection, and screenshot logic."""

    def __init__(self, page, browser_session):
        """
        Initialize page interaction helper.

        Args:
            page: Playwright Page object
            browser_session: Parent BrowserSession instance (for timeout params and callbacks)
        """
        self.page = page
        self.browser_session = browser_session

    def goto(self, url: str):
        """Navigate to a URL and wait for initial page load."""
        if hasattr(
            self.browser_session,
            "_assert_automation_allowed_during_manual_intervention",
        ):
            self.browser_session._assert_automation_allowed_during_manual_intervention(
                "goto",
                str(url or ""),
            )
        if hasattr(self.browser_session, "_record_manual_automation_action"):
            self.browser_session._record_manual_automation_action("goto", str(url or ""))
        try:
            self.page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=self.browser_session.goto_timeout_ms
            )
        except Exception as exc:
            is_timeout_like = (
                "timeout" in str(exc).lower() or "err_timed_out" in str(exc).lower()
            )
            if not is_timeout_like:
                raise
            # Some sites keep the DOM event pending under heavy trackers/captcha scripts.
            # Accept a committed navigation as fallback so scenario logic can continue.
            commit_timeout = max(
                1,
                min(
                    self.browser_session.goto_timeout_ms,
                    self.browser_session.goto_commit_timeout_ms
                )
            )
            self.page.goto(url, wait_until="commit", timeout=commit_timeout)
        # NOTE: Respect target-site TOS and only run authorized automation.
        self.browser_session._human_scan_page()
        self.browser_session._sleep_action_delay()

    def content(self) -> str:
        """Return the current page HTML snapshot."""
        return self.page.content()

    def screenshot(self, path: str, *, full_page: bool = True):
        """Capture a PNG screenshot of the current page."""
        if self.page is None:
            return
        self.page.screenshot(path=path, full_page=full_page)

    def setup_route_filter(self):
        """Setup request interception to handle heavy resources."""
        def _route_filter(route):
            # Adapted from BrowserSession._route_filter
            request = route.request
            url = str(request.url or "").lower()
            method = str(request.method or "GET").upper()

            # Block heavy resources if configured
            if self.browser_session.block_heavy_resources:
                # Block video/audio/large assets to reduce bandwidth and latency
                if any(
                    pat in url
                    for pat in (
                        ".mp4", ".webm", ".mov", ".m3u8",
                        ".mp3", ".aac", ".wav", ".m4a",
                        ".woff2", ".woff", ".ttf", ".eot",
                    )
                ):
                    return route.abort()

            # Default: allow request
            route.continue_()

        self.page.route("**/*", _route_filter)
