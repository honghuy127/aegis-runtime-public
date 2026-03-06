"""Element clicking and activation helpers for BrowserSession."""

import random
import time
from utils.logging import get_logger


log = get_logger(__name__)


class ElementClickHelper:
    """Encapsulates element clicking and field activation logic."""

    def __init__(self, browser_session):
        """
        Initialize element click helper.

        Args:
            browser_session: Parent BrowserSession instance
        """
        self.session = browser_session

    def click(self, selector: str, timeout_ms: int = None, no_wait_after: bool = False):
        """Click an element selected by CSS.

        Args:
            selector: CSS selector to click.
            timeout_ms: Timeout for locating the element.
            no_wait_after: If True, don't wait for navigation (SPA-safe). Default False.
        """
        if hasattr(
            self.session,
            "_assert_automation_allowed_during_manual_intervention",
        ):
            self.session._assert_automation_allowed_during_manual_intervention(
                "click",
                str(selector or ""),
            )
        if hasattr(self.session, "_record_manual_automation_action"):
            self.session._record_manual_automation_action("click", str(selector or ""))
        timeout = (
            self.session.action_timeout_ms if timeout_ms is None else int(timeout_ms)
        )

        log.info(
            "browser.click.start selector=%s timeout_ms=%d no_wait_after=%s",
            selector[:100] if selector else "",
            timeout,
            no_wait_after,
        )

        if timeout < 100:
            log.warning(
                "browser.click.low_timeout selector=%s timeout_ms=%d (expected >= 100ms)",
                selector[:100] if selector else "",
                timeout,
            )

        deadline = self.session._start_deadline(timeout)
        deadline_ms = max(0, int((deadline - time.monotonic()) * 1000))
        log.info(
            "browser.click.deadline selector=%s timeout_ms=%d deadline_ms=%d human_mimic=%s",
            selector[:100] if selector else "",
            timeout,
            deadline_ms,
            self.session.human_mimic,
        )
        if not self.session.human_mimic:
            try:
                self.session._ensure_deadline_not_exceeded(deadline, "click")
                self.session._log_low_remaining_ms(
                    action="click.page",
                    selector=selector,
                    timeout_ms=timeout,
                    deadline=deadline,
                    attempt="page",
                )
                log.debug(
                    "browser.click.attempt method=page.click selector=%s",
                    selector[:100] if selector else "",
                )
                self.session.page.click(
                    selector,
                    timeout=self.session._remaining_timeout_ms(deadline),
                    no_wait_after=no_wait_after,
                )
                log.info(
                    "browser.click.ok method=page.click selector=%s",
                    selector[:100] if selector else "",
                )
            except Exception as page_click_exc:
                self.session._reraise_interrupt(page_click_exc)
                last_exc = page_click_exc
                try:
                    self.session._ensure_deadline_not_exceeded(deadline, "click")
                    self.session._log_low_remaining_ms(
                        action="click.locator",
                        selector=selector,
                        timeout_ms=timeout,
                        deadline=deadline,
                        attempt="locator",
                    )
                    log.debug(
                        "browser.click.attempt method=locator.click selector=%s",
                        selector[:100] if selector else "",
                    )
                    self.session.page.locator(selector).first.click(
                        timeout=self.session._remaining_timeout_ms(deadline),
                        force=True,
                        no_wait_after=no_wait_after,
                    )
                    log.info(
                        "browser.click.ok method=locator.click selector=%s",
                        selector[:100] if selector else "",
                    )
                except Exception as locator_click_exc:
                    self.session._reraise_interrupt(locator_click_exc)
                    last_exc = locator_click_exc
                    clicked = False
                    for frame in self.session._candidate_frames():
                        try:
                            self.session._ensure_deadline_not_exceeded(deadline, "click")
                            self.session._log_low_remaining_ms(
                                action="click.frame",
                                selector=selector,
                                timeout_ms=timeout,
                                deadline=deadline,
                                attempt="frame",
                            )
                            log.debug(
                                "browser.click.attempt method=frame.click selector=%s",
                                selector[:100] if selector else "",
                            )
                            frame.click(
                                selector,
                                timeout=self.session._remaining_timeout_ms(deadline),
                                no_wait_after=no_wait_after,
                            )
                            log.info(
                                "browser.click.ok method=frame.click selector=%s",
                                selector[:100] if selector else "",
                            )
                            clicked = True
                            break
                        except Exception as frame_click_exc:
                            self.session._reraise_interrupt(frame_click_exc)
                            last_exc = frame_click_exc
                            try:
                                self.session._ensure_deadline_not_exceeded(
                                    deadline, "click"
                                )
                                self.session._log_low_remaining_ms(
                                    action="click.frame_locator",
                                    selector=selector,
                                    timeout_ms=timeout,
                                    deadline=deadline,
                                    attempt="frame_locator",
                                )
                                log.debug(
                                    "browser.click.attempt method=frame.locator.click selector=%s",
                                    selector[:100] if selector else "",
                                )
                                frame.locator(selector).first.click(
                                    timeout=self.session._remaining_timeout_ms(deadline),
                                    force=True,
                                    no_wait_after=no_wait_after,
                                )
                                log.info(
                                    "browser.click.ok method=frame.locator.click selector=%s",
                                    selector[:100] if selector else "",
                                )
                                clicked = True
                                break
                            except Exception as frame_locator_click_exc:
                                self.session._reraise_interrupt(frame_locator_click_exc)
                                last_exc = frame_locator_click_exc
                                continue
                    if not clicked:
                        log.error(
                            "browser.click.failed selector=%s error=%s",
                            selector[:100] if selector else "",
                            last_exc,
                        )
                        raise last_exc
            return

        locator = self.session.page.locator(selector).first
        log.debug(
            "browser.click.attempt method=human_mimic selector=%s",
            selector[:100] if selector else "",
        )
        try:
            self.session._ensure_deadline_not_exceeded(deadline, "click")
            self.session._log_low_remaining_ms(
                action="click.human_wait",
                selector=selector,
                timeout_ms=timeout,
                deadline=deadline,
                attempt="locator",
            )
            locator.wait_for(
                state="visible",
                timeout=self.session._remaining_timeout_ms(deadline),
            )
            box = locator.bounding_box()
            if box:
                target_x = box["x"] + (box["width"] * random.uniform(0.35, 0.65))
                target_y = box["y"] + (box["height"] * random.uniform(0.35, 0.65))
                self.session.page.mouse.move(
                    target_x, target_y, steps=random.randint(6, 20)
                )
                self.session._sleep_action_delay()
            self.session._ensure_deadline_not_exceeded(deadline, "click")
            self.session._log_low_remaining_ms(
                action="click.human_click",
                selector=selector,
                timeout_ms=timeout,
                deadline=deadline,
                attempt="locator",
            )
            locator.click(
                delay=random.randint(20, 120),
                timeout=self.session._remaining_timeout_ms(deadline),
                no_wait_after=no_wait_after,
            )
            log.info(
                "browser.click.ok method=human_mimic selector=%s",
                selector[:100] if selector else "",
            )
        except Exception as visible_click_exc:
            self.session._reraise_interrupt(visible_click_exc)
            last_exc = visible_click_exc
            # Fallback for dynamic sites where element may be attached but not "visible".
            try:
                self.session._ensure_deadline_not_exceeded(deadline, "click")
                self.session._log_low_remaining_ms(
                    action="click.human_attached_wait",
                    selector=selector,
                    timeout_ms=timeout,
                    deadline=deadline,
                    attempt="locator",
                )
                locator.wait_for(
                    state="attached",
                    timeout=self.session._remaining_timeout_ms(deadline),
                )
                self.session._ensure_deadline_not_exceeded(deadline, "click")
                self.session._log_low_remaining_ms(
                    action="click.human_attached_click",
                    selector=selector,
                    timeout_ms=timeout,
                    deadline=deadline,
                    attempt="locator",
                )
                locator.click(
                    timeout=self.session._remaining_timeout_ms(deadline),
                    force=True,
                )
            except Exception as attached_click_exc:
                self.session._reraise_interrupt(attached_click_exc)
                last_exc = attached_click_exc
                clicked = False
                for frame in self.session._candidate_frames():
                    try:
                        self.session._ensure_deadline_not_exceeded(deadline, "click")
                        self.session._log_low_remaining_ms(
                            action="click.human_frame_locator",
                            selector=selector,
                            timeout_ms=timeout,
                            deadline=deadline,
                            attempt="frame_locator",
                        )
                        frame.locator(selector).first.click(
                            timeout=self.session._remaining_timeout_ms(deadline),
                            force=True,
                        )
                        clicked = True
                        break
                    except Exception as frame_click_exc:
                        self.session._reraise_interrupt(frame_click_exc)
                        last_exc = frame_click_exc
                        continue
                if not clicked:
                    raise last_exc
            self.session._sleep_action_delay()
        self.session._sleep_action_delay()

    def activate_field_by_keywords(
        self, keywords, timeout_ms: int = None
    ) -> bool:
        """Click/focus the best visible field candidate whose metadata matches keywords."""
        if hasattr(
            self.session,
            "_assert_automation_allowed_during_manual_intervention",
        ):
            self.session._assert_automation_allowed_during_manual_intervention(
                "activate_field_by_keywords",
                ",".join([str(k) for k in (keywords or [])][:6]),
            )
        if hasattr(self.session, "_record_manual_automation_action"):
            self.session._record_manual_automation_action(
                "activate_field_by_keywords",
                ",".join([str(k) for k in (keywords or [])][:6]),
            )
        timeout = (
            self.session.action_timeout_ms if timeout_ms is None else int(timeout_ms)
        )
        deadline = self.session._start_deadline(timeout)
        tokens = []
        for kw in keywords or []:
            if isinstance(kw, str) and kw.strip():
                tokens.append(kw.strip().lower())
        if not tokens:
            return False

        def _activate_in_frame(frame):
            return frame.evaluate(
                """
                (keywords) => {
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
                  const labelsFor = (el) => {
                    const bits = [];
                    bits.push(el.getAttribute('aria-label'));
                    bits.push(el.getAttribute('placeholder'));
                    bits.push(el.getAttribute('name'));
                    bits.push(el.getAttribute('id'));
                    bits.push(el.getAttribute('data-testid'));
                    bits.push(el.getAttribute('title'));
                    bits.push(el.textContent);
                    return lower(bits.filter(Boolean).join(' '));
                  };
                  const selectors = [
                    'input:not([type="hidden"])',
                    'textarea',
                    '[role="combobox"]',
                    '[role="textbox"]',
                    '[role="searchbox"]',
                    'button',
                    '[tabindex]',
                    '[contenteditable="true"]',
                  ];
                  const seen = new Set();
                  const nodes = [];
                  for (const sel of selectors) {
                    for (const el of document.querySelectorAll(sel)) {
                      if (seen.has(el)) continue;
                      seen.add(el);
                      nodes.push(el);
                    }
                  }
                  let best = null;
                  let bestScore = -1;
                  for (const el of nodes) {
                    if (!visible(el) || el.disabled) continue;
                    const text = labelsFor(el);
                    let score = 0;
                    for (const kw of keywords) {
                      if (!kw) continue;
                      if (text.includes(kw)) score += kw.length > 2 ? 3 : 1;
                    }
                    if (score <= 0) continue;
                    const tag = lower(el.tagName);
                    const role = lower(el.getAttribute('role'));
                    if (tag === 'input' || tag === 'textarea') score += 5;
                    if (role === 'combobox' || role === 'textbox' || role === 'searchbox') score += 4;
                    if (tag === 'button') score += 2;
                    if (score > bestScore) {
                      best = el;
                      bestScore = score;
                    }
                  }
                  if (!best) return false;
                  best.scrollIntoView({ block: 'center', inline: 'center' });
                  best.focus();
                  best.click();
                  return true;
                }
                """,
                tokens,
            )

        try:
            if _activate_in_frame(self.session.page):
                self.session._sleep_action_delay()
                return True
        except Exception:
            pass

        for frame in self.session._candidate_frames():
            if self.session._deadline_exceeded(deadline):
                break
            try:
                if _activate_in_frame(frame):
                    self.session._sleep_action_delay()
                    return True
            except Exception:
                continue
        return False
