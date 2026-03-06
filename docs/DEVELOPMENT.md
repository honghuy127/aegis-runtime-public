# Development

## Local setup

### Option A: virtualenv
```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m playwright install chromium
```

### Option B: conda

Set an environment name once and reuse it:
```bash
CONDA_ENV=aegis-runtime
conda create -n "$CONDA_ENV" python=3.12 -y
conda run -n "$CONDA_ENV" python -m pip install --upgrade pip
conda run -n "$CONDA_ENV" python -m pip install -r requirements.txt
conda run -n "$CONDA_ENV" python -m playwright install chromium
```

Project-local helper scripts:
- `.codex/actions/sanity_show_python.sh`
- `.codex/actions/test_pytest.sh`

## Running tests

Default:
```bash
python -m pytest
```

Conda:
```bash
CONDA_ENV=aegis-runtime
conda run -n "$CONDA_ENV" python -m pytest
```

Pytest discovery is configured by `pytest.ini`:
- `testpaths = tests`
- `python_files = test_*.py`

## Quick smoke commands

```bash
# Show active interpreter details in conda env
bash .codex/actions/sanity_show_python.sh

# Run tests in conda env
bash .codex/actions/test_pytest.sh
```

## Refactor guardrail workflow

Use these commands when changing scenario runner structure or docs tied to runtime ownership:

```bash
# Snapshot current import graph and headers
python scripts/extract_import_graph.py core/scenario_runner.py
python scripts/extract_import_graph.py core/scenario_runner/run_agentic_scenario.py
python scripts/extract_import_graph.py core/browser/session.py
python scripts/extract_headers.py core/scenario_runner.py core/scenario_runner/run_agentic_scenario.py
python scripts/list_runtime_exports.py core/scenario_runner/run_agentic_scenario.py

# Enforce entrypoint stability + targeted tests
bash scripts/refactor_gate.sh \
  --file core/scenario_runner.py \
  --entrypoints run_agentic_scenario \
  --tests "pytest -q tests/test_architecture_invariants.py tests/test_architecture_site_ownership.py"

# Additional targeted regression checks
pytest -q tests/test_diagnostic_code_separation.py tests/test_refactor_safety_stage0.py
pytest -q tests/test_scenario_runner_timeouts.py
```

## Safely enabling plugin extraction

1. Keep fallback behavior available.
2. Enable plugin mode with env flags only in local/dev first.
3. Run full tests before enabling by default in shared environments.

Example:
```bash
export FLIGHT_WATCHER_PLUGIN_STRATEGY_ENABLED=true
export FLIGHT_WATCHER_PLUGIN_EXTRACT_ROUTER_ENABLED=true
export FLIGHT_WATCHER_EXTRACT_STRATEGY_KEY=html_llm
python -m pytest
```

Emergency rollback:
```bash
export FLIGHT_WATCHER_DISABLE_PLUGINS=true
```

## Add a new service plugin (high-level checklist)

1. Add service plugin module under `core/plugins/services/`.
2. Implement required service metadata and optional hints methods.
3. Register plugin in service plugin builder/registry path.
4. Add service URL/profile entries in `configs/services.yaml` and `configs/service_ui_profiles.json`.
5. Add focused tests:
   - registry lookup
   - URL candidate behavior
   - extraction/readiness hints behavior
6. Run full `python -m pytest`.

Keep changes incremental and avoid broad runtime rewrites.
