#!/bin/bash
################################################################################
# Repo Audit Dry-Run Script
#
# Safe, fast sanity checks for production deployment.
# - No external dependencies
# - No runtime modifications
# - No browser automation
# - No LLM/VLM invocations
#
# Usage: bash scripts/audit_dry_run.sh
################################################################################

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Tracking
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
START_TIME=$(date +%s)

# Helper functions
log_header() {
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

log_check() {
    echo -e "${YELLOW}▶ $1${NC}"
}

log_pass() {
    echo -e "${GREEN}✓ $1${NC}"
}

log_fail() {
    echo -e "${RED}✗ $1${NC}"
}

log_skip() {
    echo -e "${YELLOW}⊘ $1${NC}"
}

# Calculate elapsed time
elapsed_time() {
    local end_time=$(date +%s)
    local elapsed=$((end_time - START_TIME))
    echo "${elapsed}s"
}

# Header
log_header "REPO AUDIT DRY-RUN"
echo "Repository: $REPO_ROOT"
echo "Started: $(date)"
echo "Python: $(python --version)"
echo "Bash: $BASH_VERSION"
echo

################################################################################
# CHECK A: Python Compilation
################################################################################
log_check "A) Python compilation check (compileall)"
if python -m compileall . -q 2>/dev/null; then
    log_pass "Python compilation successful"
else
    log_fail "Python compilation failed"
    exit 1
fi
echo

################################################################################
# CHECK B: Stage 1 Safety Check
################################################################################
log_check "B) Stage 1 safety checks"
if [ -x "${SCRIPT_DIR}/foundation_guardrails_check.sh" ]; then
    if bash "${SCRIPT_DIR}/foundation_guardrails_check.sh"; then
        log_pass "Foundation guardrails checks passed"
    else
        log_fail "Foundation guardrails checks failed"
        exit 1
    fi
else
    log_skip "scripts/foundation_guardrails_check.sh not found or not executable (skipped)"
fi
echo

################################################################################
# CHECK C: KB Cards Sanity
################################################################################
log_check "C) KB cards sanity check"
if python -m utils.kb_cards_sanity_check >/dev/null 2>&1; then
    log_pass "KB cards sanity check passed"
else
    log_fail "KB cards sanity check failed"
    exit 1
fi
echo

################################################################################
# CHECK D: Docs Hygiene
################################################################################
log_check "D) Documentation hygiene checks"

# D1: Check for stray root-level *.md files
ALLOWED_ROOT_DOCS=("README.md" "LICENSE" "SECURITY.md" "AGENTS.md")
STRAY_DOCS=()
while IFS= read -r file; do
    filename=$(basename "$file")
    if [[ ! " ${ALLOWED_ROOT_DOCS[@]} " =~ " ${filename} " ]]; then
        STRAY_DOCS+=("$file")
    fi
done < <(find "$REPO_ROOT" -maxdepth 1 -name "*.md" -o -name "LICENSE")

if [ ${#STRAY_DOCS[@]} -gt 0 ]; then
    log_fail "Found stray .md files at repo root (only README.md, LICENSE, SECURITY.md, AGENTS.md allowed):"
    for doc in "${STRAY_DOCS[@]}"; do
        echo "  - $doc"
    done
    exit 1
fi
log_pass "No stray root-level .md files"

# D2: Check docs/README.md exists
if [ ! -f "$REPO_ROOT/docs/README.md" ]; then
    log_fail "docs/README.md is missing"
    exit 1
fi
log_pass "docs/README.md exists"

# D3: Check docs/kb/INDEX.md exists
if [ ! -f "$REPO_ROOT/docs/kb/INDEX.md" ]; then
    log_fail "docs/kb/INDEX.md is missing"
    exit 1
fi
log_pass "docs/kb/INDEX.md exists"

# D4: Check docs/kb/kb_index.yaml exists
if [ ! -f "$REPO_ROOT/docs/kb/kb_index.yaml" ]; then
    log_fail "docs/kb/kb_index.yaml is missing"
    exit 1
fi
log_pass "docs/kb/kb_index.yaml exists"

# D5: Check docs/kb/cards directory (if present, must not be empty)
if [ -d "$REPO_ROOT/docs/kb/cards" ]; then
    CARD_COUNT=$(find "$REPO_ROOT/docs/kb/cards" -type f -name "*.md" 2>/dev/null | wc -l)
    if [ "$CARD_COUNT" -eq 0 ]; then
        log_fail "docs/kb/cards exists but contains no card files"
        exit 1
    fi
    log_pass "docs/kb/cards directory contains $CARD_COUNT card files"
fi

echo

################################################################################
# CHECK E: Pytest (safe mode, no LLM/VLM)
################################################################################
log_check "E) Test suite validation (LLM/VLM tests skipped by default)"
if pytest -q --tb=short 2>/dev/null; then
    log_pass "Test suite passed"
else
    log_fail "Test suite failed"
    exit 1
fi
echo

################################################################################
# SUMMARY
################################################################################
ELAPSED=$(elapsed_time)
log_header "AUDIT RESULT: ${GREEN}PASS${NC}"
echo "All checks completed successfully."
echo "Elapsed time: $ELAPSED"
echo
echo "Note: This audit is a DRY-RUN only."
echo "  • LLM/VLM tests are SKIPPED by default (use pytest --run-llm for extended tests)"
echo "  • No runtime code was modified"
echo "  • No external services were contacted"
echo "  • Safe to run before deployment"
echo

exit 0
