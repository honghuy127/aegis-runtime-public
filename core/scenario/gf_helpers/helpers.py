"""Basic text and IATA helpers for Google Flights scenario.

Move-only extraction from core/scenario/google_flights.py.
Zero behavior change.
"""

from __future__ import annotations

import re
from typing import Any, Optional


_IATA_RE = re.compile(r"^[A-Z]{3}$")


def _prefer_locale_token_order(*, ja_tokens: list[str], en_tokens: list[str], locale_hint: Optional[str]) -> list[str]:
    """Return tokens ordered by locale preference (JA uses ja+en; EN uses en only)."""
    prefer_ja = str(locale_hint or "").strip().lower().startswith("ja")
    ordered = (ja_tokens + en_tokens) if prefer_ja else list(en_tokens)
    out: list[str] = []
    seen: set[str] = set()
    for token in ordered:
        t = str(token or "").strip()
        if not t:
            continue
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out


def _iata_token_in_text(text: str, token: str) -> bool:
    """Return True when text includes a standalone IATA token."""
    if not text or not token:
        return False
    upper_text = str(text).upper()
    upper_token = str(token).upper()
    if f"({upper_token})" in upper_text:
        return True
    return bool(
        re.search(rf"(?<![A-Z0-9]){re.escape(upper_token)}(?![A-Z0-9])", upper_text)
    )


def _is_iata_value(value: str) -> bool:
    """Return True when value is a 3-letter IATA code."""
    token = str(value or "").strip().upper()
    return bool(_IATA_RE.match(token))


def _normalize_commit_text(value: Any) -> Optional[str]:
    """Return compact text for commit evidence payloads."""
    text = str(value or "").strip()
    if not text:
        return None
    compact = " ".join(text.split())
    return compact[:240] if compact else None


def _dedupe_compact_selectors(values, *, max_items: int = 16) -> list[str]:
    """Return compact deduped selector list preserving original order."""
    out: list[str] = []
    seen = set()
    for raw in values or []:
        selector = str(raw or "").strip()
        if not selector or selector in seen:
            continue
        seen.add(selector)
        out.append(selector)
        if len(out) >= max(1, int(max_items)):
            break
    return out
