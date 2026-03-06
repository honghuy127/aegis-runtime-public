#!/usr/bin/env python3
"""
Repo Cleanup Utility — flight-price-watcher-agent

Safe, auditable cleanup script for removing tracked junk and organizing legacy files.
Prioritizes safety: requires clean working tree, offers --dry-run by default, tracks all changes.

Usage:
  python -m utils.repo_cleanup scan              # List candidates (default)
  python -m utils.repo_cleanup scan --category pycache
  python -m utils.repo_cleanup apply --risk LOW  # Remove LOW-risk junk only
  python -m utils.repo_cleanup apply --risk LOW --dry-run
  python -m utils.repo_cleanup prune-debug --days 7
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ============================================================================
# CONFIGURATION
# ============================================================================

PROTECTED_DIRS = {
    'docs/kb',      # Authoritative documentation
    'core',         # Runtime code
    'llm',          # LLM models
    'storage',      # State storage
    'utils',        # Utilities
    'configs',      # Configuration
}

PROTECTED_FILES = {
    'main.py',
    'pytest.ini',
    'requirements.txt',
    'AGENTS.md',
    'README.md',
    'SECURITY.md',
    'LICENSE',
    'debug_mode_examples.sh',
}

# Risk categories
CLEANUP_RULES = {
    'pycache': {
        'risk': 'LOW',
        'pattern': '__pycache__',
        'action': 'remove_from_git',
        'description': 'Python bytecode cache directories',
    },
    'pyc': {
        'risk': 'LOW',
        'pattern': '*.pyc',
        'action': 'remove_from_git',
        'description': 'Python compiled bytecode files',
    },
    'debug_dumps': {
        'risk': 'LOW',
        'pattern': 'storage/debug/*',
        'action': 'remove_from_git',
        'description': 'Local debug snapshot dumps (git-tracked only)',
    },
    'debug_html': {
        'risk': 'LOW',
        'pattern': 'storage/debug_html/*',
        'action': 'remove_from_git_except_gitkeep',
        'description': 'Debug HTML artifacts (git-tracked only, except .gitkeep)',
    },
}

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================


def run_cmd(cmd: List[str], check=True, capture=False) -> Tuple[int, str]:
    """Execute shell command with controlled output."""
    try:
        if capture:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
            return result.returncode, result.stdout + result.stderr
        else:
            result = subprocess.run(cmd, check=check)
            return result.returncode, ""
    except Exception as e:
        return 1, str(e)


def is_git_repo() -> bool:
    """Check if we're in a git repo."""
    code, _ = run_cmd(['git', 'rev-parse', '--git-dir'], check=False)
    return code == 0


def get_git_status() -> str:
    """Get porcelain git status."""
    code, out = run_cmd(['git', 'status', '--porcelain'], capture=True)
    return out.strip()


def get_tracked_files() -> List[str]:
    """Get all files tracked by git."""
    code, out = run_cmd(['git', 'ls-files'], capture=True)
    return out.strip().split('\n') if out.strip() else []


def get_git_root() -> Path:
    """Get git repository root."""
    code, out = run_cmd(['git', 'rev-parse', '--show-toplevel'], capture=True)
    return Path(out.strip()) if code == 0 else Path.cwd()


def file_size_mb(path: Path) -> float:
    """Get file size in MB."""
    try:
        return path.stat().st_size / (1024 * 1024)
    except:
        return 0.0


def age_days(path: Path) -> float:
    """Get file age in days."""
    try:
        mtime = path.stat().st_mtime
        age_seconds = (datetime.now().timestamp() - mtime)
        return age_seconds / 86400
    except:
        return 0.0


# ============================================================================
# SCANNING
# ============================================================================


def scan_pycache_artifacts() -> List[Dict]:
    """Find tracked __pycache__ and .pyc files."""
    tracked = get_tracked_files()
    candidates = []

    for f in tracked:
        if '__pycache__' in f or f.endswith('.pyc'):
            root = get_git_root()
            path = root / f
            candidates.append({
                'file': f,
                'reason': 'build_artifact',
                'category': 'pycache' if '__pycache__' in f else 'pyc',
                'size_mb': file_size_mb(path),
                'risk': 'LOW',
                'action': 'remove_from_git',
            })

    return candidates


def scan_debug_artifacts() -> List[Dict]:
    """Find tracked debug dumps in storage/debug* subdirectories."""
    tracked = get_tracked_files()
    candidates = []

    for f in tracked:
        if f.startswith('storage/debug'):
            # Exclude .gitkeep (structural marker)
            if f.endswith('.gitkeep'):
                continue

            root = get_git_root()
            path = root / f
            candidates.append({
                'file': f,
                'reason': 'debug_artifact',
                'category': 'debug_dump' if 'debug/' in f and '/' in f else 'debug_html',
                'size_mb': file_size_mb(path),
                'risk': 'LOW',
                'action': 'remove_from_git',
            })

    return candidates


def scan_orphan_scripts() -> List[Dict]:
    """Find root-level scripts that may be orphaned."""
    root = get_git_root()
    candidates = []

    # Check *.sh and lone *.py in root
    for pattern in ['*.sh', '*.py']:
        for path in root.glob(pattern):
            if path.name in PROTECTED_FILES:
                continue

            # Check for references
            code, refs = run_cmd(['grep', '-r', path.name, '.', '--include=*.md', '--include=*.py'],
                                  capture=True, check=False)
            ref_count = len([l for l in refs.split('\n') if l.strip()])

            candidates.append({
                'file': path.name,
                'reason': 'orphan_script',
                'category': 'script',
                'size_mb': file_size_mb(path),
                'risk': 'MEDIUM' if ref_count == 0 else 'MEDIUM',  # Err on side of caution
                'references': ref_count,
                'action': 'review',
            })

    return candidates


def scan_large_files(threshold_mb: float = 50.0) -> List[Dict]:
    """Find large tracked files (informational)."""
    tracked = get_tracked_files()
    root = get_git_root()
    candidates = []

    for f in tracked:
        path = root / f
        size = file_size_mb(path)
        if size >= threshold_mb:
            candidates.append({
                'file': f,
                'reason': 'large_file',
                'category': 'large',
                'size_mb': f"{size:.1f}",
                'risk': 'INFORMATIONAL',
                'action': 'monitor',
            })

    return candidates


def scan_all() -> Dict[str, List[Dict]]:
    """Run all scanning operations."""
    return {
        'pycache': scan_pycache_artifacts(),
        'debug': scan_debug_artifacts(),
        'scripts': scan_orphan_scripts(),
        'large_files': scan_large_files(),
    }


# ============================================================================
# REPORTING
# ============================================================================


def print_scan_report(results: Dict[str, List[Dict]]):
    """Print scan results in human-readable format."""
    print("\n" + "=" * 80)
    print("REPO CLEANUP SCAN REPORT")
    print("=" * 80)

    total = sum(len(v) for v in results.values())
    print(f"\n📊 Total candidates: {total}\n")

    # Group by risk level
    by_risk = {}
    for category, items in results.items():
        for item in items:
            risk = item.get('risk', 'UNKNOWN')
            if risk not in by_risk:
                by_risk[risk] = []
            by_risk[risk].append((category, item))

    # Print by risk
    for risk_level in ['LOW', 'MEDIUM', 'HIGH', 'INFORMATIONAL']:
        if risk_level not in by_risk:
            continue

        items = by_risk[risk_level]
        print(f"\n{'🔴' if risk_level == 'HIGH' else '🟡' if risk_level == 'MEDIUM' else '🟢'} {risk_level} RISK ({len(items)} candidates)")
        print("-" * 80)

        for category, item in items:
            size_str = f"{item.get('size_mb', 0.0):.2f} MB" if isinstance(item.get('size_mb'), float) else str(item.get('size_mb', ''))
            refs = f" | {item.get('references')} refs" if 'references' in item else ""
            print(f"  • {item['file']:50s} | {category:15s} | {size_str:12s}{refs}")

    print("\n" + "=" * 80)
    print("To apply cleanup, run: python -m utils.repo_cleanup apply --risk LOW")
    print("=" * 80 + "\n")


# ============================================================================
# CLEANUP OPERATIONS
# ============================================================================


def apply_cleanup(risk_level: str, dry_run: bool = True) -> bool:
    """Apply cleanup for specified risk level."""
    if not is_git_repo():
        print("❌ Not in a git repository")
        return False

    status = get_git_status()
    if status and not dry_run:
        print("❌ Git working tree is dirty. Commit or stash changes before cleanup.")
        print("   Run with --dry-run to preview changes")
        return False

    print(f"\n{'[DRY RUN]' if dry_run else '[APPLYING]'} Cleanup for {risk_level} risk level\n")

    results = scan_all()
    to_remove = []

    for category, items in results.items():
        for item in items:
            if item.get('risk') == risk_level:
                to_remove.append(item['file'])

    if not to_remove:
        print(f"No {risk_level}-risk candidates found")
        return True

    print(f"Will remove from git: {len(to_remove)} files\n")
    for f in to_remove:
        print(f"  git rm --cached {f}")

    if dry_run:
        print("\n[DRY RUN] — no changes applied. Run without --dry-run to apply.")
        return True

    # Apply removal
    print("\n✓ Applying removal...")
    for f in to_remove:
        run_cmd(['git', 'rm', '--cached', f])

    print(f"✓ Removed {len(to_remove)} files from git index")
    print("✓ Update .gitignore and run: git commit")
    return True


def prune_debug_local(days: int = 7, dry_run: bool = True):
    """Delete local (non-tracked) debug files older than N days."""
    root = get_git_root()
    debug_dirs = [root / 'storage/debug', root / 'storage/debug_html']

    removed = 0
    for debug_dir in debug_dirs:
        if not debug_dir.exists():
            continue

        threshold = datetime.now() - timedelta(days=days)
        for fpath in debug_dir.rglob('*'):
            if fpath.is_dir():
                continue
            if fpath.name == '.gitkeep':
                continue

            mtime = datetime.fromtimestamp(fpath.stat().st_mtime)
            if mtime < threshold:
                action = "DELETE" if not dry_run else "would DELETE"
                print(f"  {action:10s} {fpath.relative_to(root):60s} (age: {(datetime.now() - mtime).days}d)")

                if not dry_run:
                    fpath.unlink()
                    removed += 1

    if dry_run:
        print(f"\n[DRY RUN] Would remove {removed} debug files.")
    else:
        print(f"\n✓ Removed {removed} local debug files.")


# ============================================================================
# MAIN
# ============================================================================


def main():
    parser = argparse.ArgumentParser(
        description='Safe cleanup utility for repo junk and legacy artifacts'
    )
    subparsers = parser.add_subparsers(dest='command', help='Command to run')

    # Scan
    scan_parser = subparsers.add_parser('scan', help='Scan for cleanup candidates')
    scan_parser.add_argument('--category', choices=['pycache', 'debug', 'scripts', 'large_files'],
                             help='Filter by category')

    # Apply
    apply_parser = subparsers.add_parser('apply', help='Apply cleanup')
    apply_parser.add_argument('--risk', choices=['LOW', 'MEDIUM', 'HIGH'],
                              default='LOW', help='Risk level to cleanup')
    apply_parser.add_argument('--dry-run', action='store_true', default=True,
                              help='Preview without applying (default)')
    apply_parser.add_argument('--force', action='store_true',
                              help='Apply even with dirty working tree')

    # Prune debug
    prune_parser = subparsers.add_parser('prune-debug', help='Delete old local debug files')
    prune_parser.add_argument('--days', type=int, default=7,
                              help='Age threshold in days (default: 7)')
    prune_parser.add_argument('--dry-run', action='store_true', default=True,
                              help='Preview without deleting (default)')

    args = parser.parse_args()

    if not args.command:
        # Default: scan
        args.command = 'scan'

    if args.command == 'scan':
        results = scan_all()
        if args.category:
            results = {args.category: results.get(args.category, [])}
        print_scan_report(results)

    elif args.command == 'apply':
        force = getattr(args, 'force', False)
        dry_run = getattr(args, 'dry_run', True)
        if force and not dry_run:
            os.environ['SKIP_DIRTY_CHECK'] = '1'
        apply_cleanup(args.risk, dry_run=dry_run)

    elif args.command == 'prune-debug':
        dry_run = getattr(args, 'dry_run', True)
        prune_debug_local(days=args.days, dry_run=dry_run)


if __name__ == '__main__':
    main()
