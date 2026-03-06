"""Form filling and input helpers for BrowserSession."""

import time
from utils.logging import get_logger


log = get_logger(__name__)


class FormFillHelper:
    """Encapsulates form filling logic with multiple fallback strategies."""

    def __init__(self, browser_session):
        """
        Initialize form fill helper.

        Args:
            browser_session: Parent BrowserSession instance
        """
        self.session = browser_session

    def _try_page_fill(self, selector: str, value: str, deadline: float, timeout_ms: int):
        """Attempt page.fill() and page_locator.fill() with force flag."""
        try:
            self.session._ensure_deadline_not_exceeded(deadline, "fill")
            self.session._log_low_remaining_ms(
                action="fill.page",
                selector=selector,
                timeout_ms=timeout_ms,
                deadline=deadline,
                attempt="page",
            )
            self.session.page.fill(
                selector,
                value,
                timeout=self.session._remaining_timeout_ms(deadline),
            )
            return True
        except Exception as page_fill_exc:
            self.session._reraise_interrupt(page_fill_exc)

        # Fallback: forced locator fill
        try:
            page_locator = self.session.page.locator(selector).first
            self.session._ensure_deadline_not_exceeded(deadline, "fill")
            self.session._log_low_remaining_ms(
                action="fill.locator",
                selector=selector,
                timeout_ms=timeout_ms,
                deadline=deadline,
                attempt="locator",
            )
            page_locator.fill(
                value,
                timeout=self.session._remaining_timeout_ms(deadline),
                force=True,
            )
            return True
        except Exception as locator_fill_exc:
            self.session._reraise_interrupt(locator_fill_exc)

    def _try_frame_fills(self, selector: str, value: str, deadline: float, timeout_ms: int):
        """Attempt frame.fill() and frame.locator().fill() across all candidate frames."""
        last_exc = None
        for frame in self.session._candidate_frames():
            # Try direct frame fill
            try:
                self.session._ensure_deadline_not_exceeded(deadline, "fill")
                self.session._log_low_remaining_ms(
                    action="fill.frame",
                    selector=selector,
                    timeout_ms=timeout_ms,
                    deadline=deadline,
                    attempt="frame",
                )
                frame.fill(
                    selector,
                    value,
                    timeout=self.session._remaining_timeout_ms(deadline),
                )
                return True
            except Exception as frame_fill_exc:
                self.session._reraise_interrupt(frame_fill_exc)
                last_exc = frame_fill_exc

            # Try forced frame locator fill
            try:
                self.session._ensure_deadline_not_exceeded(deadline, "fill")
                self.session._log_low_remaining_ms(
                    action="fill.frame_locator",
                    selector=selector,
                    timeout_ms=timeout_ms,
                    deadline=deadline,
                    attempt="frame_locator",
                )
                frame.locator(selector).first.fill(
                    value,
                    timeout=self.session._remaining_timeout_ms(deadline),
                    force=True,
                )
                return True
            except Exception as frame_locator_fill_exc:
                self.session._reraise_interrupt(frame_locator_fill_exc)
                last_exc = frame_locator_fill_exc
                continue

        if last_exc is not None:
            raise last_exc

    def _try_click_type_recovery(
        self,
        selector: str,
        value: str,
        deadline: float,
        timeout_ms: int,
    ):
        """Last resort for combobox/button-like controls: click target, then type into focused field."""
        page_locator = self.session.page.locator(selector).first
        last_exc = None
        # Try page-level click+type
        try:
            self.session._ensure_deadline_not_exceeded(deadline, "fill")
            self.session._log_low_remaining_ms(
                action="fill.recovery_wait",
                selector=selector,
                timeout_ms=timeout_ms,
                deadline=deadline,
                attempt="page",
            )
            page_locator.wait_for(
                state="visible",
                timeout=self.session._remaining_timeout_ms(deadline),
            )
            self.session._ensure_deadline_not_exceeded(deadline, "fill")
            self.session._log_low_remaining_ms(
                action="fill.recovery_click",
                selector=selector,
                timeout_ms=timeout_ms,
                deadline=deadline,
                attempt="page",
            )
            page_locator.click(
                timeout=self.session._remaining_timeout_ms(deadline),
                force=True,
            )
            self.session.page.keyboard.press("ControlOrMeta+A")
            self.session.page.keyboard.press("Backspace")
            self.session.page.keyboard.type(value, delay=0)
            return True
        except Exception as page_click_type_exc:
            self.session._reraise_interrupt(page_click_type_exc)
            last_exc = page_click_type_exc

        # Fallback: try click+type in frames
        for frame in self.session._candidate_frames():
            try:
                self.session._ensure_deadline_not_exceeded(deadline, "fill")
                self.session._log_low_remaining_ms(
                    action="fill.recovery_frame_click",
                    selector=selector,
                    timeout_ms=timeout_ms,
                    deadline=deadline,
                    attempt="frame_locator",
                )
                frame.locator(selector).first.click(
                    timeout=self.session._remaining_timeout_ms(deadline),
                    force=True,
                )
                self.session.page.keyboard.press("ControlOrMeta+A")
                self.session.page.keyboard.press("Backspace")
                self.session.page.keyboard.type(value, delay=0)
                return True
            except Exception as frame_click_type_exc:
                self.session._reraise_interrupt(frame_click_type_exc)
                last_exc = frame_click_type_exc
                continue

        if last_exc is not None:
            raise last_exc

    def fill(self, selector: str, value: str, timeout_ms: int = None):
        """Fill an input-like element selected by CSS."""
        if hasattr(
            self.session,
            "_assert_automation_allowed_during_manual_intervention",
        ):
            self.session._assert_automation_allowed_during_manual_intervention(
                "fill",
                str(selector or ""),
            )
        if hasattr(self.session, "_record_manual_automation_action"):
            self.session._record_manual_automation_action("fill", str(selector or ""))
        timeout = (
            self.session.action_timeout_ms if timeout_ms is None else int(timeout_ms)
        )
        if timeout < 100:
            log.warning(
                "browser.fill.low_timeout selector=%s timeout_ms=%d (expected >= 100ms)",
                selector[:100] if selector else "",
                timeout,
            )

        deadline = self.session._start_deadline(timeout)
        deadline_ms = max(0, int((deadline - time.monotonic()) * 1000))
        log.info(
            "browser.fill.start selector=%s timeout_ms=%d deadline_ms=%d human_mimic=%s",
            selector[:100] if selector else "",
            timeout,
            deadline_ms,
            self.session.human_mimic,
        )
        page_locator = self.session.page.locator(selector).first
        if self.session._is_hidden_input_locator(page_locator):
            raise ValueError("non_fillable_hidden_input")

        if not self.session.human_mimic:
            # Try page fill (page.fill + page_locator.fill with force)
            try:
                self._try_page_fill(selector, value, deadline, timeout)
                return
            except Exception as page_exc:
                last_exc = page_exc

            # Try frame fills
            try:
                if self._try_frame_fills(selector, value, deadline, timeout):
                    return
            except Exception as frame_exc:
                last_exc = frame_exc

            # Last resort: click+type recovery
            if not self.session._deadline_exceeded(deadline):
                try:
                    if self._try_click_type_recovery(selector, value, deadline, timeout):
                        return
                except Exception as recovery_exc:
                    last_exc = recovery_exc

            raise last_exc

        # Human mimic mode: visible wait+click+type
        locator = self.session.page.locator(selector).first
        try:
            self.session._ensure_deadline_not_exceeded(deadline, "fill")
            self.session._log_low_remaining_ms(
                action="fill.human_wait",
                selector=selector,
                timeout_ms=timeout,
                deadline=deadline,
                attempt="locator",
            )
            locator.wait_for(
                state="visible",
                timeout=self.session._remaining_timeout_ms(deadline),
            )
            self.session._ensure_deadline_not_exceeded(deadline, "fill")
            self.session._log_low_remaining_ms(
                action="fill.human_click",
                selector=selector,
                timeout_ms=timeout,
                deadline=deadline,
                attempt="locator",
            )
            locator.click(timeout=self.session._remaining_timeout_ms(deadline))
            self.session._sleep_action_delay()
            # More human-like than direct fill and more robust across masked inputs.
            self.session.page.keyboard.press("ControlOrMeta+A")
            self.session.page.keyboard.press("Backspace")
            self.session.page.keyboard.type(value, delay=self.session._typing_delay())
        except Exception as visible_fill_exc:
            self.session._reraise_interrupt(visible_fill_exc)
            last_exc = visible_fill_exc
            # Fallback for dynamic sites where the field exists but isn't visible yet.
            try:
                self.session._ensure_deadline_not_exceeded(deadline, "fill")
                self.session._log_low_remaining_ms(
                    action="fill.human_attached_wait",
                    selector=selector,
                    timeout_ms=timeout,
                    deadline=deadline,
                    attempt="locator",
                )
                locator.wait_for(
                    state="attached",
                    timeout=self.session._remaining_timeout_ms(deadline),
                )
                self.session._ensure_deadline_not_exceeded(deadline, "fill")
                self.session._log_low_remaining_ms(
                    action="fill.human_attached_fill",
                    selector=selector,
                    timeout_ms=timeout,
                    deadline=deadline,
                    attempt="locator",
                )
                locator.fill(
                    value,
                    timeout=self.session._remaining_timeout_ms(deadline),
                    force=True,
                )
            except Exception as attached_fill_exc:
                self.session._reraise_interrupt(attached_fill_exc)
                last_exc = attached_fill_exc
                # Try frame fills as final fallback
                try:
                    if not self._try_frame_fills(selector, value, deadline, timeout):
                        raise last_exc
                except Exception as frame_exc:
                    raise last_exc from frame_exc
        self.session._sleep_action_delay()

    def fill_by_keywords(self, keywords, value: str, timeout_ms: int = None) -> bool:
        """Try direct value injection into best-matching editable control by keywords."""
        if hasattr(
            self.session,
            "_assert_automation_allowed_during_manual_intervention",
        ):
            self.session._assert_automation_allowed_during_manual_intervention(
                "fill_by_keywords",
                ",".join([str(k) for k in (keywords or [])][:6]),
            )
        if hasattr(self.session, "_record_manual_automation_action"):
            self.session._record_manual_automation_action(
                "fill_by_keywords",
                ",".join([str(k) for k in (keywords or [])][:6]),
            )
        timeout = (
            self.session.action_timeout_ms if timeout_ms is None else int(timeout_ms)
        )
        deadline = self.session._start_deadline(timeout)
        if not isinstance(value, str):
            return False
        tokens = []
        for kw in keywords or []:
            if isinstance(kw, str) and kw.strip():
                tokens.append(kw.strip().lower())
        if not tokens:
            return False

        def _fill_in_frame(frame):
            return bool(
                frame.evaluate(
                    """
                    ({ keywords, value }) => {
                      const visible = (el) => {
                        if (!el) return false;
                        const style = window.getComputedStyle(el);
                        if (!style || style.visibility === 'hidden' || style.display === 'none') {
                          return false;
                        }
                        const rect = el.getBoundingClientRect();
                        return rect.width > 0 && rect.height > 0;
                      };
                      const lower = (v) => (v || '').toString().toLowerCase();
                      const fieldText = (el) => {
                        const bits = [];
                        bits.push(el.getAttribute('aria-label'));
                        bits.push(el.getAttribute('placeholder'));
                        bits.push(el.getAttribute('name'));
                        bits.push(el.getAttribute('id'));
                        bits.push(el.getAttribute('data-testid'));
                        bits.push(el.getAttribute('title'));
                        bits.push(el.textContent);
                        const id = el.getAttribute('id');
                        if (id) {
                          for (const label of document.querySelectorAll(`label[for="${id}"]`)) {
                            bits.push(label.textContent);
                          }
                        }
                        const parentLabel = el.closest('label');
                        if (parentLabel) bits.push(parentLabel.textContent);
                        return lower(bits.filter(Boolean).join(' '));
                      };

                      const selectors = [
                        'input:not([type="hidden"]):not([disabled])',
                        'textarea:not([disabled])',
                        '[contenteditable="true"]',
                      ];
                      const candidates = [];
                      for (const sel of selectors) {
                        for (const el of document.querySelectorAll(sel)) {
                          if (!visible(el)) continue;
                          candidates.push(el);
                        }
                      }

                      let best = null;
                      let bestScore = -1;
                      for (const el of candidates) {
                        const text = fieldText(el);
                        let score = 0;
                        for (const kw of keywords) {
                          if (!kw) continue;
                          if (text.includes(kw)) score += kw.length > 2 ? 3 : 1;
                        }
                        if (score > bestScore) {
                          best = el;
                          bestScore = score;
                        }
                      }
                      if (!best || bestScore <= 0) return false;
                      best.scrollIntoView({ block: 'center', inline: 'center' });
                      best.focus();
                      best.click();

                      const tag = lower(best.tagName);
                      if (tag === 'input' || tag === 'textarea') {
                        best.value = value;
                        best.dispatchEvent(new Event('input', { bubbles: true }));
                        best.dispatchEvent(new Event('change', { bubbles: true }));
                        return true;
                      }
                      if (best.isContentEditable) {
                        best.textContent = value;
                        best.dispatchEvent(new Event('input', { bubbles: true }));
                        return true;
                      }
                      return false;
                    }
                    """,
                    {"keywords": tokens, "value": value},
                )
            )

        try:
            if _fill_in_frame(self.session.page):
                self.session._sleep_action_delay()
                return True
        except Exception:
            pass

        for frame in self.session._candidate_frames():
            if self.session._deadline_exceeded(deadline):
                break
            try:
                if _fill_in_frame(frame):
                    self.session._sleep_action_delay()
                    return True
            except Exception:
                continue
        return False
