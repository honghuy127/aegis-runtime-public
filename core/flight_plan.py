"""Flight-plan input parsing and validation helpers."""

from dataclasses import dataclass
from datetime import date, datetime
import json
from pathlib import Path
import re
from typing import Any, Dict, Optional


DEFAULT_FLIGHTS_URL = "https://www.google.com/travel/flights"
_IATA_RE = re.compile(r"^[A-Za-z]{3}$")
_DATE_FORMAT = "%Y-%m-%d"
_TRIP_TYPES = {"one_way", "round_trip"}


@dataclass(frozen=True)
class FlightPlan:
    """Normalized user flight inputs consumed by scenario execution."""

    origin: str
    dest: str
    depart: str
    return_date: Optional[str] = None
    trip_type: str = "one_way"
    is_domestic: bool = False
    max_trip_price: Optional[float] = None
    max_transit: Optional[int] = None
    url: str = DEFAULT_FLIGHTS_URL

    def to_dict(self) -> Dict[str, Any]:
        """Expose a serializable representation for logs/debug output."""
        return {
            "origin": self.origin,
            "dest": self.dest,
            "depart": self.depart,
            "return_date": self.return_date,
            "trip_type": self.trip_type,
            "is_domestic": self.is_domestic,
            "max_trip_price": self.max_trip_price,
            "max_transit": self.max_transit,
            "url": self.url,
        }


def _normalize_iata(value: str, field_name: str) -> str:
    """Normalize and validate one airport IATA code."""
    code = (value or "").strip().upper()
    if not _IATA_RE.match(code):
        raise ValueError(f"{field_name} must be a 3-letter IATA code (got: {value!r})")
    return code


def _normalize_depart_date(value: str) -> str:
    """Normalize and validate departure date in YYYY-MM-DD format."""
    text = (value or "").strip()
    try:
        depart_date = datetime.strptime(text, _DATE_FORMAT).date()
    except ValueError as exc:
        raise ValueError(
            f"depart must be in YYYY-MM-DD format (got: {value!r})"
        ) from exc

    if depart_date < date.today():
        raise ValueError(
            f"depart must be today or later (got: {depart_date.isoformat()})"
        )
    return depart_date.isoformat()


def _normalize_trip_type(value: Optional[str]) -> str:
    """Normalize trip type into one of supported keywords."""
    trip_type = (value or "one_way").strip().lower()
    if trip_type not in _TRIP_TYPES:
        raise ValueError(f"trip_type must be one of {_TRIP_TYPES} (got: {value!r})")
    return trip_type


def _normalize_optional_date(value: Optional[str], field_name: str) -> Optional[str]:
    """Validate optional YYYY-MM-DD date and return ISO string when present."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.strptime(text, _DATE_FORMAT).date()
    except ValueError as exc:
        raise ValueError(
            f"{field_name} must be in YYYY-MM-DD format (got: {value!r})"
        ) from exc
    return parsed.isoformat()


def _normalize_optional_price(value: Optional[Any], field_name: str) -> Optional[float]:
    """Validate optional numeric price threshold for whole trip."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = float(text)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be numeric (got: {value!r})") from exc
    if parsed <= 0:
        raise ValueError(f"{field_name} must be > 0 (got: {value!r})")
    return parsed


def _normalize_optional_non_negative_int(
    value: Optional[Any],
    field_name: str,
) -> Optional[int]:
    """Validate optional integer limit fields (e.g., max_transit)."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = int(text)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an integer >= 0 (got: {value!r})") from exc
    if parsed < 0:
        raise ValueError(f"{field_name} must be >= 0 (got: {value!r})")
    return parsed


def _normalize_bool(value: Optional[Any], field_name: str, default: bool = False) -> bool:
    """Normalize bool-ish values from CLI/config/JSON payloads."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"{field_name} must be boolean-like (got: {value!r})")


def validate_flight_plan(
    *,
    origin: str,
    dest: str,
    depart: str,
    return_date: Optional[str] = None,
    trip_type: str = "one_way",
    is_domestic: Optional[Any] = None,
    max_trip_price: Optional[Any] = None,
    max_transit: Optional[Any] = None,
    url: str = DEFAULT_FLIGHTS_URL,
) -> FlightPlan:
    """Validate and normalize raw user inputs into a FlightPlan object."""
    normalized_origin = _normalize_iata(origin, "origin")
    normalized_dest = _normalize_iata(dest, "dest")
    if normalized_origin == normalized_dest:
        raise ValueError("origin and dest must be different IATA codes")

    normalized_depart = _normalize_depart_date(depart)
    normalized_trip_type = _normalize_trip_type(trip_type)
    normalized_return = _normalize_optional_date(return_date, "return_date")
    normalized_is_domestic = _normalize_bool(is_domestic, "is_domestic", default=False)
    normalized_max_trip_price = _normalize_optional_price(max_trip_price, "max_trip_price")
    normalized_max_transit = _normalize_optional_non_negative_int(
        max_transit,
        "max_transit",
    )

    if normalized_trip_type == "round_trip":
        if not normalized_return:
            raise ValueError("return_date is required when trip_type is round_trip")
        if normalized_return < normalized_depart:
            raise ValueError(
                "return_date must be on or after depart for round_trip"
            )
    else:
        normalized_return = None

    normalized_url = (url or DEFAULT_FLIGHTS_URL).strip()
    if not normalized_url:
        normalized_url = DEFAULT_FLIGHTS_URL

    return FlightPlan(
        origin=normalized_origin,
        dest=normalized_dest,
        depart=normalized_depart,
        return_date=normalized_return,
        trip_type=normalized_trip_type,
        is_domestic=normalized_is_domestic,
        max_trip_price=normalized_max_trip_price,
        max_transit=normalized_max_transit,
        url=normalized_url,
    )


def load_flight_plan_file(path: str) -> Dict[str, Any]:
    """Load a JSON plan file and return raw key-value pairs."""
    plan_path = Path(path)
    if not plan_path.exists():
        raise ValueError(f"plan file not found: {plan_path}")

    try:
        payload = json.loads(plan_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in plan file: {plan_path}") from exc

    if not isinstance(payload, dict):
        raise ValueError("plan file must contain a JSON object")

    return payload


def resolve_flight_plan(
    *,
    origin: Optional[str] = None,
    dest: Optional[str] = None,
    depart: Optional[str] = None,
    return_date: Optional[str] = None,
    trip_type: Optional[str] = None,
    is_domestic: Optional[Any] = None,
    max_trip_price: Optional[Any] = None,
    max_transit: Optional[Any] = None,
    url: Optional[str] = None,
    plan_file: Optional[str] = None,
) -> FlightPlan:
    """Build one validated FlightPlan from CLI args and optional plan file."""
    file_payload: Dict[str, Any] = {}
    if plan_file:
        file_payload = load_flight_plan_file(plan_file)

    return validate_flight_plan(
        origin=origin or file_payload.get("origin", ""),
        dest=dest or file_payload.get("dest", ""),
        depart=depart or file_payload.get("depart", ""),
        return_date=return_date or file_payload.get("return_date"),
        trip_type=trip_type or file_payload.get("trip_type", "one_way"),
        is_domestic=is_domestic
        if is_domestic is not None
        else file_payload.get("is_domestic"),
        max_trip_price=max_trip_price
        if max_trip_price is not None
        else file_payload.get("max_trip_price"),
        max_transit=max_transit
        if max_transit is not None
        else file_payload.get("max_transit"),
        url=url or file_payload.get("url", DEFAULT_FLIGHTS_URL),
    )
