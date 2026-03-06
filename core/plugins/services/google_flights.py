"""Google Flights service plugin (delegates to legacy routing helpers)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from core import services as services_mod
from core.service_ui_profiles import get_service_ui_profile
from storage.shared_knowledge_store import map_airport_code_for_provider

_GENERIC_PRICE_RE = re.compile(r"([$\u00a3\u20ac\u00a5])\s*([0-9][0-9,]*)")
_CURRENCY_BY_SYMBOL = {
    "$": "USD",
    "£": "GBP",
    "€": "EUR",
    "¥": "JPY",
}
_SCRIPT_STYLE_RE = re.compile(
    r"<(?:script|style|noscript)\b[^>]*>.*?</(?:script|style|noscript)>",
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")


def _html_contains_any(html: str, tokens: List[str]) -> bool:
    """Check if any token appears in normalized HTML (case-insensitive).

    Args:
        html: The HTML text to search.
        tokens: List of tokens to search for.

    Returns:
        True if any token is found in html, False otherwise.
    """
    if not html or not tokens:
        return False
    html_lower = html.lower()
    for token in tokens:
        if not token:
            continue
        if str(token).lower() in html_lower:
            return True
    return False


def _visibleish_text(html: str) -> str:
    """Best-effort visible text extraction for deterministic heuristics.

    Removes script/style blobs and strips tags to avoid matching hidden embedded JSON
    payloads (which are common on Google Flights pages and can contain noise like
    tiny prices or consent-related words).
    """
    raw = str(html or "")
    if not raw:
        return ""
    without_blobs = _SCRIPT_STYLE_RE.sub(" ", raw)
    text = _TAG_RE.sub(" ", without_blobs)
    return re.sub(r"\s+", " ", text).strip()


def _is_consent_page(html: str) -> bool:
    """Detect consent/privacy/cookie-related pages by language-specific keywords.

    Looks for common consent page markers in English and Japanese.
    """
    text = _visibleish_text(html).lower()
    if not text:
        return False
    # Require stronger combinations than generic "privacy"/"cookie" footer text.
    if "before you continue" in text:
        return True
    if "accept all" in text and "cookie" in text:
        return True
    if "consent" in text and "cookie" in text:
        return True
    if "同意" in text and "クッキー" in text:
        return True
    return False


def _is_package_page(html: str) -> bool:
    """Detect flight+hotel package pages by product tokens.

    Looks for explicit package/bundled product indicators across languages.
    """
    package_tokens = [
        "Flight + Hotel",
        "flight + hotel",
        "Flights + Hotels",
        "flights + hotels",
        "packages",
        "Packages",
        # Japanese package indicators
        "フライト + ホテル",
        "フライト＋ホテル",
        "パッケージ",
        "package",
    ]
    return _html_contains_any(html, package_tokens)


def _is_flights_results_page(html: str) -> bool:
    """Detect Google Flights results page by content markers.

    Looks for typical flight results page indicators across languages.
    Includes both English Google Flights markers and Japanese equivalents.
    """
    flights_tokens = [
        # English flight result page markers
        "Best flights",
        "best flights",
        "Flights",
        "Stops",
        "stops",
        "Departure",
        "Arrival",
        "Airline",
        "Duration",
        "price",
        "Price",
        # Japanese flight result page markers
        "最安値",
        "価格",
        "所要時間",
        "航空会社",
        "フライト",
        "便",
        "出発",
        "到着",
        "乗継",
        "経由",
        "ストップ",
        # Generic result indicators
        "google.com/travel/flights",
        "travel/flights",
    ]
    return _html_contains_any(html, flights_tokens)


def extract_price_from_html(
    html: str,
    *,
    page_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Conservative Google Flights HTML price extraction for deterministic fixtures."""
    raw_html = html or ""
    visible_text = _visibleish_text(raw_html)
    html_len = len(raw_html)

    if _is_consent_page(raw_html):
        page_kind = "consent"
    elif _is_package_page(raw_html):
        page_kind = "flight_hotel_package"
    elif _is_flights_results_page(raw_html):
        page_kind = "flights_results"
    else:
        page_kind = "irrelevant_page"

    search_text = visible_text or raw_html
    matches = list(_GENERIC_PRICE_RE.finditer(search_text))
    candidates: List[Dict[str, Any]] = []
    for match in matches[:8]:
        symbol = str(match.group(1) or "").strip()
        amount_text = str(match.group(2) or "").replace(",", "").strip()
        if not amount_text.isdigit():
            continue
        amount_value = int(amount_text)
        if amount_value <= 0:
            continue
        # Reject tiny noise prices commonly found in embedded metadata / UI counters.
        if symbol == "$" and amount_value < 20:
            continue
        if symbol in {"£", "€"} and amount_value < 20:
            continue
        if symbol == "¥" and amount_value < 100:
            continue
        candidates.append(
            {
                "price": amount_value,
                "currency": _CURRENCY_BY_SYMBOL.get(symbol, ""),
                "symbol": symbol,
            }
        )

    evidence = {
        "url": page_url or "",
        "html_len": html_len,
        "page_kind": page_kind,
        "extraction_strategy_attempted": "google_flights_semantic_price_regex_v1",
        "gating_decisions": {
            "consent_detected": _is_consent_page(raw_html),
            "package_detected": _is_package_page(raw_html),
            "results_detected": _is_flights_results_page(raw_html),
        },
        "visible_text_len": len(visible_text),
        "candidate_count": len(candidates),
    }

    if candidates:
        winner = min(candidates, key=lambda item: int(item["price"]))
        return {
            "ok": True,
            "price": winner["price"],
            "currency": winner["currency"] or "USD",
            "reason_code": None,
            "page_kind": page_kind,
            "extraction_strategy": "google_flights_semantic_price_regex_v1",
            "evidence": evidence,
        }

    return {
        "ok": False,
        "price": None,
        "currency": None,
        "reason_code": "missing_price",
        "page_kind": page_kind,
        "extraction_strategy": "google_flights_semantic_price_regex_v1",
        "evidence": evidence,
    }


def _extract_airport_code_from_input(airport_input: str) -> Optional[str]:
    """Extract airport code from user input (handles 3-letter codes or names).

    For now, returns the input as-is if it looks like a 3-letter code,
    otherwise returns None (actual resolution would require external mapping).
    """
    code = str(airport_input or "").strip().upper()
    if len(code) == 3 and code.isalpha():
        return code
    return None


def _route_bound_heuristic(
    html: str,
    origin: str,
    dest: str,
    depart: str,
    return_date: Optional[str] = None,
) -> Optional[bool]:
    """Heuristic to detect if route is bound in the page state.

    Checks URL fragment patterns (flt=) and form field mentions within HTML.
    Returns True if route appears to be bound, None if cannot determine.
    """
    # Extract airport codes
    origin_code = _extract_airport_code_from_input(origin)
    dest_code = _extract_airport_code_from_input(dest)
    depart_str = str(depart or "").strip()

    if not origin_code or not dest_code or not depart_str:
        return None

    # Build expected fragment patterns
    expected_patterns = [
        f"flt={origin_code}.{dest_code}.{depart_str}",
        f"{origin_code}.{dest_code}",
        f'href="[^"]*flt={origin_code}',
        f'href="[^"]*{origin_code}\\.{dest_code}',
    ]

    # Check if any pattern appears in HTML
    for pattern in expected_patterns:
        try:
            if re.search(pattern, html, re.IGNORECASE):
                return True
        except re.error:
            continue

    # Check for direct text mentions of airport codes in form-like contexts
    html_lower = html.lower()
    origin_lower = origin_code.lower()
    dest_lower = dest_code.lower()

    # Simple heuristic: if both codes appear and origin before dest, likely bound
    origin_pos = html_lower.find(origin_lower)
    dest_pos = html_lower.find(dest_lower)

    if origin_pos >= 0 and dest_pos > origin_pos:
        return True

    return None


def _field(obj: Any, key: str, default: Any = "") -> Any:
    """Read key from dict-like or attribute object."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _locale_lang(locale: str) -> str:
    """Normalize locale to language code used by `hl`."""
    text = str(locale or "").strip()
    if not text:
        return "en"
    lang = text.split("-", 1)[0].strip().lower()
    return lang if lang else "en"


def _region_code(region: str) -> str:
    """Normalize region to two-letter uppercase `gl` code."""
    text = str(region or "").strip().upper()
    if len(text) == 2 and text.isalpha():
        return text
    return "US"


def _preferred_google_flights_judge_hl() -> str:
    """Return preferred Google Flights UI language for LLM/VLM judgeability."""
    # Keep this centralized so runtime policy is explicit and easy to audit.
    return "en"


def _normalize_google_flights_ui_lang(url: str, *, hl: Optional[str] = None) -> str:
    """Prefer a stable Google Flights UI language while preserving region/currency."""
    raw = str(url or "").strip()
    if not raw:
        return raw
    try:
        parsed = urlparse(raw)
    except Exception:
        return raw
    host = str(parsed.netloc or "").lower()
    path = str(parsed.path or "")
    if "google." not in host or "/travel/flights" not in path:
        return raw
    query_pairs = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k != "hl"]
    query_pairs.append(("hl", str(hl or _preferred_google_flights_judge_hl()).strip() or "en"))
    return urlunparse(
        (
            parsed.scheme or "https",
            parsed.netloc or "www.google.com",
            parsed.path or "/travel/flights",
            parsed.params,
            urlencode(query_pairs),
            parsed.fragment,
        )
    )


def _build_route_fragment(
    *,
    origin: str,
    dest: str,
    depart: str,
    return_date: Optional[str] = None,
    trip_type: str = "one_way",
    currency: str = "",
    base_fragment: str = "",
) -> str:
    """Build Google Flights fragment preserving non-route options."""
    origin_code = str(origin or "").strip().upper()
    dest_code = str(dest or "").strip().upper()
    if len(origin_code) != 3:
        origin_code = map_airport_code_for_provider(origin, "google_flights")
    if len(dest_code) != 3:
        dest_code = map_airport_code_for_provider(dest, "google_flights")
    leg1 = f"{origin_code}.{dest_code}.{depart}"
    route = leg1
    if str(trip_type or "").strip().lower() == "round_trip" and return_date:
        route = f"{leg1}*{dest_code}.{origin_code}.{return_date}"

    extras: List[str] = []
    for raw in str(base_fragment or "").split(";"):
        token = raw.strip()
        if not token:
            continue
        if token.startswith("flt="):
            continue
        if token.startswith("c:"):
            continue
        extras.append(token)
    out = [f"flt={route}"]
    normalized_currency = str(currency or "").strip().upper()
    if normalized_currency:
        out.append(f"c:{normalized_currency}")
    out.extend(extras)
    return ";".join(out)


def build_google_flights_deeplink(
    plan: Any,
    run_input: Any,
    *,
    base_url: str = "https://www.google.com/travel/flights",
) -> str:
    """Build deeplink URL using route plan + run-input mimic params."""
    preferred = str(base_url or "").strip() or "https://www.google.com/travel/flights"
    parsed = urlparse(preferred)
    origin = str(_field(plan, "origin", "") or "").strip()
    dest = str(_field(plan, "dest", "") or "").strip()
    depart = str(_field(plan, "depart", "") or "").strip()
    return_date = str(_field(plan, "return_date", "") or "").strip()
    trip_type = str(_field(plan, "trip_type", "one_way") or "one_way").strip()
    region = str(_field(run_input, "mimic_region", "") or "").strip()
    currency = str(_field(run_input, "mimic_currency", "") or "").strip()

    query_pairs = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k not in {"hl", "gl"}]
    # Prefer English UI text for LLM/VLM judging while preserving region/currency routing.
    query_pairs.append(("hl", _preferred_google_flights_judge_hl()))
    query_pairs.append(("gl", _region_code(region)))
    query = urlencode(query_pairs)
    fragment = _build_route_fragment(
        origin=origin,
        dest=dest,
        depart=depart,
        return_date=return_date or None,
        trip_type=trip_type,
        currency=currency,
        base_fragment=parsed.fragment or "",
    )
    return urlunparse(
        (
            parsed.scheme or "https",
            parsed.netloc or "www.google.com",
            parsed.path or "/travel/flights",
            parsed.params,
            query,
            fragment,
        )
    )


@dataclass(frozen=True)
class GoogleFlightsServicePlugin:
    """Service plugin wrapper for google_flights."""

    service_key: str = "google_flights"
    ui_profile_key: str = "google_flights"

    @property
    def display_name(self) -> str:
        return services_mod.service_name(self.service_key)

    @property
    def default_url(self) -> str:
        return _normalize_google_flights_ui_lang(services_mod.default_service_url(self.service_key))

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
        urls = services_mod.service_url_candidates(
            self.service_key,
            preferred_url=preferred_url,
            is_domestic=is_domestic,
            knowledge=knowledge,
            seed_hints=seed_hints,
        )
        out: List[str] = []
        seen: set[str] = set()
        for url in urls or []:
            normalized = _normalize_google_flights_ui_lang(url)
            key = str(normalized or "").strip()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(key)
        return out

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
        """Probe HTML to determine page readiness and classification.

        Uses deterministic heuristics to detect:
        - Page class (consent, package, flights_results, irrelevant_page, unknown)
        - Product type (flight, package, unknown)
        - Whether route is bound to page state
        - Overall readiness (True if results page with plausible state)

        Args:
            html: HTML content of the page.
            screenshot_path: Path to screenshot (currently unused).
            inputs: Dict with keys like origin, dest, depart, return_date, trip_type.

        Returns:
            Dict with keys:
            - ready: bool (True if likely a ready results page)
            - page_class: str (consent|flight_hotel_package|flights_results|
                             irrelevant_page|unknown)
            - trip_product: str (flight|package|unknown)
            - route_bound: bool|None (True if route appears bound, None if unknown)
            - reason: str (explanation of readiness decision)
        """
        _ = screenshot_path  # Not used in this deterministic version

        if not html or not isinstance(html, str):
            return {
                "ready": False,
                "page_class": "unknown",
                "trip_product": "unknown",
                "route_bound": None,
                "reason": "empty_or_invalid_html",
            }

        # Extract inputs for route binding check
        inputs_dict = inputs or {}
        origin = str(inputs_dict.get("origin", "")).strip()
        dest = str(inputs_dict.get("dest", "")).strip()
        depart = str(inputs_dict.get("depart", "")).strip()
        return_date = str(inputs_dict.get("return_date", "")).strip()

        # Stage 1: Detect consent/privacy pages
        if _is_consent_page(html):
            return {
                "ready": False,
                "page_class": "consent",
                "trip_product": "unknown",
                "route_bound": None,
                "reason": "consent_or_privacy_page_detected",
            }

        # Stage 2: Detect package pages (Flight + Hotel)
        is_package = _is_package_page(html)
        if is_package:
            route_bound = None
            if origin and dest and depart:
                route_bound = _route_bound_heuristic(html, origin, dest, depart, return_date)
            return {
                "ready": False,
                "page_class": "flight_hotel_package",
                "trip_product": "package",
                "route_bound": route_bound,
                "reason": "flight_hotel_package_detected",
            }

        # Stage 3: Detect flights results pages
        is_flights = _is_flights_results_page(html)
        if is_flights:
            route_bound = None
            if origin and dest and depart:
                route_bound = _route_bound_heuristic(html, origin, dest, depart, return_date)

            # A flights results page is ready if route is bound or cannot be determined
            # (we assume it's ready unless we have evidence otherwise)
            ready = route_bound is not False
            reason = "flights_results_ready" if ready else "flights_results_unbound"

            return {
                "ready": ready,
                "page_class": "flights_results",
                "trip_product": "flight",
                "route_bound": route_bound,
                "reason": reason,
            }

        # Stage 4: Could not classify page
        return {
            "ready": False,
            "page_class": "irrelevant_page",
            "trip_product": "unknown",
            "route_bound": None,
            "reason": "page_not_recognized",
        }

    def extraction_hints(
        self,
        html: str,
        screenshot_path: Optional[str] = None,
        *,
        inputs: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Return extraction hints for result parsing.

        Provides wait selectors and metadata useful for extracting flight results.

        Args:
            html: HTML content (currently unused).
            screenshot_path: Path to screenshot (currently unused).
            inputs: Dict with input parameters (currently unused).

        Returns:
            Dict with keys:
            - wait_selectors: list of CSS selectors to wait for
            - service: service key (google_flights)
        """
        _ = (html, screenshot_path, inputs)  # Currently unused

        profile = self.scenario_profile()
        wait_selectors = profile.get("wait_selectors", []) if isinstance(profile, dict) else []
        if not isinstance(wait_selectors, list):
            wait_selectors = []

        return {
            "wait_selectors": [v for v in wait_selectors if isinstance(v, str) and v.strip()],
            "service": self.service_key,
        }

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
