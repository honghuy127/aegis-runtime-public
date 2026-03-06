"""Tests for flight plan input normalization and validation."""

import json

import pytest

from core.flight_plan import resolve_flight_plan, validate_flight_plan


def test_validate_flight_plan_normalizes_and_accepts_valid_inputs():
    """Lowercase codes should normalize to uppercase with valid future date."""
    plan = validate_flight_plan(
        origin="hnd",
        dest="itm",
        depart="2099-03-01",
    )
    assert plan.origin == "HND"
    assert plan.dest == "ITM"
    assert plan.depart == "2099-03-01"
    assert plan.trip_type == "one_way"
    assert plan.return_date is None


def test_validate_flight_plan_rejects_same_airports():
    """Origin and destination cannot be the same code."""
    with pytest.raises(ValueError, match="origin and dest must be different"):
        validate_flight_plan(
            origin="HND",
            dest="HND",
            depart="2099-03-01",
        )


def test_validate_flight_plan_rejects_bad_date_format():
    """Date must be in strict YYYY-MM-DD format."""
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        validate_flight_plan(
            origin="HND",
            dest="ITM",
            depart="03/01/2099",
        )


def test_validate_flight_plan_round_trip_requires_return_date():
    """Round-trip mode must include return_date."""
    with pytest.raises(ValueError, match="return_date is required"):
        validate_flight_plan(
            origin="HND",
            dest="ITM",
            depart="2099-03-01",
            trip_type="round_trip",
        )


def test_validate_flight_plan_round_trip_rejects_return_before_depart():
    """Return date cannot be earlier than departure for round-trip."""
    with pytest.raises(ValueError, match="on or after depart"):
        validate_flight_plan(
            origin="HND",
            dest="ITM",
            depart="2099-03-10",
            return_date="2099-03-01",
            trip_type="round_trip",
        )


def test_validate_flight_plan_round_trip_accepts_valid_return():
    """Round-trip mode should keep a valid return_date."""
    plan = validate_flight_plan(
        origin="HND",
        dest="ITM",
        depart="2099-03-01",
        return_date="2099-03-08",
        trip_type="round_trip",
    )
    assert plan.trip_type == "round_trip"
    assert plan.return_date == "2099-03-08"


def test_validate_flight_plan_accepts_is_domestic_flag():
    """is_domestic should be normalized from bool-ish values."""
    plan = validate_flight_plan(
        origin="HND",
        dest="ITM",
        depart="2099-03-01",
        is_domestic="true",
    )
    assert plan.is_domestic is True


def test_validate_flight_plan_rejects_invalid_is_domestic_value():
    """Invalid bool-ish values should fail validation."""
    with pytest.raises(ValueError, match="is_domestic must be boolean-like"):
        validate_flight_plan(
            origin="HND",
            dest="ITM",
            depart="2099-03-01",
            is_domestic="maybe",
        )


def test_validate_flight_plan_accepts_max_trip_price():
    """max_trip_price should be stored as positive float."""
    plan = validate_flight_plan(
        origin="HND",
        dest="ITM",
        depart="2099-03-01",
        max_trip_price="15000",
    )
    assert plan.max_trip_price == 15000.0


def test_validate_flight_plan_accepts_max_transit():
    """max_transit should accept 0+ integer values."""
    plan = validate_flight_plan(
        origin="HND",
        dest="ITM",
        depart="2099-03-01",
        max_transit=1,
    )
    assert plan.max_transit == 1


def test_validate_flight_plan_rejects_negative_max_transit():
    """max_transit must be >= 0 when provided."""
    with pytest.raises(ValueError, match="max_transit must be >= 0"):
        validate_flight_plan(
            origin="HND",
            dest="ITM",
            depart="2099-03-01",
            max_transit=-1,
        )


def test_validate_flight_plan_rejects_non_integer_max_transit():
    """max_transit must be integer-like text/number."""
    with pytest.raises(ValueError, match="max_transit must be an integer >= 0"):
        validate_flight_plan(
            origin="HND",
            dest="ITM",
            depart="2099-03-01",
            max_transit="1.5",
        )


def test_validate_flight_plan_rejects_non_positive_max_trip_price():
    """max_trip_price must be > 0 when provided."""
    with pytest.raises(ValueError, match="max_trip_price must be > 0"):
        validate_flight_plan(
            origin="HND",
            dest="ITM",
            depart="2099-03-01",
            max_trip_price=0,
        )


def test_resolve_flight_plan_loads_from_json_file(tmp_path):
    """Missing CLI fields should be filled from plan-file payload."""
    payload = {
        "origin": "NRT",
        "dest": "KIX",
        "depart": "2099-04-01",
        "return_date": "2099-04-10",
        "trip_type": "round_trip",
        "is_domestic": True,
        "max_trip_price": 20000,
        "max_transit": 0,
    }
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps(payload), encoding="utf-8")

    plan = resolve_flight_plan(plan_file=str(plan_file))

    assert plan.origin == "NRT"
    assert plan.dest == "KIX"
    assert plan.depart == "2099-04-01"
    assert plan.return_date == "2099-04-10"
    assert plan.trip_type == "round_trip"
    assert plan.is_domestic is True
    assert plan.max_trip_price == 20000.0
    assert plan.max_transit == 0


def test_resolve_flight_plan_prefers_cli_over_file(tmp_path):
    """Explicit CLI args should override plan-file values."""
    payload = {
        "origin": "NRT",
        "dest": "KIX",
        "depart": "2099-04-01",
        "return_date": "2099-04-10",
        "trip_type": "round_trip",
        "is_domestic": False,
        "max_trip_price": 20000,
        "max_transit": 2,
    }
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps(payload), encoding="utf-8")

    plan = resolve_flight_plan(
        origin="HND",
        dest="ITM",
        depart="2099-05-01",
        return_date="2099-05-09",
        trip_type="round_trip",
        is_domestic=True,
        max_trip_price=12345,
        max_transit=1,
        plan_file=str(plan_file),
    )

    assert plan.origin == "HND"
    assert plan.dest == "ITM"
    assert plan.depart == "2099-05-01"
    assert plan.return_date == "2099-05-09"
    assert plan.trip_type == "round_trip"
    assert plan.is_domestic is True
    assert plan.max_trip_price == 12345.0
    assert plan.max_transit == 1
