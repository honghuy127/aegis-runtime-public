"""Alert configuration loader for monitoring notifications."""

from pathlib import Path
from typing import Any, Dict, List


DEFAULT_ALERTS_CONFIG_PATH = Path("configs/alerts.yaml")


def _parse_scalar_lines(path: Path) -> Dict[str, str]:
    """Parse key:value lines from a simple YAML-like config file."""
    if not path.exists():
        return {}

    values: Dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if not key:
            continue
        values[key] = value.strip()
    return values


def _to_bool(value: str, default: bool = False) -> bool:
    """Convert text value into bool with fallback."""
    if value is None:
        return default
    lowered = value.strip().lower()
    if lowered in ("1", "true", "yes", "on"):
        return True
    if lowered in ("0", "false", "no", "off"):
        return False
    return default


def _to_float(value: str, default: float = 0.0) -> float:
    """Convert text value into float with fallback."""
    if value is None or value == "":
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _to_int(value: str, default: int = 0) -> int:
    """Convert text value into int with fallback."""
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _split_csv(value: str) -> List[str]:
    """Split comma-separated config field into normalized items."""
    if not value:
        return []
    return [token.strip() for token in value.split(",") if token.strip()]


def load_alerts_config(path: str = str(DEFAULT_ALERTS_CONFIG_PATH)) -> Dict[str, Any]:
    """Load alert settings with safe defaults."""
    raw = _parse_scalar_lines(Path(path))

    enabled_channels = [c.lower() for c in _split_csv(raw.get("enabled_channels", ""))]
    alert_direction = (raw.get("alert_direction") or "drop").strip().lower()
    if alert_direction not in ("any", "drop", "rise"):
        alert_direction = "drop"

    return {
        "enabled": _to_bool(raw.get("enabled"), default=False),
        "enabled_channels": enabled_channels,
        "alert_direction": alert_direction,
        "min_absolute_change": _to_float(raw.get("min_absolute_change"), default=0.0),
        "min_percent_change": _to_float(raw.get("min_percent_change"), default=0.0),
        "target_price": _to_float(raw.get("target_price"), default=0.0),
        "alert_on_first_observation": _to_bool(
            raw.get("alert_on_first_observation"),
            default=False,
        ),
        "alert_on_missing_price": _to_bool(raw.get("alert_on_missing_price"), default=False),
        "cooldown_minutes": _to_int(raw.get("cooldown_minutes"), default=0),
        "telegram_bot_token_env": raw.get("telegram_bot_token_env", "TELEGRAM_BOT_TOKEN"),
        "telegram_chat_id_env": raw.get("telegram_chat_id_env", "TELEGRAM_CHAT_ID"),
        "email_smtp_host": raw.get("email_smtp_host", ""),
        "email_smtp_port": _to_int(raw.get("email_smtp_port"), default=587),
        "email_starttls": _to_bool(raw.get("email_starttls"), default=True),
        "email_username_env": raw.get("email_username_env", "SMTP_USERNAME"),
        "email_password_env": raw.get("email_password_env", "SMTP_PASSWORD"),
        "email_from_env": raw.get("email_from_env", "SMTP_FROM"),
        "email_to": _split_csv(raw.get("email_to", "")),
        "email_subject_prefix": raw.get("email_subject_prefix", "[FlightWatcher]"),
    }
