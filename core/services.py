"""Service registry and URL-candidate helpers for supported booking providers.

Legacy compatibility layer:
- This module remains the stable public surface used across runtime/tests.
- Concrete service plugins now delegate to this logic to keep behavior identical.
"""

from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse
from utils.knowledge_rules import get_knowledge_rule_tokens


# Legacy static metadata map kept for backward compatibility.
# Pluginized service modules (core/plugins/services/*) currently delegate here.
SUPPORTED_SERVICES = {
    "google_flights": {
        "name": "Google Flights",
        "url": "https://www.google.com/travel/flights",
        "domains": ["google.com"],
    },
    "skyscanner": {
        "name": "Skyscanner",
        "url": "https://www.skyscanner.com/flights",
        "domains": ["skyscanner.com", "skyscanner.net", "skyscanner.jp"],
    },
}
_THIRD_LEVEL_PUBLIC_SUFFIXES = {
    "co.jp",
    "ac.jp",
    "or.jp",
    "ne.jp",
    "go.jp",
    "co.uk",
    "org.uk",
    "ac.uk",
    "com.au",
    "net.au",
    "org.au",
}


def all_service_keys() -> List[str]:
    """Return all supported service keys in deterministic order."""
    return list(SUPPORTED_SERVICES.keys())


def is_supported_service(service_key: str) -> bool:
    """Return True when service key exists in registry."""
    return service_key in SUPPORTED_SERVICES


def service_name(service_key: str) -> str:
    """Resolve display name for a known service key."""
    return SUPPORTED_SERVICES[service_key]["name"]


def default_service_url(service_key: str) -> str:
    """Resolve default homepage/search URL for a known service key."""
    return SUPPORTED_SERVICES[service_key]["url"]


def default_service_urls() -> Dict[str, str]:
    """Return mapping of service key to default URL."""
    return {k: v["url"] for k, v in SUPPORTED_SERVICES.items()}


def _add_candidate(candidates: List[str], url: Optional[str]) -> None:
    """Normalize and append one URL while deduplicating."""
    if not isinstance(url, str):
        return
    normalized = url.strip()
    if not normalized:
        return
    if normalized not in candidates:
        candidates.append(normalized)


def _hostname(url: str) -> str:
    """Extract normalized hostname from URL string."""
    if not isinstance(url, str):
        return ""
    try:
        return (urlparse(url.strip()).hostname or "").lower()
    except Exception:
        return ""


def _base_domain(hostname: str) -> str:
    """Return coarse registrable-like domain (last two labels)."""
    host = (hostname or "").strip().lower()
    if not host:
        return ""
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    tail2 = ".".join(parts[-2:])
    if tail2 in _THIRD_LEVEL_PUBLIC_SUFFIXES and len(parts) >= 3:
        return ".".join(parts[-3:])
    return tail2


def _service_base_domains(service_key: str) -> List[str]:
    """Return trusted base domains from static service metadata."""
    out: List[str] = []
    meta = SUPPORTED_SERVICES.get(service_key, {})
    for domain in meta.get("domains", []):
        base = _base_domain((domain or "").strip().lower())
        if base and base not in out:
            out.append(base)
    if not is_supported_service(service_key):
        return out
    default_base = _base_domain(_hostname(default_service_url(service_key)))
    if default_base and default_base not in out:
        out.append(default_base)
    return out


def _other_service_base_domains(service_key: str) -> List[str]:
    """Return known base domains belonging to other registered services."""
    out: List[str] = []
    for other_key in all_service_keys():
        if other_key == service_key:
            continue
        for base in _service_base_domains(other_key):
            if base and base not in out:
                out.append(base)
    return out


def _trusted_base_domains(
    service_key: str,
    preferred_url: Optional[str],
    seed_hints: Optional[Dict[str, Any]],
) -> List[str]:
    """Build trusted base domains from service defaults + configured seed URLs."""
    values: List[str] = []
    _add_candidate(values, preferred_url)
    _add_candidate(values, default_service_url(service_key))
    if isinstance(seed_hints, dict):
        for group in ("generic", "domestic", "international", "package"):
            for value in _normalize_hint_values(seed_hints.get(group)):
                _add_candidate(values, value)
    out: List[str] = _service_base_domains(service_key)
    for value in values:
        base = _base_domain(_hostname(value))
        if base and base not in out:
            out.append(base)
    return out


def _matches_trusted_domain(url: str, trusted_base_domains: List[str]) -> bool:
    """Return True when URL host belongs to one of trusted base domains."""
    if not trusted_base_domains:
        return True
    host = _hostname(url)
    base = _base_domain(host)
    if not base:
        return False
    return base in trusted_base_domains


def url_matches_service_domain(
    service_key: str,
    url: str,
    *,
    preferred_url: Optional[str] = None,
    seed_hints: Optional[Dict[str, Any]] = None,
) -> bool:
    """Return True when URL is not clearly a different supported service domain."""
    base = _base_domain(_hostname(url))
    if not base:
        return False
    if base in _other_service_base_domains(service_key):
        return False
    # Allow unknown domains here so tests and local/dev hints can still be persisted.
    # The hard guard is only against known cross-service domains.
    return True


def _normalize_hint_values(payload: Any) -> List[str]:
    """Normalize URL-hint payload to a clean list of URL strings."""
    if isinstance(payload, list):
        return [item.strip() for item in payload if isinstance(item, str) and item.strip()]
    if isinstance(payload, str):
        text = payload.strip()
        return [text] if text else []
    return []


def _hint_group(seed_hints: Optional[Dict[str, Any]], key: str) -> List[str]:
    """Resolve seeded URL hints from config payload by hint group key."""
    if not isinstance(seed_hints, dict):
        return []
    return _normalize_hint_values(seed_hints.get(key))


def _knowledge_group(knowledge: Optional[Dict[str, Any]], key: str) -> List[str]:
    """Resolve learned URL hints from knowledge payload by group key."""
    if not isinstance(knowledge, dict):
        return []
    return _normalize_hint_values(knowledge.get(key))


def _add_many(candidates: List[str], values: Iterable[str]) -> None:
    """Append many URL values with normalization/dedupe."""
    for value in values:
        _add_candidate(candidates, value)


def _add_split_flow_hints(
    candidates: List[str],
    *,
    is_domestic: Optional[bool],
    knowledge: Optional[Dict[str, Any]],
    seed_hints: Optional[Dict[str, Any]],
    prefer_seed_first: bool = False,
) -> None:
    """Append domestic/international URL hints based on requested trip mode."""
    def _append_group(group: str) -> None:
        if prefer_seed_first:
            _add_many(candidates, _hint_group(seed_hints, group))
            _add_many(candidates, _knowledge_group(knowledge, f"local_{group}_url_hints"))
            return
        _add_many(candidates, _knowledge_group(knowledge, f"local_{group}_url_hints"))
        _add_many(candidates, _hint_group(seed_hints, group))

    if is_domestic is True:
        _append_group("domestic")
    elif is_domestic is False:
        _append_group("international")
    else:
        _append_group("domestic")
        _append_group("international")


def _url_priority_score(service_key: str, url: str, is_domestic: Optional[bool]) -> int:
    """Return lower-is-better URL priority score for service-specific routing."""
    text = (url or "").strip().lower()
    score = 0
    package_tokens = [
        token.lower() for token in get_knowledge_rule_tokens("url_package_tokens")
    ]
    if any(token and token in text for token in package_tokens):
        score += 70
    return score


def service_url_candidates(
    service_key: str,
    preferred_url: Optional[str] = None,
    is_domestic: Optional[bool] = None,
    *,
    knowledge: Optional[Dict[str, Any]] = None,
    seed_hints: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Return ordered URL candidates from root URL + hints + learned knowledge."""
    candidates: List[str] = []
    site_type = None
    if isinstance(knowledge, dict):
        site_type = knowledge.get("site_type")

    include_domain_split_groups = site_type != "single_flow"
    # Keep preferred/root URL first by default. Only front-load split hints
    # after we have explicit learned evidence that the site is split-flow.
    prefer_domain_hints_first = (
        False  # No services currently require split-flow entry points
    )
    prefer_seed_first = False

    if include_domain_split_groups and prefer_domain_hints_first:
        _add_split_flow_hints(
            candidates,
            is_domestic=is_domestic,
            knowledge=knowledge,
            seed_hints=seed_hints,
            prefer_seed_first=prefer_seed_first,
        )

    _add_candidate(candidates, preferred_url)

    # Learned knowledge should be applied before static seed hints.
    _add_many(candidates, _knowledge_group(knowledge, "local_url_hints"))
    _add_many(candidates, _knowledge_group(knowledge, "global_url_hints"))

    if include_domain_split_groups:
        _add_split_flow_hints(
            candidates,
            is_domestic=is_domestic,
            knowledge=knowledge,
            seed_hints=seed_hints,
            prefer_seed_first=prefer_seed_first,
        )

    _add_many(candidates, _hint_group(seed_hints, "generic"))
    _add_candidate(candidates, default_service_url(service_key))

    foreign_domains = _other_service_base_domains(service_key)
    domain_filtered = []
    for url in candidates:
        host = _hostname(url)
        base = _base_domain(host)
        if not host or not base:
            continue
        if base in foreign_domains:
            continue
        # Keep non-foreign unknown domains (useful in tests/dev) while still
        # excluding obvious cross-service contamination.
        domain_filtered.append(url)
    if domain_filtered:
        candidates = domain_filtered

    package_hint_set = set(_knowledge_group(knowledge, "local_package_url_hints"))
    package_hint_set.update(_hint_group(seed_hints, "package"))

    ranked = sorted(
        enumerate(candidates),
        key=lambda item: (
            _url_priority_score(service_key, item[1], is_domestic)
            + (60 if item[1] in package_hint_set else 0),
            item[0],
        ),
    )
    ranked_urls = [url for _, url in ranked]

    return ranked_urls
