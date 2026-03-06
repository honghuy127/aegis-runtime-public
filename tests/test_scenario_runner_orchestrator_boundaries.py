import ast
from pathlib import Path


def _import_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(str(alias.name or ""))
        elif isinstance(node, ast.ImportFrom):
            modules.add(str(node.module or ""))
    return modules


def test_scenario_runner_wrapper_has_no_service_runner_imports():
    path = Path("core/scenario_runner.py")
    modules = _import_modules(path)
    bad = sorted(m for m in modules if m.startswith("core.service_runners"))
    assert not bad, f"core/scenario_runner.py must not import core.service_runners modules: {bad}"


def test_scenario_runner_top_level_helpers_are_not_site_prefixed():
    path = Path("core/scenario_runner.py")
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    top_level_funcs = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]
    bad = sorted(
        name
        for name in top_level_funcs
        if name.startswith("_google_") or name.startswith("_skyscanner_")
    )
    assert not bad, (
        "site-specific top-level helper functions should live under core/scenario_runner/<site>/: "
        f"{bad}"
    )
    assert len(top_level_funcs) <= 8, (
        "core/scenario_runner.py should stay orchestration-focused with bounded "
        f"top-level helper growth; found {len(top_level_funcs)} top-level functions"
    )
