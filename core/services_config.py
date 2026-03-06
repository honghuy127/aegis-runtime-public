"""Config loader for selecting one or more booking services."""

from pathlib import Path
from typing import Dict, List

from core.services import (
    all_service_keys,
    default_service_urls,
    is_supported_service,
)


DEFAULT_SERVICES_CONFIG_PATH = Path("configs/services.yaml")


def _split_csv(value: str) -> List[str]:
    """Split comma-separated config values into normalized tokens."""
    return [token.strip() for token in value.split(",") if token.strip()]


def _parse_scalar_lines(path: Path) -> Dict[str, str]:
    """Parse a minimal key:value text config into a dictionary."""
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


def load_services_config(path: str = str(DEFAULT_SERVICES_CONFIG_PATH)) -> Dict[str, object]:
    """Load enabled services and URL overrides from config file."""
    raw = _parse_scalar_lines(Path(path))
    defaults = default_service_urls()

    enabled_text = raw.get("enabled_services", "google_flights")
    enabled = [k.lower() for k in _split_csv(enabled_text)]
    if not enabled:
        enabled = ["google_flights"]

    unknown = [k for k in enabled if not is_supported_service(k)]
    if unknown:
        raise ValueError(f"Unsupported service keys in enabled_services: {unknown}")

    urls = dict(defaults)
    hints: Dict[str, Dict[str, List[str]]] = {}
    for service_key in all_service_keys():
        hints[service_key] = {
            "generic": [],
            "domestic": [],
            "international": [],
            "package": [],
        }
        override_key = f"{service_key}_url"
        if raw.get(override_key):
            urls[service_key] = raw[override_key]
        generic_hint_key = f"{service_key}_url_hints"
        domestic_hint_key = f"{service_key}_domestic_url_hints"
        international_hint_key = f"{service_key}_international_url_hints"
        package_hint_key = f"{service_key}_package_url_hints"
        hints[service_key]["generic"] = _split_csv(raw.get(generic_hint_key, ""))
        hints[service_key]["domestic"] = _split_csv(raw.get(domestic_hint_key, ""))
        hints[service_key]["international"] = _split_csv(
            raw.get(international_hint_key, "")
        )
        hints[service_key]["package"] = _split_csv(raw.get(package_hint_key, ""))

    return {
        "enabled_services": enabled,
        "service_urls": urls,
        "service_url_hints": hints,
    }
