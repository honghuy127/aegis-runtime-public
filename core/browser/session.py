"""Browser primitives built on Playwright for page interaction and HTML capture."""

import json
import random
import re
import signal
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Union
from urllib.parse import urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright
from core.browser.verification_challenges import VerificationChallengeHelper
from core.browser.click import ElementClickHelper
from core.browser.combobox import GoogleFlightsComboboxHelper
from core.browser.fill import FormFillHelper
from core.browser.framework import BrowserFrameworkHelper
from core.browser.helpers import _wait_with_heartbeat
from core.browser.page import PageInteractionHelper
from core.browser.typing import TypingInputHelper
from core.browser.wait import ElementWaitHelper
from core.browser.manual_intervention_policy import (
    is_verification_url,
    is_skyscanner_px_captcha_surface,
    is_verification_surface,
    should_mark_manual_observation_complete,
)
from core.browser.stealth import (
    _human_mimic_stealth_init_script,
    _human_mimic_chromium_user_agent,
    derive_ua_stealth_profile,
)
from core.browser.timeouts import (
    apply_selector_timeout_strategy,
    safe_min_timeout_ms,
    wall_clock_deadline,
    wall_clock_remaining_ms,
    wall_clock_exhausted,
    enforce_wall_clock_deadline,
)
from core.run_input_config import DEFAULT_RUN_INPUTS
from utils.logging import get_logger
from utils.thresholds import get_threshold

try:
    from playwright_stealth import stealth_sync as _playwright_stealth_sync
except Exception:
    _playwright_stealth_sync = None

try:
    from fake_useragent import UserAgent as _FakeUserAgent
except Exception:
    _FakeUserAgent = None


DEFAULT_GOTO_TIMEOUT_MS = int(get_threshold("browser_goto_timeout_ms", 45_000))
DEFAULT_GOTO_COMMIT_TIMEOUT_MS = int(
    get_threshold("browser_goto_commit_timeout_ms", 25_000)
)
DEFAULT_ACTION_TIMEOUT_MS = int(get_threshold("browser_action_timeout_ms", 20_000))
DEFAULT_WAIT_TIMEOUT_MS = int(get_threshold("browser_wait_timeout_ms", 20_000))
DEFAULT_PLAYWRIGHT_ATTEMPT_TIMEOUT_FLOOR_MS = int(
    get_threshold("browser_playwright_attempt_timeout_floor_ms", 150)
)

# Browser action settle delays (milliseconds)
DEFAULT_SETTLE_WAIT_30_MS = int(get_threshold("browser_settle_wait_30ms", 30))
DEFAULT_SETTLE_WAIT_40_MS = int(get_threshold("browser_settle_wait_40ms", 40))
log = get_logger(__name__)


class BrowserSession:
    """Context-managed Playwright session for simple page actions."""
    MAX_FRAME_FALLBACKS = 8
    _HUMAN_INTERVENTION_MODES = {"off", "assist", "demo"}
    _AUX_PAGE_BLOCKED_SCHEMES = (
        "about:",
        "chrome:",
        "edge:",
        "devtools:",
        "chrome-extension:",
        "moz-extension:",
        "safari-extension:",
    )
    _AUX_PAGE_BLOCKED_HOST_TOKENS = (
        "doubleclick",
        "googlesyndication",
        "googleadservices",
        "adservice",
        "taboola",
        "outbrain",
        "criteo",
        "onetag",
        "bidswitch",
    )

    @staticmethod
    def _random_human_viewport() -> dict:
        """Return a realistic desktop viewport for anti-detection variability."""
        widths = [1280, 1366, 1440, 1536, 1600, 1680, 1728, 1920]
        heights = [720, 768, 810, 864, 900, 960, 1024, 1080]
        width = random.choice(widths)
        height = random.choice(heights)
        return {"width": width, "height": height}

    @staticmethod
    def _popup_guard_init_script() -> str:
        """Return init script that blocks popup-style navigations by default."""
        return """
(() => {
  try {
    if (window.__FPW_POPUP_GUARD_INSTALLED) return;
    window.__FPW_POPUP_GUARD_INSTALLED = true;
    const guard = {
      blocked: 0,
      allowed: 0,
      samples: [],
      max_samples: 40,
      installed_at_ms: Date.now(),
    };
    const pushSample = (entry) => {
      try {
        const item = Object.assign({ t_ms: Date.now() }, entry || {});
        guard.samples.push(item);
        if (guard.samples.length > guard.max_samples) {
          guard.samples.splice(0, guard.samples.length - guard.max_samples);
        }
      } catch (e) {}
    };
    const allowOnlyVerification = (raw) => {
      const urlText = String(raw || "").trim();
      if (!urlText) return false;
      const lower = urlText.toLowerCase();
      if (
        lower.startsWith("about:") ||
        lower.startsWith("chrome:") ||
        lower.startsWith("edge:") ||
        lower.startsWith("devtools:")
      ) {
        return false;
      }
      if (
        lower.includes("/sttc/px/") ||
        lower.includes("captcha") ||
        lower.includes("turnstile") ||
        lower.includes("hcaptcha") ||
        lower.includes("recaptcha")
      ) {
        return true;
      }
      return false;
    };
    const mark = (blocked, kind, url, reason) => {
      if (blocked) guard.blocked += 1;
      else guard.allowed += 1;
      pushSample({
        blocked: !!blocked,
        kind: String(kind || ""),
        reason: String(reason || ""),
        url_prefix: String(url || "").slice(0, 180),
      });
    };
    const originalOpen = window.open ? window.open.bind(window) : null;
    if (originalOpen) {
      window.open = function(url, target, features) {
        const urlText = String(url || "");
        if (!allowOnlyVerification(urlText)) {
          mark(true, "window.open", urlText, "popup_blocked_by_policy");
          return null;
        }
        mark(false, "window.open", urlText, "allowed_verification_surface");
        return originalOpen(url, target, features);
      };
    }
    document.addEventListener(
      "click",
      (evt) => {
        const t = evt && evt.target;
        if (!t || !t.closest) return;
        const link = t.closest("a[target='_blank']");
        if (!link) return;
        const href = String((link.getAttribute && link.getAttribute("href")) || "");
        if (!allowOnlyVerification(href)) {
          try { evt.preventDefault(); } catch (e) {}
          try { evt.stopPropagation(); } catch (e) {}
          try { evt.stopImmediatePropagation(); } catch (e) {}
          mark(true, "a_target_blank_click", href, "popup_blocked_by_policy");
          return;
        }
        mark(false, "a_target_blank_click", href, "allowed_verification_surface");
      },
      true
    );
    document.addEventListener(
      "submit",
      (evt) => {
        const form = evt && evt.target;
        if (!form || !form.getAttribute) return;
        const target = String(form.getAttribute("target") || "").toLowerCase();
        if (target !== "_blank") return;
        const action = String(form.getAttribute("action") || "");
        if (!allowOnlyVerification(action)) {
          try { evt.preventDefault(); } catch (e) {}
          try { evt.stopPropagation(); } catch (e) {}
          try { evt.stopImmediatePropagation(); } catch (e) {}
          mark(true, "form_target_blank_submit", action, "popup_blocked_by_policy");
          return;
        }
        mark(false, "form_target_blank_submit", action, "allowed_verification_surface");
      },
      true
    );
    window.__FPW_POPUP_GUARD = guard;
  } catch (e) {}
})();
"""

    @staticmethod
    def _looks_mobile_user_agent(user_agent: str) -> bool:
        """Return True when a UA string clearly represents mobile/tablet traffic."""
        ua = str(user_agent or "").lower()
        if not ua:
            return False
        mobile_tokens = (
            "mobile",
            "iphone",
            "ipad",
            "android",
            "ipod",
            "windows phone",
            "opera mini",
            "tablet",
            "crios/",
            "fxios/",
        )
        return any(token in ua for token in mobile_tokens)

    @staticmethod
    def _random_human_user_agent() -> str:
        """Resolve randomized UA with fake-useragent fallback and safe defaults."""
        def _is_modern_desktop_chromium_ua(ua_text: str) -> bool:
            ua = str(ua_text or "").strip()
            if not ua:
                return False
            if BrowserSession._looks_mobile_user_agent(ua):
                return False
            lower = ua.lower()
            if "headless" in lower:
                return False
            if "chrome/" not in lower:
                return False
            match = re.search(r"chrome/(\d+)\.", lower)
            if match is None:
                return False
            try:
                major = int(match.group(1))
            except Exception:
                return False
            # Keep UA reasonably fresh to avoid stale-fingerprint challenge triggers.
            if major < 130:
                return False
            return True

        if _FakeUserAgent is not None:
            try:
                candidate = str(_FakeUserAgent().random)
                if _is_modern_desktop_chromium_ua(candidate):
                    return candidate
            except Exception:
                pass
        fallback = [
            _human_mimic_chromium_user_agent(),
            (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/135.0.0.0 Safari/537.36"
            ),
            (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/134.0.0.0 Safari/537.36"
            ),
            (
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/133.0.0.0 Safari/537.36"
            ),
        ]
        return random.choice(fallback)

    def __init__(
        self,
        headless: bool = True,
        human_mimic: bool = False,
        disable_http2: bool = False,
        mimic_locale: str = None,
        mimic_timezone: str = None,
        mimic_currency: str = None,
        mimic_region: str = None,
        mimic_latitude: float = None,
        mimic_longitude: float = None,
        min_action_delay_ms: int = 90,
        max_action_delay_ms: int = 260,
        min_typing_delay_ms: int = 28,
        max_typing_delay_ms: int = 72,
        goto_timeout_ms: int = None,
        action_timeout_ms: int = None,
        wait_timeout_ms: int = None,
        goto_commit_timeout_ms: int = None,
        block_heavy_resources: bool = True,
        browser_engine: str = "chromium",
        allow_human_intervention: bool = None,
        human_intervention_mode: str = "",
        last_resort_manual_when_disabled: bool = False,
        manual_intervention_timeout_sec: int = 120,
        manual_intervention_event_hook=None,
        storage_state_path: str = "",
        persist_storage_state: bool = True,
    ):
        """Configure browser launch mode; does not launch until context entry."""
        self.headless = headless
        self.human_mimic = human_mimic
        self.disable_http2 = disable_http2
        self.mimic_locale = (
            mimic_locale or str(DEFAULT_RUN_INPUTS["mimic_locale"])
        ).strip()
        self.mimic_timezone = (
            mimic_timezone or str(DEFAULT_RUN_INPUTS["mimic_timezone"])
        ).strip()
        self.mimic_currency = (
            mimic_currency or str(DEFAULT_RUN_INPUTS["mimic_currency"])
        ).strip().upper()
        self.mimic_region = (
            mimic_region or str(DEFAULT_RUN_INPUTS["mimic_region"])
        ).strip().upper()
        self.mimic_latitude = float(
            DEFAULT_RUN_INPUTS["mimic_latitude"] if mimic_latitude is None else mimic_latitude
        )
        self.mimic_longitude = float(
            DEFAULT_RUN_INPUTS["mimic_longitude"] if mimic_longitude is None else mimic_longitude
        )
        self.min_action_delay_ms = max(0, min_action_delay_ms)
        self.max_action_delay_ms = max(self.min_action_delay_ms, max_action_delay_ms)
        self.min_typing_delay_ms = max(0, min_typing_delay_ms)
        self.max_typing_delay_ms = max(self.min_typing_delay_ms, max_typing_delay_ms)
        # NOTE: TOS/ethics warning - automation can violate site terms; use only with explicit authorization.
        if self.human_mimic and not self.headless and self.max_action_delay_ms < 1000:
            self.min_action_delay_ms = 1000
            self.max_action_delay_ms = 5000
        if self.human_mimic and not self.headless and self.max_typing_delay_ms < 90:
            self.min_typing_delay_ms = 90
            self.max_typing_delay_ms = 240
        self.goto_timeout_ms = int(
            DEFAULT_GOTO_TIMEOUT_MS if goto_timeout_ms is None else goto_timeout_ms
        )
        self.goto_commit_timeout_ms = int(
            DEFAULT_GOTO_COMMIT_TIMEOUT_MS
            if goto_commit_timeout_ms is None
            else goto_commit_timeout_ms
        )
        self.block_heavy_resources = bool(block_heavy_resources)
        self.browser_engine = (browser_engine or "chromium").strip().lower()
        self.action_timeout_ms = int(
            DEFAULT_ACTION_TIMEOUT_MS if action_timeout_ms is None else action_timeout_ms
        )
        self.wait_timeout_ms = int(
            DEFAULT_WAIT_TIMEOUT_MS if wait_timeout_ms is None else wait_timeout_ms
        )
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.user_agent = ""
        self.viewport = {"width": 1366, "height": 900}
        default_allow = (
            bool(allow_human_intervention)
            if allow_human_intervention is not None
            else (self.human_mimic and not self.headless)
        )
        normalized_mode = str(human_intervention_mode or "").strip().lower()
        if normalized_mode not in BrowserSession._HUMAN_INTERVENTION_MODES:
            normalized_mode = "assist" if default_allow else "off"
        if normalized_mode == "off" and default_allow and str(human_intervention_mode or "").strip() == "":
            normalized_mode = "assist"
        self.human_intervention_mode = normalized_mode
        self.allow_human_intervention = self.human_intervention_mode in {"assist", "demo"}
        self.last_resort_manual_when_disabled = bool(last_resort_manual_when_disabled)
        self.manual_intervention_timeout_sec = max(10, int(manual_intervention_timeout_sec or 120))
        self.manual_intervention_event_hook = manual_intervention_event_hook
        self.storage_state_path = str(storage_state_path or "").strip()
        self.persist_storage_state = bool(persist_storage_state)
        self._manual_intervention_active = False
        self._manual_intervention_request = ""
        self._manual_automation_actions = {"count": 0, "counts": {}, "samples": []}
        self._expected_new_pages = 0
        self._aux_page_guard = {"closed": 0, "allowed_expected": 0, "samples": []}
        self._popup_guard = {"blocked": 0, "allowed": 0, "samples": []}
        self._browser_lifecycle = {"seq": 0, "events": []}
        self._observed_page_ids = set()
        self._page_interaction = None  # Initialized in __enter__
        self._verification_challenges = None  # Initialized in __enter__
        self._last_interstitial_grace_meta = {}  # State for interstitial handling
        self._network_activity = {
            "started_ms": int(time.time() * 1000),
            "requests": 0,
            "responses": 0,
            "failed": 0,
            "status_buckets": {},
            "resource_types": {},
            "domains": {},
            "events": [],
        }

    @staticmethod
    def _host_from_url(url_text: str) -> str:
        """Return normalized host from URL-like text."""
        try:
            return str(urlparse(str(url_text or "")).hostname or "").strip().lower()
        except Exception:
            return ""

    @staticmethod
    def _registrable_root(host: str) -> str:
        """Return coarse registrable root (best effort without PSL dependency)."""
        text = str(host or "").strip().lower().lstrip(".")
        if not text:
            return ""
        parts = [p for p in text.split(".") if p]
        if len(parts) <= 2:
            return text
        return ".".join(parts[-2:])

    @staticmethod
    def _brand_token(host: str) -> str:
        """Return coarse brand token to bridge ccTLD switches (e.g. .com -> .jp)."""
        text = str(host or "").strip().lower().lstrip(".")
        if not text:
            return ""
        parts = [p for p in text.split(".") if p]
        if len(parts) < 2:
            return parts[0] if parts else ""
        # For a.b.example.com -> example, for skyscanner.jp -> skyscanner.
        token = parts[-2]
        if len(parts) >= 3 and token in {"co", "com", "net", "org"}:
            token = parts[-3]
        return str(token or "")

    @staticmethod
    def _aux_page_allowed_for_rebind(*, candidate_url: str, preferred_url: str) -> bool:
        """Allow rebind only to same-site or verification surfaces; reject ad/random pages."""
        url_now = str(candidate_url or "").strip()
        if not url_now:
            return False
        lower = url_now.lower()
        if lower.startswith(BrowserSession._AUX_PAGE_BLOCKED_SCHEMES):
            return False
        candidate_host = BrowserSession._host_from_url(lower)
        if not candidate_host:
            return False
        if any(token in candidate_host for token in BrowserSession._AUX_PAGE_BLOCKED_HOST_TOKENS):
            return False
        if is_verification_url(lower):
            return True
        preferred_host = BrowserSession._host_from_url(preferred_url)
        if not preferred_host:
            return True
        if candidate_host == preferred_host:
            return True
        if candidate_host.endswith(f".{preferred_host}") or preferred_host.endswith(
            f".{candidate_host}"
        ):
            return True
        candidate_root = BrowserSession._registrable_root(candidate_host)
        preferred_root = BrowserSession._registrable_root(preferred_host)
        if candidate_root and preferred_root and candidate_root == preferred_root:
            return True
        candidate_brand = BrowserSession._brand_token(candidate_host)
        preferred_brand = BrowserSession._brand_token(preferred_host)
        if (
            candidate_brand
            and preferred_brand
            and candidate_brand == preferred_brand
            and len(candidate_brand) >= 4
        ):
            return True
        return False

    @staticmethod
    def _should_close_unexpected_page(
        *,
        candidate_url: str,
        primary_url: str,
        expected_new_pages: int,
    ) -> bool:
        """Single-page policy: close unexpected popup/new tabs by default."""
        if int(expected_new_pages or 0) > 0:
            return False
        url_now = str(candidate_url or "").strip().lower()
        if not url_now:
            return True
        if url_now.startswith(BrowserSession._AUX_PAGE_BLOCKED_SCHEMES):
            return True
        primary = str(primary_url or "").strip()
        if not primary:
            return True
        if is_verification_url(url_now):
            return False
        # Allow same-site/brand tabs; close unrelated domains by default.
        return not BrowserSession._aux_page_allowed_for_rebind(
            candidate_url=url_now,
            preferred_url=primary,
        )

    def _enforce_single_page_policy_snapshot(self, *, reason: str = "periodic") -> Dict[str, int]:
        """Best-effort sweep to close unexpected pages when callback events are missed."""
        closed_count = 0
        allowed_count = 0
        page_count = 0
        try:
            context = getattr(self, "context", None)
            if context is None:
                return {"closed": 0, "allowed": 0, "pages": 0}
            pages = list(getattr(context, "pages", []) or [])
            page_count = len(pages)
            primary_page = getattr(self, "page", None)
            expected = int(getattr(self, "_expected_new_pages", 0) or 0)
            primary_url = ""
            try:
                primary_url = str(getattr(primary_page, "url", "") or "")
            except Exception:
                primary_url = ""
            for page_candidate in pages:
                if primary_page is not None and page_candidate is primary_page:
                    continue
                try:
                    if hasattr(page_candidate, "is_closed") and page_candidate.is_closed():
                        continue
                except Exception:
                    continue
                candidate_url = ""
                try:
                    candidate_url = str(getattr(page_candidate, "url", "") or "")
                except Exception:
                    candidate_url = ""
                if expected > 0:
                    expected -= 1
                    allowed_count += 1
                    self._record_aux_page_guard_event(
                        action="allowed_expected",
                        url=candidate_url,
                        reason=f"{reason}_expected_new_page",
                    )
                    continue
                if not BrowserSession._should_close_unexpected_page(
                    candidate_url=candidate_url,
                    primary_url=primary_url,
                    expected_new_pages=0,
                ):
                    allowed_count += 1
                    self._record_aux_page_guard_event(
                        action="allowed_expected",
                        url=candidate_url,
                        reason=f"{reason}_allowed",
                    )
                    continue
                try:
                    if hasattr(page_candidate, "close"):
                        try:
                            page_candidate.close(run_before_unload=False)
                        except TypeError:
                            page_candidate.close()
                except Exception:
                    pass
                closed_count += 1
                self._record_aux_page_guard_event(
                    action="closed",
                    url=candidate_url,
                    reason=f"{reason}_unexpected_new_page_closed",
                )
            self._expected_new_pages = max(0, expected)
        except Exception:
            return {"closed": int(closed_count), "allowed": int(allowed_count), "pages": int(page_count)}
        return {"closed": int(closed_count), "allowed": int(allowed_count), "pages": int(page_count)}

    def _record_network_event(self, kind: str, **fields: Any) -> None:
        """Store bounded network-ish metadata for runtime diagnostics."""
        state = getattr(self, "_network_activity", None)
        if not isinstance(state, dict):
            return
        events = state.get("events")
        if not isinstance(events, list):
            events = []
            state["events"] = events
        evt = {"t_ms": int(time.time() * 1000), "kind": str(kind or "unknown")}
        evt.update(fields or {})
        events.append(evt)
        if len(events) > 400:
            del events[: len(events) - 400]

    def _record_browser_lifecycle_event(self, kind: str, **fields: Any) -> None:
        """Record bounded browser/context/page lifecycle events for fast-window triage."""
        state = getattr(self, "_browser_lifecycle", None)
        if not isinstance(state, dict):
            state = {"seq": 0, "events": []}
            self._browser_lifecycle = state
        events = state.get("events")
        if not isinstance(events, list):
            events = []
            state["events"] = events
        seq = int(state.get("seq", 0) or 0) + 1
        state["seq"] = seq
        evt = {"seq": seq, "t_ms": int(time.time() * 1000), "kind": str(kind or "unknown")}
        evt.update(fields or {})
        events.append(evt)
        if len(events) > 300:
            del events[: len(events) - 300]
        log.info(
            "browser.lifecycle kind=%s seq=%s fields=%s",
            str(kind or ""),
            int(seq),
            {k: v for k, v in (fields or {}).items()},
        )

    def _attach_page_lifecycle(self, page_obj: Any, *, role: str) -> None:
        """Attach bounded page lifecycle listeners once per page instance."""
        if page_obj is None:
            return
        page_id = id(page_obj)
        seen = getattr(self, "_observed_page_ids", None)
        if not isinstance(seen, set):
            seen = set()
            self._observed_page_ids = seen
        if page_id in seen:
            return
        seen.add(page_id)
        page_url = ""
        try:
            page_url = str(getattr(page_obj, "url", "") or "")
        except Exception:
            page_url = ""
        self._record_browser_lifecycle_event(
            "page_attached",
            role=str(role or ""),
            page_id=int(page_id),
            url_prefix=page_url[:220],
        )
        if not hasattr(page_obj, "on"):
            return
        try:
            page_obj.on(
                "close",
                lambda: self._record_browser_lifecycle_event(
                    "page_close",
                    role=str(role or ""),
                    page_id=int(page_id),
                    url_prefix=str(getattr(page_obj, "url", "") or "")[:220],
                ),
            )
        except Exception:
            pass
        try:
            page_obj.on(
                "popup",
                lambda popup_page: self._record_browser_lifecycle_event(
                    "page_popup",
                    role=str(role or ""),
                    parent_page_id=int(page_id),
                    popup_page_id=int(id(popup_page)),
                    popup_url_prefix=str(getattr(popup_page, "url", "") or "")[:220],
                ),
            )
        except Exception:
            pass
        try:
            def _on_framenavigated(frame_obj: Any) -> None:
                try:
                    main_frame = getattr(page_obj, "main_frame", None)
                except Exception:
                    main_frame = None
                if main_frame is not None and frame_obj is not main_frame:
                    return
                nav_url = ""
                try:
                    nav_url = str(getattr(frame_obj, "url", "") or "")
                except Exception:
                    nav_url = ""
                self._record_browser_lifecycle_event(
                    "page_mainframe_navigate",
                    role=str(role or ""),
                    page_id=int(page_id),
                    url_prefix=nav_url[:220],
                )
            page_obj.on("framenavigated", _on_framenavigated)
        except Exception:
            pass

    def _record_aux_page_guard_event(self, *, action: str, url: str, reason: str) -> None:
        """Record bounded page-guard decisions for diagnostics and manual traces."""
        state = getattr(self, "_aux_page_guard", None)
        if not isinstance(state, dict):
            state = {"closed": 0, "allowed_expected": 0, "samples": []}
            self._aux_page_guard = state
        if action == "closed":
            state["closed"] = int(state.get("closed", 0) or 0) + 1
        if action == "allowed_expected":
            state["allowed_expected"] = int(state.get("allowed_expected", 0) or 0) + 1
        samples = state.get("samples")
        if not isinstance(samples, list):
            samples = []
            state["samples"] = samples
        if len(samples) < 32:
            samples.append(
                {
                    "ts_ms": int(time.time() * 1000),
                    "action": str(action or ""),
                    "url_prefix": str(url or "")[:160],
                    "reason": str(reason or "")[:120],
                }
            )
        BrowserSession._emit_manual_intervention_event(
            self,
            {
                "stage": "aux_page_guard",
                "reason": str(reason or ""),
                "aux_page_guard_action": str(action or ""),
                "aux_page_guard_url": str(url or "")[:240],
                "aux_page_guard_closed_count": int(state.get("closed", 0) or 0),
                "aux_page_guard_allowed_expected_count": int(
                    state.get("allowed_expected", 0) or 0
                ),
            }
        )

    def _on_context_page_opened(self, page_candidate: Any) -> None:
        """Enforce single-page policy by closing unexpected popups/new tabs."""
        try:
            if page_candidate is None:
                return
            self._attach_page_lifecycle(page_candidate, role="context_page")
            primary_page = getattr(self, "page", None)
            if primary_page is not None and page_candidate is primary_page:
                return
            candidate_url = ""
            try:
                candidate_url = str(getattr(page_candidate, "url", "") or "")
            except Exception:
                candidate_url = ""
            self._record_browser_lifecycle_event(
                "context_page_opened",
                candidate_page_id=int(id(page_candidate)),
                candidate_url_prefix=str(candidate_url or "")[:220],
                primary_page_id=int(id(primary_page)) if primary_page is not None else 0,
            )
            expected = int(getattr(self, "_expected_new_pages", 0) or 0)
            primary_url = ""
            try:
                primary_url = str(getattr(primary_page, "url", "") or "")
            except Exception:
                primary_url = ""
            if expected > 0:
                self._expected_new_pages = max(0, expected - 1)
                self._record_aux_page_guard_event(
                    action="allowed_expected",
                    url=candidate_url,
                    reason="expected_new_page",
                )
                return
            should_close = BrowserSession._should_close_unexpected_page(
                candidate_url=candidate_url,
                primary_url=primary_url,
                expected_new_pages=expected,
            )
            if not should_close:
                self._record_aux_page_guard_event(
                    action="allowed_expected",
                    url=candidate_url,
                    reason="verification_surface_allowed",
                )
                return
            try:
                if hasattr(page_candidate, "close"):
                    try:
                        page_candidate.close(run_before_unload=False)
                    except TypeError:
                        page_candidate.close()
            except Exception:
                pass
            self._record_aux_page_guard_event(
                action="closed",
                url=candidate_url,
                reason="unexpected_new_page_closed",
            )
            log.warning(
                "browser.page_guard.closed_new_page url=%s primary_url=%s",
                str(candidate_url or "")[:200],
                str(primary_url or "")[:200],
            )
        except Exception:
            return

    def _on_network_request(self, request: Any) -> None:
        """Track request-level metadata without storing sensitive payloads."""
        state = getattr(self, "_network_activity", None)
        if not isinstance(state, dict):
            return
        url = str(getattr(request, "url", "") or "")
        host = (urlparse(url).hostname or "").lower()
        rtype = str(getattr(request, "resource_type", "") or "unknown").lower()
        method = str(getattr(request, "method", "") or "GET").upper()
        state["requests"] = int(state.get("requests", 0) or 0) + 1
        resource_types = state.get("resource_types")
        if not isinstance(resource_types, dict):
            resource_types = {}
            state["resource_types"] = resource_types
        resource_types[rtype] = int(resource_types.get(rtype, 0) or 0) + 1
        domains = state.get("domains")
        if not isinstance(domains, dict):
            domains = {}
            state["domains"] = domains
        domains[host] = int(domains.get(host, 0) or 0) + 1
        self._record_network_event("request", host=host[:80], method=method[:8], rtype=rtype[:32])

    def _on_network_response(self, response: Any) -> None:
        """Track response status buckets and challenge-prone statuses."""
        state = getattr(self, "_network_activity", None)
        if not isinstance(state, dict):
            return
        status = int(getattr(response, "status", 0) or 0)
        url = str(getattr(response, "url", "") or "")
        host = (urlparse(url).hostname or "").lower()
        state["responses"] = int(state.get("responses", 0) or 0) + 1
        bucket = "unknown"
        if status > 0:
            bucket = f"{status // 100}xx"
        status_buckets = state.get("status_buckets")
        if not isinstance(status_buckets, dict):
            status_buckets = {}
            state["status_buckets"] = status_buckets
        status_buckets[bucket] = int(status_buckets.get(bucket, 0) or 0) + 1
        self._record_network_event("response", host=host[:80], status=status, bucket=bucket)

    def _on_network_request_failed(self, request: Any) -> None:
        """Track failed requests with compact failure text."""
        state = getattr(self, "_network_activity", None)
        if not isinstance(state, dict):
            return
        url = str(getattr(request, "url", "") or "")
        host = (urlparse(url).hostname or "").lower()
        rtype = str(getattr(request, "resource_type", "") or "unknown").lower()
        failure_text = ""
        if hasattr(request, "failure"):
            try:
                failure_payload = request.failure
                if isinstance(failure_payload, dict):
                    failure_text = str(failure_payload.get("errorText", "") or "")
            except Exception:
                failure_text = ""
        state["failed"] = int(state.get("failed", 0) or 0) + 1
        self._record_network_event(
            "failed",
            host=host[:80],
            rtype=rtype[:32],
            error=failure_text[:120],
        )

    def get_network_activity_snapshot(self, *, window_sec: int = 20) -> Dict[str, Any]:
        """Return aggregate network telemetry for the requested trailing window."""
        state = getattr(self, "_network_activity", None)
        if not isinstance(state, dict):
            return {"enabled": False, "reason": "network_state_missing"}
        now_ms = int(time.time() * 1000)
        cutoff_ms = now_ms - max(1, int(window_sec or 1)) * 1000
        events = list(state.get("events", []) or [])
        window_events = [evt for evt in events if int(evt.get("t_ms", 0) or 0) >= cutoff_ms]
        by_kind: Dict[str, int] = {}
        status_buckets_window: Dict[str, int] = {}
        challenge_status_hits = 0
        failed_blocked_by_client = 0
        failed_challenge_hosts = 0
        failed_challenge_hosts_blocked_by_client = 0
        challenge_host_tokens = (
            "px-cloud",
            "perimeterx",
            "captcha",
            "hcaptcha",
            "recaptcha",
            "arkoselabs",
            "datadome",
            "turnstile",
            "cloudflare",
        )
        for evt in window_events:
            kind = str(evt.get("kind", "unknown") or "unknown")
            by_kind[kind] = int(by_kind.get(kind, 0) or 0) + 1
            if kind == "response":
                bucket = str(evt.get("bucket", "unknown") or "unknown")
                status_buckets_window[bucket] = int(status_buckets_window.get(bucket, 0) or 0) + 1
                status = int(evt.get("status", 0) or 0)
                if status in {403, 429}:
                    challenge_status_hits += 1
            if kind == "failed":
                host = str(evt.get("host", "") or "").lower()
                error = str(evt.get("error", "") or "").lower()
                is_challenge_host = any(token in host for token in challenge_host_tokens)
                is_blocked_by_client = "blocked_by_client" in error
                if is_challenge_host:
                    failed_challenge_hosts += 1
                if is_blocked_by_client:
                    failed_blocked_by_client += 1
                    if is_challenge_host:
                        failed_challenge_hosts_blocked_by_client += 1
        domains = dict(state.get("domains", {}) or {})
        top_domains = sorted(domains.items(), key=lambda item: int(item[1] or 0), reverse=True)[:8]
        return {
            "enabled": True,
            "started_ms": int(state.get("started_ms", 0) or 0),
            "window_sec": int(window_sec),
            "totals": {
                "requests": int(state.get("requests", 0) or 0),
                "responses": int(state.get("responses", 0) or 0),
                "failed": int(state.get("failed", 0) or 0),
                "status_buckets": dict(state.get("status_buckets", {}) or {}),
                "resource_types": dict(state.get("resource_types", {}) or {}),
            },
            "window": {
                "events": int(len(window_events)),
                "by_kind": by_kind,
                "status_buckets": status_buckets_window,
                "challenge_status_hits": int(challenge_status_hits),
                "failed_blocked_by_client": int(failed_blocked_by_client),
                "failed_challenge_hosts": int(failed_challenge_hosts),
                "failed_challenge_hosts_blocked_by_client": int(failed_challenge_hosts_blocked_by_client),
                "top_domains": [{"host": str(host), "count": int(count)} for host, count in top_domains],
            },
        }

    def _sleep_action_delay(self):
        """Sleep a short randomized delay to reduce automation-like action cadence."""
        if not self.human_mimic:
            return
        ms = random.randint(self.min_action_delay_ms, self.max_action_delay_ms)
        time.sleep(ms / 1000.0)

    def _apply_runtime_stealth(self):
        """Apply playwright-stealth to current page when available."""
        if self.page is None or _playwright_stealth_sync is None:
            return
        try:
            _playwright_stealth_sync(self.page)
        except Exception:
            pass

    def _human_scan_page(self):
        """Lightweight post-navigation mouse/scroll choreography."""
        if not self.human_mimic or self.page is None or not hasattr(self.page, "mouse"):
            return
        width = int((self.viewport or {}).get("width", 1366))
        height = int((self.viewport or {}).get("height", 900))
        try:
            for _ in range(random.randint(1, 3)):
                x = random.randint(80, max(120, width - 80))
                y = random.randint(80, max(120, height - 80))
                self.page.mouse.move(x, y, steps=random.randint(8, 22))
                if hasattr(self.page.mouse, "wheel") and random.random() < 0.75:
                    self.page.mouse.wheel(0, random.randint(80, 260))
                if hasattr(self.page, "wait_for_timeout"):
                    self.page.wait_for_timeout(random.randint(250, 900))
                else:
                    time.sleep(random.uniform(0.25, 0.9))
        except Exception:
            pass

    def _begin_manual_ui_action_capture(self, *, lightweight: bool = False) -> Dict[str, Any]:
        """Install (idempotent) in-page UI action probes and start one capture window."""
        page = getattr(self, "page", None)
        if page is None or not hasattr(page, "evaluate"):
            return {"enabled": False, "reason": "page_unavailable"}
        try:
            started = page.evaluate(
                """
                ({ lightweight }) => {
                  const w = window;
                  if (!w.__fwManualCapture) {
                    const store = { events: [], installed: false };
                    const normText = (v) => String(v || "").replace(/\\s+/g, " ").trim().slice(0, 120);
                    const targetInfo = (el) => {
                      if (!el || !el.tagName) return "";
                      const tag = String(el.tagName || "").toLowerCase();
                      const id = String(el.id || "").slice(0, 60);
                      const cls = String(el.className || "").replace(/\\s+/g, ".").slice(0, 80);
                      const role = String((el.getAttribute && el.getAttribute("role")) || "").slice(0, 40);
                      const name = String((el.getAttribute && el.getAttribute("name")) || "").slice(0, 60);
                      const aria = String((el.getAttribute && el.getAttribute("aria-label")) || "").slice(0, 80);
                      return [tag, id ? `#${id}` : "", cls ? `.${cls}` : "", role ? `[role=${role}]` : "", name ? `[name=${name}]` : "", aria ? `[aria=${aria}]` : ""].filter(Boolean).join("");
                    };
                    const pushEvt = (evt, source = "document") => {
                      try {
                        const now = Date.now();
                        const base = {
                          t: now,
                          type: String(evt.type || ""),
                          x: Number(evt.clientX || 0),
                          y: Number(evt.clientY || 0),
                          target: targetInfo(evt.target || null),
                          source: String(source || "document"),
                        };
                        if (evt.type === "wheel") {
                          base.dx = Number(evt.deltaX || 0);
                          base.dy = Number(evt.deltaY || 0);
                        }
                        if (evt.type === "keydown") {
                          base.key = String(evt.key || "").slice(0, 32);
                          base.code = String(evt.code || "").slice(0, 32);
                        }
                        if (evt.type === "input" || evt.type === "change") {
                          const target = evt.target || {};
                          const v = String(target.value || "");
                          base.input_type = String(target.type || "").slice(0, 24);
                          base.value_len = v.length;
                          base.checked = !!target.checked;
                        }
                        store.events.push(base);
                        if (store.events.length > 5000) {
                          store.events.splice(0, store.events.length - 5000);
                        }
                      } catch (e) {}
                    };
                    const pushSynthetic = (type, target, source = "synthetic") => {
                      try {
                        store.events.push({
                          t: Date.now(),
                          type: String(type || ""),
                          x: 0,
                          y: 0,
                          target: String(target || ""),
                          source: String(source || "synthetic"),
                        });
                        if (store.events.length > 5000) {
                          store.events.splice(0, store.events.length - 5000);
                        }
                      } catch (e) {}
                    };
                    const attachIframeShellListeners = (frameEl) => {
                      if (!frameEl || frameEl.__fwManualCaptureBound) return;
                      frameEl.__fwManualCaptureBound = true;
                      ["pointerdown", "pointerup", "mousedown", "mouseup", "click", "focus", "blur", "load"].forEach((name) => {
                        try {
                          frameEl.addEventListener(
                            name,
                            (evt) => pushEvt(evt, "iframe_shell"),
                            { capture: true, passive: true },
                          );
                        } catch (e) {}
                      });
                    };
                    if (!store.installed) {
                      const opts = { capture: true, passive: true };
                      const baseEvents = [
                        "click",
                        "dblclick",
                        "contextmenu",
                        "mousedown",
                        "mouseup",
                        "pointerdown",
                        "pointerup",
                        "focusin",
                        "focusout",
                        "visibilitychange",
                        "keydown",
                        "input",
                        "change",
                      ];
                      const noisyEvents = ["wheel", "scroll", "touchstart", "touchend"];
                      const eventNames = baseEvents.concat(noisyEvents);
                      eventNames.forEach((name) => {
                        try {
                          document.addEventListener(name, (evt) => pushEvt(evt, "document"), opts);
                          if (!lightweight) {
                            window.addEventListener(name, (evt) => pushEvt(evt, "window"), opts);
                          }
                        } catch (e) {}
                      });
                      try {
                        document.querySelectorAll("iframe").forEach((f) => attachIframeShellListeners(f));
                      } catch (e) {}
                      try {
                        const mo = new MutationObserver((records) => {
                          for (const rec of records || []) {
                            for (const node of Array.from(rec.addedNodes || [])) {
                              if (!node || !node.tagName) continue;
                              const tag = String(node.tagName || "").toLowerCase();
                              if (tag === "iframe") {
                                attachIframeShellListeners(node);
                                pushSynthetic("iframe_added", targetInfo(node), "observer");
                              } else if (!lightweight && node.querySelectorAll) {
                                node.querySelectorAll("iframe").forEach((f) => {
                                  attachIframeShellListeners(f);
                                  pushSynthetic("iframe_added", targetInfo(f), "observer");
                                });
                              }
                            }
                            if (rec && rec.type === "attributes") {
                              const target = rec.target;
                              if (target && String(target.tagName || "").toLowerCase() === "iframe") {
                                attachIframeShellListeners(target);
                                pushSynthetic("iframe_attr_changed", targetInfo(target), "observer");
                              }
                            }
                          }
                        });
                        mo.observe(document.documentElement || document.body || document, {
                          childList: true,
                          subtree: true,
                          attributes: !lightweight,
                          attributeFilter: !lightweight
                            ? ["src", "title", "token", "dataframetoken", "style", "class"]
                            : undefined,
                        });
                        store.observer = mo;
                      } catch (e) {}
                      try {
                        let lastActive = "";
                        const activePoll = () => {
                          try {
                            const ae = document.activeElement || null;
                            const info = targetInfo(ae);
                            if (info && info !== lastActive) {
                              lastActive = info;
                              pushSynthetic("active_element_changed", info, "poll");
                            }
                            if (ae && String(ae.tagName || "").toLowerCase() === "iframe") {
                              pushSynthetic("iframe_focus_proxy", info, "poll");
                            }
                          } catch (e) {}
                        };
                        activePoll();
                        store.activePollTimer = window.setInterval(activePoll, lightweight ? 350 : 250);
                      } catch (e) {}
                      store.installed = true;
                    }
                    w.__fwManualCapture = store;
                  }
                  const cursor = Array.isArray(w.__fwManualCapture.events) ? w.__fwManualCapture.events.length : 0;
                  const token = `${Date.now()}_${Math.random().toString(16).slice(2, 8)}`;
                  return { enabled: true, token, cursor, started_at_ms: Date.now(), lightweight: !!lightweight };
                }
                """,
                {"lightweight": bool(lightweight)},
            )
            if isinstance(started, dict):
                return {
                    "enabled": bool(started.get("enabled", False)),
                    "token": str(started.get("token", "") or ""),
                    "cursor": int(started.get("cursor", 0) or 0),
                    "started_at_ms": int(started.get("started_at_ms", 0) or 0),
                    "lightweight": bool(started.get("lightweight", False)),
                }
        except Exception as exc:
            return {"enabled": False, "reason": str(type(exc).__name__)}
        return {"enabled": False, "reason": "capture_start_failed"}

    def _collect_manual_ui_action_capture(self, ctx: Dict[str, Any], *, max_events: int = 300) -> Dict[str, Any]:
        """Collect captured UI actions since capture start cursor."""
        if not isinstance(ctx, dict) or not bool(ctx.get("enabled", False)):
            return {"enabled": False, "reason": "capture_not_enabled"}
        page = getattr(self, "page", None)
        if page is None or not hasattr(page, "evaluate"):
            return {"enabled": False, "reason": "page_unavailable"}
        cursor = max(0, int(ctx.get("cursor", 0) or 0))
        cap = max(0, min(1000, int(max_events if max_events is not None else 300)))
        try:
            payload = page.evaluate(
                """
                ({ cursor, cap }) => {
                  const out = {
                    enabled: false,
                    event_count: 0,
                    dropped_events: 0,
                    event_counts: {},
                    events: [],
                    captured_ms: 0,
                    started_at_ms: 0,
                    ended_at_ms: Date.now(),
                  };
                  const w = window;
                  if (!w.__fwManualCapture || !Array.isArray(w.__fwManualCapture.events)) return out;
                  const all = w.__fwManualCapture.events.slice(Math.max(0, Number(cursor || 0)));
                  out.enabled = true;
                  out.event_count = all.length;
                  out.started_at_ms = all.length > 0 ? Number(all[0].t || 0) : 0;
                  out.captured_ms = all.length > 1 ? Math.max(0, Number(all[all.length - 1].t || 0) - Number(all[0].t || 0)) : 0;
                  for (const evt of all) {
                    const key = String((evt && evt.type) || "unknown");
                    out.event_counts[key] = Number(out.event_counts[key] || 0) + 1;
                  }
                  const maxKeep = Number(cap || 0);
                  if (maxKeep <= 0) {
                    out.dropped_events = all.length;
                    out.events = [];
                  } else if (all.length > maxKeep) {
                    out.dropped_events = all.length - maxKeep;
                    out.events = all.slice(all.length - maxKeep);
                  } else {
                    out.events = all;
                  }
                  return out;
                }
                """,
                {"cursor": cursor, "cap": cap},
            )
            if isinstance(payload, dict):
                payload["enabled"] = bool(payload.get("enabled", False))
                payload["event_count"] = int(payload.get("event_count", 0) or 0)
                payload["dropped_events"] = int(payload.get("dropped_events", 0) or 0)
                if not isinstance(payload.get("event_counts"), dict):
                    payload["event_counts"] = {}
                if not isinstance(payload.get("events"), list):
                    payload["events"] = []
                event_counts = dict(payload.get("event_counts", {}) or {})
                direct_keys = {
                    "click",
                    "dblclick",
                    "contextmenu",
                    "mousedown",
                    "mouseup",
                    "pointerdown",
                    "pointerup",
                    "touchstart",
                    "touchend",
                    "wheel",
                    "scroll",
                    "keydown",
                    "input",
                    "change",
                }
                proxy_keys = {
                    "focusin",
                    "focusout",
                    "visibilitychange",
                    "active_element_changed",
                    "iframe_focus_proxy",
                    "iframe_added",
                    "iframe_attr_changed",
                    "load",
                }
                direct_count = sum(int(event_counts.get(k, 0) or 0) for k in direct_keys)
                proxy_count = sum(int(event_counts.get(k, 0) or 0) for k in proxy_keys)
                signal_quality = "none"
                if direct_count > 0:
                    signal_quality = "direct"
                elif proxy_count > 0:
                    signal_quality = "proxy_only"
                payload["signal_quality"] = signal_quality
                payload["direct_event_count"] = int(direct_count)
                payload["proxy_event_count"] = int(proxy_count)
                return payload
        except Exception as exc:
            return {"enabled": False, "reason": str(type(exc).__name__)}
        return {"enabled": False, "reason": "capture_collect_failed"}

    def _emit_manual_intervention_event(self, payload: Dict[str, Any]) -> None:
        """Best-effort callback for progressive manual-window diagnostics."""
        hook = getattr(self, "manual_intervention_event_hook", None)
        if hook is None:
            return
        if not callable(hook):
            return
        try:
            event_payload = dict(payload or {})
            try:
                hook(event_payload, self)
            except TypeError:
                hook(event_payload)
        except Exception:
            return

    def _record_manual_automation_action(self, action: str, detail: str = "") -> None:
        """Record any automation action that executes while manual window is active."""
        if not bool(getattr(self, "_manual_intervention_active", False)):
            return
        action_name = str(action or "").strip().lower() or "unknown"
        detail_text = str(detail or "")[:140]
        state = getattr(self, "_manual_automation_actions", None)
        if not isinstance(state, dict):
            state = {"count": 0, "counts": {}, "samples": []}
            self._manual_automation_actions = state
        state["count"] = int(state.get("count", 0) or 0) + 1
        counts = state.get("counts")
        if not isinstance(counts, dict):
            counts = {}
            state["counts"] = counts
        counts[action_name] = int(counts.get(action_name, 0) or 0) + 1
        samples = state.get("samples")
        if not isinstance(samples, list):
            samples = []
            state["samples"] = samples
        if len(samples) < 20:
            samples.append(
                {
                    "ts_ms": int(time.time() * 1000),
                    "action": action_name,
                    "detail": detail_text,
                    "request": str(getattr(self, "_manual_intervention_request", "") or ""),
                }
            )
        self._emit_manual_intervention_event(
            {
                "stage": "automation_action",
                "reason": "automation_during_manual_intervention",
                "request": str(getattr(self, "_manual_intervention_request", "") or ""),
                "manual_automation_action": action_name,
                "manual_automation_detail": detail_text,
                "manual_automation_action_count": int(state.get("count", 0) or 0),
                "manual_automation_action_counts": dict(state.get("counts", {}) or {}),
            }
        )
        log.warning(
            "manual_intervention.automation_action_detected action=%s detail=%s request=%s mode=%s count=%s",
            action_name,
            detail_text,
            str(getattr(self, "_manual_intervention_request", "") or ""),
            str(getattr(self, "human_intervention_mode", "") or ""),
            int(state.get("count", 0) or 0),
        )

    def _assert_automation_allowed_during_manual_intervention(
        self,
        action: str,
        detail: str = "",
    ) -> None:
        """Fail closed: block mutating automation actions while manual control is active."""
        if not bool(getattr(self, "_manual_intervention_active", False)):
            return
        if hasattr(self, "_record_manual_automation_action"):
            self._record_manual_automation_action(action, detail)
        request_name = str(getattr(self, "_manual_intervention_request", "") or "")
        message = (
            f"manual_intervention_lock_active action={str(action or '')} "
            f"request={request_name}"
        )
        raise RuntimeError(message)

    def _rebind_live_page_after_target_closed(self) -> Dict[str, Any]:
        """Rebind to an already-open page after target closure without navigating."""
        result: Dict[str, Any] = {
            "attempted": True,
            "recovered": False,
            "reason": "no_live_page",
            "page_switched": False,
            "n_context_pages": 0,
            "final_url": "",
        }
        context = getattr(self, "context", None)
        if context is None:
            result["reason"] = "no_context"
            return result
        previous_page = getattr(self, "page", None)
        live_pages = []
        wait_budget_ms = 2500
        poll_ms = 250
        attempts = max(1, int(wait_budget_ms // poll_ms))
        pages = []
        for idx in range(attempts):
            try:
                pages = list(context.pages or [])
            except Exception:
                pages = []
            result["n_context_pages"] = len(pages)
            live_pages = []
            for page in pages:
                try:
                    if hasattr(page, "is_closed") and page.is_closed():
                        continue
                    live_pages.append(page)
                except Exception:
                    continue
            if live_pages:
                break
            if idx + 1 >= attempts:
                break
            try:
                page_now = getattr(self, "page", None)
                if page_now is not None and hasattr(page_now, "wait_for_timeout"):
                    page_now.wait_for_timeout(poll_ms)
                else:
                    time.sleep(poll_ms / 1000.0)
            except Exception:
                time.sleep(poll_ms / 1000.0)
        if not live_pages:
            return result
        previous_url = ""
        try:
            previous_url = str(getattr(previous_page, "url", "") or "")
        except Exception:
            previous_url = ""
        scored_candidates = []
        for page in live_pages:
            candidate_url = ""
            try:
                candidate_url = str(getattr(page, "url", "") or "")
            except Exception:
                candidate_url = ""
            if not BrowserSession._aux_page_allowed_for_rebind(
                candidate_url=candidate_url,
                preferred_url=previous_url,
            ):
                continue
            score = 0
            if is_verification_url(candidate_url):
                score += 1
            if BrowserSession._host_from_url(candidate_url) == BrowserSession._host_from_url(previous_url):
                score += 3
            if BrowserSession._registrable_root(BrowserSession._host_from_url(candidate_url)) == BrowserSession._registrable_root(
                BrowserSession._host_from_url(previous_url)
            ):
                score += 2
            scored_candidates.append((score, page, candidate_url))
        if not scored_candidates:
            result["reason"] = "no_live_page_same_site"
            return result
        scored_candidates.sort(key=lambda item: int(item[0]), reverse=True)
        _, page_candidate, page_candidate_url = scored_candidates[0]
        try:
            if hasattr(page_candidate, "bring_to_front"):
                page_candidate.bring_to_front()
        except Exception:
            pass
        self.page = page_candidate
        result["page_switched"] = page_candidate is not previous_page
        self._page_interaction = PageInteractionHelper(self.page, self)
        self._verification_challenges = VerificationChallengeHelper(self.page, self)
        self._wait = ElementWaitHelper(self)
        self._typing = TypingInputHelper(self)
        self._click = ElementClickHelper(self)
        self._fill = FormFillHelper(self)
        self._combobox = GoogleFlightsComboboxHelper(self)
        self._framework = BrowserFrameworkHelper(self)
        result["recovered"] = True
        result["reason"] = "rebound_live_page"
        try:
            result["final_url"] = str(getattr(self.page, "url", "") or page_candidate_url or "")
        except Exception:
            result["final_url"] = str(page_candidate_url or "")
        return result

    def allow_manual_verification_intervention(
        self,
        *,
        reason: str = "",
        wait_sec: int = None,
        force: bool = False,
        mode_override: str = "",
    ) -> dict:
        """Allow manual intervention in headed mode when automated recovery fails."""
        duration = max(10, int(wait_sec if wait_sec is not None else self.manual_intervention_timeout_sec))
        requested_reason = str(reason or "verification_challenge")
        intervention_mode = str(getattr(self, "human_intervention_mode", "") or "").strip().lower()
        if intervention_mode not in BrowserSession._HUMAN_INTERVENTION_MODES:
            intervention_mode = "assist" if bool(getattr(self, "allow_human_intervention", False)) else "off"
        force_last_resort = bool(force) and bool(self.last_resort_manual_when_disabled)
        override_mode = str(mode_override or "").strip().lower()
        if override_mode in {"assist", "demo"}:
            intervention_mode = override_mode
        effective_allow = bool(self.allow_human_intervention) or force_last_resort
        page_obj = getattr(self, "page", None)
        page_url_before = ""
        page_title_before = ""
        if page_obj is not None:
            page_url_before = str(getattr(page_obj, "url", "") or "")
            if hasattr(page_obj, "title"):
                try:
                    page_title_before = str(page_obj.title() or "")
                except Exception:
                    page_title_before = ""

        base_result = {
            "used": False,
            "wait_sec": duration,
            "requested_reason": requested_reason,
            "allow_human_intervention": bool(self.allow_human_intervention),
            "human_intervention_mode": intervention_mode,
            "assist_mode": intervention_mode == "assist",
            "demo_mode": intervention_mode == "demo",
            "force_requested": bool(force),
            "force_last_resort": force_last_resort,
            "headless": bool(self.headless),
            "page_available": page_obj is not None,
            "page_url_before": page_url_before,
            "page_title_before": page_title_before,
        }

        def _emit_event(stage: str, **fields: Any) -> None:
            payload = {
                "stage": str(stage or ""),
                "reason": str(fields.pop("reason", "") or ""),
                "mode": intervention_mode,
                "request": requested_reason,
                "wait_sec": duration,
                "url": str(getattr(getattr(self, "page", None), "url", "") or ""),
                "headless": bool(getattr(self, "headless", False)),
                "allow_human_intervention": bool(getattr(self, "allow_human_intervention", False)),
            }
            payload.update(fields or {})
            BrowserSession._emit_manual_intervention_event(self, payload)

        def _is_verification_surface() -> bool:
            page_now = getattr(self, "page", None)
            return bool(is_verification_surface(page_now, fallback_url=page_url_before))

        def _is_skyscanner_px_surface() -> bool:
            page_now = getattr(self, "page", None)
            return bool(is_skyscanner_px_captcha_surface(page_now, fallback_url=page_url_before))

        def _confirm_verification_clear_stable(
            *,
            checks: int = 3,
            interval_ms: int = 900,
        ) -> Dict[str, Any]:
            """Require bounded consecutive non-verification probes before resuming automation."""
            probe_count = max(1, min(4, int(checks or 1)))
            sleep_ms = max(150, min(1500, int(interval_ms or 0)))
            samples: list[Dict[str, Any]] = []
            for idx in range(probe_count):
                still_verification = bool(_is_verification_surface())
                page_now = getattr(self, "page", None)
                page_open = page_now is not None
                page_url = ""
                if page_now is not None:
                    try:
                        if hasattr(page_now, "is_closed") and page_now.is_closed():
                            page_open = False
                    except Exception:
                        page_open = False
                    try:
                        page_url = str(getattr(page_now, "url", "") or "")
                    except Exception:
                        page_url = ""
                sample = {
                    "check": idx + 1,
                    "still_verification": bool(still_verification),
                    "page_open": bool(page_open),
                    "url_prefix": str(page_url or "")[:120],
                }
                samples.append(sample)
                if still_verification or not page_open:
                    return {
                        "stable": False,
                        "checks": probe_count,
                        "samples": samples,
                    }
                if idx + 1 >= probe_count:
                    break
                page_now = getattr(self, "page", None)
                try:
                    if page_now is not None and hasattr(page_now, "wait_for_timeout"):
                        page_now.wait_for_timeout(sleep_ms)
                    else:
                        time.sleep(sleep_ms / 1000.0)
                except Exception:
                    return {
                        "stable": False,
                        "checks": probe_count,
                        "samples": samples,
                    }
            return {
                "stable": True,
                "checks": probe_count,
                "samples": samples,
            }

        def _read_captcha_runtime_fingerprint() -> Dict[str, Any]:
            page_now = getattr(self, "page", None)
            if page_now is None or not hasattr(page_now, "evaluate"):
                return {}
            try:
                payload = page_now.evaluate(
                    """
                    () => {
                      const quickSig = (v) => {
                        const s = String(v || "");
                        let h = 0;
                        for (let i = 0; i < s.length; i += 1) {
                          h = ((h << 5) - h + s.charCodeAt(i)) | 0;
                        }
                        return Math.abs(h).toString(36);
                      };
                      const isVisible = (el) => {
                        try {
                          if (!el) return false;
                          const st = window.getComputedStyle ? window.getComputedStyle(el) : null;
                          if (st && (st.display === "none" || st.visibility === "hidden")) return false;
                          const r = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
                          if (!r) return false;
                          return r.width > 0 && r.height > 0;
                        } catch (e) {
                          return false;
                        }
                      };
                      const attr = (el, name) => {
                        try {
                          return String((el && el.getAttribute && el.getAttribute(name)) || "");
                        } catch (e) {
                          return "";
                        }
                      };
                      let urlUuid = "";
                      let urlVid = "";
                      try {
                        const u = new URL(String(window.location.href || ""));
                        urlUuid = String(u.searchParams.get("uuid") || "");
                        urlVid = String(u.searchParams.get("vid") || "");
                      } catch (e) {}
                      const frames = Array.from(
                        document.querySelectorAll(
                          "#px-captcha iframe, [id*='px-captcha'] iframe, iframe[title*='Human verification' i], iframe[src*='px-cloud.net'], iframe[src*='captcha']"
                        )
                      );
                      const tokenCarrier = frames.find((f) => f && (attr(f, "token") || attr(f, "dataframetoken")))
                        || document.querySelector("iframe[token], iframe[dataframetoken]");
                      const token = tokenCarrier ? attr(tokenCarrier, "token") : "";
                      const dtoken = tokenCarrier ? attr(tokenCarrier, "dataframetoken") : "";
                      const pxContainer = document.querySelector("#px-captcha, [id*='px-captcha']");
                      const frameParts = frames
                        .slice(0, 6)
                        .map((f) => `${attr(f, "src").slice(0, 140)}|${attr(f, "title").slice(0, 64)}|${attr(f, "token").slice(0, 16)}|${attr(f, "dataframetoken").slice(0, 24)}|${isVisible(f) ? "1" : "0"}`)
                        .join("||");
                      const frameSrcSig = quickSig(frameParts);
                      let containerSig = "";
                      if (pxContainer) {
                        const containerText = String((pxContainer.textContent || "")).replace(/\\s+/g, " ").trim().slice(0, 240);
                        const containerStyle = attr(pxContainer, "style").slice(0, 160);
                        const containerClass = String(pxContainer.className || "").slice(0, 120);
                        containerSig = quickSig(`${containerText}|${containerStyle}|${containerClass}`);
                      }
                      let scriptKey = "";
                      try {
                        const scriptNodes = Array.from(document.querySelectorAll("script[src]"));
                        for (const node of scriptNodes) {
                          const src = String(attr(node, "src") || "");
                          if (!src) continue;
                          const lower = src.toLowerCase();
                          if (lower.includes("/captcha.js") || lower.includes("px-cloud.net")) {
                            const m = src.match(/\\/([A-Za-z0-9]{6,})\\/captcha\\.js/);
                            if (m && m[1]) {
                              scriptKey = String(m[1]).slice(0, 24);
                              break;
                            }
                            scriptKey = src.slice(0, 64);
                            break;
                          }
                        }
                      } catch (e) {}
                      let challengeId = "";
                      try {
                        const idNode = document.querySelector("section[class*='identifier'], [class*='identifier']");
                        challengeId = String((idNode && idNode.textContent) || "").replace(/\\s+/g, " ").trim().slice(0, 64);
                      } catch (e) {}
                      const challengeSignature = [
                        token.slice(0, 16),
                        dtoken.slice(0, 24),
                        String(frames.length),
                        String(frames.filter((f) => isVisible(f)).length),
                        frameSrcSig,
                        containerSig,
                        urlUuid.slice(0, 16),
                        urlVid.slice(0, 16),
                        scriptKey.slice(0, 24),
                        challengeId.slice(0, 24),
                      ].join("|");
                      return {
                        iframe_count: frames.length,
                        iframe_visible_count: frames.filter((f) => isVisible(f)).length,
                        token_prefix: token.slice(0, 16),
                        token_len: token.length,
                        dataframe_token_prefix: dtoken.slice(0, 24),
                        captcha_uuid_prefix: urlUuid.slice(0, 16),
                        captcha_vid_prefix: urlVid.slice(0, 16),
                        captcha_identifier_prefix: challengeId.slice(0, 24),
                        captcha_script_key: scriptKey.slice(0, 24),
                        frame_src_signature: frameSrcSig,
                        container_signature: containerSig,
                        challenge_signature: challengeSignature,
                      };
                    }
                    """
                )
                return dict(payload) if isinstance(payload, dict) else {}
            except Exception:
                return {}

        def _read_captcha_html_fallback_fingerprint() -> Dict[str, Any]:
            """Fallback parser for captcha metadata when runtime DOM evaluate misses token attrs."""
            page_now = getattr(self, "page", None)
            if page_now is None:
                return {}
            try:
                html_now = str(page_now.content() or "")
            except Exception:
                return {}
            if not html_now:
                return {}
            try:
                token_match = re.search(r'\btoken="([^"]+)"', html_now, flags=re.IGNORECASE)
                dtoken_match = re.search(r'\bdataframetoken="([^"]+)"', html_now, flags=re.IGNORECASE)
                token = str(token_match.group(1) if token_match else "")
                dtoken = str(dtoken_match.group(1) if dtoken_match else "")
                iframe_count = len(re.findall(r"<iframe\b", html_now, flags=re.IGNORECASE))
                uuid_match = re.search(r"[?&]uuid=([^&\"'\s]+)", html_now, flags=re.IGNORECASE)
                vid_match = re.search(r"[?&]vid=([^&\"'\s]+)", html_now, flags=re.IGNORECASE)
                ident_match = re.search(
                    r"<section[^>]*identifier[^>]*>([^<]+)</section>",
                    html_now,
                    flags=re.IGNORECASE,
                )
                identifier = ""
                if ident_match:
                    identifier = re.sub(r"\s+", " ", ident_match.group(1)).strip()
                script_key = ""
                script_match = re.search(
                    r"/([A-Za-z0-9]{6,})/captcha\.js",
                    html_now,
                    flags=re.IGNORECASE,
                )
                if script_match:
                    script_key = str(script_match.group(1) or "")
                frame_src_match = re.search(
                    r'<iframe[^>]*\bsrc="([^"]+)"[^>]*dataframetoken',
                    html_now,
                    flags=re.IGNORECASE,
                )
                frame_src = str(frame_src_match.group(1) if frame_src_match else "")

                def _sig(text: str) -> str:
                    if not text:
                        return ""
                    h = 0
                    for ch in text:
                        h = ((h << 5) - h + ord(ch)) & 0xFFFFFFFF
                    return format(h, "x")[:12]

                frame_src_sig = _sig(frame_src[:240])
                challenge_signature = "|".join(
                    [
                        token[:16],
                        dtoken[:24],
                        str(iframe_count),
                        "",
                        frame_src_sig,
                        "",
                        str(uuid_match.group(1) if uuid_match else "")[:16],
                        str(vid_match.group(1) if vid_match else "")[:16],
                        script_key[:24],
                        identifier[:24],
                    ]
                )
                return {
                    "token_prefix": token[:16],
                    "token_len": len(token),
                    "dataframe_token_prefix": dtoken[:24],
                    "iframe_count": int(iframe_count),
                    "captcha_uuid_prefix": str(uuid_match.group(1) if uuid_match else "")[:16],
                    "captcha_vid_prefix": str(vid_match.group(1) if vid_match else "")[:16],
                    "captcha_identifier_prefix": identifier[:24],
                    "captcha_script_key": script_key[:24],
                    "frame_src_signature": frame_src_sig,
                    "challenge_signature": challenge_signature,
                    "token_source": "html_fallback",
                }
            except Exception:
                return {}

        if not effective_allow:
            result = dict(base_result)
            result["reason"] = "manual_intervention_disabled"
            log.info(
                "manual_intervention.skip reason=%s mode=%s request=%s wait_sec=%s allow=%s effective_allow=%s force=%s headless=%s page_available=%s url=%s",
                result["reason"],
                intervention_mode,
                requested_reason,
                duration,
                bool(self.allow_human_intervention),
                effective_allow,
                bool(force),
                bool(self.headless),
                page_obj is not None,
                page_url_before,
            )
            return result
        if self.headless:
            result = dict(base_result)
            result["reason"] = "headless_mode"
            log.info(
                "manual_intervention.skip reason=%s mode=%s request=%s wait_sec=%s allow=%s headless=%s page_available=%s url=%s",
                result["reason"],
                intervention_mode,
                requested_reason,
                duration,
                bool(self.allow_human_intervention),
                bool(self.headless),
                page_obj is not None,
                page_url_before,
            )
            return result
        if self.page is None:
            result = dict(base_result)
            result["reason"] = "page_unavailable"
            log.info(
                "manual_intervention.skip reason=%s mode=%s request=%s wait_sec=%s allow=%s headless=%s page_available=%s",
                result["reason"],
                intervention_mode,
                requested_reason,
                duration,
                bool(self.allow_human_intervention),
                bool(self.headless),
                False,
            )
            return result

        start = time.monotonic()
        brought_to_front = False
        recovery_attempts = 0
        recovery_max_attempts = 2
        recovery_events = []
        action_capture_ctx: Dict[str, Any] = {"enabled": False, "reason": "not_started"}
        last_action_summary: Dict[str, Any] = {"enabled": False, "reason": "not_sampled"}
        self._manual_intervention_active = True
        self._manual_intervention_request = requested_reason
        self._manual_automation_actions = {"count": 0, "counts": {}, "samples": []}
        signal_prev_handlers = []
        signal_interrupt: Dict[str, str] = {"name": ""}
        started_on_verification_surface = _is_verification_surface()
        started_on_skyscanner_captcha = _is_skyscanner_px_surface()
        challenge_cleared_during_window = False
        captcha_fingerprints_seen: list[str] = []
        captcha_token_change_count = 0
        last_captcha_fingerprint = ""
        captcha_challenge_signatures_seen: list[str] = []
        captcha_challenge_change_count = 0
        last_captcha_challenge_signature = ""
        last_captcha_probe: Dict[str, Any] = {}
        proxy_interaction_emitted = False
        last_ui_event_count = 0
        manual_extension_rounds_left = 2 if intervention_mode in {"assist", "demo"} else 0
        manual_extension_ms = min(45_000, max(15_000, int(duration * 1000 // 2)))
        capture_restart_count = 0
        last_capture_restart_ms = 0
        capture_token_active = ""
        capture_prev_event_count = 0
        capture_prev_counts: Dict[str, int] = {}
        aggregate_event_count = 0
        aggregate_event_counts: Dict[str, int] = {}
        aggregate_captured_ms = 0
        aggregate_dropped_events = 0

        def _bump_aggregate_capture(summary: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
            nonlocal capture_token_active
            nonlocal capture_prev_event_count
            nonlocal capture_prev_counts
            nonlocal aggregate_event_count
            nonlocal aggregate_event_counts
            nonlocal aggregate_captured_ms
            nonlocal aggregate_dropped_events
            payload = dict(summary or {}) if isinstance(summary, dict) else {}
            if not bool(payload.get("enabled", False)):
                direct_keys = {
                    "click",
                    "dblclick",
                    "contextmenu",
                    "mousedown",
                    "mouseup",
                    "pointerdown",
                    "pointerup",
                    "touchstart",
                    "touchend",
                    "wheel",
                    "scroll",
                    "keydown",
                    "input",
                    "change",
                }
                proxy_keys = {
                    "focusin",
                    "focusout",
                    "visibilitychange",
                    "active_element_changed",
                    "iframe_focus_proxy",
                    "iframe_added",
                    "iframe_attr_changed",
                    "load",
                }
                direct_total = sum(int(aggregate_event_counts.get(k, 0) or 0) for k in direct_keys)
                proxy_total = sum(int(aggregate_event_counts.get(k, 0) or 0) for k in proxy_keys)
                signal_quality = "none"
                if direct_total > 0:
                    signal_quality = "direct"
                elif proxy_total > 0:
                    signal_quality = "proxy_only"
                payload["event_count"] = int(aggregate_event_count)
                payload["event_counts"] = dict(aggregate_event_counts)
                payload["captured_ms"] = int(aggregate_captured_ms)
                payload["dropped_events"] = int(aggregate_dropped_events)
                payload["direct_event_count"] = int(direct_total)
                payload["proxy_event_count"] = int(proxy_total)
                payload["signal_quality"] = signal_quality
                payload["capture_restart_count"] = int(capture_restart_count)
                return payload
            token_now = str((ctx or {}).get("token", "") or "")
            if token_now != capture_token_active:
                capture_token_active = token_now
                capture_prev_event_count = 0
                capture_prev_counts = {}
            current_event_count = int(payload.get("event_count", 0) or 0)
            delta_events = max(0, current_event_count - int(capture_prev_event_count))
            capture_prev_event_count = current_event_count
            aggregate_event_count += int(delta_events)
            current_counts_raw = dict(payload.get("event_counts", {}) or {})
            normalized_counts = {str(k): int(v or 0) for k, v in current_counts_raw.items()}
            for key, cur_val in normalized_counts.items():
                prev_val = int(capture_prev_counts.get(key, 0) or 0)
                delta_val = max(0, int(cur_val) - prev_val)
                if delta_val > 0:
                    aggregate_event_counts[key] = int(aggregate_event_counts.get(key, 0) or 0) + int(
                        delta_val
                    )
            capture_prev_counts = normalized_counts
            aggregate_captured_ms += max(0, int(payload.get("captured_ms", 0) or 0))
            aggregate_dropped_events += max(0, int(payload.get("dropped_events", 0) or 0))
            direct_keys = {
                "click",
                "dblclick",
                "contextmenu",
                "mousedown",
                "mouseup",
                "pointerdown",
                "pointerup",
                "touchstart",
                "touchend",
                "wheel",
                "scroll",
                "keydown",
                "input",
                "change",
            }
            proxy_keys = {
                "focusin",
                "focusout",
                "visibilitychange",
                "active_element_changed",
                "iframe_focus_proxy",
                "iframe_added",
                "iframe_attr_changed",
                "load",
            }
            direct_total = sum(int(aggregate_event_counts.get(k, 0) or 0) for k in direct_keys)
            proxy_total = sum(int(aggregate_event_counts.get(k, 0) or 0) for k in proxy_keys)
            signal_quality = "none"
            if direct_total > 0:
                signal_quality = "direct"
            elif proxy_total > 0:
                signal_quality = "proxy_only"
            payload["event_count"] = int(aggregate_event_count)
            payload["event_counts"] = dict(aggregate_event_counts)
            payload["captured_ms"] = int(aggregate_captured_ms)
            payload["dropped_events"] = int(aggregate_dropped_events)
            payload["direct_event_count"] = int(direct_total)
            payload["proxy_event_count"] = int(proxy_total)
            payload["signal_quality"] = signal_quality
            payload["capture_restart_count"] = int(capture_restart_count)
            return payload

        def _finalize_capture(summary: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
            payload = _bump_aggregate_capture(summary, ctx)
            payload["event_count"] = int(aggregate_event_count)
            payload["event_counts"] = dict(aggregate_event_counts)
            payload["captured_ms"] = int(aggregate_captured_ms)
            payload["dropped_events"] = int(aggregate_dropped_events)
            payload["capture_restart_count"] = int(capture_restart_count)
            return payload

        def _manual_signal_handler(signum, _frame):
            try:
                signal_interrupt["name"] = str(signal.Signals(signum).name)
            except Exception:
                signal_interrupt["name"] = str(signum)
            raise KeyboardInterrupt(signal_interrupt["name"])

        if threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    previous = signal.getsignal(sig)
                    signal_prev_handlers.append((sig, previous))
                    signal.signal(sig, _manual_signal_handler)
                except Exception:
                    continue
        try:
            # WARNING: Respect target-site TOS and legal/ethical constraints before manual solve workflows.
            print(
                f"[manual-intervention] {requested_reason} - "
                f"please solve in browser now (window {duration}s)."
            )
            log.warning(
                "manual_intervention.start mode=%s request=%s wait_sec=%s url=%s title=%s",
                intervention_mode,
                requested_reason,
                duration,
                page_url_before,
                page_title_before[:120],
            )
            if hasattr(self.page, "bring_to_front"):
                self.page.bring_to_front()
                brought_to_front = True
            if intervention_mode in {"assist", "demo"}:
                action_capture_ctx = BrowserSession._begin_manual_ui_action_capture(
                    self,
                    lightweight=(intervention_mode == "demo"),
                )
            if hasattr(self, "_enforce_single_page_policy_snapshot"):
                self._enforce_single_page_policy_snapshot(reason="manual_start")
            _emit_event("start", reason="manual_started", brought_to_front=brought_to_front)
            remaining_ms = int(duration * 1000)
            while True:
                if remaining_ms <= 0:
                    event_counts = dict((last_action_summary or {}).get("event_counts", {}) or {})
                    proxy_interaction_hits = int(
                        event_counts.get("active_element_changed", 0) or 0
                    ) + int(event_counts.get("iframe_focus_proxy", 0) or 0)
                    proxy_churn_hits = int(event_counts.get("iframe_added", 0) or 0) + int(
                        event_counts.get("iframe_attr_changed", 0) or 0
                    )
                    event_count = int((last_action_summary or {}).get("event_count", 0) or 0)
                    direct_event_count = int(
                        (last_action_summary or {}).get("direct_event_count", 0) or 0
                    )
                    signal_quality = str((last_action_summary or {}).get("signal_quality", "") or "")
                    still_captcha = started_on_verification_surface and _is_verification_surface()
                    if intervention_mode == "demo":
                        # Demo mode should not extend on proxy-only churn (iframe/token
                        # re-issues). Require direct user interaction evidence.
                        should_extend = bool(
                            still_captcha
                            and manual_extension_rounds_left > 0
                            and (
                                direct_event_count >= 1
                                or (
                                    signal_quality == "direct"
                                    and event_count >= 2
                                )
                            )
                        )
                    else:
                        should_extend = bool(
                            still_captcha
                            and manual_extension_rounds_left > 0
                            and (
                                captcha_token_change_count >= 2
                                or captcha_challenge_change_count >= 1
                                or proxy_interaction_hits >= 2
                                or proxy_churn_hits >= 2
                                or event_count >= 8
                            )
                        )
                    if should_extend:
                        manual_extension_rounds_left -= 1
                        remaining_ms = int(manual_extension_ms)
                        log.warning(
                            "manual_intervention.extend mode=%s request=%s extension_ms=%s rounds_left=%s captcha_token_changes=%s captcha_challenge_changes=%s proxy_interaction_hits=%s proxy_churn_hits=%s direct_event_count=%s event_count=%s signal_quality=%s",
                            intervention_mode,
                            requested_reason,
                            int(manual_extension_ms),
                            int(manual_extension_rounds_left),
                            int(captcha_token_change_count),
                            int(captcha_challenge_change_count),
                            int(proxy_interaction_hits),
                            int(proxy_churn_hits),
                            int(direct_event_count),
                            int(event_count),
                            signal_quality,
                        )
                        _emit_event(
                            "extend",
                            reason="manual_window_extended_for_captcha_reissue",
                            extension_ms=int(manual_extension_ms),
                            extension_rounds_left=int(manual_extension_rounds_left),
                            captcha_token_change_count=int(captcha_token_change_count),
                            captcha_challenge_change_count=int(captcha_challenge_change_count),
                            ui_action_event_count=int(event_count),
                            direct_event_count=int(direct_event_count),
                            proxy_interaction_hits=int(proxy_interaction_hits),
                            proxy_churn_hits=int(proxy_churn_hits),
                            signal_quality=signal_quality,
                        )
                        continue
                    break
                if signal_interrupt["name"]:
                    raise KeyboardInterrupt(signal_interrupt["name"])
                if hasattr(self, "_enforce_single_page_policy_snapshot"):
                    self._enforce_single_page_policy_snapshot(reason="manual_heartbeat")
                action_summary = BrowserSession._collect_manual_ui_action_capture(
                    self,
                    action_capture_ctx,
                    max_events=150,
                )
                if (
                    intervention_mode in {"assist", "demo"}
                    and remaining_ms > 1500
                    and not bool((action_summary or {}).get("enabled", False))
                ):
                    now_ms = int(time.time() * 1000)
                    if now_ms - int(last_capture_restart_ms or 0) >= 1200:
                        restarted_capture = BrowserSession._begin_manual_ui_action_capture(
                            self,
                            lightweight=(intervention_mode == "demo"),
                        )
                        if bool((restarted_capture or {}).get("enabled", False)):
                            capture_restart_count += 1
                            last_capture_restart_ms = now_ms
                            action_capture_ctx = dict(restarted_capture)
                            capture_token_active = ""
                            capture_prev_event_count = 0
                            capture_prev_counts = {}
                            _emit_event(
                                "capture_rearmed",
                                reason="manual_capture_rearmed_after_navigation",
                                capture_restart_count=int(capture_restart_count),
                            )
                            action_summary = BrowserSession._collect_manual_ui_action_capture(
                                self,
                                action_capture_ctx,
                                max_events=150,
                            )
                action_summary = _bump_aggregate_capture(action_summary, action_capture_ctx)
                if isinstance(action_summary, dict):
                    last_action_summary = dict(action_summary)
                current_ui_event_count = int(action_summary.get("event_count", 0) or 0)
                ui_activity_increased = current_ui_event_count > int(last_ui_event_count)
                if ui_activity_increased:
                    last_ui_event_count = current_ui_event_count
                captcha_fp_payload: Dict[str, Any] = {}
                if started_on_skyscanner_captcha:
                    runtime_fp = _read_captcha_runtime_fingerprint()
                    html_fallback_fp = {}
                    if int(runtime_fp.get("token_len", 0) or 0) <= 0:
                        html_fallback_fp = _read_captcha_html_fallback_fingerprint()
                    captcha_fp_payload = dict(runtime_fp or {})
                    for key, value in dict(html_fallback_fp or {}).items():
                        if key not in captcha_fp_payload:
                            captcha_fp_payload[key] = value
                            continue
                        current = captcha_fp_payload.get(key)
                        if key in {"token_len", "iframe_count"}:
                            if int(current or 0) <= 0 and int(value or 0) > 0:
                                captcha_fp_payload[key] = value
                        elif not str(current or "").strip() and str(value or "").strip():
                            captcha_fp_payload[key] = value
                    if str(captcha_fp_payload.get("token_source", "") or "").strip() == "":
                        captcha_fp_payload["token_source"] = (
                            "runtime_dom"
                            if int(runtime_fp.get("token_len", 0) or 0) > 0
                            else str(html_fallback_fp.get("token_source", "") or "runtime_dom")
                        )
                    last_captcha_probe = dict(captcha_fp_payload or {})
                    token_prefix = str(captcha_fp_payload.get("token_prefix", "") or "").strip()
                    data_prefix = str(captcha_fp_payload.get("dataframe_token_prefix", "") or "").strip()
                    iframe_count = int(captcha_fp_payload.get("iframe_count", 0) or 0)
                    visible_count = int(captcha_fp_payload.get("iframe_visible_count", 0) or 0)
                    fingerprint = f"{token_prefix}|{data_prefix}|{iframe_count}|{visible_count}"
                    if fingerprint and fingerprint != last_captcha_fingerprint:
                        if last_captcha_fingerprint:
                            captcha_token_change_count += 1
                        last_captcha_fingerprint = fingerprint
                        if len(captcha_fingerprints_seen) < 12:
                            captcha_fingerprints_seen.append(fingerprint)
                    challenge_signature = str(
                        captcha_fp_payload.get("challenge_signature", "") or ""
                    ).strip()
                    if challenge_signature and challenge_signature != last_captcha_challenge_signature:
                        if last_captcha_challenge_signature:
                            captcha_challenge_change_count += 1
                        last_captcha_challenge_signature = challenge_signature
                        if len(captcha_challenge_signatures_seen) < 16:
                            captcha_challenge_signatures_seen.append(challenge_signature)
                    if not proxy_interaction_emitted:
                        counts_now = dict(action_summary.get("event_counts", {}) or {})
                        proxy_hits = int(counts_now.get("iframe_focus_proxy", 0) or 0) + int(
                            counts_now.get("active_element_changed", 0) or 0
                        )
                        if proxy_hits > 0:
                            proxy_interaction_emitted = True
                            _emit_event(
                                "human_interaction_proxy_detected",
                                reason="human_interaction_proxy_detected",
                                proxy_event_count=proxy_hits,
                                ui_action_event_counts=counts_now,
                                captcha_token_source=str(
                                    captcha_fp_payload.get("token_source", "") or ""
                                ),
                                captcha_challenge_change_count=int(captcha_challenge_change_count),
                            )
                _emit_event(
                    "heartbeat",
                    reason="manual_in_progress",
                    elapsed_ms=int((time.monotonic() - start) * 1000),
                    remaining_ms=int(remaining_ms),
                    ui_action_event_count=int(action_summary.get("event_count", 0) or 0),
                    ui_action_event_counts=dict(action_summary.get("event_counts", {}) or {}),
                    manual_automation_action_count=int(
                        ((self._manual_automation_actions or {}).get("count", 0) or 0)
                    ),
                    manual_automation_action_counts=dict(
                        ((self._manual_automation_actions or {}).get("counts", {}) or {})
                    ),
                    captcha_token_prefix=str(captcha_fp_payload.get("token_prefix", "") or ""),
                    captcha_token_len=int(captcha_fp_payload.get("token_len", 0) or 0),
                    captcha_dataframe_token_prefix=str(
                        captcha_fp_payload.get("dataframe_token_prefix", "") or ""
                    ),
                    captcha_iframe_count=int(captcha_fp_payload.get("iframe_count", 0) or 0),
                    captcha_iframe_visible_count=int(
                        captcha_fp_payload.get("iframe_visible_count", 0) or 0
                    ),
                    captcha_token_change_count=int(captcha_token_change_count),
                    captcha_challenge_signature_prefix=str(
                        str(captcha_fp_payload.get("challenge_signature", "") or "")[:24]
                    ),
                    captcha_challenge_change_count=int(captcha_challenge_change_count),
                    captcha_uuid_prefix=str(captcha_fp_payload.get("captcha_uuid_prefix", "") or ""),
                    captcha_vid_prefix=str(captcha_fp_payload.get("captcha_vid_prefix", "") or ""),
                    captcha_identifier_prefix=str(
                        captcha_fp_payload.get("captcha_identifier_prefix", "") or ""
                    ),
                    captcha_script_key=str(captcha_fp_payload.get("captcha_script_key", "") or ""),
                    captcha_frame_src_signature=str(
                        captcha_fp_payload.get("frame_src_signature", "") or ""
                    ),
                    captcha_container_signature=str(
                        captcha_fp_payload.get("container_signature", "") or ""
                    ),
                    captcha_token_source=str(captcha_fp_payload.get("token_source", "") or ""),
                    signal_quality=str(action_summary.get("signal_quality", "") or ""),
                    direct_event_count=int(action_summary.get("direct_event_count", 0) or 0),
                    proxy_event_count=int(action_summary.get("proxy_event_count", 0) or 0),
                    ui_action_captured_ms=int(action_summary.get("captured_ms", 0) or 0),
                    ui_action_dropped_events=int(action_summary.get("dropped_events", 0) or 0),
                    capture_restart_count=int(action_summary.get("capture_restart_count", 0) or 0),
                )
                if started_on_verification_surface and not _is_verification_surface():
                    stable_started = time.monotonic()
                    stable_probe = _confirm_verification_clear_stable()
                    stable_spent_ms = int((time.monotonic() - stable_started) * 1000)
                    remaining_ms = max(0, remaining_ms - max(0, stable_spent_ms))
                    if not bool((stable_probe or {}).get("stable", False)):
                        _emit_event(
                            "clearance_unstable",
                            reason="manual_clearance_unstable",
                            elapsed_ms=int((time.monotonic() - start) * 1000),
                            remaining_ms=int(remaining_ms),
                            stable_checks=int((stable_probe or {}).get("checks", 0) or 0),
                            stable_samples=list((stable_probe or {}).get("samples", []) or [])[:4],
                        )
                        continue
                    if intervention_mode == "demo":
                        challenge_cleared_during_window = True
                        started_on_verification_surface = False
                        started_on_skyscanner_captcha = False
                        _emit_event(
                            "clearance_reached_continue_demo",
                            reason="manual_clearance_reached_continue_demo",
                            elapsed_ms=int((time.monotonic() - start) * 1000),
                            remaining_ms=int(remaining_ms),
                            stable_checks=int((stable_probe or {}).get("checks", 0) or 0),
                            stable_samples=list((stable_probe or {}).get("samples", []) or [])[:4],
                        )
                        continue
                    elapsed_ms = int((time.monotonic() - start) * 1000)
                    page_url_after = str(getattr(self.page, "url", "") or "")
                    page_title_after = ""
                    if hasattr(self.page, "title"):
                        try:
                            page_title_after = str(self.page.title() or "")
                        except Exception:
                            page_title_after = ""
                    result = dict(base_result)
                    terminal_capture = BrowserSession._collect_manual_ui_action_capture(
                        self, action_capture_ctx
                    )
                    if not bool((terminal_capture or {}).get("enabled", False)) and bool(
                        (last_action_summary or {}).get("enabled", False)
                    ):
                        terminal_capture = dict(last_action_summary)
                        terminal_capture["fallback_from_heartbeat"] = True
                    terminal_capture = _finalize_capture(terminal_capture, action_capture_ctx)
                    result.update(
                        {
                            "used": True,
                            "reason": "manual_challenge_cleared",
                            "elapsed_ms": elapsed_ms,
                            "brought_to_front": brought_to_front,
                            "page_url_after": page_url_after,
                            "page_title_after": page_title_after,
                            "recovery_attempts": recovery_attempts,
                            "recovery_events": list(recovery_events)[:3],
                            "ui_action_capture": terminal_capture,
                            "automation_activity_during_manual": dict(
                                self._manual_automation_actions or {}
                            ),
                            "captcha_token_change_count": int(captcha_token_change_count),
                            "captcha_fingerprints_seen": list(captcha_fingerprints_seen)[:12],
                            "captcha_challenge_change_count": int(captcha_challenge_change_count),
                            "captcha_challenge_signatures_seen": list(
                                captcha_challenge_signatures_seen
                            )[:16],
                            "captcha_last_probe": dict(last_captcha_probe or {}),
                            "captcha_token_source": str(
                                (last_captcha_probe or {}).get("token_source", "") or ""
                            ),
                            "clearance_stable": True,
                            "clearance_stable_checks": int(
                                (stable_probe or {}).get("checks", 0) or 0
                            ),
                            "clearance_stable_samples": list(
                                (stable_probe or {}).get("samples", []) or []
                            )[:4],
                        }
                    )
                    log.info(
                        "manual_intervention.done reason=%s mode=%s request=%s wait_sec=%s elapsed_ms=%s brought_to_front=%s recoveries=%s url_before=%s url_after=%s",
                        result["reason"],
                        intervention_mode,
                        requested_reason,
                        duration,
                        elapsed_ms,
                        brought_to_front,
                        recovery_attempts,
                        page_url_before,
                        page_url_after,
                    )
                    _emit_event(
                        "done",
                        reason=str(result.get("reason", "") or "manual_window_elapsed"),
                        elapsed_ms=elapsed_ms,
                        page_url_after=page_url_after,
                        recovery_attempts=int(recovery_attempts),
                    )
                    return result
                base_chunk_ms = 3000 if ui_activity_increased else 2000
                if intervention_mode == "assist":
                    base_chunk_ms = 2500 if ui_activity_increased else 1800
                chunk_ms = min(base_chunk_ms, max(250, remaining_ms))
                page_now = getattr(self, "page", None)
                try:
                    if page_now is None:
                        raise RuntimeError("PageUnavailableDuringManualIntervention")
                    if hasattr(page_now, "is_closed") and page_now.is_closed():
                        raise RuntimeError("TargetClosedError")
                    if hasattr(page_now, "wait_for_timeout"):
                        page_now.wait_for_timeout(chunk_ms)
                    else:
                        time.sleep(chunk_ms / 1000.0)
                    remaining_ms = max(0, remaining_ms - int(chunk_ms))
                except Exception as wait_exc:
                    err_name = str(type(wait_exc).__name__)
                    err_msg = str(wait_exc or "")
                    can_retry_closed_target = (
                        err_name == "TargetClosedError"
                        or "Target page, context or browser has been closed" in err_msg
                        or "TargetClosedError" in err_msg
                    )
                    if (
                        can_retry_closed_target
                        and intervention_mode in {"assist", "demo"}
                        and not bool(force_last_resort)
                    ):
                        # In manual modes, recover only by rebinding to an existing live page.
                        try:
                            recovery = BrowserSession._rebind_live_page_after_target_closed(self)
                        except Exception as recovery_exc:
                            recovery = {
                                "attempted": True,
                                "recovered": False,
                                "reason": "rebind_exception",
                                "error": str(type(recovery_exc).__name__),
                            }
                        recovery_events.append(recovery)
                        recovered = bool((recovery or {}).get("recovered", False))
                        if recovered:
                            recovery_attempts += 1
                        log.warning(
                            "manual_intervention.rebind attempt=%s request=%s recovered=%s reason=%s",
                            int(len(recovery_events)),
                            requested_reason,
                            recovered,
                            str((recovery or {}).get("reason", "")),
                        )
                        _emit_event(
                            "recover",
                            reason=str((recovery or {}).get("reason", "") or "rebind"),
                            recovery_attempt=int(len(recovery_events)),
                            recovered=recovered,
                            non_navigational=True,
                        )
                        if recovered:
                            continue
                        raise wait_exc
                    if (
                        can_retry_closed_target
                        and intervention_mode not in {"assist", "demo"}
                        and not bool(force_last_resort)
                        and recovery_attempts < recovery_max_attempts
                        and hasattr(self, "recover_page_after_target_closed")
                    ):
                        recovery_attempts += 1
                        preferred_url = str(page_url_before or "https://www.skyscanner.com/flights")
                        try:
                            recovery = self.recover_page_after_target_closed(preferred_url=preferred_url)
                        except Exception as recovery_exc:
                            recovery = {
                                "attempted": True,
                                "recovered": False,
                                "reason": "recovery_exception",
                                "error": str(type(recovery_exc).__name__),
                            }
                        recovery_events.append(recovery)
                        log.warning(
                            "manual_intervention.recover attempt=%s/%s request=%s recovered=%s reason=%s",
                            recovery_attempts,
                            recovery_max_attempts,
                            requested_reason,
                            bool((recovery or {}).get("recovered", False)),
                            str((recovery or {}).get("reason", "")),
                        )
                        _emit_event(
                            "recover",
                            reason=str((recovery or {}).get("reason", "") or "recovery"),
                            recovery_attempt=int(recovery_attempts),
                            recovered=bool((recovery or {}).get("recovered", False)),
                        )
                        if bool((recovery or {}).get("recovered", False)):
                            page_now = getattr(self, "page", None)
                            if page_now is not None and hasattr(page_now, "bring_to_front"):
                                try:
                                    page_now.bring_to_front()
                                    brought_to_front = True
                                except Exception:
                                    pass
                            continue
                    raise wait_exc
            page_url_after = str(getattr(self.page, "url", "") or "")
            page_title_after = ""
            if hasattr(self.page, "title"):
                try:
                    page_title_after = str(self.page.title() or "")
                except Exception:
                    page_title_after = ""
            elapsed_ms = int((time.monotonic() - start) * 1000)
            result = dict(base_result)
            terminal_capture = BrowserSession._collect_manual_ui_action_capture(
                self, action_capture_ctx
            )
            if not bool((terminal_capture or {}).get("enabled", False)) and bool(
                (last_action_summary or {}).get("enabled", False)
            ):
                terminal_capture = dict(last_action_summary)
                terminal_capture["fallback_from_heartbeat"] = True
            terminal_capture = _finalize_capture(terminal_capture, action_capture_ctx)
            result.update(
                {
                    "used": True,
                    "reason": "manual_window_elapsed",
                    "elapsed_ms": elapsed_ms,
                    "brought_to_front": brought_to_front,
                    "page_url_after": page_url_after,
                    "page_title_after": page_title_after,
                    "recovery_attempts": recovery_attempts,
                    "recovery_events": list(recovery_events)[:3],
                    "ui_action_capture": terminal_capture,
                    "automation_activity_during_manual": dict(
                        self._manual_automation_actions or {}
                    ),
                    "captcha_token_change_count": int(captcha_token_change_count),
                    "captcha_fingerprints_seen": list(captcha_fingerprints_seen)[:12],
                    "captcha_challenge_change_count": int(captcha_challenge_change_count),
                    "captcha_challenge_signatures_seen": list(captcha_challenge_signatures_seen)[
                        :16
                    ],
                    "captcha_last_probe": dict(last_captcha_probe or {}),
                    "captcha_token_source": str(
                        (last_captcha_probe or {}).get("token_source", "") or ""
                    ),
                    "challenge_cleared_during_window": bool(challenge_cleared_during_window),
                }
            )
            log.info(
                "manual_intervention.done reason=%s mode=%s request=%s wait_sec=%s elapsed_ms=%s brought_to_front=%s recoveries=%s url_before=%s url_after=%s",
                result["reason"],
                intervention_mode,
                requested_reason,
                duration,
                elapsed_ms,
                brought_to_front,
                recovery_attempts,
                page_url_before,
                page_url_after,
            )
            _emit_event(
                "done",
                reason=str(result.get("reason", "") or "manual_window_elapsed"),
                elapsed_ms=elapsed_ms,
                page_url_after=page_url_after,
                recovery_attempts=int(recovery_attempts),
                captcha_challenge_change_count=int(captcha_challenge_change_count),
            )
            return result
        except KeyboardInterrupt:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            interrupt_error = str(signal_interrupt.get("name", "") or "KeyboardInterrupt")
            page_obj_after = getattr(self, "page", None)
            page_available_after = page_obj_after is not None
            page_closed_after = False
            page_url_after = ""
            if page_obj_after is not None:
                page_url_after = str(getattr(page_obj_after, "url", "") or "")
                if hasattr(page_obj_after, "is_closed"):
                    try:
                        page_closed_after = bool(page_obj_after.is_closed())
                    except Exception:
                        page_closed_after = True
            result = dict(base_result)
            terminal_capture = BrowserSession._collect_manual_ui_action_capture(
                self, action_capture_ctx
            )
            if not bool((terminal_capture or {}).get("enabled", False)) and bool(
                (last_action_summary or {}).get("enabled", False)
            ):
                terminal_capture = dict(last_action_summary)
                terminal_capture["fallback_from_heartbeat"] = True
            terminal_capture = _finalize_capture(terminal_capture, action_capture_ctx)
            result.update(
                {
                    "used": True,
                    "reason": "manual_intervention_interrupted",
                    "elapsed_ms": elapsed_ms,
                    "brought_to_front": brought_to_front,
                    "error": interrupt_error,
                    "page_available_after": page_available_after,
                    "page_closed_after": page_closed_after,
                    "page_url_after": page_url_after,
                    "recovery_attempts": recovery_attempts,
                    "recovery_events": list(recovery_events)[:3],
                    "ui_action_capture": terminal_capture,
                    "automation_activity_during_manual": dict(
                        self._manual_automation_actions or {}
                    ),
                    "captcha_token_change_count": int(captcha_token_change_count),
                    "captcha_fingerprints_seen": list(captcha_fingerprints_seen)[:12],
                    "captcha_challenge_change_count": int(captcha_challenge_change_count),
                    "captcha_challenge_signatures_seen": list(captcha_challenge_signatures_seen)[
                        :16
                    ],
                    "captcha_last_probe": dict(last_captcha_probe or {}),
                    "captcha_token_source": str(
                        (last_captcha_probe or {}).get("token_source", "") or ""
                    ),
                }
            )
            log.warning(
                "manual_intervention.interrupted reason=%s mode=%s request=%s wait_sec=%s elapsed_ms=%s recoveries=%s page_available_after=%s page_closed_after=%s url_after=%s",
                result["reason"],
                intervention_mode,
                requested_reason,
                duration,
                elapsed_ms,
                recovery_attempts,
                page_available_after,
                page_closed_after,
                page_url_after,
            )
            _emit_event(
                "interrupted",
                reason=str(result.get("reason", "") or "manual_intervention_interrupted"),
                elapsed_ms=elapsed_ms,
                error=interrupt_error,
                page_url_after=page_url_after,
                captcha_token_change_count=int(captcha_token_change_count),
                captcha_challenge_change_count=int(captcha_challenge_change_count),
            )
            return result
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            error_type = str(type(exc).__name__)
            if error_type == "RuntimeError" and "TargetClosedError" in str(exc or ""):
                error_type = "TargetClosedError"
            page_obj_after = getattr(self, "page", None)
            page_available_after = page_obj_after is not None
            page_closed_after = False
            page_url_after = ""
            if page_obj_after is not None:
                page_url_after = str(getattr(page_obj_after, "url", "") or "")
                if hasattr(page_obj_after, "is_closed"):
                    try:
                        page_closed_after = bool(page_obj_after.is_closed())
                    except Exception:
                        page_closed_after = True
            result = dict(base_result)
            terminal_capture = BrowserSession._collect_manual_ui_action_capture(
                self, action_capture_ctx
            )
            if not bool((terminal_capture or {}).get("enabled", False)) and bool(
                (last_action_summary or {}).get("enabled", False)
            ):
                terminal_capture = dict(last_action_summary)
                terminal_capture["fallback_from_heartbeat"] = True
            terminal_capture = _finalize_capture(terminal_capture, action_capture_ctx)
            reason_code = "manual_intervention_exception"
            observation_complete = False
            if error_type == "TargetClosedError" or page_closed_after or not page_available_after:
                reissue_suspected = (
                    started_on_verification_surface
                    and is_verification_url(page_url_after or page_url_before)
                    and int(captcha_token_change_count or 0) >= 2
                    and int(captcha_challenge_change_count or 0) <= 0
                    and int((terminal_capture or {}).get("direct_event_count", 0) or 0) <= 0
                    and int((terminal_capture or {}).get("proxy_event_count", 0) or 0) >= 6
                )
                observation_complete = should_mark_manual_observation_complete(
                    intervention_mode=intervention_mode,
                    ui_capture=terminal_capture,
                    before_url=page_url_before,
                    after_url=page_url_after,
                    challenge_token_changes=captcha_token_change_count,
                    challenge_signature_changes=captcha_challenge_change_count,
                )
                reason_code = (
                    "manual_observation_complete_target_closed"
                    if observation_complete
                    else "manual_intervention_target_closed"
                )
                if not observation_complete and reissue_suspected:
                    reason_code = "manual_intervention_reissue_suspected_target_closed"
            result.update(
                {
                    "used": True,
                    "reason": reason_code,
                    "elapsed_ms": elapsed_ms,
                    "brought_to_front": brought_to_front,
                    "error": error_type,
                    "page_available_after": page_available_after,
                    "page_closed_after": page_closed_after,
                    "page_url_after": page_url_after,
                    "recovery_attempts": recovery_attempts,
                    "recovery_events": list(recovery_events)[:3],
                    "ui_action_capture": terminal_capture,
                    "automation_activity_during_manual": dict(
                        self._manual_automation_actions or {}
                    ),
                    "captcha_token_change_count": int(captcha_token_change_count),
                    "captcha_fingerprints_seen": list(captcha_fingerprints_seen)[:12],
                    "captcha_challenge_change_count": int(captcha_challenge_change_count),
                    "captcha_challenge_signatures_seen": list(captcha_challenge_signatures_seen)[
                        :16
                    ],
                    "captcha_last_probe": dict(last_captcha_probe or {}),
                    "captcha_token_source": str(
                        (last_captcha_probe or {}).get("token_source", "") or ""
                    ),
                    "observation_complete": bool(observation_complete),
                    "captcha_reissue_suspected": bool(
                        started_on_verification_surface
                        and is_verification_url(page_url_after or page_url_before)
                        and int(captcha_token_change_count or 0) >= 2
                        and int(captcha_challenge_change_count or 0) <= 0
                        and int((terminal_capture or {}).get("direct_event_count", 0) or 0) <= 0
                        and int((terminal_capture or {}).get("proxy_event_count", 0) or 0) >= 6
                    ),
                }
            )
            log.warning(
                "manual_intervention.error reason=%s mode=%s request=%s wait_sec=%s elapsed_ms=%s error=%s recoveries=%s page_available_after=%s page_closed_after=%s url_after=%s observation_complete=%s",
                result["reason"],
                intervention_mode,
                requested_reason,
                duration,
                elapsed_ms,
                error_type,
                recovery_attempts,
                page_available_after,
                page_closed_after,
                page_url_after,
                bool(observation_complete),
            )
            _emit_event(
                "error",
                reason=str(result.get("reason", "") or "manual_intervention_exception"),
                elapsed_ms=elapsed_ms,
                error=error_type,
                page_url_after=page_url_after,
                captcha_token_change_count=int(captcha_token_change_count),
                captcha_challenge_change_count=int(captcha_challenge_change_count),
                observation_complete=bool(observation_complete),
            )
            return result
        finally:
            if hasattr(self, "_enforce_single_page_policy_snapshot"):
                try:
                    self._enforce_single_page_policy_snapshot(reason="manual_finalize")
                except Exception:
                    pass
            self._manual_intervention_active = False
            self._manual_intervention_request = ""
            for sig, previous in signal_prev_handlers:
                try:
                    signal.signal(sig, previous)
                except Exception:
                    continue

    def recover_page_after_target_closed(self, *, preferred_url: str = "") -> dict:
        """Best-effort bounded recovery when current page target was closed."""
        result = {
            "attempted": True,
            "recovered": False,
            "reason": "unknown",
            "page_switched": False,
            "opened_new_page": False,
            "n_context_pages": 0,
            "preferred_url": str(preferred_url or ""),
            "final_url": "",
        }
        context = getattr(self, "context", None)
        if context is None:
            result["reason"] = "no_context"
            return result

        live_pages = []
        try:
            pages = list(context.pages or [])
        except Exception:
            pages = []
        result["n_context_pages"] = len(pages)
        for page in pages:
            try:
                if hasattr(page, "is_closed") and page.is_closed():
                    continue
                live_pages.append(page)
            except Exception:
                continue

        page_candidate = live_pages[-1] if live_pages else None
        if page_candidate is None:
            try:
                self._expected_new_pages = int(getattr(self, "_expected_new_pages", 0) or 0) + 1
                page_candidate = context.new_page()
                result["opened_new_page"] = True
                BrowserSession._record_aux_page_guard_event(
                    self,
                    action="allowed_expected",
                    url=str(getattr(page_candidate, "url", "") or ""),
                    reason="recovery_new_page",
                )
            except Exception:
                self._expected_new_pages = max(
                    0, int(getattr(self, "_expected_new_pages", 0) or 0) - 1
                )
                result["reason"] = "new_page_failed"
                return result

        previous_page = getattr(self, "page", None)
        if page_candidate is not previous_page:
            result["page_switched"] = True
        self.page = page_candidate
        self._page_interaction = PageInteractionHelper(self.page, self)
        self._verification_challenges = VerificationChallengeHelper(self.page, self)
        self._wait = ElementWaitHelper(self)
        self._typing = TypingInputHelper(self)
        self._click = ElementClickHelper(self)
        self._fill = FormFillHelper(self)
        self._combobox = GoogleFlightsComboboxHelper(self)
        self._framework = BrowserFrameworkHelper(self)

        if result["opened_new_page"] and self.block_heavy_resources:
            try:
                self.page.route("**/*", self._route_filter)
            except Exception:
                pass

        target_url = str(preferred_url or "").strip()
        try:
            if target_url:
                self.goto(target_url)
            result["final_url"] = str(getattr(self.page, "url", "") or "")
            result["recovered"] = True
            result["reason"] = "recovered"
            return result
        except Exception as exc:
            result["reason"] = "recovered_page_navigation_failed"
            result["error"] = str(type(exc).__name__)
            try:
                result["final_url"] = str(getattr(self.page, "url", "") or "")
            except Exception:
                result["final_url"] = ""
            return result

    def collect_runtime_diagnostics(self, *, selectors: Optional[list[str]] = None) -> dict:
        """Collect compact browser/runtime diagnostics for interstitial triage."""
        page_obj = getattr(self, "page", None)
        out = {
            "page_available": page_obj is not None,
            "page_open": False,
            "url": "",
            "title": "",
            "locale": str(self.mimic_locale or ""),
            "timezone": str(self.mimic_timezone or ""),
            "region": str(self.mimic_region or ""),
            "currency": str(self.mimic_currency or ""),
            "user_agent": str(self.user_agent or ""),
            "viewport": dict(self.viewport or {}),
            "storage_state_path": str(self.storage_state_path or ""),
            "persist_storage_state": bool(self.persist_storage_state),
            "cookies": {"count_total": -1, "count_for_url": -1},
            "dom_probe": {},
            "selector_probe": [],
            "network": self.get_network_activity_snapshot(window_sec=20),
            "aux_page_guard": dict(getattr(self, "_aux_page_guard", {}) or {}),
            "popup_guard": dict(getattr(self, "_popup_guard", {}) or {}),
            "browser_lifecycle": dict(getattr(self, "_browser_lifecycle", {}) or {}),
        }
        if page_obj is None:
            return out
        try:
            if hasattr(page_obj, "is_closed"):
                out["page_open"] = not bool(page_obj.is_closed())
            else:
                out["page_open"] = True
        except Exception:
            out["page_open"] = False
        try:
            out["url"] = str(getattr(page_obj, "url", "") or "")
        except Exception:
            out["url"] = ""
        try:
            if hasattr(page_obj, "title"):
                out["title"] = str(page_obj.title() or "")
        except Exception:
            out["title"] = ""
        try:
            context_obj = getattr(self, "context", None)
            if context_obj is not None and hasattr(context_obj, "cookies"):
                total = list(context_obj.cookies() or [])
                out["cookies"]["count_total"] = len(total)
                if out["url"]:
                    scoped = list(context_obj.cookies([out["url"]]) or [])
                    out["cookies"]["count_for_url"] = len(scoped)
        except Exception:
            pass
        try:
            dom_probe = page_obj.evaluate(
                """
                () => {
                  const out = {
                    cookie_enabled: !!navigator.cookieEnabled,
                    user_agent: String(navigator.userAgent || ""),
                    webdriver: !!navigator.webdriver,
                    language: String(navigator.language || ""),
                    languages: Array.isArray(navigator.languages) ? navigator.languages.slice(0, 4) : [],
                    document_cookie_len: String(document.cookie || "").length,
                    cookie_probe_settable: null,
                    local_storage_len: 0,
                    session_storage_len: 0,
                    px_iframe_count: 0,
                    px_iframe_visible_count: 0,
                    px_token_prefix: "",
                    px_token_len: 0,
                    px_dataframe_token_prefix: "",
                    px_challenge_signature: "",
                    px_uuid_prefix: "",
                    px_vid_prefix: "",
                    px_identifier_prefix: "",
                    px_script_key: "",
                    px_frame_src_signature: "",
                    px_container_signature: "",
                    popup_guard: { blocked: 0, allowed: 0, samples: [] },
                  };
                  const quickSig = (v) => {
                    const s = String(v || "");
                    let h = 0;
                    for (let i = 0; i < s.length; i += 1) {
                      h = ((h << 5) - h + s.charCodeAt(i)) | 0;
                    }
                    return Math.abs(h).toString(36);
                  };
                  const isVisible = (el) => {
                    try {
                      if (!el) return false;
                      const st = window.getComputedStyle ? window.getComputedStyle(el) : null;
                      if (st && (st.display === "none" || st.visibility === "hidden")) return false;
                      const r = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
                      if (!r) return false;
                      return r.width > 0 && r.height > 0;
                    } catch (e) {
                      return false;
                    }
                  };
                  const attr = (el, name) => {
                    try {
                      return String((el && el.getAttribute && el.getAttribute(name)) || "");
                    } catch (e) {
                      return "";
                    }
                  };
                  try {
                    const key = "__fpw_cookie_probe";
                    document.cookie = `${key}=1; path=/; SameSite=Lax`;
                    out.cookie_probe_settable = String(document.cookie || "").includes(`${key}=1`);
                    document.cookie = `${key}=; path=/; max-age=0; SameSite=Lax`;
                  } catch (e) {
                    out.cookie_probe_settable = false;
                  }
                  try {
                    const pxFrames = Array.from(
                      document.querySelectorAll(
                        "#px-captcha iframe, [id*='px-captcha'] iframe, iframe[title*='Human verification' i], iframe[src*='px-cloud.net'], iframe[src*='captcha']"
                      )
                    );
                    out.px_iframe_count = pxFrames.length;
                    out.px_iframe_visible_count = pxFrames.filter((f) => isVisible(f)).length;
                    const tokenCarrier = pxFrames.find((f) => f && (attr(f, "token") || attr(f, "dataframetoken")))
                      || document.querySelector("iframe[token], iframe[dataframetoken]");
                    if (tokenCarrier) {
                      const tk = attr(tokenCarrier, "token");
                      const dtk = attr(tokenCarrier, "dataframetoken");
                      out.px_token_len = tk.length;
                      out.px_token_prefix = tk.slice(0, 16);
                      out.px_dataframe_token_prefix = dtk.slice(0, 24);
                    }
                    let uuidPrefix = "";
                    let vidPrefix = "";
                    try {
                      const u = new URL(String(window.location.href || ""));
                      uuidPrefix = String(u.searchParams.get("uuid") || "").slice(0, 16);
                      vidPrefix = String(u.searchParams.get("vid") || "").slice(0, 16);
                    } catch (e) {}
                    out.px_uuid_prefix = uuidPrefix;
                    out.px_vid_prefix = vidPrefix;
                    const idNode = document.querySelector("section[class*='identifier'], [class*='identifier']");
                    out.px_identifier_prefix = String((idNode && idNode.textContent) || "").replace(/\\s+/g, " ").trim().slice(0, 24);
                    const frameParts = pxFrames
                      .slice(0, 6)
                      .map((f) => `${attr(f, "src").slice(0, 140)}|${attr(f, "title").slice(0, 64)}|${attr(f, "token").slice(0, 16)}|${attr(f, "dataframetoken").slice(0, 24)}|${isVisible(f) ? "1" : "0"}`)
                      .join("||");
                    out.px_frame_src_signature = quickSig(frameParts);
                    const pxContainer = document.querySelector("#px-captcha, [id*='px-captcha']");
                    if (pxContainer) {
                      const containerText = String((pxContainer.textContent || "")).replace(/\\s+/g, " ").trim().slice(0, 240);
                      const containerStyle = attr(pxContainer, "style").slice(0, 160);
                      const containerClass = String(pxContainer.className || "").slice(0, 120);
                      out.px_container_signature = quickSig(`${containerText}|${containerStyle}|${containerClass}`);
                    }
                    const scriptNodes = Array.from(document.querySelectorAll("script[src]"));
                    for (const node of scriptNodes) {
                      const src = String(attr(node, "src") || "");
                      if (!src) continue;
                      const lower = src.toLowerCase();
                      if (lower.includes("/captcha.js") || lower.includes("px-cloud.net")) {
                        const m = src.match(/\\/([A-Za-z0-9]{6,})\\/captcha\\.js/);
                        out.px_script_key = (m && m[1] ? String(m[1]) : src).slice(0, 24);
                        break;
                      }
                    }
                    out.px_challenge_signature = [
                      out.px_token_prefix,
                      out.px_dataframe_token_prefix,
                      String(out.px_iframe_count),
                      String(out.px_iframe_visible_count),
                      out.px_frame_src_signature,
                      out.px_container_signature,
                      out.px_uuid_prefix,
                      out.px_vid_prefix,
                      out.px_script_key,
                      out.px_identifier_prefix,
                    ].join("|");
                  } catch (e) {}
                  try { out.local_storage_len = window.localStorage ? window.localStorage.length : 0; } catch (e) {}
                  try { out.session_storage_len = window.sessionStorage ? window.sessionStorage.length : 0; } catch (e) {}
                  try {
                    const guard = window.__FPW_POPUP_GUARD;
                    if (guard && typeof guard === "object") {
                      const samples = Array.isArray(guard.samples) ? guard.samples : [];
                      out.popup_guard = {
                        blocked: Number(guard.blocked || 0),
                        allowed: Number(guard.allowed || 0),
                        installed_at_ms: Number(guard.installed_at_ms || 0),
                        samples: samples.slice(-24),
                      };
                    }
                  } catch (e) {}
                  return out;
                }
                """
            )
            out["dom_probe"] = dict(dom_probe) if isinstance(dom_probe, dict) else {}
            popup_guard = {}
            if isinstance(dom_probe, dict) and isinstance(dom_probe.get("popup_guard"), dict):
                popup_guard = dict(dom_probe.get("popup_guard") or {})
            if popup_guard:
                out["popup_guard"] = popup_guard
                self._popup_guard = popup_guard
        except Exception as exc:
            out["dom_probe"] = {"error": str(type(exc).__name__)}

        selector_list = [str(s or "")[:180] for s in list(selectors or [])[:24] if str(s or "").strip()]
        for sel in selector_list:
            probe = {"selector": sel, "count": -1, "visible": False, "error": ""}
            try:
                locator = page_obj.locator(sel)
                count = int(locator.count())
                probe["count"] = count
                if count > 0:
                    probe["visible"] = bool(locator.first.is_visible(timeout=120))
            except Exception as exc:
                probe["error"] = str(type(exc).__name__)
            out["selector_probe"].append(probe)
        return out

    def _typing_delay(self) -> int:
        """Delegate to TypingInputHelper for typing delay calculation."""
        if not hasattr(self, "_typing"):
            self._typing = TypingInputHelper(self)
        return self._typing.typing_delay()

    def _child_frames(self):
        """Delegate to ElementWaitHelper for child frames retrieval."""
        if not hasattr(self, "_wait"):
            self._wait = ElementWaitHelper(self)
        return self._wait.child_frames()

    def _candidate_frames(self):
        """Delegate to ElementWaitHelper for ranked candidate frames."""
        if not hasattr(self, "_wait"):
            self._wait = ElementWaitHelper(self)
        return self._wait.candidate_frames()

    @staticmethod
    def _start_deadline(timeout_ms: int) -> float:
        """Delegate to BrowserFrameworkHelper for deadline creation."""
        return BrowserFrameworkHelper.start_deadline(timeout_ms)

    @staticmethod
    def _deadline_exceeded(deadline: float) -> bool:
        """Delegate to BrowserFrameworkHelper for deadline checking."""
        return BrowserFrameworkHelper.deadline_exceeded(deadline)

    @staticmethod
    def _remaining_timeout_ms(deadline: float) -> int:
        """Delegate to BrowserFrameworkHelper for remaining timeout calculation."""
        return BrowserFrameworkHelper.remaining_timeout_ms(deadline, DEFAULT_PLAYWRIGHT_ATTEMPT_TIMEOUT_FLOOR_MS)

    def _ensure_deadline_not_exceeded(self, deadline: float, action_label: str) -> None:
        """Delegate to BrowserFrameworkHelper for deadline enforcement."""
        if not hasattr(self, "_framework"):
            self._framework = BrowserFrameworkHelper(self)
        return self._framework.ensure_deadline_not_exceeded(deadline, action_label)

    def _log_low_remaining_ms(
        self,
        *,
        action: str,
        selector: str,
        timeout_ms: int,
        deadline: float,
        attempt: str = "unknown",
    ) -> None:
        """Delegate to BrowserFrameworkHelper for low remaining time warning."""
        if not hasattr(self, "_framework"):
            self._framework = BrowserFrameworkHelper(self)
        return self._framework.log_low_remaining_ms(
            action=action,
            selector=selector,
            timeout_ms=timeout_ms,
            deadline=deadline,
            attempt=attempt,
        )

    @staticmethod
    def _reraise_interrupt(exc: Exception) -> None:
        """Delegate to BrowserFrameworkHelper for exception handling."""
        return BrowserFrameworkHelper.reraise_interrupt(exc)

    @staticmethod
    def _is_hidden_input_locator(locator) -> bool:
        """Delegate to BrowserFrameworkHelper for hidden input checking."""
        return BrowserFrameworkHelper.is_hidden_input_locator(locator)

    def __enter__(self):
        """Start Playwright, open Chromium, and return the active session."""
        self._record_browser_lifecycle_event(
            "session_enter_start",
            engine=str(self.browser_engine or ""),
            headless=bool(self.headless),
        )
        self.playwright = sync_playwright().start()
        launch_kwargs = {"headless": self.headless}
        launch_args = []
        # Non-headless + slight launch delay can reduce flakiness on dynamic sites.
        if self.human_mimic and self.headless:
            launch_kwargs["slow_mo"] = 25
        if self.human_mimic and (self.browser_engine or "").strip().lower() == "chromium":
            # Reduce trivial automation fingerprints in Chromium while keeping behavior bounded.
            launch_args.append("--disable-blink-features=AutomationControlled")
            # Keep cookie/storage behavior closer to regular browsing for challenge scripts.
            launch_args.append(
                "--disable-features=ThirdPartyStoragePartitioning,ThirdPartyCookiesPhaseout,TrackingProtection3pcd"
            )
        if self.disable_http2:
            launch_args.append("--disable-http2")
        if launch_args:
            launch_kwargs["args"] = launch_args
        browser_launcher = getattr(self.playwright, self.browser_engine, None)
        if browser_launcher is None:
            browser_launcher = self.playwright.chromium
        self.browser = browser_launcher.launch(**launch_kwargs)
        self._record_browser_lifecycle_event(
            "browser_launched",
            engine=str(self.browser_engine or ""),
            headless=bool(self.headless),
            launch_args=list(launch_args),
        )
        self.viewport = (
            self._random_human_viewport() if self.human_mimic else {"width": 1366, "height": 900}
        )
        context_kwargs = {
            "viewport": dict(self.viewport),
            "locale": self.mimic_locale,
            "timezone_id": self.mimic_timezone,
            "java_script_enabled": True,
            "service_workers": "allow",
            "geolocation": {
                "latitude": self.mimic_latitude,
                "longitude": self.mimic_longitude,
            },
            "permissions": ["geolocation"],
            "extra_http_headers": {
                "Accept-Language": (
                    f"{self.mimic_locale},{self.mimic_locale.split('-')[0]};q=0.9,en;q=0.7"
                )
            },
        }
        if self.human_mimic and (self.browser_engine or "").strip().lower() == "chromium":
            resolved_ua = self._random_human_user_agent()
            # Keep UA/viewport coherent for anti-bot checks; desktop viewport with
            # mobile UA is a strong fingerprint anomaly on challenge pages.
            if BrowserSession._looks_mobile_user_agent(resolved_ua):
                log.warning(
                    "browser.user_agent.mobile_filtered fallback=desktop viewport=%sx%s",
                    int((self.viewport or {}).get("width", 0) or 0),
                    int((self.viewport or {}).get("height", 0) or 0),
                )
                resolved_ua = _human_mimic_chromium_user_agent()
            self.user_agent = resolved_ua
            context_kwargs["user_agent"] = self.user_agent
        else:
            self.user_agent = context_kwargs.get("user_agent", "")
        if self.storage_state_path:
            state_path = Path(self.storage_state_path)
            if state_path.exists():
                context_kwargs["storage_state"] = str(state_path)
        self.context = self.browser.new_context(**context_kwargs)
        self._record_browser_lifecycle_event(
            "context_created",
            context_id=int(id(self.context)),
            locale=str(self.mimic_locale or ""),
            timezone=str(self.mimic_timezone or ""),
        )
        if hasattr(self.context, "on"):
            try:
                self.context.on("request", self._on_network_request)
                self.context.on("response", self._on_network_response)
                self.context.on("requestfailed", self._on_network_request_failed)
                self.context.on(
                    "close",
                    lambda: self._record_browser_lifecycle_event(
                        "context_close",
                        context_id=int(id(self.context)) if self.context is not None else 0,
                    ),
                )
            except Exception:
                pass
        region_literal = json.dumps(self.mimic_region)
        currency_literal = json.dumps(self.mimic_currency)
        self.context.add_init_script(
            script=(
                f"window.__FPW_MIMIC_REGION = {region_literal}; "
                f"window.__FPW_MIMIC_CURRENCY = {currency_literal};"
            )
        )
        self.context.add_init_script(script=BrowserSession._popup_guard_init_script())
        if self.human_mimic:
            ua_profile = derive_ua_stealth_profile(str(self.user_agent or ""))
            self.context.add_init_script(
                script=_human_mimic_stealth_init_script(
                    self.mimic_locale,
                    ua_platform=str(ua_profile.get("ua_platform", "macOS") or "macOS"),
                    navigator_platform=str(
                        ua_profile.get("navigator_platform", "MacIntel") or "MacIntel"
                    ),
                    chrome_major=int(ua_profile.get("chrome_major", 133) or 133),
                )
            )
        self.page = self.context.new_page()
        self._attach_page_lifecycle(self.page, role="primary_page")
        self._record_browser_lifecycle_event(
            "primary_page_created",
            page_id=int(id(self.page)),
            url_prefix=str(getattr(self.page, "url", "") or "")[:220],
        )
        if hasattr(self.context, "on"):
            try:
                self.context.on("page", self._on_context_page_opened)
            except Exception:
                pass
        self._apply_runtime_stealth()
        self._page_interaction = PageInteractionHelper(self.page, self)
        self._verification_challenges = VerificationChallengeHelper(self.page, self)
        self._wait = ElementWaitHelper(self)
        self._typing = TypingInputHelper(self)
        self._click = ElementClickHelper(self)
        self._fill = FormFillHelper(self)
        self._combobox = GoogleFlightsComboboxHelper(self)
        self._framework = BrowserFrameworkHelper(self)
        if self.block_heavy_resources:
            self.page.route("**/*", self._route_filter)
        return self

    @staticmethod
    def _route_filter(route):
        """Block heavy/tracker requests that add latency without helping extraction."""
        try:
            req = route.request
            rtype = (req.resource_type or "").lower()
            url = (req.url or "").lower()
            host = (urlparse(url).hostname or "").lower()
        except Exception:
            route.continue_()
            return

        challenge_tokens = (
            "px-cloud",
            "perimeterx",
            "captcha",
            "hcaptcha",
            "recaptcha",
            "arkoselabs",
            "datadome",
            "turnstile",
            "cloudflare",
            "/sttc/px/",
            "human-verification",
            "verify",
        )
        if any(token in host or token in url for token in challenge_tokens):
            # Keep verification resources unblocked; blocking these can trigger
            # repeated challenge re-issue loops.
            route.continue_()
            return

        if rtype in {"image", "media", "font"}:
            route.abort()
            return

        tracker_tokens = (
            "doubleclick",
            "googleadservices",
            "googletagmanager",
            "google-analytics",
            "facebook",
            "ladsp.com",
            "adservice",
            "analytics",
        )
        if any(token in host for token in tracker_tokens):
            route.abort()
            return

        route.continue_()

    def __exit__(self, exc_type, _exc, _tb):
        """Close browser resources regardless of success or failure."""
        # Close all known pages/contexts first, then stop Playwright as final fallback.
        self._record_browser_lifecycle_event(
            "session_exit_start",
            exc_type=str(getattr(exc_type, "__name__", "") or ""),
        )
        primary_page_closed = True
        try:
            page_obj = getattr(self, "page", None)
            if page_obj is not None and hasattr(page_obj, "is_closed"):
                primary_page_closed = bool(page_obj.is_closed())
            elif page_obj is not None:
                primary_page_closed = False
        except Exception:
            primary_page_closed = True
        browser_obj = getattr(self, "browser", None)
        context_obj = getattr(self, "context", None)
        exit_snapshot = {"contexts": 0, "pages_total": 0, "open_pages": 0, "page_urls": []}
        try:
            contexts = []
            if browser_obj is not None and hasattr(browser_obj, "contexts"):
                try:
                    contexts = list(browser_obj.contexts or [])
                except Exception:
                    contexts = []
            if context_obj is not None and context_obj not in contexts:
                contexts.append(context_obj)
            exit_snapshot["contexts"] = int(len(contexts))
            for ctx in contexts:
                pages = []
                try:
                    pages = list(getattr(ctx, "pages", []) or [])
                except Exception:
                    pages = []
                exit_snapshot["pages_total"] += int(len(pages))
                for pg in pages:
                    url_now = ""
                    is_closed = False
                    try:
                        url_now = str(getattr(pg, "url", "") or "")
                    except Exception:
                        url_now = ""
                    try:
                        if hasattr(pg, "is_closed"):
                            is_closed = bool(pg.is_closed())
                    except Exception:
                        is_closed = True
                    if not is_closed:
                        exit_snapshot["open_pages"] += 1
                    if len(exit_snapshot["page_urls"]) < 8:
                        exit_snapshot["page_urls"].append(url_now[:200])
        except Exception:
            pass
        self._record_browser_lifecycle_event(
            "session_exit_snapshot",
            primary_page_closed=bool(primary_page_closed),
            contexts=int(exit_snapshot.get("contexts", 0)),
            pages_total=int(exit_snapshot.get("pages_total", 0)),
            open_pages=int(exit_snapshot.get("open_pages", 0)),
            page_urls=list(exit_snapshot.get("page_urls", [])),
        )
        try:
            if hasattr(self, "_enforce_single_page_policy_snapshot"):
                self._enforce_single_page_policy_snapshot(reason="context_exit")
        except Exception:
            pass
        try:
            if (
                self.persist_storage_state
                and self.context is not None
                and str(self.storage_state_path or "").strip()
                and not bool(primary_page_closed)
            ):
                state_path = Path(self.storage_state_path)
                state_path.parent.mkdir(parents=True, exist_ok=True)
                self.context.storage_state(path=str(state_path))
            elif (
                self.persist_storage_state
                and self.context is not None
                and str(self.storage_state_path or "").strip()
                and bool(primary_page_closed)
            ):
                self._record_browser_lifecycle_event(
                    "storage_state_skip",
                    reason="primary_page_closed",
                    storage_state_path=str(self.storage_state_path or ""),
                )
        except Exception:
            pass
        closed_pages_total = 0
        closed_contexts_total = 0
        try:
            contexts = []
            if browser_obj is not None and hasattr(browser_obj, "contexts"):
                try:
                    contexts = list(browser_obj.contexts or [])
                except Exception:
                    contexts = []
            if context_obj is not None and context_obj not in contexts:
                contexts.append(context_obj)
            for ctx in contexts:
                pages = []
                try:
                    pages = list(getattr(ctx, "pages", []) or [])
                except Exception:
                    pages = []
                for pg in pages:
                    try:
                        if hasattr(pg, "is_closed") and pg.is_closed():
                            continue
                        try:
                            pg.close(run_before_unload=False)
                        except TypeError:
                            pg.close()
                        closed_pages_total += 1
                    except Exception:
                        continue
                try:
                    ctx.close()
                    closed_contexts_total += 1
                except Exception:
                    continue
        except Exception:
            pass

        try:
            if browser_obj is not None:
                browser_obj.close()
        except Exception:
            pass
        try:
            if self.playwright is not None:
                self.playwright.stop()
        except Exception:
            pass
        self._record_browser_lifecycle_event(
            "session_exit_done",
            closed_pages=int(closed_pages_total),
            closed_contexts=int(closed_contexts_total),
        )
        self.page = None
        self.context = None
        self.browser = None
        self.playwright = None

    def goto(self, url: str):
        """Navigate to a URL and wait for initial page load."""
        if hasattr(self, "_assert_automation_allowed_during_manual_intervention"):
            self._assert_automation_allowed_during_manual_intervention("goto", str(url or ""))
        return self._page_interaction.goto(url)

    def _simulate_passive_perimetrix_behavior(self, _page=None, duration_ms: int = 3000) -> dict:
        """Delegate to VerificationChallengeHelper for PerimeterX behavior simulation."""
        if not hasattr(self, "_verification_challenges"):
            self._verification_challenges = VerificationChallengeHelper(self.page, self)
        return self._verification_challenges.simulate_passive_perimetrix_behavior(duration_ms)

    def human_mimic_interstitial_grace(self, duration_ms: int = 3500):
        """Delegate to VerificationChallengeHelper for interstitial grace handling."""
        if not hasattr(self, "_verification_challenges"):
            self._verification_challenges = VerificationChallengeHelper(self.page, self)
        return self._verification_challenges.human_mimic_interstitial_grace(duration_ms)

    def human_mimic_press_and_hold_challenge(self, max_hold_ms: int = 1800) -> bool:
        """Delegate to VerificationChallengeHelper for press-and-hold challenge handling."""
        if not hasattr(self, "_verification_challenges"):
            self._verification_challenges = VerificationChallengeHelper(self.page, self)
        return self._verification_challenges.human_mimic_press_and_hold_challenge(max_hold_ms)

    def classify_verification_challenge(
        self,
        *,
        html_text: str = "",
        screenshot_b64: str = "",
        use_vision_light: bool = True,
    ) -> dict:
        """Delegate to VerificationChallengeHelper for multiclass verification classification."""
        if not hasattr(self, "_verification_challenges"):
            self._verification_challenges = VerificationChallengeHelper(self.page, self)
        return self._verification_challenges.classify_verification_challenge(
            html_text=html_text,
            screenshot_b64=screenshot_b64,
            use_vision_light=use_vision_light,
        )

    def wait(self, selector: str, timeout_ms: int = None):
        """Delegate to ElementWaitHelper for element waiting."""
        if hasattr(self, "_assert_automation_allowed_during_manual_intervention"):
            self._assert_automation_allowed_during_manual_intervention("wait", str(selector or ""))
        if not hasattr(self, "_wait"):
            self._wait = ElementWaitHelper(self)
        return self._wait.wait(selector, timeout_ms)

    def _try_page_fill(self, selector: str, value: str, deadline: float, timeout_ms: int):
        """Delegate to FormFillHelper for page-level fill attempts."""
        if not hasattr(self, "_fill"):
            self._fill = FormFillHelper(self)
        return self._fill._try_page_fill(selector, value, deadline, timeout_ms)

    def _try_frame_fills(self, selector: str, value: str, deadline: float, timeout_ms: int):
        """Delegate to FormFillHelper for frame-level fill attempts."""
        if not hasattr(self, "_fill"):
            self._fill = FormFillHelper(self)
        return self._fill._try_frame_fills(selector, value, deadline, timeout_ms)
        return False

    def _try_click_type_recovery(
        self,
        selector: str,
        value: str,
        deadline: float,
        timeout_ms: int,
    ):
        """Delegate to FormFillHelper for click+type recovery."""
        if not hasattr(self, "_fill"):
            self._fill = FormFillHelper(self)
        return self._fill._try_click_type_recovery(selector, value, deadline, timeout_ms)
        return False

    def fill(self, selector: str, value: str, timeout_ms: int = None):
        """Delegate to FormFillHelper for form filling."""
        if hasattr(self, "_assert_automation_allowed_during_manual_intervention"):
            self._assert_automation_allowed_during_manual_intervention("fill", str(selector or ""))
        if not hasattr(self, "_fill"):
            self._fill = FormFillHelper(self)
        return self._fill.fill(selector, value, timeout_ms)

    def fill_google_flights_combobox(
        self,
        activation_selectors: list = None,
        input_selectors: list = None,
        text: str = "",
        verify_tokens: list = None,
        timeout_ms: int = None,
    ) -> tuple:
        """Fill Google Flights combobox by ONLY activating container, then typing into real input.

        **CRITICAL**: Never fill() the [role='combobox'] container. Only click it to activate.
        After activation, locate the REAL input element and type into that.

        Implementation delegated to GoogleFlightsComboboxHelper (see core/browser_combobox.py).

        Args:
            activation_selectors: Selectors to click and open combobox (max 5, capped internally)
            input_selectors: Real input elements to type into (max 3, capped internally)
            text: Text value to type
            verify_tokens: Optional tokens to verify were accepted
            timeout_ms: Total timeout in milliseconds

        Returns:
            Tuple of (success: bool, activation_selector_used: str)
        """
        if not hasattr(self, "_combobox"):
            self._combobox = GoogleFlightsComboboxHelper(self)
        return self._combobox.fill_google_flights_combobox(
            activation_selectors,
            input_selectors,
            text,
            verify_tokens,
            timeout_ms,
        )

    # All old fill_google_flights_combobox implementation code removed (delegated to GoogleFlightsComboboxHelper in _combobox)

    def click(self, selector: str, timeout_ms: int = None, no_wait_after: bool = False):
        """Delegate to ElementClickHelper for clicking elements."""
        if hasattr(self, "_assert_automation_allowed_during_manual_intervention"):
            self._assert_automation_allowed_during_manual_intervention("click", str(selector or ""))
        if not hasattr(self, "_click"):
            self._click = ElementClickHelper(self)
        return self._click.click(selector, timeout_ms, no_wait_after)

    def type_active(self, value: str, timeout_ms: int = None):
        """Delegate to TypingInputHelper for keyboard input."""
        if hasattr(self, "_assert_automation_allowed_during_manual_intervention"):
            self._assert_automation_allowed_during_manual_intervention("type_active", "")
        if not hasattr(self, "_typing"):
            self._typing = TypingInputHelper(self)
        return self._typing.type_active(value, timeout_ms)

    def activate_field_by_keywords(self, keywords, timeout_ms: int = None) -> bool:
        """Delegate to ElementClickHelper for keyword-based field activation."""
        if hasattr(self, "_assert_automation_allowed_during_manual_intervention"):
            self._assert_automation_allowed_during_manual_intervention(
                "activate_field_by_keywords",
                ",".join([str(k) for k in (keywords or [])][:6]),
            )
        if not hasattr(self, "_click"):
            self._click = ElementClickHelper(self)
        return self._click.activate_field_by_keywords(keywords, timeout_ms)

    def fill_by_keywords(self, keywords, value: str, timeout_ms: int = None) -> bool:
        """Delegate to FormFillHelper for keyword-based form filling."""
        if hasattr(self, "_assert_automation_allowed_during_manual_intervention"):
            self._assert_automation_allowed_during_manual_intervention(
                "fill_by_keywords",
                ",".join([str(k) for k in (keywords or [])][:6]),
            )
        if not hasattr(self, "_fill"):
            self._fill = FormFillHelper(self)
        return self._fill.fill_by_keywords(keywords, value, timeout_ms)

    # ========== Framework / Utility Methods (framework deadline, logging, error handling) =========

    def content(self) -> str:
        """Return the current page HTML snapshot."""
        return self._page_interaction.content()

    def screenshot(self, path: str, *, full_page: bool = True):
        """Capture a PNG screenshot of the current page."""
        if self._page_interaction is None:
            return
        self._page_interaction.screenshot(path, full_page=full_page)
