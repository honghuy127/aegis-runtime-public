# Reset Learned State - Usage Guide

## Overview

The `storage.maintenance` module now includes a comprehensive reset capability to wipe/reset ALL learned/runtime state, enabling clean-slate reproduction of refactor regressions.

## Quick Start

### Dry Run (Preview Changes)
```bash
python -m storage.maintenance --reset-learned-state --dry-run
```

### Full Reset (Default Behavior)
```bash
python -m storage.maintenance --reset-learned-state --yes
```

### Reset Without Runs Directory
```bash
python -m storage.maintenance --reset-learned-state --yes --keep-runs
```

### Full Wipe Including Database
```bash
python -m storage.maintenance --reset-learned-state --yes --wipe-runs-db
```

## What Gets Reset/Deleted

### Default Behavior (--reset-learned-state)

**Files Reset to Minimal Schemas:**
- `storage/knowledge_store.json` → `{"users": {}}`
- `storage/adaptive_policy.json` → `{"sites": {}}`
- `storage/plan_store.json` → `{}`
- `storage/plan_store.local.json` → `{}`

**Files Deleted:**
- `storage/latest_run_id.txt`
- `storage/scenario_last_error.json`

**Directories Deleted:**
- `storage/runs/` (all run artifacts)
- `storage/debug/` (if exists)
- `storage/debug_html/` (if exists)

**Preserved by Default:**
- `storage/runs.db` (SQLite history)
- `storage/shared_knowledge_store.json` (airport aliases)

### Optional Deletions

**With `--wipe-runs-db`:**
- Deletes `storage/runs.db`

**With `--wipe-shared-knowledge`:**
- Deletes `storage/shared_knowledge_store.json`

**With `--keep-runs`:**
- Preserves `storage/runs/` directory

## CLI Flags

| Flag | Description |
|------|-------------|
| `--reset-learned-state` | Enable reset mode (required) |
| `--dry-run` | Preview changes without executing |
| `--yes` | Skip confirmation prompt (required for non-interactive) |
| `--no-backup` | Skip backup creation (not recommended) |
| `--backup-dir <path>` | Override default backup location |
| `--wipe-runs-db` | Also delete runs.db SQLite database |
| `--keep-runs` | Preserve storage/runs/ directory |
| `--wipe-shared-knowledge` | Delete shared_knowledge_store.json |
| `--user <id>` | User namespace (shows warning, full reset only) |

## Backup Behavior

### Default Backup (Recommended)
By default, all deleted/reset files are backed up to:
```
storage/_reset_backups/<timestamp>/
```

Example:
```
storage/_reset_backups/20260226_141028/
├── adaptive_policy.json
├── knowledge_store.json
├── plan_store.json
├── plan_store.local.json
├── latest_run_id.txt
├── scenario_last_error.json
├── debug/
└── runs/
    ├── 20260225_235913_875851/
    └── ...
```

### Custom Backup Location
```bash
python -m storage.maintenance --reset-learned-state --yes \
  --backup-dir /path/to/custom/backup
```

### Skip Backup (Not Recommended)
```bash
python -m storage.maintenance --reset-learned-state --yes --no-backup
```

## Confirmation Behavior

### Interactive Mode (Default)
Prompts: "This will delete learned state and may delete run artifacts. Continue? [y/N]"

### Non-Interactive Mode
Requires `--yes` flag or exits with error.

### Dry Run Mode
No confirmation required (safe preview mode).

## Return Statistics

The command returns a JSON statistics dictionary:

```json
{
  "deleted_files": 2,
  "deleted_dirs": 2,
  "reset_files": 4,
  "backed_up": 72,
  "dry_run": false,
  "backup_dir": "storage/_reset_backups/20260226_141028",
  "warnings": []
}
```

## Common Workflows

### Clean Slate for Regression Testing
```bash
# 1. Preview what will be deleted
python -m storage.maintenance --reset-learned-state --dry-run

# 2. Execute reset with backup
python -m storage.maintenance --reset-learned-state --yes

# 3. Run tests from clean state
pytest -q tests/test_knowledge_store.py tests/test_plan_store.py tests/test_adaptive_policy.py
```

### Keep Historical Data, Reset Learning
```bash
# Reset learned state but preserve run history and database
python -m storage.maintenance --reset-learned-state --yes --keep-runs
```

### Nuclear Option (Full Wipe)
```bash
# Delete everything including database and shared knowledge
python -m storage.maintenance --reset-learned-state --yes \
  --wipe-runs-db --wipe-shared-knowledge
```

### Iterative Development
```bash
# Reset, test, repeat without confirmation prompts
while true; do
  python -m storage.maintenance --reset-learned-state --yes --keep-runs
  pytest tests/test_my_feature.py || break
  read -p "Reset and retry? [y/N] " yn
  [[ $yn != [Yy]* ]] && break
done
```

## Safety Features

1. **Bounded to storage/ directory** - Will not delete outside workspace
2. **Backup by default** - Preserves all data before deletion
3. **Confirmation gate** - Requires explicit --yes in non-interactive mode
4. **Dry run mode** - Safe preview without modifications
5. **Idempotent** - Can be run multiple times safely
6. **Detailed stats** - Returns counts and warnings
7. **Minimal schemas** - Reset files are valid, not deleted

## Testing

### Verification Commands
```bash
# Test dry-run mode
python -m storage.maintenance --reset-learned-state --dry-run

# Test actual reset
python -m storage.maintenance --reset-learned-state --yes

# Verify minimal schemas
cat storage/knowledge_store.json
cat storage/adaptive_policy.json
cat storage/plan_store.json

# Run integration tests
pytest -q tests/test_knowledge_store.py tests/test_plan_store.py tests/test_adaptive_policy.py
```

### Expected Test Results
All targeted tests should pass after reset:
```
18 passed in 0.08s
```

## Implementation Details

### Minimal Schemas
- **knowledge_store.json**: `{"users": {}}`
- **adaptive_policy.json**: `{"sites": {}}`
- **plan_store.json**: `{}`

These schemas match the expected structure of their respective loader modules in `storage/`.

### File Operations
- Reset = Write minimal JSON schema
- Delete = Remove file (with backup)
- Delete Dir = Remove entire tree (with backup)

### Error Handling
- Missing files/directories are silently skipped
- Backup failures don't abort the operation
- Invalid paths return early with warnings

## Examples

### Example 1: Weekly Cleanup
```bash
#!/bin/bash
# weekly_cleanup.sh
python -m storage.maintenance --reset-learned-state --yes \
  --keep-runs \
  | tee storage/_reset_backups/weekly_$(date +%Y%m%d).log
```

### Example 2: CI/CD Integration
```bash
# .github/workflows/integration-tests.yml
- name: Reset learned state
  run: python -m storage.maintenance --reset-learned-state --yes --no-backup

- name: Run integration tests
  run: pytest tests/integration/
```

### Example 3: Local Development
```bash
# Makefile target
.PHONY: reset-state
reset-state:
	@python -m storage.maintenance --reset-learned-state --yes --keep-runs
	@echo "✓ Learned state reset (runs preserved)"
```

## Troubleshooting

### "ERROR: --reset-learned-state requires --yes in non-interactive contexts"
**Solution**: Add `--yes` flag when running in scripts or CI/CD.

### Permission Denied
**Solution**: Check file permissions on storage/ directory.

### Backup Directory Full
**Solution**: Use `--backup-dir` to specify alternate location or use `--no-backup`.

### Tests Fail After Reset
**Solution**: This is expected if tests depend on existing data. Reset creates minimal valid schemas.

## Related Commands

```bash
# Existing maintenance commands still available:
python -m storage.maintenance --enforce-storage-limits
python -m storage.maintenance --purge-package-url-hints-all-sites
python -m storage.maintenance --user test@example.com --purge-package-url-hints-all-sites
```
