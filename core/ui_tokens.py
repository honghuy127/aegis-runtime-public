"""Shared token normalization and selector helpers for UI text matching."""

import re
from typing import List, Optional


def normalize_visible_text(text: str) -> str:
    """Normalize visible text for conservative token matching."""
    value = str(text or "").replace("\u3000", " ").strip().lower()
    return re.sub(r"\s+", " ", value)


def _looks_cjk_token(text: str) -> bool:
    """Return True when token contains CJK/Hiragana/Katakana characters."""
    return bool(re.search(r"[\u3040-\u30ff\u3400-\u9fff]", str(text or "")))


def prioritize_tokens(tokens: List[str], locale_hint: Optional[str] = None) -> List[str]:
    """Prioritize token ordering by locale without narrowing match set."""
    ordered = []
    seen = set()
    for token in tokens or []:
        value = str(token or "").strip()
        if not value:
            continue
        marker = normalize_visible_text(value)
        if marker in seen:
            continue
        seen.add(marker)
        ordered.append(value)
    if not ordered:
        return []

    lang = normalize_visible_text(str(locale_hint or "")).split("-", 1)[0]
    if lang == "ja":
        ja = [token for token in ordered if _looks_cjk_token(token)]
        other = [token for token in ordered if token not in ja]
        return ja + other
    if lang == "en":
        en = [token for token in ordered if token.isascii()]
        other = [token for token in ordered if token not in en]
        return en + other
    return ordered


def build_button_text_selectors(tokens: List[str]) -> List[str]:
    """Build conservative button selectors from label tokens (no bare text=)."""
    selectors = []
    seen = set()
    for token in tokens or []:
        label = str(token or "").strip()
        if not label:
            continue
        candidates = [
            f"button[aria-label*='{label}']",
            f"[role='button'][aria-label*='{label}']",
            f"input[type='submit'][value*='{label}']",
            f"button:has-text('{label}')",
            f"[role='button']:has-text('{label}')",
        ]
        for selector in candidates:
            if selector in seen:
                continue
            seen.add(selector)
            selectors.append(selector)
    return selectors


def is_placeholder(text: str, tokens: List[str]) -> bool:
    """Return True when text exactly matches one placeholder token after normalization."""
    normalized = normalize_visible_text(text)
    if not normalized:
        return True
    for token in tokens or []:
        if normalized == normalize_visible_text(token):
            return True
    return False
