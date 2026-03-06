from __future__ import annotations

from pathlib import Path
from typing import Optional

from utils.run_paths import get_artifacts_dir, normalize_run_id


def snapshot_image_path(site_key: str, stage: str = "last", *, run_id: str) -> Path:
    """Return deterministic debug screenshot path for one site/stage."""
    safe_site = (site_key or "unknown").strip().lower() or "unknown"
    safe_stage = (stage or "last").strip().lower() or "last"
    return get_artifacts_dir(normalize_run_id(run_id)) / f"scenario_{safe_site}_{safe_stage}.png"


def planner_snapshot_path(site_key: str, stages: Optional[list[str]] = None, *, run_id: str) -> str:
    """Resolve most relevant screenshot path for planner multimodal assist."""
    order = list(stages or ["attempt_error", "last", "initial"])
    for stage in order:
        candidate = snapshot_image_path(site_key, stage, run_id=run_id)
        if candidate.exists():
            return str(candidate)
    return ""
