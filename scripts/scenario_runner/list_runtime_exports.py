"""Runtime symbol audit for extracted scenario-runner implementation.

This tool audits runtime-patchable symbol wiring. It is not a generic Python
export lister, so a zero `runtime_names_count` can be expected for files that
do not declare runtime patch symbol tuples/lists.
"""

from __future__ import annotations

import argparse
import ast
import importlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, os.getcwd())


def _parse_runtime_names(runner_text: str, bootstrap_text: str) -> List[str]:
    names: List[str] = []
    try:
        tree = ast.parse(bootstrap_text or runner_text)
        for node in tree.body:
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in {"_RUNTIME_PATCHABLE_SYMBOLS", "RUNTIME_PATCHABLE_SYMBOLS"}
                and isinstance(node.value, (ast.Tuple, ast.List))
            ):
                for elt in node.value.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        names.append(elt.value)
                break
    except Exception:
        names = []

    if names:
        return names

    # Legacy fallback for extraction variants that used inline runtime loops.
    match = re.search(r"for _name in \((.*?)\):", runner_text, re.S)
    if not match:
        return []
    return [n.strip().strip("\"'") for n in re.findall(r"\"([^\"]+)\"", match.group(1))]


def _parse_imported_symbols(runner_text: str) -> Dict[str, str]:
    imports: Dict[str, str] = {}
    try:
        tree = ast.parse(runner_text)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    asname = alias.asname if alias.asname else alias.name
                    imports[asname] = f"{module}.{alias.name}" if module else alias.name
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    asname = alias.asname if alias.asname else alias.name
                    imports[asname] = alias.name
        return imports
    except Exception:
        # Regex fallback for robustness on parse failures.
        for match in re.finditer(r"from\s+([A-Za-z0-9_.]+)\s+import\s+([A-Za-z0-9_, \n]+)", runner_text):
            module = match.group(1)
            block = match.group(2)
            for part in [p.strip() for p in block.split(",") if p.strip()]:
                if " as " in part:
                    name, alias = [x.strip() for x in part.split(" as ", 1)]
                    imports[alias] = f"{module}.{name}"
                else:
                    imports[part] = f"{module}.{part}"
        return imports


def build_runtime_export_audit(
    *,
    runner_path: Path,
    bootstrap_path: Path,
    scenario_runner_module: str,
) -> Dict[str, object]:
    runner_text = runner_path.read_text(encoding="utf-8")
    bootstrap_text = bootstrap_path.read_text(encoding="utf-8") if bootstrap_path.exists() else ""

    names = _parse_runtime_names(runner_text, bootstrap_text)
    imports = _parse_imported_symbols(runner_text)

    scenario_runner = importlib.import_module(scenario_runner_module)
    exported = {n for n in dir(scenario_runner) if n.startswith("_") and not n.startswith("__")}
    provided = set(imports.keys())
    not_provided = [n for n in names if n in exported and n not in provided]

    out: Dict[str, object] = {
        "runner_path": str(runner_path),
        "bootstrap_path": str(bootstrap_path),
        "runtime_names_count": len(names),
        "exported_runtime_count": len([n for n in names if n in exported]),
        "not_provided_count": len(not_provided),
        "not_provided": not_provided,
        "provided_by_imports_count": len(provided),
    }
    return out


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Audit runtime-patch symbol wiring for extracted scenario runner modules. "
            "This checks runtime patch tuples/lists and imported symbol coverage; it is "
            "not a generic Python export listing utility."
        )
    )
    parser.add_argument(
        "--runner-path",
        default="core/scenario_runner/run_agentic_scenario.py",
        help="Path to extracted runner implementation source.",
    )
    parser.add_argument(
        "--bootstrap-path",
        default="core/scenario_runner/run_agentic_bootstrap.py",
        help="Optional bootstrap module declaring runtime patchable symbols.",
    )
    parser.add_argument(
        "--scenario-runner-module",
        default="core.scenario_runner",
        help="Import path for legacy scenario runner module to compare exports.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output.",
    )
    parser.epilog = (
        "Example: `python scripts/scenario_runner/list_runtime_exports.py --pretty`\n"
        "Note: `runtime_names_count=0` can be normal when the target file has no "
        "runtime patch symbol declaration."
    )
    return parser


def main(argv: List[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    result = build_runtime_export_audit(
        runner_path=Path(args.runner_path),
        bootstrap_path=Path(args.bootstrap_path),
        scenario_runner_module=args.scenario_runner_module,
    )

    if os.getenv("DEBUG_LIST_RUNTIME_EXPORTS"):
        print("DEBUG: args=", json.dumps(vars(args), indent=2))
    print(json.dumps(result, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
