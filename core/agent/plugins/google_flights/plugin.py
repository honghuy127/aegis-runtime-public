from __future__ import annotations

from typing import Dict

from core.agent.plugins.base import RunContext, ServicePlugin
from core.agent.types import Confidence, Observation
from core.agent.plugins.google_flights.actions import base_actions
from core.agent.plugins.google_flights.objects import objects_for_locale
from storage.shared_knowledge_store import get_airport_aliases_for_provider


_RESULTS_MARKERS = [
    "Flights results",
    "フライト結果",
    "search results",
    "results",
]

def _normalized_text(value: str) -> str:
    return str(value or "").strip().lower()


def _city_match(field_value: str, expected_code: str) -> tuple:
    """Check if field value matches IATA code and return match strength.

    Returns tuple: (matched: bool, is_strong_match: bool)
    - Strong match: exact IATA match (HND == HND)
    - Weak match: city synonym (Tokyo/東京 for HND)

    Args:
        field_value: Detected value from HTML (city name or IATA).
        expected_code: Expected IATA code.

    Returns:
        (matched, is_strong) or (not_matched, False)
    """
    field_upper = field_value.upper().strip()
    expected_upper = expected_code.upper().strip()

    # Exact IATA match = STRONG
    if field_upper == expected_upper:
        return (True, True)

    # Check if shared knowledge aliases match = WEAK.
    aliases = get_airport_aliases_for_provider(expected_upper, "google_flights")
    normalized_aliases = {
        _normalized_text(token)
        for token in aliases
        if isinstance(token, str) and token.strip()
    }
    normalized_field = _normalized_text(field_value)
    if normalized_field and normalized_field in normalized_aliases:
        return (True, False)  # Matched but weak

    return (False, False)



class GoogleFlightsPlugin(ServicePlugin):
    """Google Flights service plugin with route signal detection and readiness.

    Key responsibilities:
    - dom_probe(): Extract minimal route signals and page state from HTML
    - route_bind_confidence(): Assess how well the route is bound (origin/dest/dates)
    - readiness(): Determine if page is ready (flights results visible)
    """

    service_key = "google_flights"

    def objects(self, ctx: RunContext):
        """Return object catalog (locale-aware, profile-driven)."""
        return objects_for_locale(getattr(ctx, "locale", "") or "")

    def action_catalog(self, ctx: RunContext):
        """Return base action templates from input parameters."""
        inputs = (ctx.inputs or {})
        return base_actions(inputs, locale=getattr(ctx, "locale", "") or "")

    def dom_probe(self, html: str, ctx: RunContext) -> Observation:
        """Fast observation from HTML only (no VLM, no screenshots).

        Extracts:
        - Origin/dest/dates from visible text patterns
        - Page class (flights_results vs. other)
        - Route binding status

        Args:
            html: Raw HTML as string.
            ctx: RunContext with URL, locale, inputs.

        Returns:
            Observation with detected signals.
        """
        obs = Observation()

        # Heuristic 1: page_class detection
        if self._has_flights_results_marker(html):
            obs.page_class = "flights_results"
        elif "travel/flights" in (ctx.url or ""):
            obs.page_class = "flights_form"
        else:
            obs.page_class = "unknown"

        # Heuristic 2: trip product
        if "Flights" in html or "フライト" in html or "travel/flights" in (ctx.url or ""):
            obs.trip_product = "flights"

        # Heuristic 3: Parse route signals from HTML
        fields = self._parse_route_fields(html, ctx)
        obs.fields = fields

        # Heuristic 4: Rough route_bound assessment
        if fields.get("origin") and fields.get("dest"):
            obs.route_bound = True
            obs.confidence = Confidence.medium
            obs.reason = "origin_and_dest_detected"
        elif fields.get("origin"):
            obs.route_bound = False
            obs.confidence = Confidence.low
            obs.reason = "origin_only_detected"
        else:
            obs.route_bound = False
            obs.confidence = Confidence.low
            obs.reason = "no_route_signals_detected"

        return obs

    def route_bind_confidence(self, obs: Observation, ctx: RunContext) -> Confidence:
        """Assess confidence that route is correctly bound (origin + dest + dates).

        HARDENING AUDIT FIX: Enforce strict strong/weak match distinction.
        - Strong match: exact IATA match (e.g., HND == HND)
        - Weak match: city synonym (e.g., "Tokyo" or "東京" for HND)

        Confidence mapping:
        - HIGH: All 3 fields with strong matches (IATA exact) AND depart matches
        - MEDIUM: 2+ fields match (allows weak matches), OR 3+ including weak with strong priority
        - LOW: 1 field matches or no matches

        Important: City synonyms alone (e.g., "Tokyo" for HND) = MEDIUM at best, never HIGH.

        Args:
            obs: Observation from dom_probe.
            ctx: RunContext with expected origin/dest.

        Returns:
            Confidence level (high, medium, low).
        """
        # No fields parsed => low confidence
        if not obs.fields:
            return Confidence.low

        origin = obs.fields.get("origin", "").upper()
        dest = obs.fields.get("dest", "").upper()
        depart = obs.fields.get("depart", "").upper()

        expected_origin = (ctx.inputs or {}).get("origin", "").upper()
        expected_dest = (ctx.inputs or {}).get("dest", "").upper()
        expected_depart = (ctx.inputs or {}).get("depart", "").upper()

        # Check matches and track strength
        origin_matched, origin_strong = _city_match(origin, expected_origin) if origin and expected_origin else (False, False)
        dest_matched, dest_strong = _city_match(dest, expected_dest) if dest and expected_dest else (False, False)
        depart_match = (depart == expected_depart) if depart and expected_depart else False

        # Count matches and strong matches
        total_matches = sum([origin_matched, dest_matched, depart_match])
        strong_matches = sum([origin_strong if origin_matched else False,
                               dest_strong if dest_matched else False,
                               depart_match])

        # HIGH: All three match AND all are strong (for origin/dest) or exact for depart
        if total_matches == 3 and origin_strong and dest_strong and depart_match:
            return Confidence.high

        # MEDIUM: 2+ matches (allows weak matches), but not all strong
        if total_matches >= 2:
            return Confidence.medium

        # LOW: 1 match or no matches
        return Confidence.low

    def readiness(self, obs: Observation, ctx: RunContext) -> bool:
        """Determine if page is ready for results extraction.

        Criteria (v0):
        - page_class must be "flights_results"
        - route_bind_confidence must be at least "medium"

        Args:
            obs: Observation from dom_probe.
            ctx: RunContext.

        Returns:
            True if page is ready, False otherwise.
        """
        if obs.page_class != "flights_results":
            return False

        # Check route binding confidence
        confidence = self.route_bind_confidence(obs, ctx)
        if confidence not in {Confidence.medium, Confidence.high}:
            return False

        return True

    # --- Private helpers ---

    def _has_flights_results_marker(self, html: str) -> bool:
        """Quick check for flight search results in HTML."""
        for marker in _RESULTS_MARKERS:
            if marker in html:
                return True
        return False

    def _parse_route_fields(self, html: str, ctx: RunContext) -> Dict[str, str]:
        """Extract route signals (origin, dest, dates) from HTML text.

        This is a simple heuristic using regex; a future version might use
        actual DOM parsing for improved accuracy.

        Args:
            html: Raw HTML string.
            ctx: RunContext.

        Returns:
            Dict with keys "origin", "dest", "depart", "return".
        """
        fields = {}

        # Check if input provides hints
        inputs = ctx.inputs or {}
        expected_origin = (inputs.get("origin") or "").upper()
        expected_dest = (inputs.get("dest") or "").upper()

        html_upper = html.upper()
        html_norm = _normalized_text(html)

        def _match_expected_airport(code: str) -> str:
            if not code:
                return ""
            if code in html_upper:
                return code
            aliases = get_airport_aliases_for_provider(code, "google_flights")
            for alias in aliases:
                alias_text = str(alias or "").strip()
                if not alias_text:
                    continue
                if alias_text.isascii():
                    if _normalized_text(alias_text) and _normalized_text(alias_text) in html_norm:
                        return alias_text
                elif alias_text in html:
                    return alias_text
            return ""

        matched_origin = _match_expected_airport(expected_origin)
        if matched_origin:
            fields["origin"] = matched_origin

        matched_dest = _match_expected_airport(expected_dest)
        if matched_dest:
            fields["dest"] = matched_dest

        # Date signals (simple: if the formatted date appears in HTML)
        expected_depart = (inputs.get("depart") or "")
        if expected_depart and expected_depart in html:
            fields["depart"] = expected_depart

        expected_return = (inputs.get("return_date") or "")
        if expected_return and expected_return in html:
            fields["return"] = expected_return

        return fields
