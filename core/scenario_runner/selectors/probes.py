"""Selector visibility probing and DOM inspection helpers."""

import re
from typing import Any, Dict, List

from core.scenario_runner.google_flights.service_runner_bridge import (
    _selector_candidates,
    _dedupe_selectors,
)
from utils.logging import get_logger


log = get_logger(__name__)


def _check_selector_visibility(browser, selectors: list[str], timeout_ms: int = 500) -> bool:
    """Quick pre-check: return True if at least one selector is visible.

    Used for optional click steps to avoid wasting time trying non-visible selectors.
    Returns False if NO selector is visible (allowing soft_fail early exit).
    Returns True if at least one selector is visible (attempt click normally).

    This pre-check is fast and doesn't require full selector matching or clicking,
    just a basic visibility check to avoid deadline_exceeded on unavailable buttons.

    Args:
        browser: Browser or page object with is_visible method
        selectors: List of selectors to check
        timeout_ms: Max milliseconds to wait for visibility per selector (fast check)

    Returns:
        True if at least one selector is visible, False if none are visible
    """
    if not selectors or not browser:
        return True  # Assume visible if can't check (proceed with attempt)

    try:
        page = getattr(browser, "page", None) or browser
        for selector in selectors:
            try:
                # Fast visibility check without attempting click (100ms per selector)
                if page.is_visible(selector, timeout=100):
                    return True  # At least one selector is visible
            except Exception:
                # If visibility check fails, assume not visible and continue to next selector
                continue

        # If we get here, none of the selectors are visible
        return False
    except Exception:
        # If visibility check entirely fails, assume visible (proceed with attempt)
        return True


def _visible_selector_subset(
    browser,
    selectors: list[str],
    *,
    per_selector_timeout_ms: int = 100,
    max_candidates: int = 4,
) -> list[str]:
    """Return a bounded list of visible selectors for optional click attempts.

    Falls back to an empty list on any probe issue so callers can choose a
    conservative non-visibility path.
    """
    if not selectors or not browser or max_candidates <= 0:
        return []
    page = getattr(browser, "page", None) or browser
    if page is None or not hasattr(page, "is_visible"):
        return []
    visible: list[str] = []
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


def _selector_probe_css_compatible(selector: str) -> bool:
    """Return False for Playwright-only selector engines unsupported by querySelectorAll."""
    s = str(selector or "").strip()
    if not s:
        return False
    lowered = s.lower()
    if lowered.startswith(("text=", "xpath=", "id=", "css=")):
        return False
    unsupported_tokens = (
        ":has-text(",
        ":text(",
        ":text-is(",
        ":text-matches(",
        ":nth-match(",
        ">>",
    )
    return not any(tok in lowered for tok in unsupported_tokens)


def _compact_selector_dom_probe(
    page_obj,
    selectors: List[str],
    *,
    max_selectors: int = 8,
    max_matches: int = 2,
    max_html_chars: int = 360,
    max_text_chars: int = 140,
) -> Dict[str, Any]:
    """Return bounded selector/DOM probe data for debug artifacts.

    Uses ``document.querySelectorAll`` only for CSS-compatible selectors and marks
    Playwright-only selectors as skipped instead of logging syntax errors.
    """
    if page_obj is None or not hasattr(page_obj, "evaluate"):
        return {}
    selector_list: List[str] = []
    for raw in selectors or []:
        s = str(raw or "").strip()
        if not s or s in selector_list:
            continue
        selector_list.append(s)
        if len(selector_list) >= max(1, int(max_selectors)):
            break
    if not selector_list:
        return {}

    css_selectors = [s for s in selector_list if _selector_probe_css_compatible(s)]
    skipped_items = []
    for s in selector_list:
        if s in css_selectors:
            continue
        skipped_items.append(
            {
                "selector": s,
                "match_count": 0,
                "visible_count": 0,
                "snippets": [],
                "probe_skipped": "unsupported_selector_syntax",
            }
        )

    probe = {}
    if css_selectors:
        args = {
            "selectors": css_selectors[: max(1, int(max_selectors))],
            "max_matches": max(1, min(3, int(max_matches))),
            "max_html_chars": max(60, min(600, int(max_html_chars))),
            "max_text_chars": max(40, min(240, int(max_text_chars))),
        }
        js = """
            (args) => {
              const selectors = Array.isArray(args?.selectors) ? args.selectors : [];
              const maxMatches = Math.max(1, Math.min(3, Number(args?.max_matches || 2)));
              const maxHtml = Math.max(60, Math.min(600, Number(args?.max_html_chars || 320)));
              const maxText = Math.max(40, Math.min(240, Number(args?.max_text_chars || 120)));
              const out = [];
              const clip = (v, n) => {
                const s = String(v || "");
                return s.length > n ? (s.slice(0, n) + "...") : s;
              };
              const isVisible = (el) => {
                try {
                  if (!el || !el.getBoundingClientRect) return false;
                  const r = el.getBoundingClientRect();
                  return !!r && r.width > 0 && r.height > 0;
                } catch (e) { return false; }
              };
              for (const selector of selectors.slice(0, 12)) {
                let nodes = [];
                let error = "";
                try {
                  nodes = Array.from(document.querySelectorAll(String(selector || "")));
                } catch (e) {
                  error = String(e || "");
                }
                const item = {
                  selector: String(selector || ""),
                  match_count: nodes.length,
                  visible_count: nodes.filter(isVisible).length,
                  snippets: [],
                };
                if (error) item.error = clip(error, 160);
                nodes.slice(0, maxMatches).forEach((el, idx) => {
                  const attrs = {};
                  ["role", "aria-label", "placeholder", "value", "jsname", "class"].forEach((k) => {
                    try {
                      const v = el.getAttribute ? el.getAttribute(k) : null;
                      if (v) attrs[k] = clip(v, 120);
                    } catch (e) {}
                  });
                  item.snippets.push({
                    idx,
                    tag: clip(el.tagName || "", 20).toLowerCase(),
                    visible: isVisible(el),
                    text: clip((el.innerText || el.textContent || "").trim(), maxText),
                    attrs,
                    html: clip(el.outerHTML || "", maxHtml),
                  });
                });
                out.push(item);
              }
              let active = null;
              try { active = document.activeElement; } catch (e) {}
              return {
                url: String(document.location?.href || ""),
                active_element: active ? {
                  tag: String(active.tagName || "").toLowerCase(),
                  role: String(active.getAttribute?.("role") || ""),
                  aria_label: String(active.getAttribute?.("aria-label") || "").slice(0, 120),
                  placeholder: String(active.getAttribute?.("placeholder") || "").slice(0, 120),
                  value: String(active.value || "").slice(0, 120),
                } : null,
                selector_rows: out,
                selectors: out,
              };
            }
        """
        try:
            try:
                probe = page_obj.evaluate(js, args, timeout=250)
            except TypeError:
                probe = page_obj.evaluate(js, args)
        except Exception as exc:
            return {
                "url": "",
                "active_element": None,
                "selector_rows": skipped_items,
                "selectors": skipped_items,
                "probe_error": str(exc)[:240],
            }

    result = dict(probe) if isinstance(probe, dict) else {}
    if skipped_items and "selector_rows" in result:
        result["selector_rows"].extend(skipped_items)
    if skipped_items and "selectors" in result:
        result["selectors"].extend(skipped_items)
    return result


def _selector_blob(selector) -> str:
    """Flatten selector field to one compact lower-cased string."""
    return " ".join(_selector_candidates(selector)).strip().lower()


def _contains_selector_word(selector_blob: str, token: str) -> bool:
    """Return True when token appears as a word-like selector term."""
    if not selector_blob or not token:
        return False
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", selector_blob))


def _is_clickable_selector_candidate(selector: str) -> bool:
    """Return True for selector patterns that are likely to resolve to clickable controls."""
    if not isinstance(selector, str):
        return False
    text = selector.strip().lower()
    if not text:
        return False

    if text.startswith("text="):
        # Bare text selectors often match non-interactive headings/hero content.
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

    # Keep xpath support only when it clearly targets clickable nodes.
    if text.startswith("xpath="):
        return any(token in text for token in ("button", "@role='button'", '@role="button"', "submit"))
    return False


def _safe_click_first_match(
    browser,
    selectors,
    *,
    timeout_ms=None,
    require_clickable: bool = True,
    site_key: str = "",
):
    """Click first matching selector with optional clickable-only filtering."""
    # Import locally to avoid circular dependency
    from core.scenario_runner.env import _current_mimic_locale
    from core.scenario_runner.selectors.fallbacks import _service_search_click_fallbacks
    from core.scenario_runner.google_flights.service_runner_bridge import (
        _google_display_locale_hint_from_browser,
    )

    raw_candidates = _selector_candidates(selectors)
    selector_candidates = list(raw_candidates)
    service_key = str(site_key or "").strip().lower()
    locale_hint = (
        _google_display_locale_hint_from_browser(browser)
        or _current_mimic_locale()
    )
    human_mimic = bool(getattr(browser, "human_mimic", False))
    # Keep Skyscanner click actions tightly bounded to plan-provided selectors.
    # Expanding with large generic fallback banks can exceed per-step wall-clock caps.
    if service_key == "skyscanner" and selector_candidates:
        selector_candidates = _dedupe_selectors(selector_candidates)
    else:
        selector_candidates = _dedupe_selectors(
            selector_candidates
            + _service_search_click_fallbacks(
                service_key or "google_flights",
                locale_hint_override=str(locale_hint or ""),
            )
        )
    if require_clickable:
        clickable_candidates = [
            selector for selector in selector_candidates if _is_clickable_selector_candidate(selector)
        ]
        if clickable_candidates:
            selector_candidates = clickable_candidates
        else:
            return RuntimeError("no_clickable_search_selector"), None

    if service_key == "skyscanner":
        # Hard cap selector fan-out to prevent long click churn on dynamic pages.
        selector_candidates = selector_candidates[:4]
        # Prefer selectors that are currently visible so we don't burn the full
        # per-step cap on non-rendered candidates.
        visible = _visible_selector_subset(
            browser,
            selector_candidates,
            per_selector_timeout_ms=120,
            max_candidates=4,
        )
        if visible:
            if human_mimic:
                # Keep human-mimic search-click tight: use the top visible candidates first
                # to avoid spending the whole step budget on hidden/unstable fallbacks.
                selector_candidates = visible[:2]
            else:
                tail = [sel for sel in selector_candidates if sel not in visible]
                selector_candidates = (visible + tail)[:4]
        else:
            selector_candidates = selector_candidates[:3]

    last_error = None
    per_selector_timeout_ms = timeout_ms
    if service_key == "skyscanner":
        try:
            total_timeout = int(timeout_ms or 0)
        except Exception:
            total_timeout = 0
        if human_mimic:
            # Human-mimic click path includes cursor choreography and randomized delay.
            # Keep enough budget for one realistic click attempt.
            min_delay_ms = int(getattr(browser, "min_action_delay_ms", 0) or 0)
            max_delay_ms = int(getattr(browser, "max_action_delay_ms", 0) or 0)
            mimic_floor_ms = max(6500, max_delay_ms + min_delay_ms + 1200)
            if total_timeout > 0:
                budgeted = max(
                    mimic_floor_ms,
                    total_timeout // max(1, len(selector_candidates)),
                )
                per_selector_timeout_ms = min(12000, budgeted)
            else:
                per_selector_timeout_ms = min(12000, mimic_floor_ms)
        else:
            # Keep each Skyscanner click probe short in non-mimic mode; long fan-out
            # causes step-cap failures.
            if total_timeout > 0:
                budgeted = max(700, min(1600, total_timeout // max(1, len(selector_candidates))))
                per_selector_timeout_ms = budgeted
            else:
                per_selector_timeout_ms = 1200
    for selector in selector_candidates:
        try:
            browser.click(selector, timeout_ms=per_selector_timeout_ms)
            return None, selector
        except Exception as exc:
            if isinstance(exc, (TimeoutError, KeyboardInterrupt)):
                raise
            last_error = exc
    if service_key == "skyscanner":
        page = getattr(browser, "page", None)
        page_url = ""
        try:
            page_url = str(getattr(page, "url", "") or "")
        except Exception:
            page_url = ""
        last_error_type = str(type(last_error).__name__) if last_error is not None else ""
        log.warning(
            "scenario.skyscanner.search_click_fanout_exhausted candidates=%s per_selector_timeout_ms=%s url=%s last_error=%s",
            len(list(selector_candidates or [])),
            int(per_selector_timeout_ms or 0) if per_selector_timeout_ms is not None else 0,
            page_url[:220],
            last_error_type,
        )
    return last_error, None


def _looks_non_fillable_selector_blob(selector_blob: str) -> bool:
    """Return True for selector patterns that usually resolve to hidden/code fields."""
    if not isinstance(selector_blob, str):
        return False
    tokens = (
        "airportcode",
        "iata",
        "[type='hidden']",
        '[type="hidden"]',
        "type='hidden'",
        'type="hidden"',
        "c-input-field__value",
    )
    lowered = selector_blob.lower()
    return any(token in lowered for token in tokens)
