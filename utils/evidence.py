"""Compact per-service evidence checkpoint writer with atomic updates."""

from __future__ import annotations

import json
import re
from datetime import datetime, UTC
from pathlib import Path
from typing import Any, Dict, Optional

from utils.run_paths import get_artifacts_dir, normalize_run_id
DEFAULT_EVIDENCE_DIR = None
_MAX_DEPTH = 4
_MAX_LIST_ITEMS = 24
_MAX_STRING_CHARS = 320


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _safe_token(value: str, fallback: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "").strip())
    return text or fallback


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(path)


def _compact_payload(value: Any, *, depth: int = 0) -> Any:
    if depth > _MAX_DEPTH:
        return "[truncated_depth]"
    if isinstance(value, str):
        if len(value) <= _MAX_STRING_CHARS:
            return value
        return f"{value[:_MAX_STRING_CHARS]}...[truncated:{len(value)}]"
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [
            _compact_payload(item, depth=depth + 1)
            for item in value[:_MAX_LIST_ITEMS]
        ]
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for key, item in list(value.items())[:80]:
            out[str(key)] = _compact_payload(item, depth=depth + 1)
        return out
    return str(value)


def evidence_path_for(*, run_id: str, service: str, base_dir: Optional[Path] = None) -> Path:
    safe_run = _safe_token(normalize_run_id(run_id), "run")
    safe_service = _safe_token(service, "unknown")
    if base_dir is not None:
        # Preserve explicit override semantics used by existing tests/tools.
        return Path(base_dir) / f"{safe_run}_{safe_service}_state.json"
    directory = get_artifacts_dir(safe_run)
    return directory / f"evidence_{safe_service}_state.json"


def write_service_evidence_checkpoint(
    *,
    run_id: str,
    service: str,
    checkpoint: str,
    payload: Dict[str, Any],
    enabled: bool = True,
    base_dir: Optional[Path] = None,
) -> Optional[Path]:
    """Merge one compact checkpoint into service evidence JSON."""
    if not enabled:
        return None
    if not isinstance(payload, dict):
        return None

    path = evidence_path_for(run_id=run_id, service=service, base_dir=base_dir)
    existing: Dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing = dict(loaded)
        except Exception:
            existing = {}

    checkpoints = existing.get("checkpoints")
    if not isinstance(checkpoints, dict):
        checkpoints = {}

    name = _safe_token(checkpoint, "checkpoint")
    checkpoints[name] = {
        "timestamp": _utc_now_iso(),
        "data": _compact_payload(payload),
    }

    merged = {
        "run_id": _safe_token(run_id, "run"),
        "service": _safe_token(service, "unknown"),
        "updated_at": _utc_now_iso(),
        "checkpoints": checkpoints,
    }
    _atomic_write_json(path, merged)
    return path
