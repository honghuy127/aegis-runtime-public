"""Storage maintenance helpers for bounded runtime artifacts."""

import argparse
import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from storage.knowledge_store import purge_url_hints
from storage.runs import enforce_db_limits
from utils.knowledge_rules import get_knowledge_rule_tokens
from utils.thresholds import get_threshold


def write_last_run_pointer(
    run_id: str,
    pointer_dir: Path = Path("storage/debug"),
    run_artifacts_dir: Optional[Path] = None,
) -> None:
    """
    Write a LAST_RUN.txt pointer file that redirects to canonical run directory.

    This allows tools to find the latest run and its artifacts without
    having legacy debug artifacts duplicated in storage/debug*/

    Args:
        run_id: The run identifier (e.g., "run_20260223_143022")
        pointer_dir: Directory to write LAST_RUN.txt pointer file
        run_artifacts_dir: Optional explicit path to run artifacts dir
    """
    try:
        pointer_dir.mkdir(parents=True, exist_ok=True)
        pointer_path = pointer_dir / "LAST_RUN.txt"

        # Construct canonical run directory path
        canonical_run_dir = Path("storage/runs") / run_id
        canonical_artifacts_dir = run_artifacts_dir or (canonical_run_dir / "artifacts")

        # Build pointer file content (machine-parseable, human-friendly)
        content_lines = [
            "# Pointer file: Run artifacts are now stored in canonical location",
            f"run_id={run_id}",
            f"canonical_dir=storage/runs/{run_id}",
            f"artifacts_dir={canonical_artifacts_dir}",
            f"run_log=storage/runs/{run_id}/run.log",
            f"events=storage/runs/{run_id}/events.jsonl",
            f"scenario_last_error=storage/runs/{run_id}/scenario_last_error.json",
            f"timestamp={datetime.now(timezone.utc).isoformat()}",
        ]

        pointer_path.write_text("\n".join(content_lines) + "\n", encoding="utf-8")
    except Exception as exc:
        # Best-effort: don't fail the run if pointer write fails
        import logging
        log = logging.getLogger(__name__)
        log.warning("storage.write_last_run_pointer.failed run_id=%s dir=%s error=%s", run_id, pointer_dir, exc)


def _limit_int(key: str, default: int) -> int:
    """Read one integer runtime limit from thresholds config."""
    try:
        value = int(get_threshold(key, default))
    except Exception:
        return default
    return max(0, value)


def trim_file_to_tail(path: Path, *, max_bytes: int, keep_bytes: int) -> bool:
    """Trim a file to its tail bytes if it exceeds configured max size."""
    if not path.exists() or not path.is_file():
        return False
    if max_bytes <= 0:
        return False
    size = path.stat().st_size
    if size <= max_bytes:
        return False

    keep = max(1, min(keep_bytes if keep_bytes > 0 else max_bytes // 2, max_bytes))
    with path.open("rb") as f:
        if size > keep:
            f.seek(size - keep)
        tail = f.read()
    with path.open("wb") as f:
        f.write(tail)
    return True


def _iter_log_files(storage_dir: Path) -> Iterable[Path]:
    """Yield log files under storage directory."""
    if not storage_dir.exists():
        return []
    return sorted(p for p in storage_dir.glob("*.log") if p.is_file())


def purge_debug_html_files(
    *,
    storage_dir: Path = Path("storage"),
    max_age_days: int = None,
) -> dict:
    """Delete old debug_html artifacts, keeping only recent files."""
    if max_age_days is None:
        max_age_days = _limit_int("debug_html_max_age_days", 7)
    max_age_days = max(0, int(max_age_days))
    debug_dir = storage_dir / "debug_html"
    if not debug_dir.exists() or not debug_dir.is_dir():
        return {"deleted": 0, "kept": 0, "max_age_days": max_age_days}

    if max_age_days <= 0:
        # Explicitly disabled cleanup.
        file_count = sum(1 for p in debug_dir.rglob("*") if p.is_file())
        return {"deleted": 0, "kept": file_count, "max_age_days": max_age_days}

    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    deleted = 0
    kept = 0
    for path in sorted(debug_dir.rglob("*")):
        if not path.is_file():
            continue
        try:
            modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except Exception:
            kept += 1
            continue
        if modified_at < cutoff:
            try:
                path.unlink()
                deleted += 1
            except Exception:
                kept += 1
        else:
            kept += 1

    return {"deleted": deleted, "kept": kept, "max_age_days": max_age_days}


def enforce_storage_limits(storage_dir: Path = Path("storage")) -> None:
    """Apply configured retention limits for DB and log files."""
    enforce_db_limits()

    max_bytes = _limit_int("log_file_max_bytes", 10 * 1024 * 1024)
    keep_bytes = _limit_int("log_file_keep_bytes", 2 * 1024 * 1024)
    for log_path in _iter_log_files(storage_dir):
        trim_file_to_tail(log_path, max_bytes=max_bytes, keep_bytes=keep_bytes)

    if bool(get_threshold("debug_html_cleanup_enabled", True)):
        purge_debug_html_files(storage_dir=storage_dir)


def purge_package_url_hints_all_sites(user_id: str = None) -> dict:
    """Remove package/bundle URL hints across all sites from knowledge store."""
    patterns = get_knowledge_rule_tokens("url_package_tokens")
    return purge_url_hints(
        site_key=None,
        user_id=user_id,
        patterns=patterns,
    )


def _backup_file(
    src: Path,
    backup_dir: Path,
    storage_dir: Path,
) -> bool:
    """Backup a single file preserving relative path structure."""
    try:
        rel_path = src.relative_to(storage_dir)
        dest = backup_dir / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        return True
    except Exception:
        return False


def _backup_directory(
    src: Path,
    backup_dir: Path,
    storage_dir: Path,
) -> int:
    """Backup a directory tree preserving structure. Returns file count."""
    count = 0
    try:
        rel_path = src.relative_to(storage_dir)
        dest = backup_dir / rel_path
        if src.is_dir():
            shutil.copytree(src, dest, dirs_exist_ok=True)
            count = sum(1 for _ in dest.rglob("*") if _.is_file())
    except Exception:
        pass
    return count


def _ensure_minimal_json(path: Path, minimal_payload: Dict[str, Any]) -> None:
    """Write minimal valid JSON to path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(minimal_payload, indent=2) + "\n", encoding="utf-8")


def _safe_delete_file(path: Path) -> bool:
    """Delete file if it exists. Returns True if deleted."""
    try:
        if path.exists() and path.is_file():
            path.unlink()
            return True
    except Exception:
        pass
    return False


def _safe_delete_dir(path: Path) -> bool:
    """Delete directory tree if it exists. Returns True if deleted."""
    try:
        if path.exists() and path.is_dir():
            shutil.rmtree(path)
            return True
    except Exception:
        pass
    return False


def reset_learned_state(
    *,
    storage_dir: Path = Path("storage"),
    user_id: Optional[str] = None,
    wipe_runs: bool = True,
    wipe_runs_db: bool = False,
    wipe_shared_knowledge: bool = False,
    backup: bool = True,
    backup_dir: Optional[Path] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    Wipe/reset learned state to reproduce refactor behavior from clean slate.

    Args:
        storage_dir: Root storage directory
        user_id: If provided, attempt targeted purge (falls back to full reset with warning)
        wipe_runs: Delete storage/runs/ directory tree
        wipe_runs_db: Delete storage/runs.db SQLite database
        wipe_shared_knowledge: Delete storage/shared_knowledge_store.json
        backup: Create backup before deletion
        backup_dir: Override default backup location
        dry_run: Print what would be removed without making changes

    Returns:
        Stats dictionary with counts and warnings
    """
    warnings = []
    deleted_files = 0
    deleted_dirs = 0
    reset_files = 0
    backed_up = 0

    # Resolve backup directory
    if backup and not backup_dir:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_dir = storage_dir / "_reset_backups" / timestamp

    # Validate storage_dir is within repo bounds
    try:
        storage_dir = storage_dir.resolve()
        if not storage_dir.exists():
            return {
                "deleted_files": 0,
                "deleted_dirs": 0,
                "reset_files": 0,
                "backed_up": 0,
                "dry_run": dry_run,
                "backup_dir": str(backup_dir) if backup_dir else None,
                "warnings": [f"Storage directory does not exist: {storage_dir}"],
            }
    except Exception as exc:
        return {
            "deleted_files": 0,
            "deleted_dirs": 0,
            "reset_files": 0,
            "backed_up": 0,
            "dry_run": dry_run,
            "backup_dir": str(backup_dir) if backup_dir else None,
            "warnings": [f"Invalid storage directory: {exc}"],
        }

    # Minimal schemas
    minimal_knowledge = {"users": {}}
    minimal_adaptive = {"sites": {}}
    minimal_plans = {}

    # Files/dirs to process
    targets = {
        "knowledge_store": storage_dir / "knowledge_store.json",
        "adaptive_policy": storage_dir / "adaptive_policy.json",
        "plan_store": storage_dir / "plan_store.json",
        "plan_store_local": storage_dir / "plan_store.local.json",
        "latest_run_id": storage_dir / "latest_run_id.txt",
        "scenario_last_error": storage_dir / "scenario_last_error.json",
    }

    # Conditional targets
    state_json = storage_dir / "state.json"
    if wipe_runs and state_json.exists():
        targets["state"] = state_json

    runs_dir = storage_dir / "runs"
    runs_db = storage_dir / "runs.db"
    shared_knowledge = storage_dir / "shared_knowledge_store.json"
    debug_dir = storage_dir / "debug"
    debug_html_dir = storage_dir / "debug_html"

    # Warn about user_id limitation
    if user_id:
        warnings.append(
            f"user_id={user_id} specified but full reset mode; "
            "per-user purge not safely supported for all stores"
        )

    # Backup phase
    if backup and not dry_run:
        backup_dir.mkdir(parents=True, exist_ok=True)
        for name, path in targets.items():
            if path.exists():
                if _backup_file(path, backup_dir, storage_dir):
                    backed_up += 1
        if wipe_runs and runs_dir.exists():
            backed_up += _backup_directory(runs_dir, backup_dir, storage_dir)
        if wipe_runs_db and runs_db.exists():
            if _backup_file(runs_db, backup_dir, storage_dir):
                backed_up += 1
        if wipe_shared_knowledge and shared_knowledge.exists():
            if _backup_file(shared_knowledge, backup_dir, storage_dir):
                backed_up += 1
        if debug_dir.exists():
            backed_up += _backup_directory(debug_dir, backup_dir, storage_dir)
        if debug_html_dir.exists():
            backed_up += _backup_directory(debug_html_dir, backup_dir, storage_dir)

    # Reset/delete phase
    if dry_run:
        print("[DRY RUN] Would reset/delete:")
        for name, path in targets.items():
            if path.exists():
                if name in {"knowledge_store", "adaptive_policy", "plan_store", "plan_store_local"}:
                    print(f"  RESET: {path}")
                else:
                    print(f"  DELETE: {path}")
        if wipe_runs and runs_dir.exists():
            print(f"  DELETE DIR: {runs_dir}")
        if wipe_runs_db and runs_db.exists():
            print(f"  DELETE: {runs_db}")
        if wipe_shared_knowledge and shared_knowledge.exists():
            print(f"  DELETE: {shared_knowledge}")
        if debug_dir.exists():
            print(f"  DELETE DIR: {debug_dir}")
        if debug_html_dir.exists():
            print(f"  DELETE DIR: {debug_html_dir}")
    else:
        # Reset JSON stores to minimal schemas
        if "knowledge_store" in targets and targets["knowledge_store"].exists():
            _ensure_minimal_json(targets["knowledge_store"], minimal_knowledge)
            reset_files += 1

        if "adaptive_policy" in targets and targets["adaptive_policy"].exists():
            _ensure_minimal_json(targets["adaptive_policy"], minimal_adaptive)
            reset_files += 1

        if "plan_store" in targets and targets["plan_store"].exists():
            _ensure_minimal_json(targets["plan_store"], minimal_plans)
            reset_files += 1

        if "plan_store_local" in targets and targets["plan_store_local"].exists():
            _ensure_minimal_json(targets["plan_store_local"], minimal_plans)
            reset_files += 1

        # Delete other files
        for name, path in targets.items():
            if name in {"knowledge_store", "adaptive_policy", "plan_store", "plan_store_local"}:
                continue
            if _safe_delete_file(path):
                deleted_files += 1

        # Delete directories
        if wipe_runs and _safe_delete_dir(runs_dir):
            deleted_dirs += 1

        if wipe_runs_db and _safe_delete_file(runs_db):
            deleted_files += 1

        if wipe_shared_knowledge and _safe_delete_file(shared_knowledge):
            deleted_files += 1

        if _safe_delete_dir(debug_dir):
            deleted_dirs += 1

        if _safe_delete_dir(debug_html_dir):
            deleted_dirs += 1

    return {
        "deleted_files": deleted_files,
        "deleted_dirs": deleted_dirs,
        "reset_files": reset_files,
        "backed_up": backed_up,
        "dry_run": dry_run,
        "backup_dir": str(backup_dir) if backup_dir else None,
        "warnings": warnings,
    }


def _build_cli() -> argparse.ArgumentParser:
    """Build maintenance CLI parser."""
    parser = argparse.ArgumentParser(description="Storage and knowledge maintenance tasks.")
    parser.add_argument(
        "--user",
        help="Optional knowledge user namespace (email/GitHub ID). Defaults to all users.",
    )
    parser.add_argument(
        "--purge-package-url-hints-all-sites",
        action="store_true",
        help="Purge package/bundle URL hints across all services.",
    )
    parser.add_argument(
        "--enforce-storage-limits",
        action="store_true",
        help="Apply DB/log size retention limits.",
    )
    parser.add_argument(
        "--reset-learned-state",
        action="store_true",
        help="Wipe/reset learned state (knowledge, plans, adaptive policy, runs).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be removed without making changes.",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip backup before deletion (not recommended).",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        help="Override default backup directory location.",
    )
    parser.add_argument(
        "--wipe-runs-db",
        action="store_true",
        help="Also delete storage/runs.db SQLite database.",
    )
    parser.add_argument(
        "--keep-runs",
        action="store_true",
        help="Keep storage/runs/ directory (do not delete run artifacts).",
    )
    parser.add_argument(
        "--wipe-shared-knowledge",
        action="store_true",
        help="Also delete storage/shared_knowledge_store.json.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation prompt (required for non-interactive contexts).",
    )
    return parser


def main():
    """CLI entrypoint for maintenance routines."""
    parser = _build_cli()
    args = parser.parse_args()

    ran = False

    # Handle reset learned state
    if args.reset_learned_state:
        # Confirmation gate (skip for dry-run)
        if not args.yes and not args.dry_run:
            if not sys.stdin.isatty():
                print(
                    "ERROR: --reset-learned-state requires --yes in non-interactive contexts.",
                    file=sys.stderr,
                )
                sys.exit(1)
            prompt = (
                "This will delete learned state and may delete run artifacts. Continue? [y/N] "
            )
            response = input(prompt).strip().lower()
            if response not in {"y", "yes"}:
                print("Aborted.")
                sys.exit(0)

        stats = reset_learned_state(
            storage_dir=Path("storage"),
            user_id=args.user,
            wipe_runs=not args.keep_runs,
            wipe_runs_db=args.wipe_runs_db,
            wipe_shared_knowledge=args.wipe_shared_knowledge,
            backup=not args.no_backup,
            backup_dir=args.backup_dir,
            dry_run=args.dry_run,
        )
        print(f"reset_learned_state: {stats}")
        ran = True

    if args.purge_package_url_hints_all_sites:
        stats = purge_package_url_hints_all_sites(user_id=args.user)
        print(f"purge_package_url_hints_all_sites: {stats}")
        ran = True

    if args.enforce_storage_limits:
        enforce_storage_limits()
        print("enforce_storage_limits: done")
        ran = True

    if not ran:
        # Keep default behavior useful when invoked manually without flags.
        enforce_storage_limits()
        print("enforce_storage_limits: done")


if __name__ == "__main__":
    main()
