"""Canonical extraction payload schema constants for plugin routing."""

from __future__ import annotations

from typing import Final, Set


CONFIDENCE_VALUES: Final[Set[str]] = {"low", "medium", "high"}
PAGE_CLASS_VALUES: Final[Set[str]] = {
    "flight_only",
    "flight_hotel_package",
    "garbage_page",
    "irrelevant_page",
    "unknown",
}
TRIP_PRODUCT_VALUES: Final[Set[str]] = {
    "flight_only",
    "flight_hotel_package",
    "unknown",
}
CANONICAL_PRICE_KEYS: Final[Set[str]] = {
    "price",
    "currency",
    "confidence",
    "selector_hint",
    "source",
    "reason",
    "page_class",
    "trip_product",
    "route_bound",
    "visible_price_text",
    "strategy_key",
}

NON_FLIGHT_PAGE_CLASSES: Final[Set[str]] = {
    "flight_hotel_package",
    "garbage_page",
    "irrelevant_page",
}
