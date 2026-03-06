#!/usr/bin/env python3
"""
Initialize a file-based refactor journal under docs/refactors/.

Features:
- Normalizes a target path (e.g., core/extractor.py -> core_extractor)
- Creates docs/refactors/<normalized>_refactor_journal.md
- Auto-detects candidate "locked entrypoints" (heuristic)
- Suggests targeted pytest commands (path-based mapping)
- Safe by default: refuses to overwrite unless --force

Usage:
  python scripts/init_refactor_journal.py core/extractor.py
  python scripts/init_refactor_journal.py core/browser.py --force
  python scripts/init_refactor_journal.py llm/code_model.py --entrypoints extract_price run_plugin_extraction_router
  python scripts/init_refactor_journal.py core/scenario/google_flights.py --no-autodetect

Notes:
- "Locked entrypoints" are best-effort guesses. You can edit the journal afterwards.
"""

from __future__ import annotations

import argparse
import ast
import datetime as dt
from pathlib import Path
from typing import Iterable, List, Tuple


REFACTOR_DIR = Path("docs/refactors")


# --- Heuristics ---------------------------------------------------------------

# Path-based suggested tests (edit freely as your suite evolves)
SUGGESTED_TESTS = [
    # extractor
    (("core/extractor.py",), [
        "pytest -q tests/test_extractor.py tests/test_extractor_contract.py",
        "pytest -q tests/test_audit_extraction_pipeline.py",
        "pytest -q tests/test_irrelevant_page_downgrade.py",
    ]),
    # browser
    (("core/browser.py",), [
        "pytest -q tests/test_browser_timeouts.py",
        "pytest -q tests/test_browser_stealth_init.py",
        "pytest -q tests/test_browser_google_flights_combobox.py",
    ]),
    # llm code_model
    (("llm/code_model.py",), [
        "pytest -q tests/test_llm_json_parsing.py tests/test_llm_client.py",
        "pytest -q tests/test_model_router.py",
        "pytest -q tests/test_llm_multimodal_context.py",
    ]),
    # legacy google flights scenario
    (("core/scenario/google_flights.py",), [
        "pytest -q tests/test_gf_set_date.py tests/test_gf_set_date_enhanced.py",
        "pytest -q tests/test_google_flights_iata_commit.py",
        "pytest -q tests/test_google_flights_search_commit.py",
        "pytest -q tests/test_google_deeplink_page_state_recovery.py",
    ]),
    # scenario_runner (if ever needed)
    (("core/scenario_runner.py",), [
        "pytest -q tests/test_scenario_runner_timeouts.py",
        "pytest -q tests/test_debug_artifacts_consolidated.py",
        "pytest -q tests/test_debug_budgets.py",
    ]),
]


# Strongly-likely public entrypoints by filename (optional boost)
FILENAME_ENTRYPOINT_HINTS = {
    "scenario_runner.py": ["run_agentic_scenario", "execute_plan"],
    "extractor.py": ["extract_price", "run_plugin_extraction_router"],
    "browser.py": ["apply_selector_timeout_strategy", "safe_min_timeout_ms"],
    "code_model.py": [],  # varies; will rely on autodetect
}


def normalize_target_to_stem(target: str) -> str:
    """
    core/scenario/google_flights.py -> core_scenario_google_flights
    """
    p = target.replace("\\", "/")
    if p.endswith(".py"):
        p = p[:-3]
    return p.replace("/", "_").replace("-", "_")


def suggest_tests(target_path: str) -> List[str]:
    target_norm = target_path.replace("\\", "/")
    for paths, tests in SUGGESTED_TESTS:
        if target_norm in paths:
            return list(tests)
    # fallback generic
    return ["pytest -q"]


def detect_entrypoints_via_ast(file_path: Path) -> List[str]:
    """
    Heuristic:
    - Prefer hinted entrypoints if filename matches known long-file categories.
    - Otherwise, pick top-level functions that do not start with '_' and are not obviously test helpers.
    - Keep order: hinted first, then discovered.
    """
    text = file_path.read_text(encoding="utf-8")
    tree = ast.parse(text)

    defs: List[str] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            defs.append(node.name)

    filename = file_path.name
    hinted = FILENAME_ENTRYPOINT_HINTS.get(filename, [])
    hinted = [n for n in hinted if n in defs]

    # Discovered public-looking functions
    discovered = [
        n for n in defs
        if not n.startswith("_")
        and n not in hinted
        and not n.startswith("test_")
    ]

    # Keep only a few to avoid “locking” too much by accident
    # If your file legitimately has many public functions, pass --entrypoints explicitly.
    discovered = discovered[:6]

    # If nothing found, fall back to hinted or empty.
    out = hinted + discovered
    return out


# --- Journal template ---------------------------------------------------------

def render_journal(
    target_path: str,
    locked_entrypoints: List[str],
    tests: List[str],
) -> str:
    today = dt.datetime.now().strftime("%Y-%m-%d")
    ep_lines = "\n".join([f"- {x}" for x in locked_entrypoints]) or "- <fill_me>"
    test_lines = "\n".join([f"- {t}" for t in tests]) or "- pytest -q"

    return f"""# Refactor Journal: {target_path}

Target: `{target_path}`
Start date: {today}
Mode: move-only split, zero behavior change

Hard constraints:
- No signature changes for locked entrypoints.
- No cleanup/renaming in split PRs (move + rewire only).
- Avoid circular imports.
- Keep targeted tests green per PR.

---

## Locked entrypoints (signatures must NOT change)
{ep_lines}

---

## Tooling
Commands:
- Import graph:
  - `python scripts/extract_import_graph.py {target_path} --show-lines --top 40 --max-names 20`
- Headers map:
  - `python scripts/extract_headers.py {target_path} --group`
- Gate:
  - `bash scripts/refactor_gate.sh --file {target_path} --entrypoints <...> --tests "<pytest -q ...>"`

---

## Targeted tests
{test_lines}

---

## Baseline snapshots

### Import graph summary (from extract_import_graph.py)
- Top internal imports:
- Heaviest from-import groups:
- Circular risk notes:

### Header clusters (from extract_headers.py --group)
- Buckets/modules candidates:
  - ARTIFACTS:
  - ENV/TIMEOUTS:
  - PLAN:
  - SELECTORS:
  - VLM/VISION:
  - GOOGLE:
  - SCOPE/READY:
  - MISC:
- Proposed split modules:
  - `<module_path>.py`: functions: ...

---

## PR Plan

### PR#1: <title>
Scope: move-only
Move symbols:
New modules:
Gate tests:
Status: TODO
Result:
- diffstat:
- gate: PASS/FAIL
- notes:

---

## Progress Log (append-only)

### {today} — Journal created
- Notes:
  - Initialized journal file.
  - Next: run import graph + headers, then fill Baseline snapshots.
"""


# --- CLI ----------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target_file", help="Target python file, e.g. core/extractor.py")
    ap.add_argument("--force", action="store_true", help="Overwrite existing journal if present")
    ap.add_argument("--entrypoints", nargs="*", default=None, help="Explicit locked entrypoints (overrides autodetect)")
    ap.add_argument("--no-autodetect", action="store_true", help="Disable entrypoint autodetect (journal will contain <fill_me>)")
    ap.add_argument("--out", default=None, help="Optional output path override")
    args = ap.parse_args()

    target = Path(args.target_file)
    if not target.exists():
        print(f"[ERROR] Target file not found: {target}")
        return 2
    if target.suffix != ".py":
        print(f"[ERROR] Target must be a .py file: {target}")
        return 2

    stem = normalize_target_to_stem(args.target_file)
    out_path = Path(args.out) if args.out else (REFACTOR_DIR / f"{stem}_refactor_journal.md")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if out_path.exists() and not args.force:
        print(f"[ERROR] Journal already exists: {out_path}")
        print("        Use --force to overwrite, or edit the existing journal.")
        return 3

    # Determine entrypoints
    if args.entrypoints is not None and len(args.entrypoints) > 0:
        locked = list(args.entrypoints)
    elif args.no_autodetect:
        locked = []
    else:
        locked = detect_entrypoints_via_ast(target)

    tests = suggest_tests(args.target_file)

    content = render_journal(args.target_file, locked, tests)
    out_path.write_text(content, encoding="utf-8")

    print(f"[OK] Wrote journal: {out_path}")
    if locked:
        print(f"[OK] Locked entrypoints: {', '.join(locked)}")
    else:
        print("[WARN] No entrypoints detected. Fill 'Locked entrypoints' manually.")
    print("[OK] Suggested tests:")
    for t in tests:
        print(f"  - {t}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
