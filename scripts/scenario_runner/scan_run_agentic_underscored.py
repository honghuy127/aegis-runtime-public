#!/usr/bin/env python3
"""Scan underscored-name references in run_agentic_scenario for unresolved loads.

This tool is intentionally conservative: it checks AST name resolution by scope
and reports only underscored names that are loaded but not defined in local,
enclosing, module, or builtins scope.
"""

from __future__ import annotations

import argparse
import ast
import builtins
import json
from pathlib import Path
from typing import Dict, Iterable, List, Set, Tuple


TARGET = Path("core/scenario_runner/run_agentic_scenario.py")


def _collect_store_names(target: ast.AST) -> Set[str]:
    names: Set[str] = set()
    for node in ast.walk(target):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
    return names


def _collect_scope_defs(nodes: List[ast.stmt]) -> Set[str]:
    defs: Set[str] = set()
    queue: List[ast.stmt] = list(nodes)
    while queue:
        node = queue.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defs.add(node.name)
            continue
        if isinstance(node, ast.If):
            queue.extend(node.body)
            queue.extend(node.orelse)
            continue
        if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            queue.extend(node.body)
            queue.extend(node.orelse)
        if isinstance(node, ast.With):
            queue.extend(node.body)
        if isinstance(node, ast.Try):
            queue.extend(node.body)
            queue.extend(node.orelse)
            queue.extend(node.finalbody)
            for handler in node.handlers:
                queue.extend(handler.body)
        if isinstance(node, ast.Match):
            for case in node.cases:
                queue.extend(case.body)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                defs.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                defs.update(_collect_store_names(target))
        elif isinstance(node, ast.AnnAssign):
            defs.update(_collect_store_names(node.target))
        elif isinstance(node, ast.AugAssign):
            defs.update(_collect_store_names(node.target))
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            defs.update(_collect_store_names(node.target))
        elif isinstance(node, ast.With):
            for item in node.items:
                if item.optional_vars is not None:
                    defs.update(_collect_store_names(item.optional_vars))
        elif isinstance(node, ast.Try):
            for handler in node.handlers:
                if handler.name:
                    defs.add(handler.name)
    return defs


def _iter_children_without_nested_scopes(node: ast.AST) -> Iterable[ast.AST]:
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        yield child


def _iter_load_names(nodes: List[ast.stmt]) -> List[Tuple[str, int]]:
    loads: List[Tuple[str, int]] = []
    for stmt in nodes:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        stack = [stmt]
        while stack:
            node = stack.pop()
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                loads.append((node.id, getattr(node, "lineno", 0)))
            stack.extend(_iter_children_without_nested_scopes(node))
    return loads


def _function_local_defs(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> Set[str]:
    defs = {arg.arg for arg in fn.args.args}
    defs.update(arg.arg for arg in fn.args.kwonlyargs)
    if fn.args.vararg:
        defs.add(fn.args.vararg.arg)
    if fn.args.kwarg:
        defs.add(fn.args.kwarg.arg)
    defs.update(_collect_scope_defs(fn.body))
    for stmt in fn.body:
        defs.update(_collect_store_names(stmt))
    return defs


def _scan_function(
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
    module_defs: Set[str],
    enclosing_defs: Set[str],
    missing: Dict[str, Dict[str, object]],
) -> None:
    local_defs = _function_local_defs(fn)
    visible = set(module_defs) | set(enclosing_defs) | set(local_defs)
    for name, lineno in _iter_load_names(fn.body):
        if not name.startswith("_"):
            continue
        if name in visible:
            continue
        if name in dir(builtins):
            continue
        record = missing.setdefault(
            name,
            {"name": name, "first_lineno": lineno, "occurrences": [], "scopes": set()},
        )
        record["occurrences"].append(lineno)
        record["scopes"].add(fn.name)
        if lineno and (record.get("first_lineno") or 0) > lineno:
            record["first_lineno"] = lineno

    child_enclosing = set(enclosing_defs) | set(local_defs)
    for node in fn.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _scan_function(node, module_defs, child_enclosing, missing)


def scan_file(path: Path) -> Dict[str, object]:
    if not path.exists():
        raise FileNotFoundError(f"file not found: {path}")

    tree = ast.parse(path.read_text(encoding="utf-8"))
    module_defs = _collect_scope_defs(tree.body)
    missing: Dict[str, Dict[str, object]] = {}

    # Module-level loads.
    for name, lineno in _iter_load_names(tree.body):
        if not name.startswith("_"):
            continue
        if name in module_defs:
            continue
        if name in dir(builtins):
            continue
        record = missing.setdefault(
            name,
            {"name": name, "first_lineno": lineno, "occurrences": [], "scopes": set()},
        )
        record["occurrences"].append(lineno)
        record["scopes"].add("<module>")

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _scan_function(node, module_defs, set(), missing)

    for record in missing.values():
        record["occurrences"] = sorted(set(record["occurrences"]))
        record["scopes"] = sorted(record["scopes"])

    missing_list = sorted(
        missing.values(),
        key=lambda item: (int(item.get("first_lineno") or 0), str(item["name"])),
    )
    return {
        "path": str(path),
        "missing_count": len(missing_list),
        "missing": missing_list,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="print JSON result")
    parser.add_argument("--path", default=str(TARGET), help="target file path")
    args = parser.parse_args()

    try:
        result = scan_file(Path(args.path))
    except Exception as exc:  # pragma: no cover - script-level UX
        print(f"ERROR: {exc}")
        return 2

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    print(f"Target: {result['path']}")
    print("Missing (underscored loads unresolved by scope):")
    if not result["missing"]:
        print("(none)")
    else:
        for item in result["missing"]:
            print(
                f"{item['name']} first={item['first_lineno']} "
                f"occ={item['occurrences']} scopes={item['scopes']}"
            )
    print(f"\nCount missing: {result['missing_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
