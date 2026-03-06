# ADR 0003: Orchestrator-Only scenario_runner

## Status
Accepted

## Context
The previous `scenario_runner.py` accumulated site logic, selector banks, plan defaults, and helper clusters. This increased coupling, made changes harder to review, and blurred the boundary between orchestration and provider-specific behavior. A refactor split `scenario_runner` into focused submodules and site-specific folders.

## Decision
- `scenario_runner.py` is an orchestrator only: flow control, state transitions, and coordination between modules.
- Site-specific logic lives under `core/scenario_runner/<site>/`.
- Generic selector utilities live under `core/scenario_runner/selectors/` and must remain service-agnostic.
- Extraction logic remains under `core/plugins/services/` and must not import browser/session logic.
- New service integrations require a site module, selector bank, and plan preset; `scenario_runner.py` changes are limited to minimal wiring.

## Consequences
- Feature work is localized to site modules, enabling targeted reviews and tests.
- Orchestrator complexity remains bounded and auditable.
- Refactors are less likely to cause cross-service regressions.
- Future enforcement can be automated by import and path checks in invariants/tests.
