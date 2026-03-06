"""Debug mode run episode manager: creates self-contained run folders with logs + artifacts.

When debug mode is enabled, creates:
  storage/runs/<run_id>/
    manifest.json
    run.log
    events.jsonl
    final_summary.json
    scenario_last_error.json
    artifacts/
      initial.html
      last.html
      pre_action_*.png
      post_action_*.png
      dom_diff_*.json
"""

import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import threading
import atexit
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from secrets import token_hex

from utils.run_paths import ensure_run_dirs, write_latest_run_id

# Thread-local storage for current RunEpisode instance
_current_episode_context = threading.local()


# ============================================================================
# Run ID generation
# ============================================================================

def generate_run_id() -> str:
    """Generate sortable run ID: YYYYMMDD_HHMMSS_<6hex>."""
    now = datetime.now()
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    random_suffix = token_hex(3)  # 6 hex chars
    return f"{timestamp}_{random_suffix}"


def ensure_run_id(run_id: Optional[str] = None) -> str:
    """Ensure run_id exists or create new one."""
    if run_id and run_id.strip():
        return run_id.strip()
    return generate_run_id()


def get_current_episode() -> Optional[Any]:  # Returns RunEpisode | None
    """Get currently active RunEpisode instance (thread-safe)."""
    return getattr(_current_episode_context, 'episode', None)


def emit_ui_driver_fallback_event(
    site_id: str,
    from_driver: str = "agent",
    to_driver: str = "legacy",
    reason: str = "bind_failed",
) -> None:
    """Emit a structured fallback event to the current episode.

    This should be called when an adapter fallback occurs (e.g., agent→legacy).
    If no RunEpisode is active, logs a warning but does not fail.

    Args:
        site_id: Site identifier (e.g., "google_flights")
        from_driver: Source driver mode (default "agent")
        to_driver: Target driver mode (default "legacy")
        reason: Reason for fallback (default "bind_failed")
    """
    episode = get_current_episode()
    if episode is None:
        # No active episode, use logging instead
        logging.getLogger(__name__).debug(
            "ui_driver.fallback_event_no_episode site=%s from=%s to=%s reason=%s",
            site_id,
            from_driver,
            to_driver,
            reason,
        )
        return

    # Emit structured event to events.jsonl
    try:
        episode.emit_event(
            {
                "event_type": "ui_driver_fallback",
                "site_id": site_id,
                "from_driver": from_driver,
                "to_driver": to_driver,
                "reason": reason,
            }
        )
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "Failed to emit fallback event: %s", exc
        )


# ============================================================================
# Git metadata extraction
# ============================================================================

def get_git_metadata() -> Dict[str, Any]:
    """Extract git commit, branch, and dirty flag. Fail gracefully if not a git repo."""
    metadata = {
        "commit": None,
        "branch": None,
        "dirty": False,
        "available": False,
    }

    try:
        # Get commit SHA
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if result.returncode == 0:
            metadata["commit"] = result.stdout.strip()
            metadata["available"] = True

        # Get branch name
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if result.returncode == 0:
            metadata["branch"] = result.stdout.strip()

        # Check if repo is dirty
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if result.returncode == 0:
            metadata["dirty"] = bool(result.stdout.strip())

    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        # Not a git repo or git not available
        pass

    return metadata


# ============================================================================
# Run folder management
# ============================================================================

class RunEpisode:
    """Manages a debug run episode folder with logs, events, and artifacts."""

    def __init__(
        self,
        run_id: str,
        base_dir: Path = Path("storage/runs"),
        config_snapshot: Optional[Dict[str, Any]] = None,
        services: Optional[List[str]] = None,
        models_config: Optional[Dict[str, Any]] = None,
    ):
        """Initialize run episode.

        Args:
            run_id: Unique run identifier
            base_dir: Base directory for run folders
            config_snapshot: Runtime config snapshot
            services: List of services targeted
            models_config: LLM models configuration
        """
        self.run_id = run_id
        self.base_dir = Path(base_dir)
        dirs = ensure_run_dirs(run_id, base_dir=self.base_dir)
        self.run_dir = dirs["run_dir"]
        self.artifacts_dir = dirs["artifacts_dir"]
        self.episode_dir = dirs["episode_dir"]
        self.started_at = datetime.now().isoformat()
        self.finished_at: Optional[str] = None

        self.config_snapshot = config_snapshot or {}
        self.services = services or []
        self.models_config = models_config or {}

        # File paths
        self.manifest_path = self.run_dir / "manifest.json"
        self.log_path = self.run_dir / "run.log"
        self.events_path = self.run_dir / "events.jsonl"
        self.summary_path = self.run_dir / "final_summary.json"
        self.error_path = self.run_dir / "scenario_last_error.json"

        # Write canonical latest-run pointer (single source of truth).
        try:
            write_latest_run_id(self.run_id, storage_root=self.base_dir.parent)
        except Exception:
            logging.getLogger(__name__).debug("Failed to write latest_run_id pointer", exc_info=True)

        # Set up logging
        self._setup_logging()

        # Event counter
        self._event_counter = 0
        self._finalized = False
        atexit.register(self._finalize_on_exit)

    def _setup_logging(self) -> None:
        """Configure file logging for this run."""
        # Create file handler for run.log
        file_handler = logging.FileHandler(self.log_path, mode='a')
        file_handler.setLevel(logging.DEBUG)

        # Same format as console
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        file_handler.setFormatter(formatter)

        # Add to root logger
        root_logger = logging.getLogger()
        root_logger.addHandler(file_handler)

        # Store handler for cleanup
        self._file_handler = file_handler

    def emit_event(self, event_data: Dict[str, Any]) -> None:
        """Append one JSON event to events.jsonl.

        Args:
            event_data: Event dict with at least 'event' key
        """
        # Add standard fields
        event_data.setdefault("ts", datetime.now().isoformat())
        event_data.setdefault("run_id", self.run_id)
        event_data.setdefault("seq", self._event_counter)
        self._event_counter += 1

        # Append to JSONL file
        with open(self.events_path, "a") as f:
            f.write(json.dumps(event_data) + "\n")

    def save_artifact(
        self,
        content: Any,
        filename: str,
        binary: bool = False,
    ) -> Path:
        """Save artifact to artifacts/ folder.

        Args:
            content: Content to save (str, bytes, or dict for JSON)
            filename: Artifact filename
            binary: Whether to write in binary mode

        Returns:
            Path to saved artifact
        """
        artifact_path = self.artifacts_dir / filename

        if isinstance(content, dict):
            # Save as JSON
            with open(artifact_path, "w") as f:
                json.dump(content, f, indent=2)
        elif binary or isinstance(content, bytes):
            # Binary mode
            with open(artifact_path, "wb") as f:
                f.write(content if isinstance(content, bytes) else content.encode())
        else:
            # Text mode
            with open(artifact_path, "w") as f:
                f.write(str(content))

        return artifact_path

    def save_manifest(self) -> None:
        """Write manifest.json with run metadata."""
        git_meta = get_git_metadata()

        manifest = {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "git": git_meta,
            "platform": {
                "python_version": sys.version,
                "platform": platform.platform(),
                "system": platform.system(),
                "machine": platform.machine(),
            },
            "config": self.config_snapshot,
            "services": self.services,
            "models": self.models_config,
        }

        with open(self.manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)

    def copy_error_file(self, source_path: Path) -> None:
        """Copy scenario_last_error.json into run folder if it exists.

        Args:
            source_path: Path to source error file
        """
        if source_path.exists():
            shutil.copy2(source_path, self.error_path)

    def copy_summary_file(self, source_path: Path) -> None:
        """Copy final summary into run folder if it exists.

        Args:
            source_path: Path to source summary file
        """
        if source_path.exists():
            shutil.copy2(source_path, self.summary_path)

    def finalize(self, finished_at: Optional[str] = None) -> None:
        """Finalize run episode: update manifest with finish time.

        Args:
            finished_at: Optional finish timestamp (defaults to now)
        """
        if self._finalized:
            return
        self._finalized = True
        self.finished_at = finished_at or datetime.now().isoformat()
        self.save_manifest()

        # Remove file handler from root logger
        if hasattr(self, "_file_handler"):
            root_logger = logging.getLogger()
            root_logger.removeHandler(self._file_handler)
            self._file_handler.close()

    def _finalize_on_exit(self) -> None:
        """Best-effort manifest finalization for non-graceful process exits."""
        try:
            self.finalize()
        except Exception:
            return

    def __enter__(self):
        """Context manager entry. Sets this episode as the current context."""
        _current_episode_context.episode = self
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit. Clears the current context and finalizes."""
        _current_episode_context.episode = None
        self.finalize()
        return False


# ============================================================================
# Cleanup utilities
# ============================================================================

def cleanup_old_runs(base_dir: Path, keep_last: int) -> int:
    """Delete old run folders, keeping only the most recent N.

    Args:
        base_dir: Base directory containing run folders
        keep_last: Number of most recent runs to keep

    Returns:
        Number of folders deleted
    """
    if keep_last <= 0:
        return 0

    base_dir = Path(base_dir)
    if not base_dir.exists():
        return 0

    # Find all run folders (directories only)
    run_folders = sorted(
        [d for d in base_dir.iterdir() if d.is_dir()],
        key=lambda p: p.name,
        reverse=True,  # Newest first (sorted by name YYYYMMDD_HHMMSS_*)
    )

    # Delete older ones
    deleted = 0
    for folder in run_folders[keep_last:]:
        try:
            shutil.rmtree(folder)
            deleted += 1
        except Exception as e:
            logging.warning(f"Failed to delete old run folder {folder}: {e}")

    return deleted


# ============================================================================
# Artifact capture helpers
# ============================================================================

def should_capture_artifacts(
    scenario_result: Optional[Dict[str, Any]] = None,
) -> bool:
    """Determine if artifacts should be captured based on scenario outcome.

    Args:
        scenario_result: Scenario result dict with 'ready', 'price', 'reason' keys

    Returns:
        True if artifacts should be captured
    """
    if not scenario_result:
        return True  # Capture if no result provided

    # Capture on failure indicators
    if not scenario_result.get("ready", False):
        return True

    if scenario_result.get("price") is None:
        return True

    # Capture if reason indicates failure
    reason = scenario_result.get("reason", "")
    failure_keywords = [
        "error",
        "timeout",
        "budget",
        "selector_not_found",
        "calendar_not_open",
        "month_nav_exhausted",
    ]
    if any(kw in reason.lower() for kw in failure_keywords):
        return True

    return False
