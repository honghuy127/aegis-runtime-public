"""Helper functions for Google Flights date opener selection."""

from __future__ import annotations

import re
from typing import Any, Optional

from core.scenario.gf_helpers.date_tokens import _google_date_opener_tokens


def _resolve_visible_enabled_opener_candidate(page, selector: str) -> tuple[Optional[Any], Optional[int], bool, bool]:
    """Return (locator, idx, visible, enabled) preferring a visible enabled match.

    Google Flights often renders hidden clones before the live date chip/input.
    Scanning a small prefix avoids false `calendar_not_open` from `.first`.
    """
    locator_group = page.locator(selector)
    first_locator = locator_group.first
    any_visible = False
    any_enabled = False
    for idx in range(6):  # bounded duplicate scan
        candidate = locator_group.nth(idx)
        try:
            visible = bool(candidate.is_visible(timeout=80))
        except Exception:
            visible = False
        if not visible:
            continue
        any_visible = True
        try:
            enabled = bool(candidate.is_enabled(timeout=60))
        except Exception:
            enabled = False
        any_enabled = any_enabled or enabled
        if enabled:
            return candidate, idx, True, True
        if not any_enabled:
            first_locator = candidate
    # Fall back to first match precheck if no visible/enabled candidate found
    try:
        visible = bool(first_locator.is_visible(timeout=80))
    except Exception:
        visible = False
    try:
        enabled = bool(first_locator.is_enabled(timeout=60)) if visible else False
    except Exception:
        enabled = False
    any_visible = any_visible or visible
    any_enabled = any_enabled or enabled
    return first_locator, (0 if (visible or enabled) else None), any_visible, any_enabled


def _opener_kind(selector: str) -> str:
    lower = str(selector or "").lower()
    if lower.startswith("input["):
        return "input"
    if "[role='combobox']" in lower:
        return "combobox"
    if "[role='button']" in lower or lower.startswith("button["):
        return "button"
    return "other"


def _selector_has_role_token(
    selector: str,
    *,
    role_key: str,
    target_date: str,
    locale_hint: str,
) -> bool:
    role_tokens = _google_date_opener_tokens(
        role=role_key,
        target_date=target_date,
        locale_hint=locale_hint,
    ).get("role_tokens", [])
    raw = str(selector or "")
    return any(str(tok or "").strip() and str(tok) in raw for tok in (role_tokens or []))


def _selector_lang_score(selector: str, *, locale_hint: str) -> int:
    raw = str(selector or "")
    has_cjk = bool(re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", raw))
    prefer_ja = str(locale_hint or "").strip().lower().startswith("ja")
    if prefer_ja:
        return 1 if has_cjk else 0
    return 1 if not has_cjk else 0


def _promote_into_head(
    opener_attempt_selectors: list[str],
    predicate,
    *,
    role_key: str,
    target_date: str,
    locale_hint: str,
    head_cap: int = 8,
    insert_at: int = 0,
) -> None:
    if not opener_attempt_selectors:
        return
    cap = min(max(1, int(head_cap)), len(opener_attempt_selectors))
    if any(predicate(s) for s in opener_attempt_selectors[:cap]):
        return
    best_idx = None
    best_score = None
    for idx, sel in enumerate(opener_attempt_selectors[cap:], start=cap):
        if not predicate(sel):
            continue
        score = (
            int(
                _selector_has_role_token(
                    sel,
                    role_key=role_key,
                    target_date=target_date,
                    locale_hint=locale_hint,
                )
            ),
            int(_selector_lang_score(sel, locale_hint=locale_hint)),
            -idx,
        )
        if best_score is None or score > best_score:
            best_score = score
            best_idx = idx
    if best_idx is None:
        return
    candidate = opener_attempt_selectors.pop(best_idx)
    opener_attempt_selectors.insert(min(max(0, int(insert_at)), cap - 1), candidate)
