#!/usr/bin/env python3
"""
List nested functions inside a Python file (defs inside defs).

Outputs:
- parent -> child relationships
- nesting depth
- line ranges (best-effort)
- free variable hints (closure risk) via AST analysis
- top candidates for extraction (pure-ish, low closure risk)

Usage:
  python scripts/list_nested_functions.py core/scenario_runner.py
  python scripts/list_nested_functions.py core/scenario_runner.py --json
"""

from __future__ import annotations

import argparse
import ast
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


@dataclass
class NestedFunc:
    name: str
    qualname: str
    parent: Optional[str]
    depth: int
    lineno: int
    end_lineno: int
    args: str
    free_vars: List[str]
    assigns: List[str]
    calls: int
    lines: int


class _ScopeVisitor(ast.NodeVisitor):
    """
    Collect:
    - assigned names in current function
    - referenced names in current function
    - nested defs (handled elsewhere)
    """

    def __init__(self):
        self.assigned: Set[str] = set()
        self.referenced: Set[str] = set()
        self.calls: int = 0

    def visit_Name(self, node: ast.Name):
        if isinstance(node.ctx, ast.Store):
            self.assigned.add(node.id)
        elif isinstance(node.ctx, ast.Load):
            self.referenced.add(node.id)

    def visit_Call(self, node: ast.Call):
        self.calls += 1
        self.generic_visit(node)

    def visit_statements(self, stmts):
        for st in stmts:
            self.visit(st)

    def visit_FunctionDef(self, node: ast.FunctionDef):
        # Do not descend into nested functions for scope accounting.
        # Nested functions are analyzed separately.
        self.assigned.add(node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self.assigned.add(node.name)

    def visit_ClassDef(self, node: ast.ClassDef):
        self.assigned.add(node.name)


def _format_args(fn: ast.AST) -> str:
    # Best-effort rendering without external libs.
    if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return ""
    a = fn.args
    parts: List[str] = []
    for arg in a.posonlyargs:
        parts.append(arg.arg)
    if a.posonlyargs:
        parts.append("/")
    for arg in a.args:
        parts.append(arg.arg)
    if a.vararg:
        parts.append("*" + a.vararg.arg)
    elif a.kwonlyargs:
        parts.append("*")
    for arg in a.kwonlyargs:
        parts.append(arg.arg)
    if a.kwarg:
        parts.append("**" + a.kwarg.arg)
    return "(" + ", ".join(parts) + ")"


def _builtins_set() -> Set[str]:
    # Conservative list; avoids marking common builtins as free vars.
    import builtins

    return set(dir(builtins))


def _collect_nested_functions(tree: ast.AST, source_lines: List[str]) -> List[NestedFunc]:
    builtins_names = _builtins_set()

    nested: List[NestedFunc] = []

    def walk(node: ast.AST, parent_qual: Optional[str], depth: int):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = child.name if not parent_qual else f"{parent_qual}.{child.name}"
                parent = parent_qual
                lineno = getattr(child, "lineno", -1)
                end_lineno = getattr(child, "end_lineno", lineno)
                lines = max(1, end_lineno - lineno + 1) if lineno > 0 else 0

                # Determine if this is nested: depth>=1 means it's inside another def
                is_nested = parent_qual is not None
                scope = _ScopeVisitor()
                scope.visit_statements(child.body)

                # Names referenced but not assigned locally are potential free vars.
                # Filter out params, locals, and builtins.
                params = set()
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    params |= {a.arg for a in child.args.posonlyargs}
                    params |= {a.arg for a in child.args.args}
                    params |= {a.arg for a in child.args.kwonlyargs}
                    if child.args.vararg:
                        params.add(child.args.vararg.arg)
                    if child.args.kwarg:
                        params.add(child.args.kwarg.arg)

                # assigned contains nested fn names too (by visitor)
                local = set(scope.assigned) | params

                free = sorted(
                    n for n in scope.referenced
                    if n not in local and n not in builtins_names and not n.startswith("__")
                )

                if is_nested:
                    nested.append(
                        NestedFunc(
                            name=child.name,
                            qualname=qual,
                            parent=parent,
                            depth=depth,
                            lineno=lineno,
                            end_lineno=end_lineno,
                            args=_format_args(child),
                            free_vars=free,
                            assigns=sorted(scope.assigned),
                            calls=scope.calls,
                            lines=lines,
                        )
                    )

                # Descend further (this function could contain nested defs)
                walk(child, qual, depth + 1)
            else:
                walk(child, parent_qual, depth)

    walk(tree, None, 0)
    return nested


def _score_candidate(nf: NestedFunc) -> Tuple[int, int, int]:
    """
    Lower score = better extraction candidate.
    We want: fewer free vars, more lines (bigger win), moderate call density.
    Return tuple for sorting.
    """
    free = len(nf.free_vars)
    # prefer larger blocks first if similarly safe
    neg_lines = -nf.lines
    # fewer calls might be easier to isolate (heuristic)
    calls = nf.calls
    return (free, calls, neg_lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", help="Python file to analyze")
    ap.add_argument("--json", action="store_true", help="Output JSON")
    ap.add_argument("--top", type=int, default=25, help="Show top N extraction candidates")
    args = ap.parse_args()

    path = Path(args.path)
    src = path.read_text(encoding="utf-8")
    lines = src.splitlines()

    tree = ast.parse(src, filename=str(path))
    nested = _collect_nested_functions(tree, lines)

    nested_sorted = sorted(nested, key=lambda x: (x.depth, x.lineno))

    candidates = sorted(nested, key=_score_candidate)[: max(1, args.top)]

    payload = {
        "file": str(path),
        "nested_count": len(nested),
        "nested": [asdict(n) for n in nested_sorted],
        "top_candidates": [asdict(n) for n in candidates],
    }

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    print(f"FILE: {path}")
    print(f"Nested functions: {len(nested_sorted)}")
    print()

    print("=== NESTED FUNCTIONS (by position) ===")
    for n in nested_sorted:
        print(
            f"- {n.qualname}{n.args}  "
            f"[L{n.lineno}-L{n.end_lineno}] depth={n.depth} lines={n.lines} "
            f"free_vars={len(n.free_vars)} calls={n.calls}"
        )
        if n.free_vars:
            print(f"    free_vars: {', '.join(n.free_vars[:12])}" + (" ..." if len(n.free_vars) > 12 else ""))
    print()

    print(f"=== TOP {len(candidates)} EXTRACTION CANDIDATES (heuristic) ===")
    for n in candidates:
        print(
            f"- {n.qualname}{n.args}  "
            f"[L{n.lineno}-L{n.end_lineno}] lines={n.lines} free_vars={len(n.free_vars)} calls={n.calls}"
        )
        if n.free_vars:
            print(f"    free_vars: {', '.join(n.free_vars)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
