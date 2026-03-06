"""Typing and keyboard input helpers for BrowserSession."""

import random
from utils.logging import get_logger


log = get_logger(__name__)

# Browser action settle delays (milliseconds) - imported from browser.py
DEFAULT_SETTLE_WAIT_30_MS = 30
DEFAULT_SETTLE_WAIT_40_MS = 40


class TypingInputHelper:
    """Encapsulates typing and keyboard input logic."""

    def __init__(self, browser_session):
        """
        Initialize typing input helper.

        Args:
            browser_session: Parent BrowserSession instance
        """
        self.session = browser_session

    def typing_delay(self) -> int:
        """Return per-keystroke delay in milliseconds."""
        return random.randint(
            self.session.min_typing_delay_ms, self.session.max_typing_delay_ms
        )

    def type_active(self, value: str, timeout_ms: int = None):
        """Type into the currently focused control as a selector-free fallback."""
        if hasattr(
            self.session,
            "_assert_automation_allowed_during_manual_intervention",
        ):
            self.session._assert_automation_allowed_during_manual_intervention(
                "type_active",
                "",
            )
        if hasattr(self.session, "_record_manual_automation_action"):
            self.session._record_manual_automation_action("type_active", "")
        timeout = (
            self.session.action_timeout_ms if timeout_ms is None else int(timeout_ms)
        )
        deadline = self.session._start_deadline(timeout)
        if not isinstance(value, str):
            return
        if self.session.page is None:
            return

        def _active_is_typable() -> bool:
            return bool(
                self.session.page.evaluate(
                    """
                    () => {
                      const el = document.activeElement;
                      if (!el) return false;
                      const tag = (el.tagName || '').toLowerCase();
                      const role = (el.getAttribute('role') || '').toLowerCase();
                      const type = (el.getAttribute('type') || '').toLowerCase();
                      if (tag === 'input') return type !== 'hidden' && !el.disabled;
                      if (tag === 'textarea') return !el.disabled;
                      if (el.isContentEditable) return true;
                      if (role === 'textbox' || role === 'searchbox' || role === 'combobox') return true;
                      return false;
                    }
                    """
                )
            )

        can_type = _active_is_typable()
        if not can_type:
            # Some booking UIs focus a wrapper first; advance focus to a typable control.
            for _ in range(8):
                self.session.page.keyboard.press("Tab")
                self.session.page.wait_for_timeout(DEFAULT_SETTLE_WAIT_30_MS)
                if _active_is_typable():
                    can_type = True
                    break
        if not can_type:
            raise ValueError("no_active_typing_target")
        self.session.page.wait_for_timeout(DEFAULT_SETTLE_WAIT_40_MS)
        self.session.page.keyboard.press("ControlOrMeta+A")
        self.session.page.keyboard.press("Backspace")
        if self.session.human_mimic:
            self.session.page.keyboard.type(value, delay=self.typing_delay())
        else:
            self.session.page.keyboard.type(value, delay=0)
        if not self.session._deadline_exceeded(deadline):
            self.session.page.keyboard.press("Enter")
        self.session._sleep_action_delay()
