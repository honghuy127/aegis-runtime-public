"""Canonical runtime artifact paths (run_id-centric)."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional


DEFAULT_RUNS_BASE_DIR = Path("storage/runs")
DEFAULT_STORAGE_ROOT = Path("storage")


def normalize_run_id(run_id: Optional[str]) -> str:
    """Return a validated run_id or raise for missing/placeholder values."""
    value = str(run_id or "").strip()
    if not value or value.lower() == "unknown":
        raise ValueError("missing_run_id")
    return value


def get_run_dir(run_id: str, *, base_dir: Path = DEFAULT_RUNS_BASE_DIR) -> Path:
    """Return canonical run directory for one run_id."""
    return Path(base_dir) / normalize_run_id(run_id)


def get_artifacts_dir(run_id: str, *, base_dir: Path = DEFAULT_RUNS_BASE_DIR) -> Path:
    """Return canonical artifacts directory for one run_id."""
    return get_run_dir(run_id, base_dir=base_dir) / "artifacts"


def get_episode_dir(run_id: str, *, base_dir: Path = DEFAULT_RUNS_BASE_DIR) -> Path:
    """Return canonical episode subdirectory for one run_id."""
    return get_run_dir(run_id, base_dir=base_dir) / "episode"


def ensure_run_dirs(run_id: str, *, base_dir: Path = DEFAULT_RUNS_BASE_DIR) -> Dict[str, Path]:
    """Create canonical run/artifacts/episode directories and return them."""
    run_dir = get_run_dir(run_id, base_dir=base_dir)
    artifacts_dir = run_dir / "artifacts"
    episode_dir = run_dir / "episode"
    run_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    episode_dir.mkdir(parents=True, exist_ok=True)
    return {
        "run_dir": run_dir,
        "artifacts_dir": artifacts_dir,
        "episode_dir": episode_dir,
    }


def latest_run_id_path(*, storage_root: Path = DEFAULT_STORAGE_ROOT) -> Path:
    """Return the single canonical latest-run pointer file path."""
    return Path(storage_root) / "latest_run_id.txt"


def write_latest_run_id(
    run_id: str,
    *,
    storage_root: Path = DEFAULT_STORAGE_ROOT,
) -> Path:
    """Persist the latest run_id pointer to storage/latest_run_id.txt."""
    value = normalize_run_id(run_id)
    path = latest_run_id_path(storage_root=storage_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value + "\n", encoding="utf-8")
    return path


def read_latest_run_id(*, storage_root: Path = DEFAULT_STORAGE_ROOT) -> str:
    """Read storage/latest_run_id.txt and return run_id, or empty string."""
    path = latest_run_id_path(storage_root=storage_root)
    if not path.exists():
        return ""
    try:
        return normalize_run_id(path.read_text(encoding="utf-8").strip())
    except Exception:
        return ""
