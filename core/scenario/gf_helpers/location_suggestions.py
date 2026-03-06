"""Google Flights location suggestion ranking and selection helpers.

Extracted from core/scenario/google_flights.py google_fill_and_commit_location function.
Encapsulates suggestion detection, probe handling, selector building, and candidate ranking logic.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from core.scenario.gf_helpers.helpers import _dedupe_compact_selectors, _iata_token_in_text, _is_iata_value
from core.scenario.gf_helpers.selectors import _ranked_suggestion_selector_candidates
from core.scenario.gf_helpers.location_fields import _extract_selector_visible_text
from core.scenario.gf_helpers.suggestions import _rank_airport_suggestion_impl
from core.browser import safe_min_timeout_ms


def _probe_suggestion_on_unseen_container(
    *,
    browser: Any,
    option_selectors: list[str],
    target_value: str,
    activation_selector: str,
    input_candidates: list[str],
    role_candidates: list[str],
    role_key: str,
    fill_selector: str,
    suggestion_settle_ms: int,
    _budgeted_timeout,
    timeout_fn,
    read_visible_text_fn,
) -> tuple[bool, dict[str, Any]]:
    """Probe first suggestion when suggestion container not yet detected.

    Returns:
        (suggestion_seen, result_update_dict) where result_update_dict has keys:
        - suggestion_used, suggestion_text, committed, reason, commit_method, suggestion_rank
        or empty dict if probe failed.
    """
    result_update = {}

    probe_candidates = _ranked_suggestion_selector_candidates(
        option_selectors,
        max_rank=3,
    )
    probe_selector = probe_candidates[0] if probe_candidates else option_selectors[0]

    try:
        suggestion_text, _suggestion_error = _extract_selector_visible_text(
            browser,
            probe_selector,
        )
        # Only count as a suggestion if we actually found visible text
        if suggestion_text:
            # Check IATA constraint if applicable
            if not _is_iata_value(target_value) or _iata_token_in_text(
                suggestion_text,
                target_value,
            ):
                browser.click(
                    probe_selector,
                    timeout_ms=safe_min_timeout_ms(_budgeted_timeout(), 600),
                )
                time.sleep(suggestion_settle_ms / 1000.0)
                committed_text, _commit_errors, verify_evidence = read_visible_text_fn(
                    browser,
                    role=role_key,
                    selectors=[activation_selector] + input_candidates + role_candidates,
                    fill_selector=fill_selector,
                )
                if not committed_text:
                    return False, {}

                result_update = {
                    "suggestion_used": True,
                    "suggestion_text": suggestion_text,
                    "committed": True,
                    "reason": "suggestion_clicked_probe",
                    "commit_method": "suggestion_click",
                    "suggestion_rank": 0,
                }
                result_update.update(verify_evidence)  # Merge verification evidence
                return True, result_update
    except Exception:
        pass

    return False, {}


def _build_value_specific_suggestion_selectors(
    *,
    target_value: str,
    option_selectors: list[str],
) -> list[str]:
    """Build value-specific suggestion selectors prioritizing IATA codes or city names.

    For IATA codes: Creates selectors seeking parenthesized tokens like "(JFK)" or "（JAL）"
    For city names: Creates selectors with escaped target value

    Returns:
        List of selector strings prioritizing value-specific matches
    """
    value_selectors = []
    value_token = str(target_value or "").strip()

    if not value_token:
        return value_selectors

    token_escaped = value_token.replace("'", "\\'")
    iata_value = _is_iata_value(target_value)

    if iata_value:
        iata_token = token_escaped.upper()
        iata_paren = f"({iata_token})"
        iata_paren_full = f"（{iata_token}）"  # Japanese parentheses variant
        value_selectors = [
            f"[role='listbox'] [role='option']:has-text('{iata_paren}')",
            f"[role='listbox'] [role='option']:has-text('{iata_paren_full}')",
            f"[role='listbox'] [role='option']:has-text('{iata_token}')",
            f"[role='option']:has-text('{iata_paren}')",
            f"[role='option']:has-text('{iata_paren_full}')",
            f"[role='option']:has-text('{iata_token}')",
        ]
    else:
        value_selectors = [
            f"[role='listbox'] [role='option']:has-text('{token_escaped}')",
            f"[role='option']:has-text('{token_escaped}')",
        ]

    return value_selectors


def _rank_and_sort_suggestion_candidates(
    *,
    browser: Any,
    suggestion_candidates: list[str],
    target_value: str,
    deadline: Optional[float],
    context: str = "google_fill_commit",
) -> tuple[list[tuple[int, str, str, str, bool]], list[dict[str, Any]]]:
    """Score, rank, and sort suggestion candidates by match quality.

    For IATA: Prioritizes suggestions containing the IATA token explicitly
    For city names: Sorts by match score and suggestion quality

    Returns:
        (ordered_candidates, evidence_errors) where ordered_candidates is a list of:
        (score, selector, suggestion_text, match_type, has_iata_token)
    """
    from core.browser import enforce_wall_clock_deadline

    scored_candidates = []
    fallback_candidates = []
    evidence_errors = []

    iata_value = _is_iata_value(target_value)

    for selector in suggestion_candidates:
        enforce_wall_clock_deadline(deadline, context=context)
        suggestion_text, suggestion_error = _extract_selector_visible_text(browser, selector)

        if suggestion_error:
            evidence_errors.append(suggestion_error)

        # Score the suggestion using the ranking implementation
        score, match_type = _rank_airport_suggestion_impl(
            suggestion_text, target_value, is_iata=iata_value
        )
        has_iata_token = _iata_token_in_text(suggestion_text or "", target_value)
        item = (score, selector, suggestion_text, match_type, has_iata_token)

        if iata_value and not has_iata_token:
            fallback_candidates.append(item)
            continue

        if score >= 2:
            scored_candidates.append(item)
        else:
            fallback_candidates.append(item)

    # Sort by IATA presence first (for IATA), then by score
    scored_candidates.sort(key=lambda item: (item[4], item[0]), reverse=True)
    ordered = scored_candidates + fallback_candidates

    # For IATA: filter to only IATA token matches for stricter selection
    if iata_value and ordered:
        ordered = [item for item in ordered if item[4]]

    return ordered, evidence_errors
