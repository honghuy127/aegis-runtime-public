"""Tests for Google Flights IATA commitment hardening (Phase 2)."""

import pytest
from core.scenario.gf_helpers.suggestions import _rank_airport_suggestion_impl
from core.scenario.gf_helpers.helpers import _is_iata_value


def test_rank_airport_suggestion_iata_in_parens():
    """IATA code in parentheses should be strongest match (rank 6)."""
    rank, match_type = _rank_airport_suggestion_impl(
        "Los Angeles (LAX)", "LAX", is_iata=True
    )
    assert rank == 6
    assert match_type == "iata_in_parens"


def test_rank_airport_suggestion_iata_exact_boundary():
    """IATA code with word boundaries should be strong match (rank 5)."""
    rank, match_type = _rank_airport_suggestion_impl(
        "LAX Terminal 2", "LAX", is_iata=True
    )
    assert rank == 5
    assert match_type == "iata_exact_boundary"


def test_rank_airport_suggestion_iata_substring():
    """IATA code as substring (risky) should be weak match (rank 2)."""
    rank, match_type = _rank_airport_suggestion_impl(
        "Los Angeles Relaxation Center",  # Contains "LAX" in "reLAXation" - substring match
        "LAX",
        is_iata=True,
    )
    # This substring match (in wrong context) still gets rank 2; forcing explicit click decision
    # rather than auto-commit to prevent mis-selection of wrong airport
    assert rank == 2
    assert match_type == "iata_substring"


def test_rank_airport_suggestion_iata_no_match():
    """IATA code with no match should be rejected (rank 0)."""
    rank, match_type = _rank_airport_suggestion_impl(
        "New York JFK", "LAX", is_iata=True
    )
    assert rank == 0
    assert match_type == "iata_no_match"


def test_rank_airport_suggestion_non_iata_strong():
    """Non-IATA city name with strong match scores high."""
    rank, match_type = _rank_airport_suggestion_impl(
        "San Francisco (SFO)", "San Francisco", is_iata=False
    )
    assert rank >= 4  # Strong match
    assert "match" in match_type


def test_rank_airport_suggestion_non_iata_weak():
    """Non-IATA city name with weak match gets lower rank."""
    rank, match_type = _rank_airport_suggestion_impl(
        "Francisco Terminal", "San Francisco", is_iata=False
    )
    assert rank >= 2  # At least weak match
    assert "match" in match_type


def test_rank_airport_suggestion_empty_text():
    """Empty text should be rejected."""
    rank, match_type = _rank_airport_suggestion_impl(
        "", "LAX", is_iata=True
    )
    assert rank == 0
    assert match_type == "empty_text"


def test_rank_airport_suggestion_empty_value():
    """Empty target value should be rejected."""
    rank, match_type = _rank_airport_suggestion_impl(
        "Los Angeles (LAX)", "", is_iata=True
    )
    assert rank == 0
    assert match_type == "empty_text"


def test_is_iata_value_valid():
    """Valid IATA codes should be recognized."""
    assert _is_iata_value("LAX") is True
    assert _is_iata_value("lax") is True  # Case-insensitive
    assert _is_iata_value("JFK") is True
    assert _is_iata_value("HND") is True


def test_is_iata_value_invalid():
    """Non-IATA codes should be rejected."""
    assert _is_iata_value("LAYB") is False  # Too long
    assert _is_iata_value("LA") is False  # Too short
    assert _is_iata_value("San Francisco") is False  # City name
    assert _is_iata_value("") is False  # Empty
    assert _is_iata_value("L1X") is False  # Contains digit


def test_rank_airport_suggestion_case_insensitive():
    """Ranking should be case-insensitive for IATA codes."""
    rank1, _ = _rank_airport_suggestion_impl("Los Angeles (LAX)", "lax", is_iata=True)
    rank2, _ = _rank_airport_suggestion_impl("Los Angeles (LAX)", "LAX", is_iata=True)
    assert rank1 == rank2 == 6


def test_rank_airport_suggestion_iata_substring_requires_click_decision():
    """Substring IATA matches (rank 2) should require explicit click approval."""
    rank, match_type = _rank_airport_suggestion_impl(
        "String containing LAX somewhere",
        "LAX",
        is_iata=True,
    )
    # Rank 2 means system will skip click unless explicitly approved
    assert rank == 2
    assert match_type == "iata_substring"
