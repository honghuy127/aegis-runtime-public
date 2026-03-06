import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple


@dataclass
class DefInfo:
    kind: str  # "function" | "class" | "method"
    name: str
    lineno: int
    end_lineno: int
    signature: str
    decorators: List[str]
    parent: Optional[str] = None  # class name if method


def _safe_unparse(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return "<unparse_failed>"


def _one_line_def_signature(lines: List[str], lineno: int) -> str:
    """
    Best-effort: return a compact, one-line "def ...:" / "class ...:" header
    even if it spans multiple lines.
    """
    i = lineno - 1
    if i < 0 or i >= len(lines):
        return "<signature_out_of_range>"

    buf = []
    # collect until we see ":" that likely ends the header
    # (handles multi-line params)
    for j in range(i, min(i + 30, len(lines))):
        buf.append(lines[j].rstrip("\n"))
        joined = " ".join(x.strip() for x in buf).strip()
        if ":" in joined and (joined.startswith("def ") or joined.startswith("class ")):
            # keep only up to first ":" to avoid inline body
            k = joined.find(":")
            return re.sub(r"\s+", " ", joined[: k + 1]).strip()

    joined = " ".join(x.strip() for x in buf).strip()
    return re.sub(r"\s+", " ", joined).strip()


def extract_file_headers(
    path: str,
    *,
    group_by_prefix: bool = False,
    prefix_rules: Optional[List[Tuple[str, str]]] = None,
) -> str:
    """
    Extract:
      - imports (all)
      - top-level funcs
      - classes & methods
    Return formatted text.

    group_by_prefix: if True, group functions by prefix buckets.
    prefix_rules: list of (bucket_name, regex_pattern) applied in order.
    """
    p = Path(path)
    src = p.read_text(encoding="utf-8")
    lines = src.splitlines(keepends=True)
    tree = ast.parse(src)

    # imports: preserve order; include multi-line imports
    import_lines: List[str] = []
    for i, line in enumerate(lines, 1):
        if line.startswith("import ") or line.startswith("from "):
            import_lines.append(f"{i:5d}:{line.rstrip()}")
        # stop scanning imports after first def/class for speed
        if line.startswith("def ") or line.startswith("class "):
            break

    defs: List[DefInfo] = []

    def decorators_of(n: ast.AST) -> List[str]:
        return [_safe_unparse(d) for d in getattr(n, "decorator_list", [])]

    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            defs.append(
                DefInfo(
                    kind="function",
                    name=node.name,
                    lineno=node.lineno,
                    end_lineno=getattr(node, "end_lineno", node.lineno),
                    signature=_one_line_def_signature(lines, node.lineno),
                    decorators=decorators_of(node),
                )
            )
        elif isinstance(node, ast.ClassDef):
            class_sig = _one_line_def_signature(lines, node.lineno)
            defs.append(
                DefInfo(
                    kind="class",
                    name=node.name,
                    lineno=node.lineno,
                    end_lineno=getattr(node, "end_lineno", node.lineno),
                    signature=class_sig,
                    decorators=decorators_of(node),
                )
            )
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    defs.append(
                        DefInfo(
                            kind="method",
                            name=item.name,
                            lineno=item.lineno,
                            end_lineno=getattr(item, "end_lineno", item.lineno),
                            signature=_one_line_def_signature(lines, item.lineno),
                            decorators=decorators_of(item),
                            parent=node.name,
                        )
                    )

    # optional grouping (only for top-level functions)
    if prefix_rules is None:
        prefix_rules = [
            ("GOOGLE", r"^_google_"),
            ("SKYSCANNER", r"^_skyscanner_"),
            ("VLM/VISION", r"^_(vision|vlm)_"),
            ("PLAN", r"^_(plan|infer|annotate|retarget|reconcile)_"),
            ("SELECTORS", r"^_(selector|selectors|fill_selector|prioritize|filter|blocked|service_).*"),
            ("ARTIFACTS", r"^_write_"),
            ("ENV/TIMEOUTS", r"^_(env_|threshold_|get_model_timeout|apply_model_timeout|normalize_selector_timeout|optional_)"),
            ("SCOPE/READY", r"^_(normalize_page_class|resolve_page_scope_class|is_results_ready|should_block_ready|record_scope_)"),
            ("MISC", r".*"),
        ]

    def bucket_for(name: str) -> str:
        for bucket, pat in prefix_rules:
            if re.match(pat, name):
                return bucket
        return "MISC"

    out: List[str] = []
    out.append(f"# FILE: {p}\n")

    out.append("## IMPORTS (early section)\n")
    out.extend(import_lines if import_lines else ["<no imports found at top>"])
    out.append("")

    # print defs
    top_funcs = [d for d in defs if d.kind == "function"]
    classes = [d for d in defs if d.kind == "class"]
    methods = [d for d in defs if d.kind == "method"]

    out.append(f"## COUNTS\n- functions: {len(top_funcs)}\n- classes: {len(classes)}\n- methods: {len(methods)}\n")

    def fmt_def(d: DefInfo) -> str:
        dec = ""
        if d.decorators:
            dec = " " + " ".join(f"@{x}" for x in d.decorators)
        rng = f"L{d.lineno}-L{d.end_lineno}"
        if d.kind == "method":
            return f"- [{rng}] {d.parent}.{d.name}{dec}\n  {d.signature}"
        return f"- [{rng}] {d.name}{dec}\n  {d.signature}"

    if group_by_prefix:
        out.append("## TOP-LEVEL FUNCTIONS (grouped)\n")
        buckets = {}
        for d in top_funcs:
            buckets.setdefault(bucket_for(d.name), []).append(d)
        for b in buckets:
            out.append(f"### {b}\n")
            for d in sorted(buckets[b], key=lambda x: x.lineno):
                out.append(fmt_def(d))
            out.append("")
    else:
        out.append("## TOP-LEVEL FUNCTIONS\n")
        for d in sorted(top_funcs, key=lambda x: x.lineno):
            out.append(fmt_def(d))
        out.append("")

    if classes:
        out.append("## CLASSES\n")
        for c in sorted(classes, key=lambda x: x.lineno):
            out.append(fmt_def(c))
            # attach methods
            ms = [m for m in methods if m.parent == c.name]
            for m in sorted(ms, key=lambda x: x.lineno):
                out.append("  " + fmt_def(m).replace("\n", "\n  "))
            out.append("")

    return "\n".join(out).rstrip() + "\n"


def main(argv: List[str]) -> int:
    if len(argv) < 2:
        print("Usage: python extract_headers.py <path> [--group]")
        return 2
    path = argv[1]
    group = "--group" in argv[2:]
    text = extract_file_headers(path, group_by_prefix=group)
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
