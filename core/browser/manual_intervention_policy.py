"""Shared policy helpers for human/manual intervention flows."""

from __future__ import annotations

from typing import Any, Dict, List


_VERIFICATION_URL_MARKERS = (
    "/sttc/px/captcha-v2/",
    "/px/captcha",
    "captcha-v2/index.html",
    "/captcha",
    "captcha.",
    "human-verification",
    "verify you are human",
    "are you a robot",
    "/sorry/",
    "interstitial",
)

_SKYSCANNER_PX_URL_MARKERS = (
    "/sttc/px/captcha-v2/",
    "/px/captcha",
    "captcha-v2/index.html",
)

_DEFAULT_DIAGNOSTIC_SELECTORS = (
    "#px-captcha",
    "[id*='px-captcha']",
    "iframe[title*='Human verification' i]",
    "iframe[src*='captcha' i]",
    "section[class*='resolve' i]",
    "[class*='captcha' i]",
    "[id*='captcha' i]",
)


def _visible_selector_probe_script() -> str:
    return """
    (selectors) => {
      const isVisible = (el) => {
        try {
          if (!el) return false;
          const st = window.getComputedStyle ? window.getComputedStyle(el) : null;
          if (st && (st.display === "none" || st.visibility === "hidden")) return false;
          const r = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
          if (!r) return false;
          return r.width > 10 && r.height > 10;
        } catch (e) {
          return false;
        }
      };
      const list = Array.isArray(selectors) ? selectors : [];
      for (const sel of list) {
        if (!sel) continue;
        const el = document.querySelector(String(sel));
        if (isVisible(el)) return true;
      }
      return false;
    }
    """


def is_verification_url(url_text: str) -> bool:
    """Return True when URL resembles a challenge/captcha/interstitial surface."""
    lower = str(url_text or "").strip().lower()
    if not lower:
        return False
    return any(token in lower for token in _VERIFICATION_URL_MARKERS)


def is_skyscanner_px_captcha_url(url_text: str) -> bool:
    """Return True when URL resembles Skyscanner PX captcha."""
    lower = str(url_text or "").strip().lower()
    if not lower:
        return False
    return any(token in lower for token in _SKYSCANNER_PX_URL_MARKERS)


def is_verification_surface(page: Any, *, fallback_url: str = "") -> bool:
    """Best-effort runtime check for a visible verification challenge surface."""
    url_now = str(fallback_url or "")
    try:
        if page is not None:
            url_now = str(getattr(page, "url", "") or fallback_url or "")
    except Exception:
        url_now = str(fallback_url or "")
    if is_verification_url(url_now):
        return True
    if page is None or not hasattr(page, "evaluate"):
        return False
    try:
        return bool(
            page.evaluate(
                _visible_selector_probe_script(),
                list(_DEFAULT_DIAGNOSTIC_SELECTORS),
            )
        )
    except Exception:
        return False


def is_skyscanner_px_captcha_surface(page: Any, *, fallback_url: str = "") -> bool:
    """Best-effort runtime check for Skyscanner PX captcha-specific surface."""
    url_now = str(fallback_url or "")
    try:
        if page is not None:
            url_now = str(getattr(page, "url", "") or fallback_url or "")
    except Exception:
        url_now = str(fallback_url or "")
    if is_skyscanner_px_captcha_url(url_now):
        return True
    if page is None or not hasattr(page, "evaluate"):
        return False
    try:
        return bool(
            page.evaluate(
                _visible_selector_probe_script(),
                ["#px-captcha", "[id*='px-captcha']"],
            )
        )
    except Exception:
        return False


def should_mark_manual_observation_complete(
    *,
    intervention_mode: str,
    ui_capture: Dict[str, Any],
    before_url: str,
    after_url: str,
    challenge_token_changes: int = 0,
    challenge_signature_changes: int = 0,
) -> bool:
    """Determine whether a target-closed manual run should be observation-complete."""
    mode = str(intervention_mode or "").strip().lower()
    if mode != "demo":
        return False
    capture = dict(ui_capture or {}) if isinstance(ui_capture, dict) else {}
    event_count = int(capture.get("event_count", 0) or 0)
    direct_count = int(capture.get("direct_event_count", 0) or 0)
    url_before = str(before_url or "").strip().lower()
    url_after = str(after_url or "").strip().lower()
    url_changed = bool(url_before and url_after and url_before != url_after)
    token_changes = int(challenge_token_changes or 0)
    signature_changes = int(challenge_signature_changes or 0)
    started_on_verification = is_verification_url(url_before)

    # When the terminal URL is still a verification surface, token churn alone is
    # not sufficient: repeated PRESS&HOLD reissues can rotate tokens without clear.
    if started_on_verification and is_verification_url(url_after):
        if signature_changes >= 1 and (token_changes >= 2 or event_count >= 20 or direct_count >= 4):
            return True
        return False

    if is_verification_url(url_after):
        return False
    if direct_count >= 20:
        return True
    if url_changed and direct_count >= 10:
        return True
    if (
        url_changed
        and event_count >= 80
        and token_changes <= 0
        and signature_changes <= 0
    ):
        return True
    return False


def manual_intervention_diagnostic_selectors(site_key: str = "") -> List[str]:
    """Return compact selector hints for runtime manual-window diagnostics."""
    site = str(site_key or "").strip().lower()
    selectors = list(_DEFAULT_DIAGNOSTIC_SELECTORS)
    if site == "skyscanner":
        selectors.extend(
            [
                "iframe[src*='px-cloud.net']",
                "section[class*='identifier' i]",
            ]
        )
    return list(dict.fromkeys([str(sel) for sel in selectors if str(sel or "").strip()]))
