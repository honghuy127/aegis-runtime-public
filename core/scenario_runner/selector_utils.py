from __future__ import annotations

import re
from typing import List, Optional

from utils.knowledge_rules import get_knowledge_rule_tokens
from core.scenario_runner.google_flights.service_runner_bridge import (
    _selector_candidates,
)


def check_selector_visibility(browser, selectors: List[str], *, timeout_ms: int = 500) -> bool:
    if not selectors or not browser:
        return True
    try:
        page = getattr(browser, "page", None) or browser
        for selector in selectors:
            try:
                if page.is_visible(selector, timeout=100):
                    return True
            except Exception:
                continue
        return False
    except Exception:
        return True


def visible_selector_subset(
    browser,
    selectors: List[str],
    *,
    per_selector_timeout_ms: int = 100,
    max_candidates: int = 4,
) -> List[str]:
    if not selectors or not browser or max_candidates <= 0:
        return []
    page = getattr(browser, "page", None) or browser
    if page is None or not hasattr(page, "is_visible"):
        return []
    visible: List[str] = []
    timeout_ms = max(0, int(per_selector_timeout_ms))
    try:
        for selector in selectors:
            try:
                if page.is_visible(selector, timeout=timeout_ms):
                    visible.append(selector)
                    if len(visible) >= max_candidates:
                        break
            except Exception:
                continue
    except Exception:
        return []
    return visible


def selector_blob(selector) -> str:
    return " ".join(_selector_candidates(selector)).strip().lower()


def contains_selector_word(selector_blob: str, token: str) -> bool:
    if not selector_blob or not token:
        return False
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", selector_blob))


def selectors_look_search_submit(selectors) -> bool:
    vals = [str(s or "").strip().lower() for s in list(selectors or []) if str(s or "").strip()]
    if not vals:
        return False
    blob = " ".join(vals)
    tokens = get_knowledge_rule_tokens("search_submit_tokens")
    if not tokens:
        tokens = ["search", "submit"]
    has_submit_token = any(token in blob for token in tokens)
    if not has_submit_token:
        return False
    # Require actionable selector intent to avoid matching
    # result-shell wait probes like [data-testid*='search-results'].
    has_clickable_selector = any(is_clickable_selector_candidate(sel) for sel in vals)
    return has_clickable_selector


def selectors_look_post_search_wait(selector_candidates) -> bool:
    vals = [str(s or "").strip().lower() for s in list(selector_candidates or []) if str(s or "").strip()]
    if not vals:
        return False
    anchors = {"[role='main']", "main", "[aria-live]"}
    return any(v in anchors for v in vals[:5])


def is_clickable_selector_candidate(selector: str) -> bool:
    if not isinstance(selector, str):
        return False
    text = selector.strip().lower()
    if not text:
        return False
    if text.startswith("text="):
        return False
    if "input[type='submit'" in text or 'input[type="submit"' in text:
        return True
    if "[type='submit'" in text or '[type="submit"' in text:
        return True
    if "button" in text:
        return True
    if "[role='button'" in text or '[role="button"' in text or "role=button" in text:
        return True
    if text.startswith("a") or "a:has-text(" in text:
        return True
    if text.startswith("xpath="):
        return any(token in text for token in ("button", "@role='button'", '@role="button"', "submit"))
    return False


def selectors_look_domain_toggle(selectors) -> bool:
    blob = " ".join(selectors).lower()
    tokens = ("domestic", "international", "国内", "海外", "国際")
    return any(token in blob for token in tokens)
