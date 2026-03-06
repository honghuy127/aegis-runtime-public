#!/usr/bin/env bash
set -euo pipefail

# clean_pytemp.sh
# Remove common Python temporary files and caches from the repository.
# Safe by default: lists matches. Use --yes to actually delete.

# Behavior:
# - Default: dry-run and list matches
# - Use --yes to skip confirmation and delete
# - Interactive prompt will ask for confirmation when deletions are pending
# - Script excludes VCS folder .git and common venv dirs by default

DRY_RUN=1
YES=0

# default exclude paths (relative to repo root)
EXCLUDE_PATHS=("./.git" "./venv" "./.venv" "./env" "./.env" "./envs" "./venvs")

usage() {
  cat <<EOF
Usage: $0 [--yes] [--dry-run] [--help] [--exclude PATH]

Options:
  --yes        Actually perform deletions without prompting.
  --dry-run    Explicit dry-run (default).
  --help       Show this help and exit.
  --exclude    Add an extra path to exclude from search (can be repeated).

Examples:
  # show what would be removed
  ./scripts/clean_pytemp.sh

  # actually remove files
  ./scripts/clean_pytemp.sh --yes

  # exclude an additional directory from scanning
  ./scripts/clean_pytemp.sh --exclude ./storage/runs --dry-run
EOF
}

# Patterns for files and directories to remove
FILE_PATTERNS=("*.pyc" "*.pyo" "*.pyd" "*.py[co]" "*~" ".coverage" ".coverage.*")
DIR_PATTERNS=("__pycache__" ".pytest_cache" ".mypy_cache" ".pytype" "*.egg-info")

while [[ ${#@} -gt 0 ]]; do
  case "$1" in
    --help)
      usage
      exit 0
      ;;
    --yes)
      DRY_RUN=0
      YES=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      YES=0
      shift
      ;;
    --exclude)
      if [[ -z "${2:-}" ]]; then
        echo "--exclude requires a path argument"
        exit 2
      fi
      EXCLUDE_PATHS+=("$2")
      shift 2
      ;;
    *)
      echo "Unknown option: $1"
      usage
      exit 2
      ;;
  esac
done

exclude_expr=()
for p in "${EXCLUDE_PATHS[@]}"; do
  # normalize paths without trailing slashes
  p=${p%/}
  exclude_expr+=( -not -path "${p}/*" )
done

echo "Searching for Python temp files and caches (dry-run=${DRY_RUN})..."

# Build find commands
file_find=(find . -type f '(')
for i in "${!FILE_PATTERNS[@]}"; do
  if [[ $i -gt 0 ]]; then
    file_find+=( -o )
  fi
  file_find+=( -name "${FILE_PATTERNS[$i]}" )
done
file_find+=( ')' )
file_find+=( "${exclude_expr[@]}" )

dir_find=(find . -type d '(')
for i in "${!DIR_PATTERNS[@]}"; do
  if [[ $i -gt 0 ]]; then
    dir_find+=( -o )
  fi
  dir_find+=( -name "${DIR_PATTERNS[$i]}" )
done
dir_find+=( ')' )
dir_find+=( "${exclude_expr[@]}" )

# Use temp files to capture lists and counts
TMP_FILES=$(mktemp -d)
FILES_LIST="$TMP_FILES/files.txt"
DIRS_LIST="$TMP_FILES/dirs.txt"

# shellcheck disable=SC2086
"${file_find[@]}" -print > "$FILES_LIST" || true
# shellcheck disable=SC2086
"${dir_find[@]}" -print > "$DIRS_LIST" || true

FILES_COUNT=$(wc -l < "$FILES_LIST" | tr -d '[:space:]')
DIRS_COUNT=$(wc -l < "$DIRS_LIST" | tr -d '[:space:]')

echo "--- files ($FILES_COUNT) ---"
if [[ $FILES_COUNT -gt 0 ]]; then
  sed -n '1,200p' "$FILES_LIST"
  if [[ $FILES_COUNT -gt 200 ]]; then
    echo "... (showing first 200 of $FILES_COUNT)"
  fi
fi

echo "--- directories ($DIRS_COUNT) ---"
if [[ $DIRS_COUNT -gt 0 ]]; then
  sed -n '1,200p' "$DIRS_LIST"
  if [[ $DIRS_COUNT -gt 200 ]]; then
    echo "... (showing first 200 of $DIRS_COUNT)"
  fi
fi

if [[ $FILES_COUNT -eq 0 && $DIRS_COUNT -eq 0 ]]; then
  echo
  echo "No matching temporary Python files or directories found."
  rm -rf "$TMP_FILES"
  exit 0
fi

if [[ $YES -ne 1 ]]; then
  # prompt for confirmation in interactive shells
  if [[ -t 1 ]]; then
    echo
    read -r -p "Proceed to delete $FILES_COUNT files and $DIRS_COUNT directories? [y/N] " resp
    case "$resp" in
      [yY]|[yY][eE][sS])
        ;;
      *)
        echo "Aborted. No deletions performed."
        rm -rf "$TMP_FILES"
        exit 0
        ;;
    esac
  else
    echo
    echo "Non-interactive shell and --yes not specified. No deletions performed."
    rm -rf "$TMP_FILES"
    exit 0
  fi
fi

# Perform deletions and record how many were actually removed
REMOVED_FILES=0
REMOVED_DIRS=0

if [[ $FILES_COUNT -gt 0 ]]; then
  # shellcheck disable=SC2086
  "${file_find[@]}" -print -exec rm -f {} + || true
  REMOVED_FILES=$FILES_COUNT
fi

if [[ $DIRS_COUNT -gt 0 ]]; then
  # remove directories in reverse-depth order to avoid nesting issues
  tac "$DIRS_LIST" | xargs -r -I {} rm -rf "{}" || true
  REMOVED_DIRS=$DIRS_COUNT
fi

echo
echo "Deleted: $REMOVED_FILES files and $REMOVED_DIRS directories."
rm -rf "$TMP_FILES"
echo "Cleanup complete."
