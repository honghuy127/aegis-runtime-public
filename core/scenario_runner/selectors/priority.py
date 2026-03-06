"""Selector prioritization and ranking helpers."""

import re

from core.scenario_runner.selectors.probes import _looks_non_fillable_selector_blob
from core.scenario_runner.google_flights.service_runner_bridge import _dedupe_selectors
from core.ui_tokens import normalize_visible_text


def _fill_selector_priority(selector: str) -> int:
    """Score fill selectors: lower score means more likely fillable/stable."""
    text = selector.strip().lower()
    score = 0

    # Strongly prefer explicit input selectors first.
    if text.startswith("input"):
        score -= 8
    elif text.startswith("textarea"):
        score -= 6
    elif text.startswith("["):
        score += 3

    if "[name" in text:
        score -= 3
    if "data-testid" in text:
        score -= 2
    if "placeholder" in text:
        score -= 1
    if "aria-label" in text:
        score -= 1

    # De-prioritize controls that are often indirect/non-fillable.
    if "[role='combobox'" in text or '[role="combobox"' in text:
        score += 3
    if "button" in text:
        score += 6
    if "text=" in text or ":has-text(" in text:
        score += 5

    # Hidden/code-like fields are almost always bad fill targets.
    if _looks_non_fillable_selector_blob(text):
        score += 10
    return score


def _prioritize_fill_selectors(selectors):
    """Reorder fill selector candidates by likely fillability."""
    unique = _dedupe_selectors(selectors)
    ranked = sorted(
        enumerate(unique),
        key=lambda pair: (_fill_selector_priority(pair[1]), pair[0]),
    )
    return [selector for _, selector in ranked]


def _filter_blocked_selectors(selectors, blocked_selectors):
    """De-prioritize known failing selectors while preserving fallback breadth."""
    unique = _dedupe_selectors(selectors)
    blocked = set()
    for value in blocked_selectors or []:
        if isinstance(value, str) and value.strip():
            blocked.add(value.strip())
    if not blocked:
        return unique
    preferred = [s for s in unique if s not in blocked]
    deferred = [s for s in unique if s in blocked]
    # If everything is blocked, keep all candidates (do not collapse to one).
    if not preferred:
        return unique
    return preferred + deferred


def _prepend_ranked_selectors(current, prioritized, *, limit: int = 12):
    """Merge selector arrays with priority-first dedupe semantics."""
    merged = []
    for value in prioritized + current:
        if not isinstance(value, str) or not value.strip():
            continue
        if value not in merged:
            merged.append(value)
        if len(merged) >= max(1, int(limit)):
            break
    return merged


def _reorder_search_selectors_for_locale(selectors: list[str], *, locale_hint: str = "") -> list[str]:
    """Reorder multilingual search selectors while preserving bounded fallback coverage.

    This is a stable partition, not a broad sort: relative order within each bucket is
    preserved. It is used to prefer EN-vs-JA search labels based on the display-locale
    hint (for example Google Flights `hl=en`) without removing the fallback language.
    """
    items = [s for s in (selectors or []) if isinstance(s, str) and s.strip()]
    if not items:
        return []
    lang = normalize_visible_text(str(locale_hint or "")).split("-", 1)[0]
    if lang not in {"ja", "en"}:
        return list(items)

    preferred: list[str] = []
    neutral: list[str] = []
    fallback: list[str] = []
    for selector in items:
        lowered = str(selector or "").lower()
        has_ja = bool(re.search(r"[\u3040-\u30ff\u3400-\u9fff]", selector))
        has_en_search = "search" in lowered
        if lang == "en":
            if has_en_search:
                preferred.append(selector)
            elif has_ja:
                fallback.append(selector)
            else:
                neutral.append(selector)
        else:  # lang == "ja"
            if has_ja:
                preferred.append(selector)
            elif has_en_search:
                fallback.append(selector)
            else:
                neutral.append(selector)
    return preferred + neutral + fallback
