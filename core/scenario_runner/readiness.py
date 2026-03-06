"""Results-readiness detection helpers extracted from core.scenario_runner."""

from __future__ import annotations

import re

from core.scenario_runner.page_scope import is_non_flight_page_class
from core.scenario_runner.google_flights.service_runner_bridge import (
    _google_quick_page_class,
    _google_has_contextual_price_card,
    _google_has_results_shell_for_context,
    _strip_nonvisible_html,
)

_PRICE_TOKEN_RE = re.compile(r"[\$€£¥₩₹]\s?\d|\d[\d,\.]{2,}\s?(usd|jpy|eur|gbp)", re.IGNORECASE)
_RESULT_HINT_RE = re.compile(
    r"(Best|Cheapest|Duration|stops?|layover|itinerary|flight|depart|arrival|price|運賃|最安|直行)",
    re.IGNORECASE,
)
_SKYSCANNER_RESULTS_HINT_RE = re.compile(
    r"(itinerar|sort by|filters?|stops?|layover|duration|outbound|inbound|"
    r"best|cheapest|fastest|運賃|最安|直行|経由|乗継|所要時間|フィルタ|並べ替え|航空券)",
    re.IGNORECASE,
)
_SKYSCANNER_HOME_HINT_RE = re.compile(
    r"(出発地|目的地|country,\s*city|airport|国、都市または空港|origininput|destinationinput|flights-home)",
    re.IGNORECASE,
)
_SKYSCANNER_PRICE_SIGNAL_RE = re.compile(
    r"([¥$€£]\s?[0-9][0-9,\.]{2,}|[0-9][0-9,\.]{2,}\s?(jpy|usd|eur|gbp))",
    re.IGNORECASE,
)
_SKYSCANNER_PRICE_LABEL_WITH_VALUE_RE = re.compile(
    r"(最安|最も安い|cheapest|from)\s*[:：]?\s*([¥$€£]|\d)",
    re.IGNORECASE,
)
_SKYSCANNER_RAW_PRICE_STRUCTURAL_RE = re.compile(
    r"(itinerary-price|price-per-adult|data-testid=\"[^\"]*price[^\"]*\"|\"itineraries\"\s*:\s*\[[^\]]+\])",
    re.IGNORECASE,
)
_SKYSCANNER_PRICE_PAYLOAD_HINT_RE = re.compile(
    r"(itineraryprice|priceperadult|minprice|cheapestprice|\"itineraries\"\s*:\s*\[|\"price\"\s*:\s*\{)",
    re.IGNORECASE,
)
_SKYSCANNER_TAB_SELECTED_RE_TEMPLATE = r"id=[\"']{tab_id}[\"'][^>]*aria-selected=[\"']true[\"']|aria-selected=[\"']true[\"'][^>]*id=[\"']{tab_id}[\"']"


def _is_skyscanner_hotels_context(html: str, *, page_url: str = "") -> bool:
    """Detect obvious Hotels surface so flight-results readiness cannot false-positive."""
    page_url_lower = str(page_url or "").strip().lower()
    if "/hotels" in page_url_lower:
        return True
    html_lower = str(html or "").lower()
    if not html_lower:
        return False
    flights_tab_selected = bool(
        re.search(_SKYSCANNER_TAB_SELECTED_RE_TEMPLATE.format(tab_id="airli"), html_lower, re.IGNORECASE)
    )
    if flights_tab_selected:
        return False
    hotels_tab_selected = bool(
        re.search(_SKYSCANNER_TAB_SELECTED_RE_TEMPLATE.format(tab_id="skhot"), html_lower, re.IGNORECASE)
    )
    return hotels_tab_selected


def _visible_text_probe(html: str) -> str:
    cleaned = _strip_nonvisible_html(html)
    if not cleaned:
        return ""
    no_tags = re.sub(r"(?is)<[^>]+>", " ", cleaned)
    return re.sub(r"\s+", " ", no_tags).strip()


def has_skyscanner_price_signal(html: str) -> bool:
    """Best-effort signal that fare content has hydrated on Skyscanner results/day-view."""
    if not isinstance(html, str) or not html:
        return False
    visible_text = _visible_text_probe(html)
    if not visible_text:
        return False
    if _SKYSCANNER_PRICE_SIGNAL_RE.search(visible_text):
        return True
    if _SKYSCANNER_PRICE_LABEL_WITH_VALUE_RE.search(visible_text):
        return True
    if _SKYSCANNER_RAW_PRICE_STRUCTURAL_RE.search(html) and _SKYSCANNER_RESULTS_HINT_RE.search(visible_text):
        return True
    return False


def is_skyscanner_results_shell_incomplete(html: str, *, page_url: str = "") -> bool:
    """Detect `/transport/flights/...` script shell where visible UI failed to hydrate."""
    if not isinstance(html, str) or not html:
        return False
    page_url_lower = str(page_url or "").strip().lower()
    if "/transport/flights/" not in page_url_lower:
        return False
    html_lower = html.lower()
    if "/sttc/px/captcha-v2/" in page_url_lower or "captcha-v2/index.html" in page_url_lower:
        return False
    visible_text = _visible_text_probe(html)
    if has_skyscanner_price_signal(html):
        return False
    if bool(_SKYSCANNER_HOME_HINT_RE.search(visible_text)):
        return False
    has_dayview_marker = any(
        token in html_lower
        for token in (
            "\"pagename\":\"day-view\"",
            "\"pagename\":\"flights.dayview\"",
            "\"pagetype\":\"flights-day-view\"",
            "\"pagetype\":\"flights:dayview\"",
            "day-view",
            "dayview",
        )
    )
    has_route_state = (
        ("\"flightsearch\"" in html_lower or "\"searchparams\"" in html_lower)
        and ("\"originid\"" in html_lower or "\"origin\"" in html_lower)
        and ("\"destinationid\"" in html_lower or "\"destination\"" in html_lower)
    )
    if not has_route_state:
        return False
    visible_len = len(visible_text.strip())
    if has_dayview_marker and visible_len < 220:
        return True
    # Fallback for cases where day-view markers vary by locale/build and the page
    # is effectively blank despite route-bound URL + state payload.
    return visible_len < 80


def is_results_ready(
    html: str,
    *,
    site_key: str = "",
    origin: str = "",
    dest: str = "",
    depart: str = "",
    return_date: str = "",
    page_url: str = "",
) -> bool:
    """Best-effort detector for a likely search-results state."""
    if not isinstance(html, str) or not html:
        return False
    site_norm = (site_key or "").strip().lower()
    visible_text = _visible_text_probe(html)
    if site_norm == "google_flights":
        quick_class = _google_quick_page_class(
            html,
            origin=origin,
            dest=dest,
            depart=depart,
            return_date=return_date,
        )
        if is_non_flight_page_class(quick_class):
            return False
        if origin and dest and depart:
            visible_html = _strip_nonvisible_html(html)
            context = {
                "origin": origin,
                "dest": dest,
                "depart": depart,
                "return_date": return_date or "",
            }
            if _google_has_contextual_price_card(visible_html, context):
                return True
            if _google_has_results_shell_for_context(visible_html, context):
                return True
            return False
    if site_norm == "skyscanner":
        html_lower = html.lower()
        page_url_lower = str(page_url or "").strip().lower()
        if _is_skyscanner_hotels_context(html, page_url=page_url):
            return False
        if page_url_lower.rstrip("/").endswith("/flights"):
            # Skyscanner home/search shell can include teaser "cheapest/from" text and
            # must not be treated as hydrated results readiness.
            return False
        if "/sttc/px/captcha-v2/" in page_url_lower or "captcha-v2/index.html" in page_url_lower:
            return False
        if "/transport/flights/" in page_url_lower:
            if has_skyscanner_price_signal(html):
                return True
            has_day_view_route_state = (
                ("\"pagename\":\"day-view\"" in html_lower or "\"pagetype\":\"flights-day-view" in html_lower)
                and "\"flightsearch\"" in html_lower
                and "\"originid\"" in html_lower
                and "\"destinationid\"" in html_lower
            )
            if has_day_view_route_state and bool(_SKYSCANNER_PRICE_PAYLOAD_HINT_RE.search(html_lower)):
                return True
            return False
        if not _PRICE_TOKEN_RE.search(visible_text):
            return False
        has_results_shell = bool(_SKYSCANNER_RESULTS_HINT_RE.search(visible_text))
        if bool(_SKYSCANNER_HOME_HINT_RE.search(visible_text)) and not has_results_shell:
            return False
        return has_results_shell
    if not _PRICE_TOKEN_RE.search(visible_text):
        return False
    if not _RESULT_HINT_RE.search(visible_text):
        return False
    return True
