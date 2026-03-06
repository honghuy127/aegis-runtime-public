import ast
from pathlib import Path


def test_google_flights_service_runner_avoids_private_scenario_runner_imports():
    path = Path("core/service_runners/google_flights.py")
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    disallowed_modules = {
        "core.scenario_runner.selectors.fallbacks",
        "core.scenario_runner.google_flights.route_bind",
        "core.scenario_runner.google_flights.route_recovery",
    }

    bad_private_imports = []
    bad_module_imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = str(node.module or "")
            if module in disallowed_modules:
                bad_module_imports.append((module, node.lineno))
            if module.startswith("core.scenario_runner"):
                for alias in node.names:
                    if str(alias.name or "").startswith("_"):
                        bad_private_imports.append((module, alias.name, node.lineno))

    assert not bad_module_imports, (
        "legacy google_flights service runner must consume scenario-runner helpers via "
        "service_runner_bridge, not direct route/selectors imports: "
        f"{bad_module_imports}"
    )
    assert not bad_private_imports, (
        "cross-module private underscore imports from core.scenario_runner are disallowed: "
        f"{bad_private_imports}"
    )
