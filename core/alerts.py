"""Alert decision and delivery utilities for flight monitoring."""

from datetime import datetime, timedelta, UTC
from email.message import EmailMessage
import os
import smtplib
from typing import Any, Dict, Optional

import requests


def _direction_matches(delta: float, direction: str) -> bool:
    """Return True when delta matches configured direction policy."""
    if direction == "any":
        return delta != 0
    if direction == "drop":
        return delta < 0
    if direction == "rise":
        return delta > 0
    return False


def _parse_created_at(value: Optional[str]) -> Optional[datetime]:
    """Parse ISO timestamp safely."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            # Legacy rows may be naive; normalize to UTC for safe arithmetic.
            return parsed.replace(tzinfo=UTC)
        return parsed
    except ValueError:
        return None


def evaluate_alert(
    *,
    current_price: Optional[float],
    previous_price: Optional[float],
    previous_created_at: Optional[str],
    config: Dict[str, Any],
) -> Dict[str, Any]:
    """Evaluate whether a notification should be sent for a price update."""
    cooldown_minutes = int(config.get("cooldown_minutes", 0))
    previous_dt = _parse_created_at(previous_created_at)
    if cooldown_minutes > 0 and previous_dt and datetime.now(UTC) - previous_dt < timedelta(minutes=cooldown_minutes):
        return {"should_alert": False, "reason": "cooldown_active"}

    if not config.get("enabled", False):
        return {"should_alert": False, "reason": "alerts_disabled"}

    if current_price is None:
        if config.get("alert_on_missing_price", False):
            return {"should_alert": True, "reason": "missing_price"}
        return {"should_alert": False, "reason": "missing_price"}

    target_price = float(config.get("target_price", 0.0))
    if target_price > 0 and current_price <= target_price:
        abs_change = None
        pct_change = None
        if previous_price is not None:
            abs_change = current_price - previous_price
            if previous_price != 0:
                pct_change = abs(abs_change) / previous_price * 100.0
        return {
            "should_alert": True,
            "reason": "target_price_reached",
            "absolute_change": abs_change,
            "percent_change": pct_change,
        }

    if previous_price is None:
        if config.get("alert_on_first_observation", False):
            return {
                "should_alert": True,
                "reason": "first_observation",
                "absolute_change": None,
                "percent_change": None,
            }
        return {"should_alert": False, "reason": "no_previous_price"}

    delta = current_price - previous_price
    abs_change = abs(delta)
    pct_change = 0.0
    if previous_price != 0:
        pct_change = abs_change / previous_price * 100.0

    if not _direction_matches(delta, config.get("alert_direction", "drop")):
        return {
            "should_alert": False,
            "reason": "direction_mismatch",
            "absolute_change": delta,
            "percent_change": pct_change,
        }

    min_abs = float(config.get("min_absolute_change", 0.0))
    min_pct = float(config.get("min_percent_change", 0.0))
    threshold_match = (abs_change >= min_abs and pct_change >= min_pct)
    if not threshold_match:
        return {
            "should_alert": False,
            "reason": "below_threshold",
            "absolute_change": delta,
            "percent_change": pct_change,
        }

    return {
        "should_alert": True,
        "reason": "threshold_met",
        "absolute_change": delta,
        "percent_change": pct_change,
    }


def build_alert_message(
    *,
    service_key: str,
    route_label: str,
    current_price: Optional[float],
    previous_price: Optional[float],
    currency: Optional[str],
    decision: Dict[str, Any],
    result_url: str,
) -> str:
    """Build one compact human-readable alert message."""
    currency_text = currency or ""
    lines = [
        f"Flight price alert [{service_key}]",
        f"Route: {route_label}",
        f"Current: {current_price} {currency_text}".strip(),
        f"Previous: {previous_price} {currency_text}".strip(),
    ]

    if decision.get("absolute_change") is not None:
        lines.append(f"Delta: {decision.get('absolute_change'):.2f}")
    if decision.get("percent_change") is not None:
        lines.append(f"Change: {decision.get('percent_change'):.2f}%")
    lines.append(f"Reason: {decision.get('reason')}")
    lines.append(f"URL: {result_url}")

    return "\n".join(lines)


def send_telegram_message(message: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """Send one Telegram message using bot token and chat id from env."""
    token_env = config.get("telegram_bot_token_env", "TELEGRAM_BOT_TOKEN")
    chat_env = config.get("telegram_chat_id_env", "TELEGRAM_CHAT_ID")
    token = os.getenv(token_env, "")
    chat_id = os.getenv(chat_env, "")

    if not token or not chat_id:
        return {"ok": False, "channel": "telegram", "error": "missing_telegram_credentials"}

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message},
            timeout=15,
        )
        response.raise_for_status()
        return {"ok": True, "channel": "telegram"}
    except Exception as exc:
        return {"ok": False, "channel": "telegram", "error": str(exc)}


def send_email_message(
    *,
    subject: str,
    message: str,
    config: Dict[str, Any],
) -> Dict[str, Any]:
    """Send one email via SMTP using env-backed credentials."""
    smtp_host = config.get("email_smtp_host", "")
    smtp_port = int(config.get("email_smtp_port", 587))
    username = os.getenv(config.get("email_username_env", "SMTP_USERNAME"), "")
    password = os.getenv(config.get("email_password_env", "SMTP_PASSWORD"), "")
    from_addr = os.getenv(config.get("email_from_env", "SMTP_FROM"), "")
    recipients = config.get("email_to", [])

    if not smtp_host or not username or not password or not from_addr or not recipients:
        return {"ok": False, "channel": "email", "error": "missing_email_credentials"}

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(recipients)
    msg.set_content(message)

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as client:
            if config.get("email_starttls", True):
                client.starttls()
            client.login(username, password)
            client.send_message(msg)
        return {"ok": True, "channel": "email"}
    except Exception as exc:
        return {"ok": False, "channel": "email", "error": str(exc)}


def dispatch_alert(
    *,
    message: str,
    config: Dict[str, Any],
    service_key: str,
) -> Dict[str, Any]:
    """Send alert message to all enabled channels."""
    channels = [c.lower() for c in config.get("enabled_channels", [])]
    supported_channels = {"telegram", "email"}
    channels = [c for c in channels if c in supported_channels]
    outcomes = []

    if "telegram" in channels:
        outcomes.append(send_telegram_message(message, config))

    if "email" in channels:
        subject_prefix = config.get("email_subject_prefix", "[FlightWatcher]")
        subject = f"{subject_prefix} {service_key} price change"
        outcomes.append(send_email_message(subject=subject, message=message, config=config))

    if not channels:
        return {"ok": False, "reason": "no_channels_enabled", "outcomes": []}

    return {
        "ok": all(item.get("ok", False) for item in outcomes),
        "outcomes": outcomes,
    }
