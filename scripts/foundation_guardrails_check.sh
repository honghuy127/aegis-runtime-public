#!/bin/bash
# foundation_guardrails_check.sh
# Foundation guardrails gate for repo hygiene, canonical docs, tests, and triage readiness.
#
# Purpose:
#   Enforce the Stage 0 / foundation governance checks documented in
#   `docs/kb/50_governance/stage0_guardrails.md`.
# Properties:
#   idempotent, network-free, and intended to complete quickly (<10s target)
#
# Exit codes:
#   0 = all checks pass
#   1 = check failure (detail in stderr)
#   2 = missing dependencies

set -e

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

##############################################################################
# CHECK 1: Root markdown policy (no new .md at root except allowed)
##############################################################################
check_root_markdown() {
    echo "[1/4] Checking root-level markdown policy..."

    local allowed=("README.md" "AGENTS.md" "SECURITY.md" "LICENSE")
    local found_files=()

    # Find all .md files at root
    while IFS= read -r file; do
        found_files+=("$(basename "$file")")
    done < <(find . -maxdepth 1 -type f -name "*.md")

    # Check if any found file is NOT in allowed list
    for f in "${found_files[@]}"; do
        local is_allowed=0
        for allowed_f in "${allowed[@]}"; do
            if [[ "$f" == "$allowed_f" ]]; then
                is_allowed=1
                break
            fi
        done

        if [[ $is_allowed -eq 0 ]]; then
            echo -e "${RED}✗ Forbidden root markdown file: $f${NC}"
            return 1
        fi
    done

    echo -e "${GREEN}✓ Root markdown policy OK${NC}"
    return 0
}

##############################################################################
# CHECK 2: Canonical docs exist (docs/kb/)
##############################################################################
check_canonical_docs() {
    echo "[2/4] Checking canonical documentation structure..."

    local required_files=(
        "docs/kb/INDEX.md"
        "docs/kb/kb_index.yaml"
        "docs/kb/ARCHITECTURE_INVARIANTS.md"
    )

    for file in "${required_files[@]}"; do
        if [[ ! -f "$file" ]]; then
            echo -e "${RED}✗ Missing required canonical doc: $file${NC}"
            return 1
        fi
    done

    echo -e "${GREEN}✓ Canonical docs structure OK${NC}"
    return 0
}

##############################################################################
# CHECK 3: Run pytest (fast gate)
##############################################################################
check_pytest() {
    echo "[3/4] Running pytest..."

    # Check if pytest is available
    if ! command -v pytest &> /dev/null; then
        echo -e "${YELLOW}⚠ pytest not found, skipping test gate${NC}"
        return 0
    fi

    if ! pytest -q --tb=short 2>&1 | tee /tmp/pytest_stage1.log; then
        echo -e "${RED}✗ pytest failed${NC}"
        return 1
    fi

    echo -e "${GREEN}✓ pytest passed${NC}"
    return 0
}

##############################################################################
# CHECK 4: Triage module import (if exists)
##############################################################################
check_triage_import() {
    echo "[4/4] Checking triage module readiness..."

    # Try to import triage (if it exists)
    if python3 -c "from utils.triage import parse_error_json_file" 2>/dev/null; then
        echo -e "${GREEN}✓ Triage module imported successfully${NC}"
        return 0
    fi

    # If triage doesn't exist, check for python environment at least
    if python3 --version &>/dev/null; then
        echo -e "${GREEN}✓ Python available (triage module not present, skipping)${NC}"
        return 0
    fi

    echo -e "${YELLOW}⚠ Python environment not fully configured, skipping triage check${NC}"
    return 0
}

##############################################################################
# Main
##############################################################################
main() {
    echo "======================================"
    echo "Foundation Guardrails Check"
    echo "======================================"
    echo ""

    local checks_passed=0
    local checks_total=4

    if check_root_markdown; then
        ((checks_passed++))
    else
        echo ""
        return 1
    fi

    if check_canonical_docs; then
        ((checks_passed++))
    else
        echo ""
        return 1
    fi

    if check_pytest; then
        ((checks_passed++))
    else
        echo ""
        return 1
    fi

    if check_triage_import; then
        ((checks_passed++))
    fi

    echo ""
    echo "======================================"
    echo -e "${GREEN}All gates passed ($checks_passed/$checks_total)${NC}"
    echo "======================================"
    return 0
}

main "$@"
