#!/usr/bin/env python3
"""Classify unresolved underscored names from scan_run_agentic_underscored."""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
from pathlib import Path
from typing import Dict, List, Set


def _load_scan_file_fn():
    scan_path = Path(__file__).with_name("scan_run_agentic_underscored.py")
    spec = importlib.util.spec_from_file_location("scan_run_agentic_underscored", str(scan_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load scan script: {scan_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fn = getattr(mod, "scan_file", None)
    if not callable(fn):
        raise RuntimeError("scan_run_agentic_underscored.scan_file is unavailable")
    return fn


def _runtime_patchable_symbols(path: Path) -> Set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    symbols: Set[str] = set()
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "_RUNTIME_PATCHABLE_SYMBOLS"
            and isinstance(node.value, (ast.Tuple, ast.List))
        ):
            for elt in node.value.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    symbols.add(elt.value)
    return symbols


def classify(path: Path) -> Dict[str, object]:
    scan_file = _load_scan_file_fn()
    scan_result = scan_file(path)
    runtime_patchable = _runtime_patchable_symbols(path)
    actionable: List[Dict[str, object]] = []
    runtime_patchable_missing: List[Dict[str, object]] = []
    maybe_dynamic_alias: List[Dict[str, object]] = []

    for item in scan_result["missing"]:
        name = str(item["name"])
        if name in runtime_patchable:
            runtime_patchable_missing.append(item)
        elif name.endswith("_impl") or name in {"_sr"}:
            maybe_dynamic_alias.append(item)
        else:
            actionable.append(item)

    return {
        "path": scan_result["path"],
        "missing_count": scan_result["missing_count"],
        "actionable_count": len(actionable),
        "runtime_patchable_missing_count": len(runtime_patchable_missing),
        "maybe_dynamic_alias_count": len(maybe_dynamic_alias),
        "actionable": actionable,
        "runtime_patchable_missing": runtime_patchable_missing,
        "maybe_dynamic_alias": maybe_dynamic_alias,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="print JSON result")
    parser.add_argument(
        "--path",
        default="core/scenario_runner/run_agentic_scenario.py",
        help="target file path",
    )
    args = parser.parse_args()

    try:
        result = classify(Path(args.path))
    except Exception as exc:  # pragma: no cover - script-level UX
        print(f"ERROR: {exc}")
        return 2

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    print(f"Target: {result['path']}")
    print(f"Missing: {result['missing_count']}")
    print(f"Actionable: {result['actionable_count']}")
    print(f"Runtime patchable: {result['runtime_patchable_missing_count']}")
    print(f"Dynamic alias candidates: {result['maybe_dynamic_alias_count']}")

    if result["actionable"]:
        print("\nActionable unresolved names:")
        for item in result["actionable"]:
            print(
                f"{item['name']} first={item['first_lineno']} "
                f"occ={item['occurrences']} scopes={item['scopes']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
