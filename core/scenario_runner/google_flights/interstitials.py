"""Google Flights interstitials functions."""

from typing import Any, Callable, Dict, List, Optional, Tuple
from utils.logging import get_logger

log = get_logger(__name__)

import time

def _detect_site_interstitial_block(html_text: str, site_key: str) -> Dict[str, Any]:
    """Detect obvious non-actionable interstitial/captcha pages to fail fast.

    Purpose:
    - Prevent expensive selector cycling on anti-bot pages that do not expose the
      expected flight search form controls.
    - Keep retries bounded and diagnostics explicit.

    Returns empty dict when no hard block is detected.

    Note: Skyscanner interstitial detection is in core/scenario_runner/skyscanner/interstitials.py
    """
    html = str(html_text or "")
    lower = html.lower()
    site = str(site_key or "").strip().lower()
    if not html or not site:
        return {}

    # Generic anti-bot/access block fallback (less specific, but general purpose).
    generic_hits: list[str] = []
    for token in (
        "captcha",
        "verify you are human",
        "access denied",
        "forbidden",
        "are you a robot",
    ):
        if token in lower:
            generic_hits.append(token)

    if len(generic_hits) >= 2:
        return {
            "reason": "blocked_interstitial_page",
            "page_kind": "interstitial",
            "block_type": "generic",
            "evidence": {
                "html.length": len(html),
                "ui.token_hits": generic_hits[:6],
                "ui.site_brand_detected": False,
            },
        }

    return {}


def _attempt_human_mimic_interstitial_grace(
    browser,
    *,
    site_key: str,
    hard_block: Dict[str, Any],
    human_mimic: bool,
    grace_ms: int,
) -> Dict[str, Any]:
    """One bounded grace window for transient captcha/interstitial pages.

    Note: Skyscanner grace handling is in core/scenario_runner/skyscanner/interstitials.py
    Returns an unsupported result for non-Google Flights sites.
    """
    site = str(site_key or "").strip().lower()
    block_reason = str((hard_block or {}).get("reason", "") or "")
    duration_ms = max(0, int(grace_ms or 0))

    if site != "google_flights":
        return {"used": False, "cleared": False, "html": "", "reason": "site_not_supported"}

    if block_reason != "blocked_interstitial_page":
        return {"used": False, "cleared": False, "html": "", "reason": "not_captcha"}
    if not bool(human_mimic):
        return {"used": False, "cleared": False, "html": "", "reason": "human_mimic_disabled"}
    if duration_ms < 500:
        return {"used": False, "cleared": False, "html": "", "reason": "grace_disabled"}

    try:
        if hasattr(browser, "human_mimic_interstitial_grace"):
            browser.human_mimic_interstitial_grace(duration_ms=duration_ms)
        else:
            page = getattr(browser, "page", None)
            if page is not None and hasattr(page, "wait_for_timeout"):
                page.wait_for_timeout(duration_ms)
            else:
                time.sleep(duration_ms / 1000.0)
    except Exception:
        pass

    try:
        html_after = str(browser.content() or "")
    except Exception:
        html_after = ""
    hard_block_after = _detect_site_interstitial_block(html_after, site)
    grace_meta = getattr(browser, "_last_interstitial_grace_meta", {})
    return {
        "used": True,
        "cleared": not bool(hard_block_after),
        "html": html_after,
        "reason": "cleared" if not hard_block_after else str(hard_block_after.get("reason", "")),
        "press_hold_probe_attempts": int(grace_meta.get("press_hold_probe_attempts", 0) or 0)
        if isinstance(grace_meta, dict)
        else 0,
        "press_hold_executed": bool(grace_meta.get("press_hold_executed", False))
        if isinstance(grace_meta, dict)
        else False,
        "press_hold_probes": list(grace_meta.get("press_hold_probes", []) or [])[:6]
        if isinstance(grace_meta, dict)
        else [],
        "px_shell_nudged": bool(grace_meta.get("px_shell_nudged", False))
        if isinstance(grace_meta, dict)
        else False,
    }
