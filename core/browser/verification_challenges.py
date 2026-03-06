"""Verification challenge detection and handling helpers for BrowserSession."""

import base64
import json
import os
import random
import re
import time
from pathlib import Path
from typing import Any, Dict
from llm.prompts import (
    VLM_VERIFICATION_ACTION_PROMPT,
    VLM_VERIFICATION_MULTICLASS_PROMPT,
)
from utils.logging import get_logger
from utils.thresholds import get_threshold


log = get_logger(__name__)

_MODELS_CONFIG_PATH = Path(__file__).resolve().parents[2] / "configs" / "models.yaml"
_VERIFICATION_CLASSIFIER_TIMEOUT_SEC = 12
_VERIFICATION_CLASSES: Dict[str, Dict[str, Any]] = {
    "interstitial_press_hold": {
        "methods": [
            "PerimeterX PRESS & HOLD",
            "DataDome human verification interstitial",
            "Imperva soft challenge interstitial",
        ],
        "tokens": [
            "press & hold",
            "press and hold",
            "px-captcha",
            "are you a person or a robot",
            "human verification",
            "perimeterx",
            "datadome",
        ],
        "solution": "bounding box to the location of the press-and-hold button",
    },
    "text_captcha": {
        "methods": [
            "image character captcha",
            "audio captcha",
            "custom alphanumeric captcha",
        ],
        "tokens": [
            "enter the characters",
            "type the characters",
            "captcha image",
            "characters you see",
            "distorted text",
            "security code",
            "ocr",
        ],
        "solution": "displayed captcha characters for OCR/transcription",
    },
    "checkbox_captcha": {
        "methods": [
            "Google reCAPTCHA checkbox",
            "hCaptcha checkbox",
        ],
        "tokens": [
            "i'm not a robot",
            "recaptcha",
            "g-recaptcha",
            "hcaptcha",
            "h-captcha",
        ],
        "solution": "bounding box of the checkbox challenge widget",
    },
    "puzzle_captcha": {
        "methods": [
            "GeeTest slider",
            "Arkose FunCaptcha rotate/puzzle",
            "custom slider puzzle captcha",
        ],
        "tokens": [
            "slide to verify",
            "drag the slider",
            "geetest",
            "funcaptcha",
            "arkose",
            "rotate the object",
            "puzzle",
        ],
        "solution": "drag path target and puzzle alignment hint",
    },
    "turnstile_challenge": {
        "methods": [
            "Cloudflare Turnstile",
            "Cloudflare managed challenge",
            "browser integrity check page",
        ],
        "tokens": [
            "turnstile",
            "cloudflare",
            "checking your browser",
            "managed challenge",
            "cf-chl",
            "challenge-platform",
        ],
        "solution": "challenge widget area and continue/verify button location",
    },
    "javascript_challenge": {
        "methods": [
            "JavaScript computation challenge",
            "cookie/token bootstrap challenge",
            "meta refresh anti-automation gate",
        ],
        "tokens": [
            "enable javascript",
            "please wait while we verify",
            "checking if the site connection is secure",
            "browser integrity",
            "verify you are human",
            "ddos protection",
        ],
        "solution": "wait-and-refresh action with challenge status text",
    },
    "cookie_requirement_interstitial": {
        "methods": [
            "cookie-required interstitial",
            "browser cookie availability gate",
            "script/cookie runtime requirement page",
        ],
        "tokens": [
            "cookies turned on",
            "enable cookies",
            "turn cookies on",
            "browser isn’t blocking them from loading",
            "browser isn't blocking them from loading",
            "check javascript and cookies",
        ],
        "solution": "allow first-party cookies and challenge scripts, then retry from the same session",
    },
    "access_denied_block": {
        "methods": [
            "403 forbidden hard block",
            "rate-limit hard deny",
            "IP reputation deny page",
        ],
        "tokens": [
            "access denied",
            "forbidden",
            "request blocked",
            "temporarily blocked",
            "error 403",
            "security policy",
            "unusual traffic",
        ],
        "solution": "hard block detected; rotate fingerprint/proxy and retry later",
    },
    "queue_waiting_room": {
        "methods": [
            "Queue-it waiting room",
            "virtual queue",
            "traffic surge hold page",
        ],
        "tokens": [
            "queue-it",
            "you are in line",
            "waiting room",
            "estimated wait",
            "queue number",
        ],
        "solution": "queue position and countdown timer text",
    },
    "no_protection": {
        "methods": [],
        "tokens": [],
        "solution": "no verification protection detected",
    },
}
_VERIFICATION_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def get_verification_protection_method_groups() -> Dict[str, Any]:
    """Return all known verification/captcha methods grouped into N classes."""
    out: Dict[str, Any] = {}
    for label, cfg in _VERIFICATION_CLASSES.items():
        out[label] = list(cfg.get("methods", []) or [])
    return out


def _extract_json_blob(raw: str) -> Dict[str, Any]:
    text = str(raw or "").strip()
    if not text:
        return {}
    match = _VERIFICATION_JSON_RE.search(text)
    if match:
        text = match.group(0)
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _normalize_verification_classification(raw: Dict[str, Any]) -> Dict[str, str]:
    if not isinstance(raw, dict):
        return {
            "protector_label": "no_protection",
            "solution": str(_VERIFICATION_CLASSES["no_protection"]["solution"]),
        }
    label = str(raw.get("protector_label", "") or "").strip().lower()
    if label not in _VERIFICATION_CLASSES:
        label = "no_protection"
    solution = str(raw.get("solution", "") or "").strip()
    if not solution:
        solution = str(_VERIFICATION_CLASSES[label]["solution"])
    return {"protector_label": label, "solution": solution}


def _classify_verification_protection_heuristic(html_text: str) -> Dict[str, str]:
    lower = str(html_text or "").lower()
    if not lower:
        return _normalize_verification_classification({})

    # Some flight result surfaces include anti-bot library references (for example
    # "recaptcha" strings or PX telemetry scripts) without showing an actual
    # challenge widget. Fail-open on clear results indicators unless strong
    # challenge markers are present.
    looks_like_results_surface = (
        "/transport/flights/" in lower
        and (
            "day-view" in lower
            or "updatedpriceamount" in lower
            or "search-results" in lower
            or "itinerary" in lower
        )
    )
    has_strong_challenge_marker = any(
        token in lower
        for token in (
            "px-captcha",
            "captcha-v2",
            "are you a person or a robot",
            "human verification challenge",
            "press & hold",
            "press and hold",
            "still having problems accessing the page",
            "cookies turned on",
            "turn cookies on",
            "i'm not a robot",
            "g-recaptcha",
            "hcaptcha",
            "h-captcha",
            "cf-chl",
            "challenge-platform",
            "turnstile",
        )
    )
    if looks_like_results_surface and not has_strong_challenge_marker:
        return _normalize_verification_classification({})

    scored = []
    for label, cfg in _VERIFICATION_CLASSES.items():
        if label == "no_protection":
            continue
        tokens = list(cfg.get("tokens", []) or [])
        hits = [token for token in tokens if token and token in lower]
        # Bare "recaptcha" mention is frequently present in scripts on normal pages.
        # Treat it as ambiguous unless explicit checkbox-widget markers are present.
        if label == "checkbox_captcha" and hits and set(hits).issubset({"recaptcha"}):
            continue
        score = len(hits)
        if score > 0:
            scored.append((score, label))
    if not scored:
        return _normalize_verification_classification({})
    scored.sort(reverse=True)
    return _normalize_verification_classification({"protector_label": scored[0][1]})


def _load_vision_light_model_name() -> str:
    env_model = (os.getenv("FLIGHT_WATCHER_VISION_LIGHT_MODEL") or "").strip()
    if env_model:
        return env_model

    if not _MODELS_CONFIG_PATH.exists():
        return "qwen3-vl:2b"

    vision_light = ""
    vision_fallback = ""
    try:
        for raw_line in _MODELS_CONFIG_PATH.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            if key == "vision_light" and value:
                vision_light = value
            if key == "vision" and value:
                vision_fallback = value
    except Exception:
        return "qwen3-vl:2b"

    return vision_light or vision_fallback or "qwen3-vl:2b"


def _classify_verification_protection_with_vision_light(
    screenshot_b64: str,
    *,
    html_hint: str = "",
) -> Dict[str, str]:
    if not screenshot_b64:
        return _normalize_verification_classification({})
    try:
        from llm.llm_client import call_llm
    except Exception:
        return _normalize_verification_classification({})

    class_docs = []
    for label, cfg in _VERIFICATION_CLASSES.items():
        methods = ", ".join(list(cfg.get("methods", []) or [])[:4])
        class_docs.append(f"- {label}: {methods}")
    short_html = str(html_hint or "").strip().replace("\n", " ")[:2000]
    prompt = VLM_VERIFICATION_MULTICLASS_PROMPT.format(
        class_docs=chr(10).join(class_docs),
        dom_hint=short_html,
    )

    try:
        raw = call_llm(
            prompt,
            model=_load_vision_light_model_name(),
            think=False,
            json_mode=True,
            timeout_sec=_VERIFICATION_CLASSIFIER_TIMEOUT_SEC,
            images=[screenshot_b64],
            endpoint_policy="generate_only",
            strict_json=True,
            fail_fast_on_timeout=True,
        )
    except Exception as exc:
        log.debug("verification.classifier.vision_light_failed error=%s", str(exc)[:160])
        return _normalize_verification_classification({})
    return _normalize_verification_classification(_extract_json_blob(raw))


def _normalize_bbox(raw: Any) -> list[float] | None:
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        x, y, w, h = [float(v) for v in raw]
    except Exception:
        return None
    if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0 and 0.0 < w <= 1.0 and 0.0 < h <= 1.0):
        return None
    if x + w > 1.0 or y + h > 1.0:
        return None
    return [x, y, w, h]


def _extract_verification_action_with_vision_light(
    screenshot_b64: str,
    *,
    html_hint: str = "",
) -> Dict[str, Any]:
    if not screenshot_b64:
        return {"protector_label": "no_protection", "solution": "", "target_bbox": None, "confidence": "low"}
    try:
        from llm.llm_client import call_llm
    except Exception:
        return {"protector_label": "no_protection", "solution": "", "target_bbox": None, "confidence": "low"}

    class_docs = []
    for label, cfg in _VERIFICATION_CLASSES.items():
        methods = ", ".join(list(cfg.get("methods", []) or [])[:4])
        class_docs.append(f"- {label}: {methods}")
    short_html = str(html_hint or "").strip().replace("\n", " ")[:2000]
    prompt = VLM_VERIFICATION_ACTION_PROMPT.format(
        class_docs=chr(10).join(class_docs),
        dom_hint=short_html,
    )

    try:
        raw = call_llm(
            prompt,
            model=_load_vision_light_model_name(),
            think=False,
            json_mode=True,
            timeout_sec=_VERIFICATION_CLASSIFIER_TIMEOUT_SEC,
            images=[screenshot_b64],
            endpoint_policy="generate_only",
            strict_json=True,
            fail_fast_on_timeout=True,
        )
        parsed = _extract_json_blob(raw)
    except Exception as exc:
        log.debug("verification.action.vision_light_failed error=%s", str(exc)[:160])
        return {"protector_label": "no_protection", "solution": "", "target_bbox": None, "confidence": "low"}

    normalized = _normalize_verification_classification(parsed if isinstance(parsed, dict) else {})
    bbox = _normalize_bbox(parsed.get("target_bbox") if isinstance(parsed, dict) else None)
    confidence = str((parsed or {}).get("confidence", "low") if isinstance(parsed, dict) else "low").strip().lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = "low"
    return {
        "protector_label": normalized.get("protector_label", "no_protection"),
        "solution": normalized.get("solution", ""),
        "target_bbox": bbox,
        "confidence": confidence,
    }


def classify_verification_challenge_multiclass(
    *,
    html_text: str = "",
    screenshot_b64: str = "",
    use_vision_light: bool = True,
) -> Dict[str, str]:
    """Classify verification/captcha protection into N classes with strict 2-field JSON output."""
    heuristic = _classify_verification_protection_heuristic(html_text)
    if heuristic.get("protector_label") != "no_protection":
        return heuristic
    if not use_vision_light:
        return heuristic
    via_vision = _classify_verification_protection_with_vision_light(
        screenshot_b64,
        html_hint=html_text,
    )
    if via_vision.get("protector_label") != "no_protection":
        return via_vision
    return heuristic


class VerificationChallengeHelper:
    """Encapsulates verification challenge handling and interstitial logic."""

    def __init__(self, page, browser_session):
        """
        Initialize verification challenge helper.

        Args:
            page: Playwright Page object
            browser_session: Parent BrowserSession instance
        """
        self.page = page
        self.browser_session = browser_session

    def classify_verification_challenge(
        self,
        *,
        html_text: str = "",
        screenshot_b64: str = "",
        use_vision_light: bool = True,
    ) -> Dict[str, str]:
        """Classify detected protector and suggested solution."""
        return classify_verification_challenge_multiclass(
            html_text=html_text,
            screenshot_b64=screenshot_b64,
            use_vision_light=use_vision_light,
        )

    def simulate_passive_perimetrix_behavior(self, duration_ms: int = 3000) -> dict:
        """Simulate passive human-like behavior for PerimeterX behavioral challenges.

        PerimeterX modern implementation uses behavioral signals instead of iframe click challenges:
        - Mouse movement with natural jitter/curves
        - Scroll patterns
        - JavaScript execution patterns
        - Cumulative time on page

        Returns dict with behavioral signal counts for evidence.
        """
        signals = {
            "mouse_moves": 0,
            "scroll_events": 0,
            "js_triggers": 0,
            "hidden_iframe_detected": False,
            "elapsed_ms": 0,
        }

        if self.page is None or not hasattr(self.page, "mouse"):
            return signals

        start_time = time.monotonic()
        deadline = start_time + (duration_ms / 1000.0)

        try:
            # Get viewport dimensions
            width, height = 1366, 900
            viewport = getattr(self.page, "viewport_size", None)
            if viewport:
                width = int(viewport.get("width", width) or width)
                height = int(viewport.get("height", height) or height)

            # First: Try JavaScript trigger for PerimeterX challenge completion
            try:
                self.page.evaluate("""
                    () => {
                        // Try to access PerimeterX global if available
                        if (typeof __px !== 'undefined' && typeof __px.attemptHumanVerification === 'function') {
                            __px.attemptHumanVerification();
                        }
                        // Trigger any pending challenge initialization
                        if (document.readyState !== 'complete') {
                            document.dispatchEvent(new Event('px_initialized'));
                        }
                    }
                """)
                signals["js_triggers"] += 1
            except Exception:
                pass  # JS trigger is best-effort

            # Early scroll phase (execute scrolls before mouse moves for better signal sequence)
            try:
                if time.monotonic() < deadline:
                    num_scrolls = random.randint(2, 4)
                    for scroll_idx in range(num_scrolls):
                        if time.monotonic() >= deadline:
                            break

                        scroll_amount = random.randint(60, 180)
                        if hasattr(self.page, "mouse") and hasattr(self.page.mouse, "wheel"):
                            self.page.mouse.wheel(0, scroll_amount)
                            signals["scroll_events"] += 1

                        if hasattr(self.page, "wait_for_timeout"):
                            self.page.wait_for_timeout(random.randint(100, 250))
                        else:
                            time.sleep(random.randint(100, 250) / 1000.0)
            except Exception:
                pass  # Scroll is best-effort

            # Main mouse movement phase: More aggressive movement patterns
            num_move_pairs = random.randint(8, 12)
            for move_idx in range(num_move_pairs):
                if time.monotonic() >= deadline:
                    break

                # Generate natural curved mouse path (using intermediate points)
                start_x = random.randint(80, width - 80)
                start_y = random.randint(80, height - 80)

                # Move to start position with curve steps
                self.page.mouse.move(start_x, start_y, steps=random.randint(6, 12))
                signals["mouse_moves"] += 1

                # Pause before curve movement (shorter to allow more moves)
                pause_before_ms = random.randint(80, 180)
                if hasattr(self.page, "wait_for_timeout"):
                    self.page.wait_for_timeout(pause_before_ms)
                else:
                    time.sleep(pause_before_ms / 1000.0)

                # Curved movement to new location
                end_x = random.randint(80, width - 80)
                end_y = random.randint(80, height - 80)
                self.page.mouse.move(end_x, end_y, steps=random.randint(10, 20))
                signals["mouse_moves"] += 1

                if time.monotonic() >= deadline:
                    break

                # Pause after curve movement (medium duration)
                pause_after_ms = random.randint(120, 280)
                if hasattr(self.page, "wait_for_timeout"):
                    self.page.wait_for_timeout(pause_after_ms)
                else:
                    time.sleep(pause_after_ms / 1000.0)

            # Check for hidden iframe that may have appeared during simulation
            try:
                hidden_iframe_result = self.page.evaluate("""
                    () => {
                        const iframes = Array.from(document.querySelectorAll("iframe"));
                        const px_hidden = iframes.filter(f => {
                            const title = (f.getAttribute("title") || "").toLowerCase();
                            if (!title.includes("human verification")) return false;
                            const style = window.getComputedStyle(f);
                            return style.display === "none" || style.visibility === "hidden";
                        });
                        return px_hidden.length > 0;
                    }
                """)
                if hidden_iframe_result:
                    signals["hidden_iframe_detected"] = True
                    # If we found a hidden iframe, try to trigger it
                    try:
                        self.page.evaluate("""
                            () => {
                                const iframe = Array.from(document.querySelectorAll("iframe"))
                                    .find(f => (f.getAttribute("title") || "").toLowerCase().includes("human verification"));
                                if (iframe) {
                                    iframe.click?.();
                                    iframe.style.display = 'block';
                                    iframe.style.visibility = 'visible';
                                }
                            }
                        """)
                    except Exception:
                        pass  # Trigger attempt is best-effort
            except Exception:
                pass  # Hidden iframe detection is best-effort
        except Exception:
            pass  # All behavioral signals are best-effort

        signals["elapsed_ms"] = int((time.monotonic() - start_time) * 1000)
        return signals

    def human_mimic_interstitial_grace(self, duration_ms: int = 3500):
        """Spend a short bounded grace window on verification/interstitial pages.

        Some verification checks complete client-side after the initial interstitial renders. This
        method gives the page one human-like settle window (mouse movement, optional scroll,
        then wait) without introducing retry loops.
        """
        duration = max(0, int(duration_ms or 0))
        if duration <= 0:
            return
        page = self.page
        if page is None:
            time.sleep(duration / 1000.0)
            return

        deadline = time.monotonic() + (duration / 1000.0)
        self.browser_session._last_interstitial_grace_meta = {
            "press_hold_probe_attempts": 0,
            "press_hold_executed": False,
            "press_hold_success": False,
            "press_hold_success_signal": "",
            "press_hold_probes": [],
            "px_shell_nudged": False,
            "px_container_hold_attempted": False,
            "px_container_hold_executed": False,
            "vision_guided_press_attempted": False,
            "vision_guided_press_executed": False,
            "vision_guided_hint": {},
            "passive_behavior": {
                "mouse_moves": 0,
                "scroll_events": 0,
                "js_triggers": 0,
                "elapsed_ms": 0,
            },
        }

        meta = self.browser_session._last_interstitial_grace_meta
        press_hold_ready_wait_ms = max(
            1500,
            min(
                12000,
                int(get_threshold("skyscanner_press_hold_ready_wait_ms", 5000) or 5000),
            ),
        )
        press_hold_poll_interval_ms = max(
            100,
            min(
                1200,
                int(get_threshold("skyscanner_press_hold_poll_interval_ms", 250) or 250),
            ),
        )
        press_hold_min_hold_ms = max(
            9000,
            min(
                15000,
                int(get_threshold("skyscanner_press_hold_min_hold_ms", 12000) or 12000),
            ),
        )
        press_hold_degraded_min_ms = max(
            900,
            min(
                5000,
                int(get_threshold("skyscanner_press_hold_degraded_min_ms", 1800) or 1800),
            ),
        )
        meta["press_hold_timing"] = {
            "ready_wait_ms": int(press_hold_ready_wait_ms),
            "poll_interval_ms": int(press_hold_poll_interval_ms),
            "min_hold_ms": int(press_hold_min_hold_ms),
            "degraded_min_ms": int(press_hold_degraded_min_ms),
        }
        first_probe_defer_min_remaining_ms = max(
            1200,
            min(2500, int(press_hold_poll_interval_ms * 4)),
        )
        meta["press_hold_timing"]["first_probe_defer_min_remaining_ms"] = int(
            first_probe_defer_min_remaining_ms
        )

        def _probe_press_hold_surface_state() -> dict:
            try:
                if not hasattr(page, "evaluate"):
                    return {}
                data = page.evaluate(
                    """
                    () => {
                      const out = {
                        px_shell_present: false,
                        px_root_visible: false,
                        px_iframe_total: 0,
                        px_iframe_visible: 0,
                        hidden_human_iframe: false,
                        press_hold_text_visible: false,
                        loader_dots_visible: false,
                      };
                      try {
                        const pxRoot = document.querySelector("#px-captcha, [id*='px-captcha']");
                        if (pxRoot) {
                          out.px_shell_present = true;
                          const rr = pxRoot.getBoundingClientRect ? pxRoot.getBoundingClientRect() : null;
                          const rs = window.getComputedStyle ? window.getComputedStyle(pxRoot) : null;
                          out.px_root_visible = !!rr && rr.width > 0 && rr.height > 0 && !(rs && (rs.display === "none" || rs.visibility === "hidden"));
                          const rootText = String(pxRoot.innerText || pxRoot.textContent || "")
                            .replace(/\\s+/g, " ")
                            .trim()
                            .toLowerCase();
                          if (rootText) {
                            out.press_hold_text_visible = (
                              rootText.includes("press & hold")
                              || rootText.includes("press and hold")
                              || rootText.includes("press&hold")
                              || rootText.includes("長押し")
                              || rootText.includes("押し続け")
                            );
                            out.loader_dots_visible = (
                              rootText === "."
                              || rootText === ".."
                              || rootText === "..."
                              || rootText === "…"
                            );
                          }
                        }
                        const frames = Array.from(document.querySelectorAll("iframe"));
                        out.px_iframe_total = frames.filter((f) => {
                          const s = String((f.getAttribute && (f.getAttribute("src") || f.getAttribute("title"))) || "").toLowerCase();
                          return s.includes("px-cloud") || s.includes("human verification");
                        }).length;
                        out.px_iframe_visible = frames.filter((f) => {
                          const s = String((f.getAttribute && (f.getAttribute("src") || f.getAttribute("title"))) || "").toLowerCase();
                          if (!(s.includes("px-cloud") || s.includes("human verification"))) return false;
                          const r = f.getBoundingClientRect ? f.getBoundingClientRect() : null;
                          const st = window.getComputedStyle ? window.getComputedStyle(f) : null;
                          if (!r || r.width < 40 || r.height < 20) return false;
                          if (st && (st.display === "none" || st.visibility === "hidden")) return false;
                          return true;
                        }).length;
                        out.hidden_human_iframe = frames.some((f) => {
                          const t = String((f.getAttribute && f.getAttribute("title")) || "").toLowerCase();
                          if (!t.includes("human verification")) return false;
                          const st = window.getComputedStyle ? window.getComputedStyle(f) : null;
                          return !!st && (st.display === "none" || st.visibility === "hidden");
                        });
                      } catch (e) {}
                      return out;
                    }
                    """
                )
                return dict(data) if isinstance(data, dict) else {}
            except Exception:
                return {}

        def _wait_for_press_hold_ready(remaining_cap_ms: int) -> dict:
            wait_cap_ms = min(
                max(0, int(remaining_cap_ms or 0)),
                int(press_hold_ready_wait_ms),
            )
            if wait_cap_ms <= 0:
                state = _probe_press_hold_surface_state()
                return {
                    "ready": bool(
                        int(state.get("px_iframe_visible", 0) or 0) > 0
                        or bool(state.get("press_hold_text_visible", False))
                    ),
                    "waited_ms": 0,
                    "last_state": state,
                    "reason": "budget_exhausted",
                }
            started = time.monotonic()
            last_state: dict = {}
            while True:
                elapsed_ms = int((time.monotonic() - started) * 1000)
                if elapsed_ms >= wait_cap_ms:
                    break
                last_state = _probe_press_hold_surface_state()
                ready = bool(
                    int(last_state.get("px_iframe_visible", 0) or 0) > 0
                    or bool(last_state.get("press_hold_text_visible", False))
                )
                if ready:
                    return {
                        "ready": True,
                        "waited_ms": elapsed_ms,
                        "last_state": last_state,
                        "reason": "ready_signal_detected",
                    }
                pause_ms = min(int(press_hold_poll_interval_ms), max(80, wait_cap_ms - elapsed_ms))
                if pause_ms <= 0:
                    break
                try:
                    if hasattr(page, "wait_for_timeout"):
                        page.wait_for_timeout(pause_ms)
                    else:
                        time.sleep(pause_ms / 1000.0)
                except Exception:
                    break
            final_state = _probe_press_hold_surface_state()
            final_ready = bool(
                int(final_state.get("px_iframe_visible", 0) or 0) > 0
                or bool(final_state.get("press_hold_text_visible", False))
            )
            return {
                "ready": final_ready,
                "waited_ms": int((time.monotonic() - started) * 1000),
                "last_state": final_state,
                "reason": "ready_signal_detected" if final_ready else "ready_signal_timeout",
            }

        def _is_px_challenge_url() -> bool:
            try:
                current_url = str(getattr(page, "url", "") or "").lower()
            except Exception:
                current_url = ""
            if not current_url:
                return False
            return (
                "/sttc/px/captcha-v2/" in current_url
                or "/px/captcha" in current_url
                or "captcha-v2/index.html" in current_url
            )

        def _maybe_probe_press_hold_once() -> bool:
            remaining_ms = int((deadline - time.monotonic()) * 1000)
            meta["press_hold_probe_attempts"] = int(meta.get("press_hold_probe_attempts", 0) or 0) + 1
            attempt_no = int(meta.get("press_hold_probe_attempts", 0) or 0)
            probe_list = meta.setdefault("press_hold_probes", [])
            probe_entry = {
                "attempt": attempt_no,
                "remaining_ms": remaining_ms,
            }
            probe_entry.update(_probe_press_hold_surface_state())
            px_shell_present = bool(probe_entry.get("px_shell_present", False))
            px_iframe_total = int(probe_entry.get("px_iframe_total", 0) or 0)
            px_iframe_visible = int(probe_entry.get("px_iframe_visible", 0) or 0)
            press_hold_text_visible = bool(probe_entry.get("press_hold_text_visible", False))
            loader_dots_visible = bool(probe_entry.get("loader_dots_visible", False))
            challenge_url_hint = _is_px_challenge_url()
            long_hold_surface = bool(
                press_hold_text_visible
                or loader_dots_visible
                or px_shell_present
                or px_iframe_visible > 0
                or (challenge_url_hint and px_iframe_total > 0)
            )
            long_hold_mode = bool(long_hold_surface)
            probe_entry["challenge_url_hint"] = bool(challenge_url_hint)
            probe_entry["long_hold_surface"] = bool(long_hold_surface)
            should_wait_ready = (
                long_hold_mode
                and not press_hold_text_visible
                and remaining_ms >= int(press_hold_degraded_min_ms + 900)
            )
            if should_wait_ready:
                # Keep enough budget for a full long-hold attempt after waiting for
                # loader dots/press text readiness; otherwise we regress to short holds.
                ready_wait_budget_ms = max(0, remaining_ms - int(press_hold_min_hold_ms + 450))
                probe_entry["ready_wait_budget_ms"] = int(ready_wait_budget_ms)
                if ready_wait_budget_ms > 0:
                    readiness = _wait_for_press_hold_ready(
                        min(remaining_ms - 300, ready_wait_budget_ms)
                    )
                else:
                    readiness = {
                        "ready": False,
                        "waited_ms": 0,
                        "last_state": {},
                        "reason": "preserve_long_hold_budget",
                    }
                probe_entry["ready_wait_ms"] = int(readiness.get("waited_ms", 0) or 0)
                probe_entry["ready_wait_reason"] = str(readiness.get("reason", "") or "")
                probe_entry["ready_after_wait"] = bool(readiness.get("ready", False))
                last_state = readiness.get("last_state")
                if isinstance(last_state, dict) and last_state:
                    probe_entry.update(
                        {
                            "post_wait_px_iframe_total": int(last_state.get("px_iframe_total", 0) or 0),
                            "post_wait_px_iframe_visible": int(last_state.get("px_iframe_visible", 0) or 0),
                            "post_wait_press_hold_text_visible": bool(
                                last_state.get("press_hold_text_visible", False)
                            ),
                            "post_wait_loader_dots_visible": bool(last_state.get("loader_dots_visible", False)),
                        }
                    )
                remaining_ms = int((deadline - time.monotonic()) * 1000)
                refreshed_probe = _probe_press_hold_surface_state()
                if isinstance(refreshed_probe, dict) and refreshed_probe:
                    probe_entry.update(refreshed_probe)
                    px_shell_present = bool(refreshed_probe.get("px_shell_present", False))
                    px_iframe_total = int(refreshed_probe.get("px_iframe_total", 0) or 0)
                    px_iframe_visible = int(refreshed_probe.get("px_iframe_visible", 0) or 0)
                    press_hold_text_visible = bool(refreshed_probe.get("press_hold_text_visible", False))
                    loader_dots_visible = bool(refreshed_probe.get("loader_dots_visible", False))
                    challenge_url_hint = _is_px_challenge_url()
                    long_hold_surface = bool(
                        press_hold_text_visible
                        or loader_dots_visible
                        or px_shell_present
                        or px_iframe_visible > 0
                        or (challenge_url_hint and px_iframe_total > 0)
                    )
                    long_hold_mode = bool(long_hold_surface)
                    probe_entry["challenge_url_hint"] = bool(challenge_url_hint)
                    probe_entry["long_hold_surface"] = bool(long_hold_surface)
            hold_cap_ms = 15000 if long_hold_mode else 2200
            hold_floor_ms = 1500 if long_hold_mode else 700
            hold_budget_ms = max(0, min(hold_cap_ms, remaining_ms - 300))
            if long_hold_mode:
                if hold_budget_ms >= press_hold_min_hold_ms:
                    hold_budget_ms = max(press_hold_min_hold_ms, hold_budget_ms)
                elif hold_budget_ms >= press_hold_degraded_min_ms:
                    probe_entry["degraded_hold_budget"] = True
            hold_target_ms = int(hold_budget_ms)
            if long_hold_mode and hold_budget_ms >= hold_floor_ms:
                lower_bound = max(hold_floor_ms, int(hold_budget_ms * 0.78))
                hold_target_ms = random.randint(int(lower_bound), int(hold_budget_ms))
            elif hold_budget_ms >= hold_floor_ms:
                lower_bound = max(hold_floor_ms, int(hold_budget_ms * 0.72))
                hold_target_ms = random.randint(int(lower_bound), int(hold_budget_ms))
            probe_entry["long_hold_mode"] = long_hold_mode
            probe_entry["hold_budget_ms"] = hold_budget_ms
            probe_entry["hold_target_ms"] = int(hold_target_ms)
            if (
                attempt_no == 1
                and not bool(probe_entry.get("px_shell_present", False))
                and int(probe_entry.get("px_iframe_visible", 0) or 0) <= 0
                and not bool(probe_entry.get("press_hold_text_visible", False))
                and remaining_ms >= int(first_probe_defer_min_remaining_ms)
            ):
                # First probe is a lightweight readiness sample. Avoid attempting a hold
                # until we have at least one follow-up probe signal.
                probe_entry["executed"] = False
                probe_entry["deferred_first_probe"] = True
                probe_list.append(probe_entry)
                return False
            if hold_budget_ms < hold_floor_ms:
                probe_entry["executed"] = False
                probe_list.append(probe_entry)
                return False
            if self.human_mimic_press_and_hold_challenge(max_hold_ms=hold_target_ms):
                meta["press_hold_executed"] = True
                probe_entry["executed"] = True
                if hasattr(page, "wait_for_timeout"):
                    settle_ms = min(1600, max(350, int(hold_target_ms // 6)))
                    if settle_ms > 0:
                        try:
                            page.wait_for_timeout(settle_ms)
                        except Exception:
                            pass
                post_probe = _probe_press_hold_surface_state()
                probe_entry["post_px_shell_present"] = bool(post_probe.get("px_shell_present", False))
                probe_entry["post_px_root_visible"] = bool(post_probe.get("px_root_visible", False))
                probe_entry["post_px_iframe_total"] = int(post_probe.get("px_iframe_total", 0) or 0)
                probe_entry["post_px_iframe_visible"] = int(post_probe.get("px_iframe_visible", 0) or 0)
                pre_shell = bool(probe_entry.get("px_shell_present", False))
                post_shell = bool(post_probe.get("px_shell_present", False))
                pre_iframe_visible = int(probe_entry.get("px_iframe_visible", 0) or 0)
                post_iframe_visible = int(post_probe.get("px_iframe_visible", 0) or 0)
                success_signal = ""
                if pre_shell and not post_shell:
                    success_signal = "px_shell_disappeared"
                elif pre_iframe_visible > 0 and post_iframe_visible <= 0:
                    success_signal = "px_iframe_hidden"
                probe_entry["success_signal"] = success_signal
                if success_signal:
                    meta["press_hold_success"] = True
                    meta["press_hold_success_signal"] = success_signal
                probe_list.append(probe_entry)
                return True
            probe_entry["executed"] = False
            probe_list.append(probe_entry)
            return False

        def _maybe_nudge_px_shell_once() -> bool:
            if bool(meta.get("px_shell_nudged", False)):
                return False
            probe_list = meta.get("press_hold_probes", [])
            last_probe = probe_list[-1] if isinstance(probe_list, list) and probe_list else {}
            if not isinstance(last_probe, dict):
                return False
            if int(meta.get("press_hold_probe_attempts", 0) or 0) < 2:
                return False
            if not bool(last_probe.get("px_shell_present", False)):
                return False
            if int(last_probe.get("px_iframe_visible", 0) or 0) > 0:
                return False
            px_root_visible = bool(last_probe.get("px_root_visible", False))
            px_iframe_total = int(last_probe.get("px_iframe_total", 0) or 0)
            if not px_root_visible:
                return False
            # Run telemetry showed: iframe_total=1, iframe_visible=0, hidden_human_iframe=false.
            # Treat any non-visible iframe state as nudge-eligible, not only hidden-title matches.
            if px_iframe_total > 0 and int(last_probe.get("px_iframe_visible", 0) or 0) > 0:
                return False
            if not hasattr(page, "mouse") or not hasattr(page, "evaluate"):
                return False
            try:
                # Best-effort reveal for injected challenge iframe containers before pointer nudge.
                page.evaluate(
                    """
                    () => {
                      const frames = Array.from(document.querySelectorAll("iframe"));
                      for (const f of frames.slice(0, 12)) {
                        const marker = String((f.getAttribute && (f.getAttribute("src") || f.getAttribute("title"))) || "").toLowerCase();
                        if (!(marker.includes("px-cloud") || marker.includes("human verification"))) continue;
                        try {
                          f.style.display = "block";
                          f.style.visibility = "visible";
                          f.style.opacity = "1";
                          f.style.pointerEvents = "auto";
                        } catch (e) {}
                      }
                    }
                    """
                )
            except Exception:
                pass
            try:
                target = page.evaluate(
                    """
                    () => {
                      const el = document.querySelector("#px-captcha, [id*='px-captcha']");
                      if (!el) return null;
                      const rect = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
                      const st = window.getComputedStyle ? window.getComputedStyle(el) : null;
                      if (!rect || rect.width < 120 || rect.height < 40) return null;
                      if (st && (st.display === "none" || st.visibility === "hidden")) return null;
                      return { x: rect.x + (rect.width / 2), y: rect.y + (rect.height / 2) };
                    }
                    """
                )
            except Exception:
                target = None
            if not isinstance(target, dict):
                return False
            try:
                x = float(target.get("x", 0))
                y = float(target.get("y", 0))
            except Exception:
                return False
            if x <= 0 or y <= 0:
                return False
            try:
                page.mouse.move(x, y, steps=random.randint(8, 16))
                if hasattr(page, "wait_for_timeout"):
                    page.wait_for_timeout(random.randint(80, 160))
                page.mouse.down()
                if hasattr(page, "wait_for_timeout"):
                    page.wait_for_timeout(random.randint(120, 220))
                else:
                    time.sleep(0.16)
                page.mouse.up()
                meta["px_shell_nudged"] = True
                return True
            except Exception:
                return False

        def _maybe_hold_px_container_once() -> bool:
            if bool(meta.get("px_container_hold_attempted", False)):
                return False
            probe_list = meta.get("press_hold_probes", [])
            last_probe = probe_list[-1] if isinstance(probe_list, list) and probe_list else {}
            if not isinstance(last_probe, dict):
                return False
            if int(meta.get("press_hold_probe_attempts", 0) or 0) < 2:
                return False
            if not bool(last_probe.get("px_shell_present", False)):
                return False
            if not bool(last_probe.get("px_root_visible", False)):
                return False
            if int(last_probe.get("px_iframe_visible", 0) or 0) > 0:
                return False
            if bool(meta.get("press_hold_executed", False)):
                return False
            if not hasattr(page, "mouse") or not hasattr(page, "evaluate"):
                return False
            remaining_ms = int((deadline - time.monotonic()) * 1000)
            long_hold_mode = int(last_probe.get("px_iframe_visible", 0) or 0) <= 0
            hold_floor_ms = (press_hold_min_hold_ms if long_hold_mode else 700)
            hold_cap_ms = 15000 if long_hold_mode else 1800
            hold_budget_ms = max(0, min(hold_cap_ms, remaining_ms - 300))
            if long_hold_mode and hold_budget_ms < hold_floor_ms and hold_budget_ms >= press_hold_degraded_min_ms:
                hold_floor_ms = int(press_hold_degraded_min_ms)
                meta["px_container_hold_degraded"] = True
            if hold_budget_ms < hold_floor_ms:
                return False
            if long_hold_mode:
                hold_lower = max(hold_floor_ms, int(hold_budget_ms * 0.8))
                hold_ms = random.randint(int(hold_lower), int(hold_budget_ms))
            else:
                hold_lower = max(hold_floor_ms, int(hold_budget_ms * 0.72))
                hold_ms = random.randint(int(hold_lower), int(hold_budget_ms))
            meta["px_container_hold_attempted"] = True
            try:
                target = page.evaluate(
                    """
                    () => {
                      const el = document.querySelector("#px-captcha, [id*='px-captcha']");
                      if (!el) return null;
                      const rect = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
                      const st = window.getComputedStyle ? window.getComputedStyle(el) : null;
                      if (!rect || rect.width < 120 || rect.height < 40) return null;
                      if (st && (st.display === "none" || st.visibility === "hidden")) return null;
                      return { x: rect.x + (rect.width / 2), y: rect.y + (rect.height / 2) };
                    }
                    """
                )
            except Exception:
                target = None
            if not isinstance(target, dict):
                return False
            try:
                x = float(target.get("x", 0))
                y = float(target.get("y", 0))
            except Exception:
                return False
            if x <= 0 or y <= 0:
                return False
            try:
                page.mouse.move(x, y, steps=random.randint(10, 22))
                if hasattr(page, "wait_for_timeout"):
                    page.wait_for_timeout(random.randint(120, 220))
                page.mouse.down()
                if hasattr(page, "wait_for_timeout"):
                    page.wait_for_timeout(hold_ms)
                else:
                    time.sleep(hold_ms / 1000.0)
                page.mouse.up()
                if hasattr(page, "wait_for_timeout"):
                    page.wait_for_timeout(random.randint(180, 320))
                meta["px_container_hold_executed"] = True
                return True
            except Exception:
                return False

        def _maybe_vision_guided_press_once() -> bool:
            if bool(meta.get("vision_guided_press_attempted", False)):
                return False
            meta["vision_guided_press_attempted"] = True
            if not hasattr(page, "screenshot") or not hasattr(page, "mouse"):
                return False
            remaining_ms = int((deadline - time.monotonic()) * 1000)
            hold_ms = max(0, min(2200, remaining_ms - 300))
            if hold_ms < 700:
                return False
            try:
                image_bytes = page.screenshot(type="png", full_page=True)
                if not isinstance(image_bytes, (bytes, bytearray)) or not image_bytes:
                    return False
                image_b64 = base64.b64encode(bytes(image_bytes)).decode("ascii")
            except Exception:
                return False
            html_hint = ""
            try:
                if hasattr(page, "content"):
                    html_hint = str(page.content() or "")[:2000]
            except Exception:
                html_hint = ""
            hint = _extract_verification_action_with_vision_light(image_b64, html_hint=html_hint)
            if isinstance(hint, dict):
                meta["vision_guided_hint"] = {
                    "protector_label": str(hint.get("protector_label", "") or ""),
                    "solution": str(hint.get("solution", "") or ""),
                    "confidence": str(hint.get("confidence", "low") or "low"),
                    "target_bbox": list(hint.get("target_bbox", []) or [])[:4]
                    if isinstance(hint.get("target_bbox"), (list, tuple))
                    else None,
                }
            label = str(hint.get("protector_label", "no_protection") or "no_protection")
            bbox = hint.get("target_bbox")
            if label == "no_protection" or not isinstance(bbox, list) or len(bbox) != 4:
                return False
            long_hold_mode = label == "interstitial_press_hold"
            hold_floor_ms = (press_hold_min_hold_ms if long_hold_mode else 700)
            hold_cap_ms = 15000 if long_hold_mode else 2200
            hold_budget_ms = max(0, min(hold_cap_ms, remaining_ms - 300))
            if long_hold_mode and hold_budget_ms < hold_floor_ms and hold_budget_ms >= press_hold_degraded_min_ms:
                hold_floor_ms = int(press_hold_degraded_min_ms)
                meta["vision_guided_press_degraded"] = True
            if hold_budget_ms < hold_floor_ms:
                return False
            if long_hold_mode:
                hold_lower = max(hold_floor_ms, int(hold_budget_ms * 0.8))
                hold_ms = random.randint(int(hold_lower), int(hold_budget_ms))
            else:
                hold_lower = max(hold_floor_ms, int(hold_budget_ms * 0.72))
                hold_ms = random.randint(int(hold_lower), int(hold_budget_ms))
            viewport = getattr(page, "viewport_size", None) or {}
            width = int(viewport.get("width", 1366) or 1366)
            height = int(viewport.get("height", 900) or 900)
            try:
                x = (float(bbox[0]) + (float(bbox[2]) / 2.0)) * width
                y = (float(bbox[1]) + (float(bbox[3]) / 2.0)) * height
            except Exception:
                return False
            if x <= 1 or y <= 1:
                return False
            try:
                page.mouse.move(x, y, steps=random.randint(10, 24))
                if hasattr(page, "wait_for_timeout"):
                    page.wait_for_timeout(random.randint(120, 220))
                page.mouse.down()
                if hasattr(page, "wait_for_timeout"):
                    page.wait_for_timeout(hold_ms if label == "interstitial_press_hold" else random.randint(180, 320))
                else:
                    time.sleep((hold_ms if label == "interstitial_press_hold" else 240) / 1000.0)
                page.mouse.up()
                if hasattr(page, "wait_for_timeout"):
                    page.wait_for_timeout(random.randint(180, 320))
                meta["vision_guided_press_executed"] = True
                return True
            except Exception:
                return False

        def _px_shell_present() -> bool:
            try:
                if not hasattr(page, "evaluate"):
                    return False
                return bool(
                    page.evaluate(
                        """
                        () => {
                          const pxRoot = document.querySelector("#px-captcha, [id*='px-captcha']");
                          const headline = Array.from(document.querySelectorAll("h1, h2, [role='heading']"))
                            .slice(0, 8)
                            .some((el) => String(el.innerText || el.textContent || "").toLowerCase().includes("person or a robot"));
                          return !!pxRoot || headline;
                        }
                        """
                    )
                )
            except Exception:
                return False

        if self.browser_session.human_mimic:
            if _maybe_probe_press_hold_once():
                remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
                if remaining_ms <= 0:
                    return
            px_shell_seen = _px_shell_present()

            # Enhanced PerimeterX behavioral simulation (non-iframe-based challenges)
            remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
            passive_budget_ms = max(0, int(remaining_ms * 0.4))
            if px_shell_seen:
                # Reserve most budget for long press-hold interaction once challenge is ready.
                passive_budget_ms = min(passive_budget_ms, int(press_hold_poll_interval_ms))

            # When the PX shell is already present, avoid extra passive pointer
            # choreography and reserve budget for direct press-hold probing.
            if passive_budget_ms >= 1000 and not px_shell_seen:
                behavioral_signals = self.simulate_passive_perimetrix_behavior(duration_ms=passive_budget_ms)
                if isinstance(meta, dict):
                    meta["passive_behavior"] = behavioral_signals

            remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
            if remaining_ms <= 0:
                return

            try:
                if px_shell_seen:
                    raise RuntimeError("skip_pointer_moves_px_shell_present")
                width, height = 1366, 900
                if getattr(page, "viewport_size", None):
                    try:
                        viewport = page.viewport_size or {}
                        width = int(viewport.get("width", width) or width)
                        height = int(viewport.get("height", height) or height)
                    except Exception:
                        pass
                if hasattr(page, "mouse"):
                    for _ in range(2):
                        if time.monotonic() >= deadline:
                            break
                        x = random.randint(80, max(120, width - 80))
                        y = random.randint(80, max(120, height - 80))
                        page.mouse.move(x, y, steps=random.randint(8, 24))
                        if hasattr(page.mouse, "wheel") and random.random() < 0.6:
                            page.mouse.wheel(0, random.randint(60, 220))
                        if hasattr(page, "wait_for_timeout"):
                            page.wait_for_timeout(random.randint(120, 260))
            except Exception:
                pass

            try:
                press_hold_done = False
                while (
                    int(meta.get("press_hold_probe_attempts", 0) or 0) < (5 if px_shell_seen else 3)
                    and time.monotonic() < deadline
                ):
                    if _maybe_probe_press_hold_once():
                        press_hold_done = True
                        break
                    _maybe_nudge_px_shell_once()
                    # Escalation path for runs where iframe stays non-visible even after nudge.
                    if _maybe_hold_px_container_once():
                        if _maybe_probe_press_hold_once():
                            press_hold_done = True
                            break
                    # Final bounded assist: vision_light-guided bbox hold/click, then immediate re-probe.
                    if _maybe_vision_guided_press_once():
                        if _maybe_probe_press_hold_once():
                            press_hold_done = True
                            break
                    post_probe_remaining_ms = int((deadline - time.monotonic()) * 1000)
                    if post_probe_remaining_ms < 500:
                        break
                    probe_list = meta.get("press_hold_probes", [])
                    last_probe = probe_list[-1] if isinstance(probe_list, list) and probe_list else {}
                    last_iframe_total = int((last_probe or {}).get("px_iframe_total", 0) or 0) if isinstance(last_probe, dict) else 0
                    last_iframe_visible = int((last_probe or {}).get("px_iframe_visible", 0) or 0) if isinstance(last_probe, dict) else 0
                    if px_shell_seen and last_iframe_total == 0:
                        target_pause = random.randint(650, 980)
                    elif px_shell_seen and last_iframe_visible == 0:
                        target_pause = random.randint(360, 700)
                    else:
                        target_pause = random.randint(180, 420)
                    pause_ms = min(target_pause, max(120, post_probe_remaining_ms - 300))
                    if hasattr(page, "wait_for_timeout"):
                        page.wait_for_timeout(pause_ms)
                    else:
                        time.sleep(pause_ms / 1000.0)
                if not press_hold_done:
                    meta["press_hold_executed"] = False
                    meta["press_hold_success"] = False
                    meta["press_hold_success_signal"] = ""
            except Exception:
                pass

        remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
        if remaining_ms <= 0:
            return
        try:
            if hasattr(page, "wait_for_timeout"):
                page.wait_for_timeout(remaining_ms)
            else:
                time.sleep(remaining_ms / 1000.0)
        except Exception:
            pass

    def human_mimic_press_and_hold_challenge(self, max_hold_ms: int = 1800) -> bool:
        """Attempt a single bounded PRESS & HOLD interaction on visible challenge controls.

        Returns True if a hold gesture was executed, False otherwise.
        """
        page = self.page
        if page is None or not self.browser_session.human_mimic:
            return False
        hold_ms = max(700, min(15000, int(max_hold_ms or 0)))
        if hold_ms <= 0:
            return False

        try:
            target = page.evaluate(
                """
                () => {
                  const norm = (v) => String(v || "").replace(/\\s+/g, " ").trim().toLowerCase();
                  const matches = [];
                  const nodes = Array.from(document.querySelectorAll(
                    "button,[role='button'],div,span"
                  ));
                  for (const el of nodes.slice(0, 600)) {
                    const txt = norm(el.innerText || el.textContent || "");
                    if (!txt) continue;
                    if (!(txt.includes("press & hold") || txt.includes("press and hold"))) continue;
                    const rect = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
                    if (!rect || rect.width < 40 || rect.height < 20) continue;
                    const style = window.getComputedStyle ? window.getComputedStyle(el) : null;
                    if (style && (style.visibility === "hidden" || style.display === "none")) continue;
                    matches.push({
                      x: rect.x + (rect.width / 2),
                      y: rect.y + (rect.height / 2),
                      w: rect.width,
                      h: rect.height,
                      text: txt.slice(0, 80),
                      kind: "press_hold_text",
                    });
                  }
                  matches.sort((a, b) => (b.w * b.h) - (a.w * a.h));
                  if (matches[0]) return matches[0];

                  // PX fallback: the visible "PRESS & HOLD" control may render inside a
                  // cross-origin iframe, so parent-DOM text scanning misses it.
                  const pxRoot = document.querySelector("#px-captcha, [id*='px-captcha']");
                  if (pxRoot) {
                    const rootRect = pxRoot.getBoundingClientRect ? pxRoot.getBoundingClientRect() : null;
                    const rootStyle = window.getComputedStyle ? window.getComputedStyle(pxRoot) : null;
                    const rootVisible = !!rootRect
                      && rootRect.width >= 160
                      && rootRect.height >= 50
                      && !(rootStyle && (rootStyle.visibility === "hidden" || rootStyle.display === "none"));
                    if (rootVisible) {
                      const rootFrames = Array.from(pxRoot.querySelectorAll ? pxRoot.querySelectorAll("iframe") : []);
                      const visibleRootFrames = rootFrames.filter((frame) => {
                        if (!frame || !frame.getBoundingClientRect) return false;
                        const fr = frame.getBoundingClientRect();
                        const fs = window.getComputedStyle ? window.getComputedStyle(frame) : null;
                        if (!fr || fr.width < 40 || fr.height < 20) return false;
                        if (fs && (fs.visibility === "hidden" || fs.display === "none")) return false;
                        return true;
                      });
                      return {
                        x: rootRect.x + (rootRect.width / 2),
                        y: rootRect.y + (rootRect.height / 2),
                        w: rootRect.width,
                        h: rootRect.height,
                        text: "px-captcha-shell",
                        kind: visibleRootFrames.length > 0 ? "px_container" : "px_shell",
                      };
                    }
                  }

                  const pxCandidates = Array.from(document.querySelectorAll(
                    "#px-captcha, [id*='px-captcha'], iframe[title*='Human verification' i], iframe[src*='px-cloud']"
                  ));
                  const pxMatches = [];
                  for (const el of pxCandidates.slice(0, 24)) {
                    if (!el) continue;
                    const rect = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
                    if (!rect || rect.width < 120 || rect.height < 40) continue;
                    const style = window.getComputedStyle ? window.getComputedStyle(el) : null;
                    if (style && (style.visibility === "hidden" || style.display === "none")) continue;
                    const tagName = String(el.tagName || "").toLowerCase();
                    let interactive = tagName === "iframe";
                    if (!interactive) {
                      try {
                        const childFrames = Array.from(el.querySelectorAll ? el.querySelectorAll("iframe") : []);
                        interactive = childFrames.some((frame) => {
                          const fr = frame && frame.getBoundingClientRect ? frame.getBoundingClientRect() : null;
                          if (!fr || fr.width < 120 || fr.height < 40) return false;
                          const fs = window.getComputedStyle ? window.getComputedStyle(frame) : null;
                          if (fs && (fs.visibility === "hidden" || fs.display === "none")) return false;
                          return true;
                        });
                      } catch (e) {}
                    }
                    if (!interactive) continue;
                    if (tagName === "iframe") {
                      const title = String((el.getAttribute && el.getAttribute("title")) || "").toLowerCase();
                      const ariaHidden = String((el.getAttribute && el.getAttribute("aria-hidden")) || "").toLowerCase();
                      const role = String((el.getAttribute && el.getAttribute("role")) || "").toLowerCase();
                      const likelyTelemetry = (
                        rect.width <= 110
                        && rect.height <= 110
                        && rect.x <= 16
                        && rect.y <= 16
                        && !title
                        && (ariaHidden === "true" || role === "presentation")
                      );
                      if (likelyTelemetry) continue;
                    }
                    pxMatches.push({
                      x: rect.x + (rect.width / 2),
                      y: rect.y + (rect.height / 2),
                      w: rect.width,
                      h: rect.height,
                      text: "px-captcha",
                      kind: "px_container",
                    });
                  }
                  pxMatches.sort((a, b) => (b.w * b.h) - (a.w * a.h));
                  return pxMatches[0] || null;
                }
                """
            )
        except Exception:
            return False

        if not isinstance(target, dict):
            return False

        try:
            x = float(target.get("x", 0))
            y = float(target.get("y", 0))
        except Exception:
            return False
        if x <= 0 or y <= 0 or not hasattr(page, "mouse"):
            return False

        target_width = max(40.0, float(target.get("w", 0) or 0.0))
        target_height = max(20.0, float(target.get("h", 0) or 0.0))
        long_hold_mode = hold_ms >= 9000
        if long_hold_mode:
            # Avoid repeated perfectly-centered robotic holds on long challenges.
            jitter_x = random.uniform(-1.0, 1.0) * min(12.0, target_width * 0.08)
            jitter_y = random.uniform(-1.0, 1.0) * min(8.0, target_height * 0.08)
            x += jitter_x
            y += jitter_y

        try:
            log.info(
                "browser.interstitial.press_hold.detected x=%.1f y=%.1f hold_ms=%d kind=%s",
            x,
            y,
            hold_ms,
            str(target.get("kind", "unknown") or "unknown"),
        )
            target_kind = str(target.get("kind", "unknown") or "unknown").strip().lower()
            move_steps = random.randint(10, 24)
            if long_hold_mode:
                move_steps = random.randint(6, 14)
            if long_hold_mode:
                # Add a bounded approach trajectory before the hold so pointer behavior
                # is less "teleport to center + down".
                approach_dx = max(22.0, min(80.0, target_width * 0.32))
                approach_dy = max(14.0, min(56.0, target_height * 0.45))
                start_x = float(x + random.uniform(-approach_dx, approach_dx))
                start_y = float(y + random.uniform(-approach_dy, approach_dy))
                mid_x = float((start_x + x) / 2.0 + random.uniform(-8.0, 8.0))
                mid_y = float((start_y + y) / 2.0 + random.uniform(-6.0, 6.0))
                page.mouse.move(start_x, start_y, steps=random.randint(8, 18))
                if hasattr(page, "wait_for_timeout"):
                    page.wait_for_timeout(random.randint(70, 180))
                page.mouse.move(mid_x, mid_y, steps=random.randint(6, 14))
                if hasattr(page, "wait_for_timeout"):
                    page.wait_for_timeout(random.randint(45, 120))
                # Small final correction for shell/container targets where iframe can appear late.
                if target_kind in {"px_shell", "px_container"}:
                    fine_x = float(x + random.uniform(-3.0, 3.0))
                    fine_y = float(y + random.uniform(-2.0, 2.0))
                    page.mouse.move(fine_x, fine_y, steps=random.randint(2, 5))
                    x = fine_x
                    y = fine_y
            page.mouse.move(x, y, steps=move_steps)
            if hasattr(page, "wait_for_timeout"):
                page.wait_for_timeout(random.randint(120, 260))
            page.mouse.down()
            if long_hold_mode:
                # Keep hold continuous while adding tiny human-like drift/noise.
                remaining_ms = int(hold_ms)
                last_x = float(x)
                last_y = float(y)
                safe_dx = max(4.0, min(18.0, target_width * 0.16))
                safe_dy = max(3.0, min(12.0, target_height * 0.18))
                min_x = float(x - safe_dx)
                max_x = float(x + safe_dx)
                min_y = float(y - safe_dy)
                max_y = float(y + safe_dy)
                while remaining_ms > 0:
                    chunk_upper = min(2800, remaining_ms)
                    chunk_lower = min(chunk_upper, max(900, int(chunk_upper * 0.48)))
                    chunk_ms = min(remaining_ms, random.randint(int(chunk_lower), int(chunk_upper)))
                    if hasattr(page, "wait_for_timeout"):
                        page.wait_for_timeout(chunk_ms)
                    else:
                        time.sleep(chunk_ms / 1000.0)
                    remaining_ms -= int(chunk_ms)
                    if remaining_ms <= 0:
                        break
                    try:
                        if random.random() < 0.72:
                            drift_x = random.uniform(-1.8, 1.8)
                            drift_y = random.uniform(-1.4, 1.4)
                            next_x = min(max_x, max(min_x, last_x + drift_x))
                            next_y = min(max_y, max(min_y, last_y + drift_y))
                            last_x = float(next_x)
                            last_y = float(next_y)
                            page.mouse.move(last_x, last_y, steps=random.randint(1, 4))
                    except Exception:
                        # Keep hold active even if drift move fails.
                        pass
            else:
                if hasattr(page, "wait_for_timeout"):
                    page.wait_for_timeout(hold_ms)
                else:
                    time.sleep(hold_ms / 1000.0)
            page.mouse.up()
            if hasattr(page, "wait_for_timeout"):
                page.wait_for_timeout(random.randint(220, 420))
            log.info("browser.interstitial.press_hold.done hold_ms=%d", hold_ms)
            return True
        except Exception:
            return False
