"""Plugin extraction candidate normalization helpers."""

from __future__ import annotations

from typing import Any, Dict, Optional

from core.plugins.extraction.schemas import (
    CANONICAL_PRICE_KEYS,
    CONFIDENCE_VALUES,
    PAGE_CLASS_VALUES,
    TRIP_PRODUCT_VALUES,
)


def _to_float(value: Any) -> Optional[float]:
    """Best-effort numeric coercion for price fields."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if not text:
            return None
        try:
            return float(text)
        except Exception:
            return None
    return None


def _normalize_currency(value: Any) -> Optional[str]:
    """Normalize currency code to uppercase token."""
    if not isinstance(value, str):
        return None
    text = value.strip().upper()
    return text or None


def _normalize_confidence(value: Any) -> str:
    """Normalize confidence enum with low fallback."""
    text = str(value or "").strip().lower()
    return text if text in CONFIDENCE_VALUES else "low"


def _normalize_page_class(value: Any) -> Optional[str]:
    text = str(value or "").strip().lower()
    if not text:
        return None
    return text if text in PAGE_CLASS_VALUES else "unknown"


def _normalize_trip_product(value: Any) -> Optional[str]:
    text = str(value or "").strip().lower()
    if not text:
        return None
    return text if text in TRIP_PRODUCT_VALUES else "unknown"


def normalize_plugin_candidate(
    raw: Any,
    *,
    strategy_key: str = "",
    source_default: str = "plugin_strategy",
    visible_price_text_max_chars: int = 220,
) -> Dict[str, Any]:
    """Normalize one plugin candidate into canonical extraction payload shape.

    Returns {} when payload is not usable.
    This helper never raises outward.
    """
    try:
        if not isinstance(raw, dict) or not raw:
            return {}

        out: Dict[str, Any] = {}
        price = _to_float(raw.get("price"))
        out["price"] = price
        out["currency"] = _normalize_currency(raw.get("currency"))
        out["confidence"] = _normalize_confidence(raw.get("confidence"))
        out["selector_hint"] = raw.get("selector_hint")
        out["reason"] = str(raw.get("reason", "") or "")
        out["source"] = str(raw.get("source", source_default) or source_default)
        if strategy_key:
            out["strategy_key"] = strategy_key

        page_class = _normalize_page_class(raw.get("page_class"))
        if page_class is not None:
            out["page_class"] = page_class

        trip_product = _normalize_trip_product(raw.get("trip_product"))
        if trip_product is not None:
            out["trip_product"] = trip_product

        route_bound = raw.get("route_bound")
        if isinstance(route_bound, bool):
            out["route_bound"] = route_bound

        visible_price_text = raw.get("visible_price_text")
        if isinstance(visible_price_text, str):
            text = visible_price_text.strip()
            if visible_price_text_max_chars > 0 and len(text) > visible_price_text_max_chars:
                text = text[:visible_price_text_max_chars]
            out["visible_price_text"] = text or None

        # Keep only canonical keys for stability.
        out = {k: v for k, v in out.items() if k in CANONICAL_PRICE_KEYS}
        return out
    except Exception:
        return {}
