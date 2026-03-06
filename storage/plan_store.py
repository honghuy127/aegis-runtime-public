"""Persistent storage for scenario action plans keyed by site identifier.

Committed seed plans live in ``storage/plan_store.json``.
Local runtime updates are written to ``storage/plan_store.local.json`` and overlay
the seed at read time.
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

SEED_STORE_PATH = Path("storage/plan_store.json")
LOCAL_STORE_PATH = Path("storage/plan_store.local.json")
# Backward-compatible alias retained for tests/callers that monkeypatch STORE_PATH.
STORE_PATH = LOCAL_STORE_PATH

_TRANSIENT_NOTE_PATTERNS = [
    re.compile(r"verify\.route_core_observed_dest=\d+\b"),
    re.compile(r"^PhaseBDeterministicFallback:", re.IGNORECASE),
]


def _is_persistable_note(note: str) -> bool:
    """Return False for known transient/debug notes that should not persist."""
    text = str(note or "").strip()
    if not text:
        return False
    return not any(p.search(text) for p in _TRANSIENT_NOTE_PATTERNS)


def _load_store_file(path: Path):
    """Load one plan store file from disk."""
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def load_store():
    """Load the merged plan store (seed + local overlay) from disk."""
    seed = _load_store_file(SEED_STORE_PATH)
    local = _load_store_file(STORE_PATH)
    if not seed:
        return local
    if not local:
        return seed
    merged = dict(seed)
    merged.update(local)
    return merged


def load_local_store():
    """Load only the local runtime plan store overlay."""
    return _load_store_file(STORE_PATH)


def save_store(store):
    """Persist the local runtime plan store overlay to disk."""
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STORE_PATH.write_text(json.dumps(store, indent=2, ensure_ascii=False), encoding="utf-8")


def _normalize_notes(raw: Any) -> List[str]:
    """Normalize optional planner notes as a short list of non-empty strings."""
    notes: List[str] = []
    if isinstance(raw, str):
        text = raw.strip()
        if text and _is_persistable_note(text):
            notes.append(text[:180])
    elif isinstance(raw, list):
        for item in raw:
            if not isinstance(item, str):
                continue
            text = item.strip()
            if not text:
                continue
            if not _is_persistable_note(text):
                continue
            notes.append(text[:180])
            if len(notes) >= 8:
                break
    deduped: List[str] = []
    seen = set()
    for note in notes:
        if note in seen:
            continue
        seen.add(note)
        deduped.append(note)
    return deduped


def _normalize_plan_entry(entry: Any) -> Dict[str, Any]:
    """Normalize one plan store entry into {steps, notes} shape."""
    steps: Optional[List[Dict[str, Any]]] = None
    notes: List[str] = []

    if isinstance(entry, list):
        steps = entry
    elif isinstance(entry, dict):
        for key in ("steps", "plan", "actions"):
            candidate = entry.get(key)
            if isinstance(candidate, list):
                steps = candidate
                break
        notes = _normalize_notes(entry.get("notes"))

    if not isinstance(steps, list):
        steps = None
    return {"steps": steps, "notes": notes}


def get_plan(site_key):
    """Return one saved action plan by site key, if present."""
    store = load_store()
    entry = _normalize_plan_entry(store.get(site_key))
    return entry.get("steps")


def get_plan_notes(site_key) -> List[str]:
    """Return short planner notes associated with one saved plan."""
    store = load_store()
    entry = _normalize_plan_entry(store.get(site_key))
    return entry.get("notes", [])


def save_plan(site_key, plan, *, notes=None):
    """Upsert one action plan under a site key and persist the store."""
    store = load_local_store()
    normalized_notes = _normalize_notes(notes)
    if normalized_notes:
        store[site_key] = {"steps": plan, "notes": normalized_notes}
    else:
        store[site_key] = {"steps": plan}
    save_store(store)
