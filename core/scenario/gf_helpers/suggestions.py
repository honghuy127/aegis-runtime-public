"""Suggestion and alias matching helpers for Google Flights scenario.

Move-only extraction from core/scenario/google_flights.py.
Zero behavior change.
"""

from __future__ import annotations

import re
from typing import Optional

from core.scenario.gf_helpers.helpers import _iata_token_in_text
from storage.shared_knowledge_store import get_airport_aliases_for_provider


def _airport_name_from_suggestion(text: str) -> str:
    """Extract the airport name portion from one suggestion label."""
    raw = str(text or "").strip()
    if not raw:
        return ""
    if "(" in raw:
        return raw.split("(", 1)[0].strip()
    return raw


def _suggestion_commit_matches(
    final_text: str,
    target_value: str,
    suggestion_text: Optional[str],
) -> bool:
    """Verify commit after suggestion click using IATA or airport name tokens."""
    observed = str(final_text or "").strip()
    if not observed:
        return True
    target = str(target_value or "").strip().upper()
    if target and _iata_token_in_text(observed, target):
        return True
    airport_name = _airport_name_from_suggestion(suggestion_text or "")
    if airport_name and airport_name in observed:
        return True
    return False


def _provider_alias_tokens(value: str) -> set[str]:
    """Return provider-aware alias token set for one airport-like value."""
    token = str(value or "").strip().upper()
    if not token:
        return set()
    aliases = set(get_airport_aliases_for_provider(token, "google_flights"))
    aliases.add(token)
    return aliases


def _contains_alias_token(text: str, token: str) -> bool:
    """Case-aware containment match used for commit verification."""
    if not token:
        return False
    if token.isascii():
        upper_text = str(text or "").upper()
        upper_token = token.upper()
        if re.search(rf"(?<![A-Z0-9]){re.escape(upper_token)}(?![A-Z0-9])", upper_text):
            return True
        return len(upper_token) >= 5 and upper_token in upper_text
    return token in str(text or "")


def _text_matches_airport_alias(text: str, value: str) -> bool:
    """Return True when one text blob contains IATA or provider alias tokens."""
    observed = str(text or "").strip()
    target = str(value or "").strip().upper()
    if not observed or not target:
        return False
    if target in observed.upper():
        return True
    aliases = _provider_alias_tokens(target)
    return any(_contains_alias_token(observed, token) for token in aliases)


def _score_suggestion_text_for_value(text: str, value: str) -> int:
    """Score one suggestion label for airport-targeted selection."""
    observed = str(text or "").strip()
    target = str(value or "").strip().upper()
    if not observed or not target:
        return 0
    score = 0
    upper_observed = observed.upper()
    if f"({target})" in upper_observed:
        score += 6
    if target in upper_observed:
        score += 3
    # Token-level matching for multi-word targets (e.g., "San Francisco")
    if " " in target and score == 0:
        tokens = [token for token in target.split() if len(token) > 1]
        upper_obs_tokens = upper_observed.split()
        if any(any(t in obs_token for obs_token in upper_obs_tokens) for t in tokens):
            score = 2
    aliases = _provider_alias_tokens(target)
    for token in aliases:
        if _contains_alias_token(observed, token):
            score += 2
            break
    return score


def _rank_airport_suggestion_impl(
    text: str, value: str, is_iata: bool = False
) -> tuple[int, str]:
    """
    Rank airport suggestion with IATA-aware commitment verification.

    For IATA codes, enforces strict matching to prevent wrong-airport commits.
    Returns a tuple of (rank_score, match_type) where rank_score guides selection
    confidence. Rank 0-1 = reject, 2+ = acceptable, 6+ = strong.

    Args:
        text: Suggestion visible text from UI.
        value: Target airport code/name to match.
        is_iata: True if value is a 3-letter IATA code.

    Returns:
        Tuple of (rank_score, match_type_str) where:
        - rank_score: 0 (reject) through 6 (strong match)
        - match_type: string label for decision logging
    """
    observed = str(text or "").strip()
    target = str(value or "").strip().upper()

    if not observed or not target:
        return 0, "empty_text"

    upper_observed = observed.upper()

    # IATA-specific strict matching
    if is_iata:
        # Strongest match: IATA code in parentheses (e.g., "San Francisco (SFO)")
        if f"({target})" in upper_observed:
            return 6, "iata_in_parens"
        # Strong match: IATA code with airport-related keywords (e.g., "LAX Terminal 2")
        airport_keywords = r'\b(terminal|airport|gate|t\d+|concourse|baggage)\b'
        if re.search(r'\b' + re.escape(target) + r'\b', upper_observed) and \
           re.search(airport_keywords, upper_observed, re.IGNORECASE):
            return 5, "iata_exact_boundary"
        # Weak match: IATA substring anywhere or as general word without airport context
        if target in upper_observed:
            return 2, "iata_substring"
        # Provider aliases for IATA (fallback)
        aliases = _provider_alias_tokens(target)
        for token in aliases:
            if _contains_alias_token(observed, token):
                return 1, "iata_alias_match"
        # No match for IATA code
        return 0, "iata_no_match"

    # Non-IATA matching (more lenient for city names)
    score = _score_suggestion_text_for_value(text, value)
    if score >= 6:
        return 6, "strong_match"
    elif score >= 3:
        return 4, "good_match"
    elif score >= 2:
        return 2, "weak_match"
    else:
        return 0, "no_match"
