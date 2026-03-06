#!/usr/bin/env python3
"""
Extract a lightweight import graph from a Python file.

Usage:
  python scripts/extract_import_graph.py core/extractor.py
  python scripts/extract_import_graph.py core/extractor.py --top 40 --max-names 12
"""

import ast
import argparse
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Tuple


INTERNAL_PREFIXES = ("core.", "llm.", "storage.", "utils.", "configs.")


def _is_internal(mod: str) -> bool:
    return mod.startswith(INTERNAL_PREFIXES)


def _fmt_names(names: List[str], max_names: int) -> str:
    if len(names) <= max_names:
        return ", ".join(names)
    return ", ".join(names[:max_names]) + f", ... (+{len(names) - max_names})"


def extract_imports(path: Path) -> Tuple[List[str], Counter, Dict[str, Counter], List[Tuple[int, str]]]:
    src = path.read_text(encoding="utf-8")
    lines = src.splitlines()
    tree = ast.parse(src)

    raw_lines: List[Tuple[int, str]] = []  # (lineno, line)
    module_counts = Counter()
    from_imports: Dict[str, Counter] = defaultdict(Counter)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod = alias.name
                module_counts[mod] += 1
            # keep original line (best-effort)
            if hasattr(node, "lineno"):
                raw_lines.append((node.lineno, lines[node.lineno - 1].rstrip()))
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            # "from . import x" => mod may be ""
            level = getattr(node, "level", 0) or 0
            mod_display = ("." * level) + mod if level else mod
            module_counts[mod_display] += 1

            for alias in node.names:
                name = alias.name
                from_imports[mod_display][name] += 1

            if hasattr(node, "lineno"):
                raw_lines.append((node.lineno, lines[node.lineno - 1].rstrip()))

    raw_lines.sort(key=lambda x: x[0])

    # "from X import (...)" might span multiple lines; we keep only the first line above.
    # That’s ok: the goal is quick risk visibility, not perfect reconstruction.

    # Return also a normalized list of unique "from X import ..." entries later
    return src.splitlines(), module_counts, from_imports, raw_lines


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file", help="Path to a Python file")
    ap.add_argument("--top", type=int, default=30, help="How many top imported modules to show")
    ap.add_argument("--max-names", type=int, default=15, help="Max imported names shown per from-import")
    ap.add_argument("--show-lines", action="store_true", help="Show raw import lines with line numbers")
    args = ap.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"[ERROR] File not found: {path}")
        return 2

    _, module_counts, from_imports, raw_lines = extract_imports(path)

    internal = Counter({k: v for k, v in module_counts.items() if _is_internal(k)})
    external = Counter({k: v for k, v in module_counts.items() if not _is_internal(k)})

    print(f"# IMPORT GRAPH: {path}")
    print()

    if args.show_lines:
        print("## RAW IMPORT LINES")
        for ln, line in raw_lines:
            print(f"{ln:5d}: {line}")
        print()

    def _print_top(title: str, c: Counter):
        print(title)
        if not c:
            print("  <none>")
            return
        for mod, cnt in c.most_common(args.top):
            print(f"  {cnt:3d}  {mod}")
        print()

    _print_top("## TOP IMPORTED MODULES (all)", module_counts)
    _print_top("## TOP IMPORTED MODULES (internal)", internal)
    _print_top("## TOP IMPORTED MODULES (external)", external)

    print("## FROM-IMPORTS (grouped)")
    if not from_imports:
        print("  <none>")
        return 0

    # Sort by how many imported names (descending), then module name
    items = sorted(
        from_imports.items(),
        key=lambda kv: (sum(kv[1].values()), kv[0]),
        reverse=True,
    )

    for mod, names_counter in items[: args.top]:
        names = [n for n, _ in names_counter.most_common()]
        total = sum(names_counter.values())
        kind = "internal" if _is_internal(mod) else "external"
        print(f"- {mod}  ({kind}, names={len(names_counter)}, refs={total})")
        print(f"  imports: {_fmt_names(names, args.max_names)}")
    print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
