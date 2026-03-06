"""Google Flights helper utilities extracted from core.extractor.

These helpers handle Google-specific visible text parsing, deeplink context
matching, and scope detection while keeping extraction logic decoupled from
core.extractor.
"""

from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse
import re

from bs4 import BeautifulSoup

from core.extr_helpers_google_shared import (
    HEURISTIC_MIN_PRICE,
    HEURISTIC_MAX_PRICE,
    _contains_route_token,
    _extract_price_candidates,
    _hint_token_any,
    _load_token_group,
    _price_grounding_tolerance,
    _RESULT_HINT_MATCHERS,
    _ROUTE_HINT_MATCHERS,
)
from storage.shared_knowledge_store import get_airport_aliases_for_provider


_GOOGLE_EMBEDDED_ROUTE_RE = re.compile(
    r'\["(?P<depart>\d{4}-\d{2}-\d{2})","(?P<return>\d{4}-\d{2}-\d{2})".{0,4000}?\[\[null,(?P<price>\d+(?:\.\d+)?)\].{0,4000}?"(?P<a1>[A-Z]{3})".{0,4000}?"(?P<a2>[A-Z]{3})"',
    re.DOTALL,
)

_GOOGLE_NON_FLIGHT_MAP_TOKENS = tuple(
    token.lower()
    for token in _load_token_group(
        group="google",
        key="non_flight_map",
        fallback=[
            "地図を表示",
            "リストを表示",
            "地図データ",
            "gmp-internal-camera-control",
            "map data",
        ],
    )
)
_GOOGLE_NON_FLIGHT_HOTEL_TOKENS = tuple(
    token.lower()
    for token in _load_token_group(
        group="google",
        key="non_flight_hotel",
        fallback=[
            "hotel",
            "hotels",
            "ホテル",
            "宿泊",
            "check-in",
            "check out",
            "チェックイン",
        ],
    )
)


def _is_google_flights_site(site: Any) -> bool:
    """Return True when site key resolves to Google Flights."""
    return str(site or "").strip().lower() == "google_flights"


def _google_visible_text(html: str) -> str:
    """Return compact visible text for conservative route/context checks."""
    if not isinstance(html, str) or not html:
        return ""
    try:
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        return " ".join(soup.stripped_strings)
    except Exception:
        return html


def _google_visible_price_values(
    html: str,
    *,
    currency: Optional[str] = None,
    max_values: int = 1200,
) -> List[float]:
    """Extract numeric price candidates from visible text for grounding checks."""
    text = _google_visible_text(html)
    if not text:
        return []
    wanted = str(currency or "").strip().upper()
    out: List[float] = []
    for value, parsed_currency in _extract_price_candidates(text):
        if wanted and parsed_currency and parsed_currency.upper() != wanted:
            continue
        out.append(float(value))
        if len(out) >= max_values:
            break
    return out


def _google_price_is_grounded_in_html(
    html: str,
    *,
    price: Any,
    currency: Optional[str] = None,
) -> bool:
    """Return True when one candidate price is numerically grounded in visible HTML text."""
    try:
        target = float(price)
    except Exception:
        return False
    tolerance = _price_grounding_tolerance(target)
    candidates = _google_visible_price_values(html, currency=currency)
    if not candidates:
        return False
    for candidate in candidates:
        if abs(candidate - target) <= tolerance:
            return True
    return False


def _google_route_aliases(code: Optional[str]):
    """Return normalized airport and metro aliases for one IATA code."""
    return get_airport_aliases_for_provider(code or "", "google_flights")


def _parse_google_deeplink_context(page_url: Optional[str]) -> Optional[Dict[str, str]]:
    """Extract route/date context from Google Flights deep-link URL."""
    if not isinstance(page_url, str) or "flt=" not in page_url:
        return None
    try:
        parsed = urlparse(page_url)
    except Exception:
        return None
    flt = None
    fragment = parsed.fragment or ""
    if fragment:
        for segment in fragment.split(";"):
            seg = segment.strip()
            if seg.startswith("flt="):
                flt = seg.split("=", 1)[1]
                break
    if not flt:
        flt = (parse_qs(parsed.query).get("flt") or [None])[0]
    if not isinstance(flt, str) or not flt.strip():
        return None
    legs = [leg.strip() for leg in flt.split("*") if leg.strip()]
    first = legs[0].split(".") if legs else []
    if len(first) < 3:
        return None
    second = legs[1].split(".") if len(legs) > 1 else []
    return {
        "origin": (first[0] or "").strip().upper(),
        "dest": (first[1] or "").strip().upper(),
        "depart": (first[2] or "").strip(),
        "return_date": (second[2] or "").strip() if len(second) >= 3 else "",
    }


def _google_deeplink_context_matches(
    page_url: Optional[str],
    *,
    origin: Optional[str],
    dest: Optional[str],
    depart: Optional[str],
    return_date: Optional[str],
) -> bool:
    """Return True when deeplink URL encodes the requested route/date context."""
    if not all([origin, dest, depart]):
        return False
    context = _parse_google_deeplink_context(page_url)
    if not isinstance(context, dict):
        return False
    origin_aliases = _google_route_aliases(origin)
    dest_aliases = _google_route_aliases(dest)
    if not origin_aliases or not dest_aliases:
        return False
    ctx_origin = (context.get("origin") or "").strip().upper()
    ctx_dest = (context.get("dest") or "").strip().upper()
    route_matches = (
        (ctx_origin in origin_aliases and ctx_dest in dest_aliases)
        or (ctx_origin in dest_aliases and ctx_dest in origin_aliases)
    )
    if not route_matches:
        return False
    if str(context.get("depart") or "").strip() != str(depart or "").strip():
        return False
    requested_return = str(return_date or "").strip()
    if requested_return:
        return str(context.get("return_date") or "").strip() == requested_return
    return True


def _extract_google_embedded_price(
    html: str,
    *,
    origin: Optional[str],
    dest: Optional[str],
    depart: Optional[str],
    return_date: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Parse route+date keyed price tuples from Google embedded JSON blobs."""
    if not all([origin, dest, depart]) or not isinstance(html, str):
        return None
    origin_aliases = _google_route_aliases(origin)
    dest_aliases = _google_route_aliases(dest)
    if not origin_aliases or not dest_aliases:
        return None

    prices = []
    for match in _GOOGLE_EMBEDDED_ROUTE_RE.finditer(html):
        leg_depart = match.group("depart")
        leg_return = match.group("return")
        if leg_depart != depart:
            continue
        if return_date and leg_return != return_date:
            continue
        a1 = match.group("a1")
        a2 = match.group("a2")
        if not (
            (a1 in origin_aliases and a2 in dest_aliases)
            or (a1 in dest_aliases and a2 in origin_aliases)
        ):
            continue
        try:
            prices.append(float(match.group("price")))
        except Exception:
            continue

    if not prices:
        return None
    value = min(prices)
    if not (HEURISTIC_MIN_PRICE <= value <= HEURISTIC_MAX_PRICE):
        return None
    return {
        "price": value,
        "currency": "JPY",
        "confidence": "low",
        "selector_hint": None,
        "source": "heuristic_embedded",
        "reason": "google_embedded_route_match",
    }


def _google_page_context_matches(
    html: str,
    *,
    origin: Optional[str],
    dest: Optional[str],
    depart: Optional[str],
    return_date: Optional[str],
    page_url: Optional[str] = None,
) -> bool:
    """Check if page-level context contains the requested route/date pair."""
    if not isinstance(html, str) or not html:
        return False
    if not origin or not dest or not depart:
        return False
    if depart not in html:
        return False
    if return_date and return_date not in html:
        return False
    raw_blob = html
    upper_blob = raw_blob.upper()

    def _contains_any(tokens):
        for token in tokens:
            if _contains_route_token(raw_blob, upper_blob, token):
                return True
        return False

    if _contains_any(_google_route_aliases(origin)) and _contains_any(
        _google_route_aliases(dest)
    ):
        return True

    # Deeplink context is a useful hint, but not sufficient by itself:
    # Google can keep flt=... in URL while UI drifts to hotel/explore scope.
    if not _google_deeplink_context_matches(
        page_url,
        origin=origin,
        dest=dest,
        depart=depart,
        return_date=return_date,
    ):
        return False

    visible = _google_visible_text(html)
    if not visible:
        return False
    if depart not in visible:
        return False
    if return_date and return_date not in visible:
        return False

    visible_upper = visible.upper()
    has_origin = any(
        _contains_route_token(visible, visible_upper, token)
        for token in _google_route_aliases(origin)
    )
    has_dest = any(
        _contains_route_token(visible, visible_upper, token)
        for token in _google_route_aliases(dest)
    )
    has_route_hint = _hint_token_any(visible, _ROUTE_HINT_MATCHERS) or _hint_token_any(
        visible,
        _RESULT_HINT_MATCHERS,
    )
    return has_origin and has_dest and has_route_hint


def _google_non_flight_scope_detected(
    html: str,
    *,
    origin: Optional[str],
    dest: Optional[str],
    depart: Optional[str],
    return_date: Optional[str],
    page_url: Optional[str] = None,
) -> bool:
    """Detect Google Flights pages that are likely hotel/map scope, not fare results."""
    if not isinstance(html, str) or not html:
        return False
    blob = html
    lowered = blob.lower()
    map_hits = sum(1 for token in _GOOGLE_NON_FLIGHT_MAP_TOKENS if token.lower() in lowered)
    hotel_hits = sum(1 for token in _GOOGLE_NON_FLIGHT_HOTEL_TOKENS if token.lower() in lowered)
    route_bound = _google_page_context_matches(
        blob,
        origin=origin,
        dest=dest,
        depart=depart,
        return_date=return_date,
        page_url=page_url,
    )
    # Strong mismatch pattern seen in mis-bound runs:
    # map/list UI + hotel cues while requested route/date context is not present.
    return (map_hits >= 2 and hotel_hits >= 1 and not route_bound)