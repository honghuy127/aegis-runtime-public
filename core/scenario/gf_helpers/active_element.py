"""Active element helpers for Google Flights scenario.

Move-only extraction from core/scenario/google_flights.py.
Zero behavior change.
"""

from __future__ import annotations

from typing import Optional


def _infer_locale_hint_from_keywords(keywords) -> str:
    """Infer locale hint from activation keyword list."""
    for token in keywords or []:
        if not isinstance(token, str):
            continue
        if any(ord(ch) > 127 for ch in token):
            return "ja"
    return ""


def _active_element_aria_label(browser) -> Optional[str]:
    """Return aria-label for the currently focused element, if available."""
    page = getattr(browser, "page", None)
    if page is None or not hasattr(page, "evaluate"):
        return None
    try:
        label = page.evaluate(
            """
            () => {
              const el = document.activeElement;
              if (!el) return '';
              return String(el.getAttribute('aria-label') || '');
            }
            """
        )
    except Exception:
        return None
    text = str(label or "").strip()
    return text or None


def _active_element_matches_expected(label: Optional[str], tokens: list[str]) -> bool:
    """Return True when the active element label matches expected tokens."""
    if not label:
        return False
    for token in tokens or []:
        if not token:
            continue
        if token.isascii():
            if token.lower() in label.lower():
                return True
        elif token in label:
            return True
    return False
