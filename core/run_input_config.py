"""Loader for default run inputs used by main.py when CLI args are omitted."""

import json
import os
from pathlib import Path
from typing import Any, Dict, List


DEFAULT_RUN_CONFIG_PATH = Path("configs/run.yaml")
DEFAULT_RUN_INPUTS = {
    "trip_type": "round_trip",
    "is_domestic": True,
    "task": "price",
    "save_html": False,
    "debug_save_service_html": True,
    "human_mimic": True,
    "mimic_locale": "ja-JP",
    "mimic_timezone": "Asia/Tokyo",
    "mimic_currency": "JPY",
    "mimic_region": "JP",
    "mimic_latitude": 35.6762,
    "mimic_longitude": 139.6503,
    "llm_mode": "full",
    "agentic_multimodal_mode": "judge_primary",
    "knowledge_user": "public",
    "disable_alerts": False,
    "kb_cards_enabled": False,
    "ui_driver_mode": "agent",
    "ui_driver_fallback_to_legacy": True,
    "auto_heal_enabled": False,
    "auto_heal_apply_patch": False,
    "auto_heal_max_files": 2,
    "auto_heal_max_changed_lines": 80,
    "auto_heal_test_cmd": "pytest -q tests/test_triage.py tests/test_failure_reasons.py",
    "auto_heal_llm_enabled": False,
    "thresholds_profile": "default",
    "adaptive_escalation_enabled": True,
    "escalation_reason_repeat_threshold": 2,
    "escalation_soft_fail_threshold": 3,
    "escalation_max_turns_without_ready": 2,
    "escalation_route_fill_mismatch_threshold": 2,
    "escalation_calendar_loop_detection": True,
    "graph_policy_stats_enabled": False,
    "graph_policy_stats_global_enabled": False,
    "graph_policy_stats_global_path": "storage/graph_policy_stats_global.json",
    # Calendar Adapter Configuration (Step 6.8.1)
    "calendar_selector_scoring_enabled": True,
    "calendar_verify_after_commit": True,
    "calendar_parsing_utility": "new",
    # Calendar Snapshot Configuration (Step 6.8.2)
    "calendar_snapshot_on_failure": True,
    "calendar_snapshot_write_md": False,
    "calendar_snapshot_max_chars": 120000,
}


def _parse_scalar_lines(path: Path) -> Dict[str, str]:
    """Parse key:value lines from a simple YAML-like file."""
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


def _clean_value(value: str) -> str:
    """Normalize a scalar token by trimming whitespace and paired quotes."""
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    return text


def _parse_trips_from_text(raw_text: str) -> List[Dict[str, Any]]:
    """Parse a minimal YAML-like `trips:` list block from run.yaml text."""
    lines = raw_text.splitlines()
    in_trips_block = False
    trips: List[Dict[str, Any]] = []
    current: Dict[str, Any] = {}

    for raw_line in lines:
        stripped = raw_line.strip()
        if not in_trips_block:
            if stripped.startswith("trips:"):
                inline = stripped.split(":", 1)[1].strip()
                if inline:
                    try:
                        payload = json.loads(inline)
                    except json.JSONDecodeError:
                        return []
                    if isinstance(payload, list):
                        return [item for item in payload if isinstance(item, dict)]
                    return []
                in_trips_block = True
            continue

        if not raw_line.startswith((" ", "\t")):
            if current:
                trips.append(current)
                current = {}
            break
        if not stripped or stripped.startswith("#"):
            continue

        if stripped.startswith("-"):
            if current:
                trips.append(current)
            current = {}
            remainder = stripped[1:].strip()
            if remainder and ":" in remainder:
                key, value = remainder.split(":", 1)
                key = key.strip()
                if key:
                    current[key] = _clean_value(value)
            continue

        if ":" in stripped:
            key, value = stripped.split(":", 1)
            key = key.strip()
            if key:
                if not current:
                    current = {}
                current[key] = _clean_value(value)

    if in_trips_block and current:
        trips.append(current)
    return trips


def _parse_trips(path: Path, raw_scalars: Dict[str, str]) -> List[Dict[str, Any]]:
    """Resolve multi-trip rows from either `trips:` block or JSON scalar field."""
    if not path.exists():
        return []

    raw_text = path.read_text(encoding="utf-8")
    trips = _parse_trips_from_text(raw_text)
    if trips:
        return trips

    for key in ("trips_json", "trips"):
        value = raw_scalars.get(key, "").strip()
        if not value:
            continue
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
    return []


def _normalize_trip_rows(raw_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Type-normalize trip rows from config to runtime-friendly values."""
    normalized: List[Dict[str, Any]] = []
    for row in raw_rows:
        if not isinstance(row, dict):
            continue
        trip: Dict[str, Any] = {}
        for key in ("origin", "dest", "depart", "return_date", "trip_type"):
            value = row.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text != "":
                trip[key] = text
        if "is_domestic" in row:
            trip["is_domestic"] = _to_bool(str(row.get("is_domestic")), default=False)
        if "max_trip_price" in row:
            trip["max_trip_price"] = _to_float(str(row.get("max_trip_price")), default=None)
        if "max_transit" in row:
            trip["max_transit"] = _to_int(str(row.get("max_transit")), default=None)
        if trip:
            normalized.append(trip)
    return normalized


def _to_bool(value: str, default: bool = False) -> bool:
    """Convert a text value into bool with fallback."""
    if value is None:
        return default
    lowered = value.strip().lower()
    if lowered in ("1", "true", "yes", "on"):
        return True
    if lowered in ("0", "false", "no", "off"):
        return False
    return default


def _to_float(value: str, default: Any = None):
    """Convert text to float when possible."""
    if value is None or value == "":
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _to_int(value: str, default: Any = None):
    """Convert text to int when possible."""
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _default_knowledge_user() -> str:
    """Resolve default knowledge namespace from env for multi-user sharing."""
    for key in ("FLIGHT_WATCHER_USER", "USER_EMAIL", "GITHUB_USER", "GITHUB_ACTOR"):
        value = os.getenv(key, "").strip()
        if value:
            return value
    return str(DEFAULT_RUN_INPUTS["knowledge_user"])


def _parse_ui_driver_overrides(path: Path, raw_scalars: Dict[str, str]) -> Dict[str, str]:
    """Parse ui_driver_overrides from run.yaml (YAML dict format).

    Supports simple YAML dict-style blocks. For now, returns simplified
    dict if present; full YAML parsing is out of scope.
    """
    if not path.exists():
        return {}

    try:
        raw_text = path.read_text(encoding="utf-8")
        lines = raw_text.splitlines()
        overrides = {}
        in_overrides_block = False

        for raw_line in lines:
            stripped = raw_line.strip()

            if not in_overrides_block:
                if stripped.startswith("ui_driver_overrides:"):
                    in_overrides_block = True
                continue

            if stripped and not raw_line.startswith((" ", "\t")):
                break

            if not stripped or stripped.startswith("#"):
                continue

            if ":" in stripped:
                key, value = stripped.split(":", 1)
                key = key.strip()
                value = _clean_value(value.strip())
                if key and value in ("agent", "legacy"):
                    overrides[key] = value

        return overrides
    except Exception:
        return {}


def _normalize_llm_mode(value: Any, default: str = "full") -> str:
    """Normalize llm_mode string to supported values."""
    text = str(value).strip().lower() if value is not None else default
    if text in ("full", "light"):
        return text
    return default


def _normalize_multimodal_mode(value: Any, default: str = "off") -> str:
    """Normalize multimodal extraction mode to supported values."""
    text = str(value).strip().lower() if value is not None else default
    if text in ("off", "assist", "primary", "judge", "judge_primary"):
        return text
    return default


def _normalize_ui_driver_mode(value: Any, default: str = "agent") -> str:
    """Normalize ui_driver_mode string to supported values."""
    text = str(value).strip().lower() if value is not None else default
    if text in ("agent", "legacy"):
        return text
    return default


def _normalize_thresholds_profile(value: Any, default: str = "default") -> str:
    """Normalize thresholds_profile to supported values."""
    text = str(value).strip().lower() if value is not None else default
    if text in ("default", "debug"):
        return text
    return default


def load_run_input_config(path: str = str(DEFAULT_RUN_CONFIG_PATH)) -> Dict[str, Any]:
    """Load run-input defaults for trip parameters and monitoring behavior."""
    config_path = Path(path)
    raw = _parse_scalar_lines(config_path)
    trips = _normalize_trip_rows(_parse_trips(config_path, raw))
    ui_driver_overrides = _parse_ui_driver_overrides(config_path, raw)
    return {
        "origin": raw.get("origin"),
        "dest": raw.get("dest"),
        "depart": raw.get("depart"),
        "return_date": raw.get("return_date"),
        "trip_type": raw.get("trip_type") or DEFAULT_RUN_INPUTS["trip_type"],
        "is_domestic": _to_bool(
            raw.get("is_domestic"),
            default=bool(DEFAULT_RUN_INPUTS["is_domestic"]),
        ),
        "max_trip_price": _to_float(raw.get("max_trip_price"), default=None),
        "max_transit": _to_int(
            raw.get("max_transit"),
            default=None,
        ),
        "human_mimic": _to_bool(
            raw.get("human_mimic"),
            default=bool(DEFAULT_RUN_INPUTS["human_mimic"]),
        ),
        "mimic_locale": raw.get("mimic_locale") or str(DEFAULT_RUN_INPUTS["mimic_locale"]),
        "mimic_timezone": raw.get("mimic_timezone")
        or str(DEFAULT_RUN_INPUTS["mimic_timezone"]),
        "mimic_currency": raw.get("mimic_currency")
        or str(DEFAULT_RUN_INPUTS["mimic_currency"]),
        "mimic_region": raw.get("mimic_region") or str(DEFAULT_RUN_INPUTS["mimic_region"]),
        "mimic_latitude": _to_float(
            raw.get("mimic_latitude"),
            default=float(DEFAULT_RUN_INPUTS["mimic_latitude"]),
        ),
        "mimic_longitude": _to_float(
            raw.get("mimic_longitude"),
            default=float(DEFAULT_RUN_INPUTS["mimic_longitude"]),
        ),
        "llm_mode": _normalize_llm_mode(
            raw.get("llm_mode"),
            default=str(DEFAULT_RUN_INPUTS["llm_mode"]),
        ),
        "agentic_multimodal_mode": _normalize_multimodal_mode(
            raw.get("agentic_multimodal_mode"),
            default=str(DEFAULT_RUN_INPUTS["agentic_multimodal_mode"]),
        ),
        "knowledge_user": raw.get("knowledge_user") or _default_knowledge_user(),
        "plan_file": raw.get("plan_file"),
        "task": raw.get("task") or str(DEFAULT_RUN_INPUTS["task"]),
        "save_html": _to_bool(raw.get("save_html"), default=bool(DEFAULT_RUN_INPUTS["save_html"])),
        "debug_save_service_html": _to_bool(
            raw.get("debug_save_service_html"),
            default=bool(DEFAULT_RUN_INPUTS["debug_save_service_html"]),
        ),
        "disable_alerts": _to_bool(
            raw.get("disable_alerts"),
            default=bool(DEFAULT_RUN_INPUTS["disable_alerts"]),
        ),
        "kb_cards_enabled": _to_bool(
            raw.get("kb_cards_enabled"),
            default=bool(DEFAULT_RUN_INPUTS["kb_cards_enabled"]),
        ),
        "ui_driver_mode": _normalize_ui_driver_mode(
            raw.get("ui_driver_mode"),
            default=str(DEFAULT_RUN_INPUTS["ui_driver_mode"]),
        ),
        "ui_driver_overrides": ui_driver_overrides,
        "ui_driver_fallback_to_legacy": _to_bool(
            raw.get("ui_driver_fallback_to_legacy"),
            default=bool(DEFAULT_RUN_INPUTS["ui_driver_fallback_to_legacy"]),
        ),
        "auto_heal_enabled": _to_bool(
            raw.get("auto_heal_enabled"),
            default=bool(DEFAULT_RUN_INPUTS["auto_heal_enabled"]),
        ),
        "auto_heal_apply_patch": _to_bool(
            raw.get("auto_heal_apply_patch"),
            default=bool(DEFAULT_RUN_INPUTS["auto_heal_apply_patch"]),
        ),
        "auto_heal_max_files": _to_int(
            raw.get("auto_heal_max_files"),
            default=int(DEFAULT_RUN_INPUTS["auto_heal_max_files"]),
        ),
        "auto_heal_max_changed_lines": _to_int(
            raw.get("auto_heal_max_changed_lines"),
            default=int(DEFAULT_RUN_INPUTS["auto_heal_max_changed_lines"]),
        ),
        "auto_heal_test_cmd": raw.get("auto_heal_test_cmd")
        or str(DEFAULT_RUN_INPUTS["auto_heal_test_cmd"]),
        "auto_heal_llm_enabled": _to_bool(
            raw.get("auto_heal_llm_enabled"),
            default=bool(DEFAULT_RUN_INPUTS["auto_heal_llm_enabled"]),
        ),
        "thresholds_profile": _normalize_thresholds_profile(
            raw.get("thresholds_profile"),
            default=str(DEFAULT_RUN_INPUTS["thresholds_profile"]),
        ),
        "adaptive_escalation_enabled": _to_bool(
            raw.get("adaptive_escalation_enabled"),
            default=bool(DEFAULT_RUN_INPUTS["adaptive_escalation_enabled"]),
        ),
        "escalation_reason_repeat_threshold": _to_int(
            raw.get("escalation_reason_repeat_threshold"),
            default=int(DEFAULT_RUN_INPUTS["escalation_reason_repeat_threshold"]),
        ),
        "escalation_soft_fail_threshold": _to_int(
            raw.get("escalation_soft_fail_threshold"),
            default=int(DEFAULT_RUN_INPUTS["escalation_soft_fail_threshold"]),
        ),
        "escalation_max_turns_without_ready": _to_int(
            raw.get("escalation_max_turns_without_ready"),
            default=int(DEFAULT_RUN_INPUTS["escalation_max_turns_without_ready"]),
        ),
        "escalation_route_fill_mismatch_threshold": _to_int(
            raw.get("escalation_route_fill_mismatch_threshold"),
            default=int(DEFAULT_RUN_INPUTS["escalation_route_fill_mismatch_threshold"]),
        ),
        "escalation_calendar_loop_detection": _to_bool(
            raw.get("escalation_calendar_loop_detection"),
            default=bool(DEFAULT_RUN_INPUTS["escalation_calendar_loop_detection"]),
        ),
        "graph_policy_stats_enabled": _to_bool(
            raw.get("graph_policy_stats_enabled"),
            default=bool(DEFAULT_RUN_INPUTS["graph_policy_stats_enabled"]),
        ),
        "graph_policy_stats_global_enabled": _to_bool(
            raw.get("graph_policy_stats_global_enabled"),
            default=bool(DEFAULT_RUN_INPUTS["graph_policy_stats_global_enabled"]),
        ),
        "graph_policy_stats_global_path": raw.get("graph_policy_stats_global_path")
        or str(DEFAULT_RUN_INPUTS["graph_policy_stats_global_path"]),
        "calendar_selector_scoring_enabled": _to_bool(
            raw.get("calendar_selector_scoring_enabled"),
            default=bool(DEFAULT_RUN_INPUTS["calendar_selector_scoring_enabled"]),
        ),
        "calendar_verify_after_commit": _to_bool(
            raw.get("calendar_verify_after_commit"),
            default=bool(DEFAULT_RUN_INPUTS["calendar_verify_after_commit"]),
        ),
        "calendar_parsing_utility": raw.get("calendar_parsing_utility")
        or str(DEFAULT_RUN_INPUTS["calendar_parsing_utility"]),
        "calendar_snapshot_on_failure": _to_bool(
            raw.get("calendar_snapshot_on_failure"),
            default=bool(DEFAULT_RUN_INPUTS["calendar_snapshot_on_failure"]),
        ),
        "calendar_snapshot_write_md": _to_bool(
            raw.get("calendar_snapshot_write_md"),
            default=bool(DEFAULT_RUN_INPUTS["calendar_snapshot_write_md"]),
        ),
        "calendar_snapshot_max_chars": _to_int(
            raw.get("calendar_snapshot_max_chars"),
            default=int(DEFAULT_RUN_INPUTS["calendar_snapshot_max_chars"]),
        ),
        "trips": trips,
    }
