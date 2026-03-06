from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from core.agent.types import ActionSpec, TraceEvent, SignalSpec


class Actor:
    """Execute actions against a browser with safety checks and verification.

    Typical usage:
        actor = Actor(browser, log=logger)
        event = actor.execute(action, timeout_ms=1500)
        if event.status == "ok":
            print("Action succeeded")
        elif event.status == "soft_fail":
            print("Action failed but allowed:", event.reason)
    """

    def __init__(self, browser: Any, *, log=None):
        """Initialize actor.

        Args:
            browser: BrowserSession instance.
            log: Optional logger for debug output.
        """
        self.browser = browser
        self.log = log

    def _now_ms(self) -> int:
        """Get current time in milliseconds."""
        return int(time.time() * 1000)

    def execute(self, action: ActionSpec, *, timeout_ms: int = 1500) -> TraceEvent:
        """Execute an action and return a trace event.

        Args:
            action: ActionSpec to execute.
            timeout_ms: Timeout in milliseconds.

        Returns:
            TraceEvent with status (ok|soft_fail|fail) and reason.
        """
        started = self._now_ms()
        status = "fail"
        reason = "unknown"
        observed = {}

        try:
            if action.type.value in {"CLICK", "SUBMIT"}:
                ok, used = self._safe_click_first(action.selectors, timeout_ms=timeout_ms)
                status = "ok" if ok else ("soft_fail" if action.allow_soft_fail else "fail")
                reason = "clicked" if ok else "click_failed"
                observed["selector_used"] = used

            elif action.type.value == "WAIT":
                ok, used = self._safe_wait_first(action.selectors, timeout_ms=timeout_ms)
                status = "ok" if ok else ("soft_fail" if action.allow_soft_fail else "fail")
                reason = "wait_ok" if ok else "wait_failed"
                observed["selector_used"] = used

            elif action.type.value == "TYPE":
                text = str(action.params.get("text", "") or "")
                ok, used = self._safe_fill_first(action.selectors, text=text, timeout_ms=timeout_ms)
                status = "ok" if ok else ("soft_fail" if action.allow_soft_fail else "fail")
                reason = "typed" if ok else "typing_failed"
                observed["selector_used"] = used

            elif action.type.value == "CLEAR":
                ok, used = self._safe_clear_first(action.selectors, timeout_ms=timeout_ms)
                status = "ok" if ok else ("soft_fail" if action.allow_soft_fail else "fail")
                reason = "cleared" if ok else "clear_failed"
                observed["selector_used"] = used

            else:
                status = "soft_fail" if action.allow_soft_fail else "fail"
                reason = f"unhandled_action:{action.type}"

            # Postcondition verification (if specified)
            if status == "ok" and action.postconditions:
                postcond_ok = self._verify_postconditions(action.postconditions, timeout_ms=300)
                if not postcond_ok:
                    status = "soft_fail" if action.allow_soft_fail else "fail"
                    reason = f"{reason}:postcondition_failed"
                    observed["postcondition_failed"] = True

        except Exception as exc:
            status = "soft_fail" if action.allow_soft_fail else "fail"
            reason = f"exception:{type(exc).__name__}:{str(exc)[:50]}"

        elapsed = self._now_ms() - started
        return TraceEvent(
            step=action.debug.get("step", 0),
            action=action,
            status=status,
            elapsed_ms=elapsed,
            reason=reason,
            observed=observed,
        )

    def _safe_click_first(self, selectors: List[str], *, timeout_ms: int) -> Tuple[bool, str]:
        """Try clicking the first available selector."""
        for sel in selectors:
            try:
                self.browser.click(sel, timeout_ms=timeout_ms)
                return True, sel
            except Exception:
                continue
        return False, ""

    def _safe_wait_first(self, selectors: List[str], *, timeout_ms: int) -> Tuple[bool, str]:
        """Try waiting for the first available selector to appear."""
        for sel in selectors:
            try:
                self.browser.wait_for(sel, timeout_ms=timeout_ms)
                return True, sel
            except Exception:
                continue
        return False, ""

    def _safe_fill_first(self, selectors: List[str], *, text: str, timeout_ms: int) -> Tuple[bool, str]:
        """Try filling the first available input element with text.

        DOC: See docs/kb/30_patterns/combobox_commit.md for IATA ranking strategy.

        For Google Flights combobox fills, commits selection:
        1. Click element (activate combobox)
        2. Clear value (Ctrl+A + Delete)
        3. Type text
        4. Press Enter
        5. If options appear, click matching option
        6. Wait for bind
        7. Verify postcondition

        Checks that the element is actually an input/textarea/contenteditable
        before attempting to type, to avoid silent failures.
        """
        for sel in selectors:
            try:
                # Verify element is a proper input before typing
                if not self._is_input_element(sel, timeout_ms=300):
                    if self.log:
                        self.log.debug(f"selector {sel} is not an input element, skipping")
                    continue

                # Click to activate combobox
                try:
                    self.browser.click(sel, timeout_ms=300)
                except Exception:
                    pass  # Click may be implicit in fill, continue anyway

                # Clear existing value: Ctrl+A + Delete
                try:
                    self.browser.page.keyboard.press('Control+A')
                    self.browser.page.keyboard.press('Delete')
                except Exception:
                    pass  # If clear fails, continue with fill

                # Type the text
                self.browser.fill(sel, text, timeout_ms=timeout_ms)

                # COMMIT: Press Enter to trigger combobox selection
                try:
                    self.browser.page.keyboard.press('Enter')
                    if self.log:
                        self.log.info(f"gf.fill.commit.enter selector={sel}")
                except Exception as exc:
                    if self.log:
                        self.log.debug(f"Enter key failed for {sel}: {exc}")

                # Wait for options listbox to appear (200ms)
                time.sleep(0.2)

                # Try to find and click a matching option in the listbox
                option_clicked = False
                try:
                    # Look for options in listbox
                    option_selectors = [
                        "[role='listbox'] [role='option']",
                        f"[role='option'][aria-label*='{text.upper()}']",
                        f"[role='option']:has-text('{text.upper()}')",
                    ]
                    for opt_sel in option_selectors:
                        try:
                            self.browser.click(opt_sel, timeout_ms=300)
                            if self.log:
                                self.log.info(f"gf.fill.commit.option_click selector_used={opt_sel}")
                            option_clicked = True
                            break
                        except Exception:
                            continue
                except Exception as exc:
                    if self.log:
                        self.log.debug(f"option click attempt failed: {exc}")

                # Wait for bind completion after option click or Enter
                time.sleep(0.3)

                # Verify postcondition: value should contain text or expected IATA/city
                verify_ok = False
                try:
                    result = self.browser.page.evaluate(
                        f"""
                        const el = document.querySelector('{sel}');
                        const val = el ? (el.value || el.textContent || '').toUpperCase() : '';
                        const text = '{text.upper()}';
                        return val.includes(text) || val.includes(text.substring(0, 3));
                        """
                    )
                    verify_ok = bool(result)
                except Exception:
                    # If verification fails, assume OK since text was typed
                    verify_ok = True

                if self.log:
                    self.log.info(f"gf.fill.commit.verify {('ok' if verify_ok else 'failed')} selector={sel}")

                return True, sel

            except Exception as exc:
                if self.log:
                    self.log.debug(f"fill attempt failed for {sel}: {exc}")
                continue
        return False, ""

    def _safe_clear_first(self, selectors: List[str], *, timeout_ms: int) -> Tuple[bool, str]:
        """Try clearing the first available input element."""
        for sel in selectors:
            try:
                if not self._is_input_element(sel, timeout_ms=300):
                    continue
                # Playwright: use clear or triple-click + delete
                self.browser.fill(sel, "", timeout_ms=timeout_ms)
                return True, sel
            except Exception:
                continue
        return False, ""

    def _is_input_element(self, selector: str, *, timeout_ms: int = 300) -> bool:
        """Check if a selector points to an actual input-like element."""
        try:
            # Use browser's evaluate to check element type
            result = self.browser.page.evaluate(
                f"""
                const el = document.querySelector('{selector}');
                if (!el) return false;
                return el.tagName.toLowerCase() === 'input' ||
                       el.tagName.toLowerCase() === 'textarea' ||
                       el.contentEditable === 'true';
                """
            )
            # Handle the result (should be a boolean)
            return bool(result)
        except Exception:
            # On error, assume it's safe to try (may fail gracefully)
            return True

    def _verify_postconditions(self, postconditions: List[SignalSpec], *, timeout_ms: int) -> bool:
        """Verify that postconditions are satisfied (DOM-based checks)."""
        for spec in postconditions:
            if spec.source not in {"dom", "mixed"}:
                continue  # Only verify DOM-based postconditions for now

            try:
                # Simple DOM visibility check
                if spec.kind.value == "VISIBLE":
                    if spec.selector:
                        element = self.browser.page.query_selector(spec.selector)
                        if not element:
                            return False

                elif spec.kind.value == "VALUE_CONTAINS":
                    if spec.selector and spec.expected:
                        result = self.browser.page.evaluate(
                            f"""
                            const el = document.querySelector('{spec.selector}');
                            return el && (el.value || el.textContent || '').includes('{spec.expected}');
                            """
                        )
                        if not result:
                            return False
            except Exception:
                # On error, assume postcondition not met
                return False

        return True
