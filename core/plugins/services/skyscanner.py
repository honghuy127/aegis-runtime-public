"""Skyscanner service plugin (delegates to legacy routing helpers)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from core import services as services_mod
from core.service_ui_profiles import get_service_ui_profile


_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_CANONICAL_RE = re.compile(
    r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_META_SITE_NAME_RE = re.compile(
    r'<meta[^>]+property=["\']og:site_name["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_PRICE_CONTEXT_RE = re.compile(
    r"(?is)(?:price|fare|itinerary-price|data-testid[^>]{0,40}price)[^<]{0,40}"
    r"(?:<[^>]+>){0,3}\s*([$\u00a3\u20ac\u00a5])\s*([0-9][0-9,]*)"
)
_GENERIC_PRICE_RE = re.compile(r"([$\u00a3\u20ac\u00a5])\s*([0-9][0-9,]*)")
_CURRENCY_BY_SYMBOL = {
    "$": "USD",
    "£": "GBP",
    "€": "EUR",
    "¥": "JPY",
}


def _is_transport_results_url(value: str) -> bool:
    url = str(value or "").strip().lower()
    return "/transport/flights/" in url and "captcha-v2/index.html" not in url


def _extract_tag_text(pattern: re.Pattern[str], html: str) -> str:
    match = pattern.search(html or "")
    if not match:
        return ""
    return str(match.group(1) or "").strip()


def classify_skyscanner_page(
    html: str,
    *,
    page_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Return a conservative page-kind classification for Skyscanner HTML."""
    raw_html = html or ""
    html_lower = raw_html.lower()
    title_text = _extract_tag_text(_TITLE_RE, raw_html).lower()
    canonical_url = _extract_tag_text(_CANONICAL_RE, raw_html).lower()
    og_site_name = _extract_tag_text(_META_SITE_NAME_RE, raw_html).lower()
    url_text = str(page_url or "").strip().lower()
    results_url_detected = _is_transport_results_url(url_text) or _is_transport_results_url(canonical_url)

    brand_detected = any(
        "skyscanner" in value
        for value in (html_lower, title_text, canonical_url, og_site_name, url_text)
        if value
    )
    flights_detected = any(
        token in html_lower
        for token in (
            "flight",
            "flights",
            "itinerary",
            "search-results",
            "data-testid=\"itinerary",
            "data-testid='itinerary",
        )
    )
    results_detected = results_url_detected or any(
        token in html_lower
        for token in (
            "search-results",
            "data-testid=\"search-results",
            "data-testid='search-results",
            "data-testid=\"itinerary",
            "data-testid='itinerary",
            "result-card",
            "data-testid=\"itinerary-price",
            "data-testid='itinerary-price",
            "data-testid=\"day-view",
            "data-testid='day-view",
            "flightsresults",
        )
    )
    search_form_detected = any(
        token in html_lower
        for token in (
            "origininput-input",
            "destinationinput-input",
            "depart-btn",
            "return-btn",
            "flights-search-controls",
        )
    )

    if brand_detected and flights_detected and results_detected:
        page_kind = "flights_results"
        reason = "brand_and_results_markers_detected"
    elif brand_detected and search_form_detected:
        page_kind = "search_form"
        reason = "brand_and_search_form_markers_detected"
    elif brand_detected and not flights_detected:
        page_kind = "irrelevant_page"
        reason = "brand_without_flight_markers"
    elif brand_detected:
        page_kind = "flight_page_loading"
        reason = "brand_detected_results_markers_missing"
    else:
        page_kind = "unknown"
        reason = "brand_markers_missing"

    return {
        "page_kind": page_kind,
        "reason": reason,
        "brand_detected": brand_detected,
        "flights_detected": flights_detected,
        "results_detected": results_detected,
        "results_url_detected": results_url_detected,
        "search_form_detected": search_form_detected,
        "title": title_text[:120],
        "canonical_url": canonical_url[:240],
    }


def extract_price_from_html(
    html: str,
    *,
    page_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Conservative Skyscanner HTML price extraction for deterministic fixtures."""
    raw_html = html or ""
    classification = classify_skyscanner_page(raw_html, page_url=page_url)
    html_len = len(raw_html)
    strategy = "skyscanner_semantic_price_regex_v1"
    on_results_page = str(classification.get("page_kind", "")).strip().lower() == "flights_results"

    matches = []
    if on_results_page:
        matches = list(_PRICE_CONTEXT_RE.finditer(raw_html))
        if not matches:
            matches = list(_GENERIC_PRICE_RE.finditer(raw_html))

    candidates: List[Dict[str, Any]] = []
    for match in matches[:8]:
        symbol = str(match.group(1) or "").strip()
        amount_text = str(match.group(2) or "").replace(",", "").strip()
        if not amount_text.isdigit():
            continue
        amount_value = int(amount_text)
        if amount_value <= 0:
            continue
        candidates.append(
            {
                "symbol": symbol,
                "currency": _CURRENCY_BY_SYMBOL.get(symbol, ""),
                "price": amount_value,
            }
        )

    evidence = {
        "url": page_url or "",
        "html_len": html_len,
        "page_kind": classification.get("page_kind", "unknown"),
        "extraction_strategy_attempted": strategy,
        "gating_decisions": {
            "brand_detected": bool(classification.get("brand_detected")),
            "flights_detected": bool(classification.get("flights_detected")),
            "results_detected": bool(classification.get("results_detected")),
            "results_url_detected": bool(classification.get("results_url_detected")),
            "search_form_detected": bool(classification.get("search_form_detected")),
            "on_results_page": bool(on_results_page),
        },
        "candidate_count": len(candidates),
    }

    if candidates:
        winner = min(candidates, key=lambda item: int(item["price"]))
        return {
            "ok": True,
            "price": winner["price"],
            "currency": winner["currency"] or "USD",
            "reason_code": None,
            "page_kind": classification.get("page_kind", "unknown"),
            "extraction_strategy": strategy,
            "evidence": evidence,
        }

    return {
        "ok": False,
        "price": None,
        "currency": None,
        "reason_code": "missing_price",
        "page_kind": classification.get("page_kind", "unknown"),
        "extraction_strategy": strategy,
        "evidence": evidence,
    }


@dataclass(frozen=True)
class SkyscannerServicePlugin:
    """Service plugin wrapper for skyscanner."""

    service_key: str = "skyscanner"
    ui_profile_key: str = "skyscanner"

    @property
    def display_name(self) -> str:
        return services_mod.service_name(self.service_key)

    @property
    def default_url(self) -> str:
        return services_mod.default_service_url(self.service_key)

    @property
    def base_domains(self) -> List[str]:
        return list(services_mod._service_base_domains(self.service_key))

    # Backward-compatible aliases used by early plugin stages.
    @property
    def key(self) -> str:
        return self.service_key

    @property
    def name(self) -> str:
        return self.display_name

    @property
    def domains(self) -> List[str]:
        return self.base_domains

    def url_candidates(
        self,
        preferred_url: Optional[str] = None,
        is_domestic: Optional[bool] = None,
        *,
        knowledge: Optional[Dict[str, Any]] = None,
        seed_hints: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        return services_mod.service_url_candidates(
            self.service_key,
            preferred_url=preferred_url,
            is_domestic=is_domestic,
            knowledge=knowledge,
            seed_hints=seed_hints,
        )

    def ui_profile(self) -> Optional[Dict[str, Any]]:
        return self.scenario_profile()

    def scenario_profile(self) -> Dict[str, Any]:
        return get_service_ui_profile(self.ui_profile_key)

    def readiness_probe(
        self,
        html: str,
        screenshot_path: Optional[str] = None,
        *,
        inputs: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        _ = (html, screenshot_path, inputs)
        return {
            "ready": False,
            "page_class": "unknown",
            "trip_product": "unknown",
            "route_bound": None,
            "reason": "plugin_not_configured",
        }

    def extraction_hints(
        self,
        html: str,
        screenshot_path: Optional[str] = None,
        *,
        inputs: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        _ = (html, screenshot_path, inputs)
        return {}

    def readiness_hints(self, *, inputs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        _ = inputs
        profile = self.scenario_profile()
        wait_selectors = profile.get("wait_selectors", []) if isinstance(profile, dict) else []
        if not isinstance(wait_selectors, list):
            wait_selectors = []
        return {"wait_selectors": [v for v in wait_selectors if isinstance(v, str) and v.strip()]}

    def scope_hints(self, *, inputs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        _ = inputs
        profile = self.scenario_profile()
        if not isinstance(profile, dict):
            return {}
        return {
            "product_toggle_labels": profile.get("product_toggle_labels", {}),
            "mode_toggle_labels": profile.get("mode_toggle_labels", {}),
        }
