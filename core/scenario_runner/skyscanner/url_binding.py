"""Skyscanner URL route/date binding helpers for scenario orchestration."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from storage.shared_knowledge_store import get_airport_aliases_for_provider


def _is_skyscanner_route_value_already_bound_from_url(
    browser: Any,
    *,
    role: str,
    value: str,
) -> bool:
    """Best-effort Skyscanner route binding check from `/transport/flights/...` URL."""
    if role not in {"origin", "dest"}:
        return False
    expected = str(value or "").strip().lower()
    if not expected:
        return False
    page = getattr(browser, "page", None)
    if page is None:
        return False
    try:
        current_url = str(getattr(page, "url", "") or "").strip().lower()
    except Exception:
        return False
    if "/transport/flights/" not in current_url:
        return False
    try:
        parsed = urlparse(current_url)
    except Exception:
        return False
    path = str(parsed.path or "")
    if "/transport/flights/" not in path:
        return False
    tail = path.split("/transport/flights/", 1)[-1].strip("/")
    parts = [p for p in tail.split("/") if p]
    if len(parts) < 2:
        return False
    observed = parts[0] if role == "origin" else parts[1]
    observed = str(observed or "").strip().lower()
    if not observed:
        return False
    aliases = get_airport_aliases_for_provider(value.strip(), "google_flights")
    aliases_l = {str(a or "").strip().lower() for a in aliases if str(a or "").strip()}
    if not aliases_l:
        aliases_l = {expected}
    return observed in aliases_l


def _is_skyscanner_date_value_already_bound_from_url(
    browser: Any,
    *,
    role: str,
    value: str,
) -> bool:
    """Best-effort Skyscanner date binding check from `/transport/flights/...` URL."""
    if role not in {"depart", "return"}:
        return False
    raw_value = str(value or "").strip()
    if not raw_value:
        return False
    normalized = ""
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            dt = datetime.strptime(raw_value, fmt)
            normalized = dt.strftime("%y%m%d")
            break
        except Exception:
            continue
    if not normalized:
        return False
    page = getattr(browser, "page", None)
    if page is None:
        return False
    try:
        current_url = str(getattr(page, "url", "") or "").strip().lower()
    except Exception:
        return False
    if "/transport/flights/" not in current_url:
        return False
    try:
        parsed = urlparse(current_url)
    except Exception:
        return False
    path = str(parsed.path or "")
    if "/transport/flights/" not in path:
        return False
    tail = path.split("/transport/flights/", 1)[-1].strip("/")
    parts = [p for p in tail.split("/") if p]
    if len(parts) < 4:
        return False
    observed = parts[2] if role == "depart" else parts[3]
    return str(observed or "").strip().lower() == normalized.lower()
