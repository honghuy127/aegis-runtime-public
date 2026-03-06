#!/usr/bin/env bash
set -euo pipefail

# Refactor gate:
# - generates headers_before/after via scripts/extract_headers.py
# - checks entrypoint signatures unchanged vs HEAD
# - checks renamed/moved python modules have no stale imports in repo
# - runs targeted tests (one or more commands)
#
# Usage:
#   bash scripts/refactor_gate.sh --file path.py --entrypoints f1 f2 --tests "pytest -q ..."

FILE=""
ENTRYPOINTS=()
TEST_CMDS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --file)
      FILE="$2"; shift 2;;
    --entrypoints)
      shift
      while [[ $# -gt 0 && "$1" != --tests && "$1" != --file ]]; do
        ENTRYPOINTS+=("$1"); shift
      done
      ;;
    --tests)
      TEST_CMDS+=("$2"); shift 2;;
    *)
      echo "[ERROR] Unknown arg: $1" >&2
      exit 2
      ;;
  esac
done

if [[ -z "${FILE}" ]]; then
  echo "[ERROR] --file is required" >&2
  exit 2
fi
if [[ ${#ENTRYPOINTS[@]} -eq 0 ]]; then
  echo "[ERROR] --entrypoints requires at least 1 function name" >&2
  exit 2
fi
if [[ ${#TEST_CMDS[@]} -eq 0 ]]; then
  echo "[ERROR] --tests requires at least 1 command" >&2
  exit 2
fi

echo "[preflight] Validate --tests commands"
has_pytest=0
for cmd in "${TEST_CMDS[@]}"; do
  trimmed="$(echo "${cmd}" | awk '{$1=$1; print}')"
  if [[ -z "${trimmed}" ]]; then
    echo "[ERROR] --tests contains an empty command" >&2
    exit 2
  fi
  case "${trimmed}" in
    true|:|/usr/bin/true|/bin/true)
      echo "[ERROR] --tests command '${trimmed}' is a no-op; provide a real test command" >&2
      exit 2
      ;;
  esac
  if [[ "${trimmed}" == *"|| true"* ]]; then
    echo "[ERROR] --tests command '${trimmed}' masks failures with '|| true'" >&2
    exit 2
  fi
  if [[ "${trimmed}" =~ (^|[[:space:]])pytest([[:space:]]|$) ]] || [[ "${trimmed}" =~ (^|[[:space:]])python([0-9.]*)[[:space:]]+-m[[:space:]]+pytest([[:space:]]|$) ]]; then
    has_pytest=1
  fi
done
if [[ "${has_pytest}" -ne 1 ]]; then
  echo "[ERROR] --tests must include at least one pytest command" >&2
  exit 2
fi

if [[ ! -f "scripts/extract_headers.py" ]]; then
  echo "[ERROR] scripts/extract_headers.py not found" >&2
  exit 2
fi

echo "=== Refactor Gate ==="
echo "FILE: ${FILE}"
echo "ENTRYPOINTS: ${ENTRYPOINTS[*]}"
echo "TESTS: ${#TEST_CMDS[@]} command(s)"
echo

OUTDIR=".refactor_gate"
mkdir -p "${OUTDIR}"

# 1) headers before/after
echo "[1/5] Extract headers_before/after"
python scripts/extract_headers.py "${FILE}" --group > "${OUTDIR}/headers_after.txt"

# baseline from HEAD for "before"
python - <<PY > "${OUTDIR}/headers_before.txt"
import subprocess, sys, tempfile, os, pathlib
file_path = "${FILE}"
blob = subprocess.check_output(["git", "show", f"HEAD:{file_path}"], text=True)
tmp = tempfile.NamedTemporaryFile("w", delete=False, suffix=".py", encoding="utf-8")
tmp.write(blob)
tmp.close()
subprocess.run([sys.executable, "scripts/extract_headers.py", tmp.name, "--group"], check=True, stdout=sys.stdout)
os.unlink(tmp.name)
PY

echo "  wrote ${OUTDIR}/headers_before.txt"
echo "  wrote ${OUTDIR}/headers_after.txt"
echo

# 2) Signature check for selected entrypoints
echo "[2/5] Check entrypoint signatures unchanged vs HEAD"
GATE_FILE="${FILE}" GATE_ENTRYPOINTS="$(IFS=,; echo "${ENTRYPOINTS[*]}")" python - <<'PY'
import ast, subprocess, sys, tempfile, os, re

FILE = os.environ.get("GATE_FILE")
ENTRYPOINTS = os.environ.get("GATE_ENTRYPOINTS","").split(",")

def one_line_signature(lines, lineno):
    i = lineno - 1
    buf = []
    for j in range(i, min(i + 40, len(lines))):
        buf.append(lines[j].rstrip("\n"))
        joined = " ".join(x.strip() for x in buf).strip()
        if joined.startswith(("def ", "class ")) and ":" in joined:
            k = joined.find(":")
            return re.sub(r"\s+", " ", joined[:k+1]).strip()
    joined = " ".join(x.strip() for x in buf).strip()
    return re.sub(r"\s+", " ", joined).strip()

def extract_signatures(src: str, targets):
    lines = src.splitlines(keepends=True)
    tree = ast.parse(src)
    found = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in targets:
            found[node.name] = one_line_signature(lines, node.lineno)
    return found

def read_head_blob(file_path: str) -> str:
    return subprocess.check_output(["git", "show", f"HEAD:{file_path}"], text=True)

def read_working(file_path: str) -> str:
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()

file_path = FILE
targets = set([x for x in ENTRYPOINTS if x])

head_src = read_head_blob(file_path)
work_src = read_working(file_path)

head_sigs = extract_signatures(head_src, targets)
work_sigs = extract_signatures(work_src, targets)

missing = sorted(targets - set(work_sigs.keys()))
if missing:
    print(f"[FAIL] Missing entrypoints in working tree: {missing}")
    sys.exit(1)

changed = []
for name in sorted(targets):
    hs = head_sigs.get(name)
    ws = work_sigs.get(name)
    if hs != ws:
        changed.append((name, hs, ws))

if changed:
    print("[FAIL] Entry point signature changed:")
    for name, hs, ws in changed:
        print(f"  - {name}")
        print(f"    HEAD: {hs}")
        print(f"    NOW : {ws}")
    sys.exit(1)

print("[OK] Entry point signatures unchanged.")
PY
echo

# 3) Detect python file renames/moves and ensure no stale imports remain
echo "[3/5] Check stale imports after renames/moves (python files)"
python - <<'PY'
import subprocess, sys, os, re
from pathlib import Path

def path_to_module(p: str) -> str:
    # file.py -> file ; a/b/c.py -> a.b.c
    p = p.replace("\\", "/")
    if not p.endswith(".py"):
        return ""
    p = p[:-3]
    return p.replace("/", ".")

# Identify renames and deletions in working tree vs HEAD
diff = subprocess.check_output(["git", "diff", "--name-status", "HEAD"], text=True).splitlines()

renames = []  # (old, new)
deletes = []  # old
for line in diff:
    parts = line.split("\t")
    if not parts:
        continue
    status = parts[0]
    if status.startswith("R") and len(parts) >= 3:
        renames.append((parts[1], parts[2]))
    elif status == "D" and len(parts) >= 2:
        deletes.append(parts[1])

targets = renames + [(d, "") for d in deletes]
py_targets = [(a,b) for a,b in targets if a.endswith(".py")]

if not py_targets:
    print("[OK] No python renames/deletes detected.")
    sys.exit(0)

# Build search patterns for old module paths
old_modules = []
for old, _new in py_targets:
    mod = path_to_module(old)
    if mod:
        old_modules.append(mod)

# Use ripgrep if available, fallback to grep
def has_rg():
    try:
        subprocess.run(["rg", "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return True
    except Exception:
        return False

patterns = []
for mod in old_modules:
    # match: import mod | from mod import ...
    patterns.append(rf"(^|\s)import\s+{re.escape(mod)}(\s|$)")
    patterns.append(rf"(^|\s)from\s+{re.escape(mod)}\s+import(\s|$)")

bad_hits = []
if has_rg():
    for pat in patterns:
        p = subprocess.run(["rg", "-n", "--hidden", "--glob", "*.py", pat, "."], text=True, capture_output=True)
        if p.returncode == 0 and p.stdout.strip():
            bad_hits.append(p.stdout.strip())
else:
    # crude grep fallback
    for mod in old_modules:
        p = subprocess.run(["grep", "-RIn", f"from {mod} import", "."], text=True, capture_output=True)
        if p.returncode == 0 and p.stdout.strip():
            bad_hits.append(p.stdout.strip())
        p = subprocess.run(["grep", "-RIn", f"import {mod}", "."], text=True, capture_output=True)
        if p.returncode == 0 and p.stdout.strip():
            bad_hits.append(p.stdout.strip())

if bad_hits:
    print("[FAIL] Found stale imports referencing renamed/deleted modules:")
    for h in bad_hits[:5]:
        print("----")
        print(h)
    if len(bad_hits) > 5:
        print(f"... (+{len(bad_hits)-5} more blocks)")
    sys.exit(1)

print("[OK] No stale imports for renamed/deleted python modules.")
PY
echo

# 4) Diffstat
echo "[4/5] Write diffstat"
git diff --stat HEAD > "${OUTDIR}/diffstat.txt" || true
echo "  wrote ${OUTDIR}/diffstat.txt"
echo

# 5) Run targeted tests
echo "[5/5] Run targeted tests"
for cmd in "${TEST_CMDS[@]}"; do
  echo ">> ${cmd}"
  bash -lc "${cmd}"
done

echo
echo "[PASS] Refactor gate OK."
echo "Artifacts in ${OUTDIR}/"
