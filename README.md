# AegisRuntime

A governance-aware agent runtime for resilient web task execution under bounded control.

> Disclaimer: This is a vibe-coding project built with AI-assisted development (ChatGPT, Codex, and GitHub Copilot) with minimal direct human intervention.

## Featured Technical Report

- [docs/research/aegis-runtime.md](docs/research/aegis-runtime.md) - **Governance-Aware Hybrid Agent Runtime with Bounded Recovery Graphs**
- [docs/research/formal_math_modeling.md](docs/research/formal_math_modeling.md) - **Formal mathematical modeling addendum for AegisRuntime**

## What This Repository Contains

AegisRuntime combines:
- probabilistic reasoning (LLM/VLM) for planning and interpretation
- deterministic policies for invariants, budgets, and hard gates
- bounded recovery orchestration with explicit reason codes and evidence

The goal is runtime stability in dynamic environments, not unconstrained exploration.

## Quick Start

### Prerequisites

- Python 3.12+
- Playwright runtime dependencies
- Optional local model runtime for LLM/VLM flows

### Install (venv)

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m playwright install chromium
```

### Install (conda)

```bash
CONDA_ENV=aegis-runtime
conda create -n "$CONDA_ENV" python=3.12 -y
conda run -n "$CONDA_ENV" python -m pip install --upgrade pip
conda run -n "$CONDA_ENV" python -m pip install -r requirements.txt
conda run -n "$CONDA_ENV" python -m playwright install chromium
```

## Running

### Single run example

```bash
python main.py \
  --origin HND \
  --dest ITM \
  --depart 2026-06-01 \
  --return-date 2026-06-08 \
  --services google_flights
```

### Config-driven run

```bash
python main.py --services-config configs/services.yaml
```

### Mode switches

```bash
python main.py --light-mode
python main.py --full-mode
python main.py --multimodal-mode assist
```

## Configuration

Runtime configuration lives in `configs/`:
- `run.yaml` - default trip inputs and run flags
- `services.yaml` - enabled services and URLs
- `models.yaml` - planner/coder/vision model selection
- `thresholds.yaml` - runtime budgets and feature flags
- `alerts.yaml` - alert policy
- `knowledge_rules.yaml` - heuristic/rule mappings
- `service_ui_profiles.json` - adapter-level UI hints

See [docs/CONFIG.md](docs/CONFIG.md) for detailed configuration guidance.

## Project Layout

- `core/scenario_runner.py` - stable scenario entrypoint
- `core/scenario_runner/` - service-specific orchestration modules
- `core/extractor.py` - extraction entrypoint and routing
- `core/plugins/services/` - extraction service adapters
- `storage/runs/<run_id>/` - canonical run artifacts and evidence
- `docs/kb/` - canonical knowledge base (authoritative runtime docs)

## Documentation

- [docs/README.md](docs/README.md) - documentation index
- [docs/kb/INDEX.md](docs/kb/INDEX.md) - KB reading order
- [docs/kb/kb_index.yaml](docs/kb/kb_index.yaml) - machine-readable KB index
- [docs/kb/00_foundation/doctrine.md](docs/kb/00_foundation/doctrine.md) - design doctrine
- [docs/kb/00_foundation/architecture.md](docs/kb/00_foundation/architecture.md) - system architecture
- [docs/kb/10_runtime_contracts/runtime_contracts.md](docs/kb/10_runtime_contracts/runtime_contracts.md) - runtime contracts
- [docs/kb/20_decision_system/runtime_playbook.md](docs/kb/20_decision_system/runtime_playbook.md) - runtime troubleshooting

## Development and Testing

Run tests:

```bash
python -m pytest
```

Useful targeted checks:

```bash
bash scripts/refactor_gate.sh \
  --file core/scenario_runner.py \
  --entrypoints run_agentic_scenario \
  --tests "pytest -q tests/test_architecture_invariants.py tests/test_architecture_site_ownership.py"

pytest -q tests/test_diagnostic_code_separation.py tests/test_refactor_safety_stage0.py
pytest -q tests/test_scenario_runner_timeouts.py
```

## Responsible Use

This repository is for systems research and engineering.

Users are responsible for complying with the terms of service and applicable policies of any external sites they interact with.
Use interstitial detection with safe handling, a human verification manual gate, and conservative rate limiting.
