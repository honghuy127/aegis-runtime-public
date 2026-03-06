"""Shared airport/metro knowledge store used across providers."""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


STORE_PATH = Path("storage/shared_knowledge_store.json")
BUILTIN_PROVIDER_DATA_PATH = Path(__file__).resolve().parent / "builtin_provider_airport_data.json"
_CACHE: Dict[str, Any] = {}
_DEFAULT_STORE: Dict[str, Any] = {
    "airport_aliases": {},
    "provider_airport_code_map": {},
}
_BUILTIN_PROVIDER_AIRPORT_CODE_MAP: Dict[str, Dict[str, str]] = {}
_BUILTIN_PROVIDER_AIRPORT_ALIAS_TOKENS: Dict[str, Dict[str, List[str]]] = {}


def _load_builtin_provider_data() -> None:
    """Load optional external builtin provider airport data (JSON).

    The JSON file `storage/builtin_provider_airport_data.json` may define
    provider-specific airport code maps and multilingual alias tokens. The
    structure is intentionally simple and extendable by language, for example:

    {
      "provider_airport_code_map": { "google_flights": { "HND": "TYO" } },
      "provider_airport_alias_tokens": {
         "google_flights": {
            "HND": { "ja": ["羽田"], "en": ["Haneda"] }
         }
      }
    }

    If the file is absent or invalid we silently fall back to a small internal
    default set to preserve previous behavior.
    """
    global _BUILTIN_PROVIDER_AIRPORT_CODE_MAP, _BUILTIN_PROVIDER_AIRPORT_ALIAS_TOKENS
    builtin_path = BUILTIN_PROVIDER_DATA_PATH
    try:
        if builtin_path.exists():
            raw = json.loads(builtin_path.read_text(encoding="utf-8"))
        else:
            raw = {}
    except Exception:
        raw = {}

    # Provider code map
    pcm = raw.get("provider_airport_code_map") if isinstance(raw, dict) else None
    if isinstance(pcm, dict):
        _BUILTIN_PROVIDER_AIRPORT_CODE_MAP = _normalize_provider_maps(pcm)
    else:
        _BUILTIN_PROVIDER_AIRPORT_CODE_MAP = {}

    # Provider alias tokens (may be multilingual). Flatten across language keys
    # so callers receive provider->code->flat list of tokens (backwards-compatible).
    pat = raw.get("provider_airport_alias_tokens") if isinstance(raw, dict) else None
    out_aliases: Dict[str, Dict[str, List[str]]] = {}
    if isinstance(pat, dict):
        for provider, codes in pat.items():
            provider_key = (provider or "").strip().lower()
            if not provider_key or not isinstance(codes, dict):
                continue
            out_aliases.setdefault(provider_key, {})
            for code, lang_map in codes.items():
                norm_code = _normalize_code(code)
                if not norm_code:
                    continue
                tokens: List[str] = []
                # If the value is a dict, expect language -> list
                if isinstance(lang_map, dict):
                    for lang_vals in lang_map.values():
                        if isinstance(lang_vals, list):
                            for t in lang_vals:
                                if isinstance(t, str) and t.strip() and t.strip() not in tokens:
                                    tokens.append(t.strip())
                # If the value is already a list, accept it
                elif isinstance(lang_map, list):
                    for t in lang_map:
                        if isinstance(t, str) and t.strip() and t.strip() not in tokens:
                            tokens.append(t.strip())
                # Ensure the code itself appears as a token
                if norm_code not in tokens:
                    tokens.append(norm_code)
                out_aliases[provider_key][norm_code] = tokens
    else:
        out_aliases = {}

    _BUILTIN_PROVIDER_AIRPORT_ALIAS_TOKENS = out_aliases


# NOTE: builtin provider data will be populated after normalization helpers are
# defined (see call below, just before load_shared_knowledge()).


def _normalize_code(value: Optional[str]) -> str:
    """Normalize airport/provider code strings."""
    if not isinstance(value, str):
        return ""
    return value.strip().upper()


def _normalize_aliases(payload: Any) -> Dict[str, List[str]]:
    """Normalize airport alias payload into code -> alias list."""
    out: Dict[str, List[str]] = {}
    if not isinstance(payload, dict):
        return out
    for code, aliases in payload.items():
        normalized_code = _normalize_code(code)
        if not normalized_code:
            continue
        unique: List[str] = []
        if isinstance(aliases, list):
            for alias in aliases:
                if not isinstance(alias, str):
                    continue
                token = alias.strip()
                if not token or token in unique:
                    continue
                unique.append(token)
        if normalized_code not in unique:
            unique.append(normalized_code)
        out[normalized_code] = unique
    return out


def _normalize_provider_maps(payload: Any) -> Dict[str, Dict[str, str]]:
    """Normalize provider airport-code rewrite maps."""
    out: Dict[str, Dict[str, str]] = {}
    if not isinstance(payload, dict):
        return out
    for provider, mapping in payload.items():
        provider_key = (provider or "").strip().lower()
        if not provider_key or not isinstance(mapping, dict):
            continue
        table: Dict[str, str] = {}
        for from_code, to_code in mapping.items():
            src = _normalize_code(from_code)
            dst = _normalize_code(to_code)
            if not src or not dst:
                continue
            table[src] = dst
        out[provider_key] = table
    return out


def _normalize_store(payload: Any) -> Dict[str, Any]:
    """Normalize on-disk shared knowledge store shape."""
    store = dict(_DEFAULT_STORE)
    if not isinstance(payload, dict):
        return store
    store["airport_aliases"] = _normalize_aliases(payload.get("airport_aliases"))
    store["provider_airport_code_map"] = _normalize_provider_maps(
        payload.get("provider_airport_code_map")
    )
    return store


def load_shared_knowledge(force_reload: bool = False) -> Dict[str, Any]:
    """Load shared knowledge JSON from disk with normalization."""
    global _CACHE
    # Populate builtin provider data (deferred until normalization helpers exist)
    try:
        # Idempotent: _load_builtin_provider_data will safely re-run if needed
        _load_builtin_provider_data()
    except Exception:
        pass
    if _CACHE and not force_reload:
        return json.loads(json.dumps(_CACHE))

    if not STORE_PATH.exists():
        _CACHE = dict(_DEFAULT_STORE)
        return json.loads(json.dumps(_CACHE))

    try:
        payload = json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    _CACHE = _normalize_store(payload)
    return json.loads(json.dumps(_CACHE))


def save_shared_knowledge(payload: Dict[str, Any]) -> None:
    """Persist one normalized shared knowledge payload to disk."""
    global _CACHE
    normalized = _normalize_store(payload)
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STORE_PATH.write_text(
        json.dumps(normalized, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    _CACHE = normalized


def get_airport_aliases(code: str) -> Set[str]:
    """Return shared aliases for one airport/metro code."""
    normalized = _normalize_code(code)
    if not normalized:
        return set()
    store = load_shared_knowledge()
    aliases = set(store.get("airport_aliases", {}).get(normalized, []))
    if normalized not in aliases:
        aliases.add(normalized)
    return aliases


def _provider_code_map(provider: str, *, store: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """Return merged provider code map with built-in defaults and store overrides."""
    provider_key = (provider or "").strip().lower()
    if not provider_key:
        return {}
    base = _normalize_provider_maps({provider_key: _BUILTIN_PROVIDER_AIRPORT_CODE_MAP.get(provider_key, {})})
    merged: Dict[str, str] = dict(base.get(provider_key, {}) or {})
    store_payload = store if isinstance(store, dict) else load_shared_knowledge()
    store_map = (
        store_payload.get("provider_airport_code_map", {}).get(provider_key, {}) or {}
    )
    normalized_store_map = _normalize_provider_maps({provider_key: store_map})
    merged.update(normalized_store_map.get(provider_key, {}) or {})
    return merged


def _provider_builtin_alias_tokens(provider: str, code: str) -> Set[str]:
    """Return deterministic provider-localized alias tokens for one code."""
    provider_key = (provider or "").strip().lower()
    normalized = _normalize_code(code)
    if not provider_key or not normalized:
        return set()
    provider_tokens = _BUILTIN_PROVIDER_AIRPORT_ALIAS_TOKENS.get(provider_key, {})
    raw_tokens = provider_tokens.get(normalized, [])
    out: Set[str] = set()
    for token in raw_tokens:
        if not isinstance(token, str):
            continue
        cleaned = token.strip()
        if cleaned:
            out.add(cleaned)
    return out


def get_airport_aliases_for_provider(code: str, provider: str) -> Set[str]:
    """Return airport aliases expanded with provider metro-code rewrites/peers."""
    normalized = _normalize_code(code)
    if not normalized:
        return set()
    store = load_shared_knowledge()
    provider_map = _provider_code_map(provider, store=store)

    variants: Set[str] = {normalized}
    mapped = _normalize_code(provider_map.get(normalized))
    if mapped:
        variants.add(mapped)

    # Include reverse mappings and same-metro peers (e.g., HND/NRT -> TYO, ITM/KIX -> OSA).
    for source_code, target_code in provider_map.items():
        src = _normalize_code(source_code)
        dst = _normalize_code(target_code)
        if not src or not dst:
            continue
        if dst == normalized:
            variants.add(src)
        if mapped and dst == mapped:
            variants.add(src)

    aliases: Set[str] = set()
    for variant in variants:
        aliases.update(get_airport_aliases(variant))
        aliases.update(_provider_builtin_alias_tokens(provider, variant))
    if normalized not in aliases:
        aliases.add(normalized)
    return aliases


def upsert_airport_aliases(code: str, aliases: List[str]) -> None:
    """Upsert shared aliases for one airport/metro code."""
    normalized_code = _normalize_code(code)
    if not normalized_code:
        return
    store = load_shared_knowledge()
    updated = _normalize_aliases({normalized_code: aliases}).get(normalized_code, [])
    if not updated:
        return
    store.setdefault("airport_aliases", {})[normalized_code] = updated
    save_shared_knowledge(store)


def map_airport_code_for_provider(code: str, provider: str) -> str:
    """Map airport code to provider-preferred code when available."""
    normalized_code = _normalize_code(code)
    if not normalized_code:
        return ""
    provider_key = (provider or "").strip().lower()
    if not provider_key:
        return normalized_code
    provider_map = _provider_code_map(provider_key)
    mapped = _normalize_code(provider_map.get(normalized_code))
    return mapped or normalized_code


def upsert_provider_airport_code_map(
    provider: str,
    source_code: str,
    target_code: str,
) -> None:
    """Upsert one provider-specific airport-code rewrite rule."""
    provider_key = (provider or "").strip().lower()
    src = _normalize_code(source_code)
    dst = _normalize_code(target_code)
    if not provider_key or not src or not dst:
        return
    store = load_shared_knowledge()
    provider_map = store.setdefault("provider_airport_code_map", {}).setdefault(
        provider_key, {}
    )
    provider_map[src] = dst
    save_shared_knowledge(store)
