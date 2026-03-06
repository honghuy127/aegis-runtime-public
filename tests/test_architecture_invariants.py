"""Lightweight dependency-boundary invariants.

These checks are intentionally simple and conservative. They catch obvious
cross-layer coupling without enforcing a full import graph policy.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import List


ROOT = Path(__file__).resolve().parents[1]


def _python_files(rel_dir: str) -> List[Path]:
    base = ROOT / rel_dir
    return sorted(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)


def _imports_for_file(path: Path) -> List[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imports.append(module)
            if module == "core":
                imports.extend(f"core.{alias.name}" for alias in node.names)
    return imports


def test_storage_has_no_playwright_or_core_browser_imports():
    violations: List[str] = []
    for path in _python_files("storage"):
        for imp in _imports_for_file(path):
            if imp.startswith("playwright") or imp == "core.browser":
                violations.append(f"{path.relative_to(ROOT)} imports {imp}")

    assert not violations, "\n".join(violations)


def test_llm_has_no_core_scenario_module_imports():
    violations: List[str] = []
    banned_prefixes = ("core.scenario_runner", "core.scenario")

    for path in _python_files("llm"):
        for imp in _imports_for_file(path):
            if imp.startswith(banned_prefixes):
                violations.append(f"{path.relative_to(ROOT)} imports {imp}")

    assert not violations, "\n".join(violations)
