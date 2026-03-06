# System Architecture

**Scope**: Component map, data flows, configuration layers, extension points
**Defines**: Module responsibilities, contracts, plugin interface
**Does NOT define**: Individual command details, test structure, configuration syntax

---

## Component Diagram

```
CLI/configs → main.py
  ↓
scenario_runner.run_agentic_scenario()
  ├─→ browser actions (plan, execute, repair)
  ├─→ HTML/screenshot artifacts
  └─→ StepResult collection
  ↓
extractor.extract_price()
  ├─→ plugin router (optional, flag-gated)
  │     ├─→ strategy.extract
  │     ├─→ normalize + accept + scope guard
  │     └─→ accepted ⇒ return | rejected ⇒ fallback
  └─→ legacy chain: heuristics → LLM → VLM
  ↓
storage.runs.save_run()
alerts.evaluate/dispatch()
```

---

## Core Modules (Responsibilities)

| Module | Responsibility | Contract |
|--------|---------------|----------|
| `core/scenario_runner.py` | Public scenario entrypoint; stable callable surface | [scenario_runner.md](../10_runtime_contracts/scenario_runner.md) |
| `core/scenario_runner/run_agentic_scenario.py` | Extracted `run_agentic_scenario` implementation module | [scenario_runner.md](../10_runtime_contracts/scenario_runner.md) |
| `core/scenario_runner/google_flights/*` | Google Flights site-specific scenario logic and plan policies | [scenario_runner.md](../10_runtime_contracts/scenario_runner.md) |
| `core/scenario_runner/skyscanner/*` | Skyscanner site-specific scenario logic and plan policies | [scenario_runner.md](../10_runtime_contracts/scenario_runner.md) |
| `core/agent/plugins/common/*` | Shared object/action catalog primitives for agent UI drivers | [runtime_contracts.md](../10_runtime_contracts/runtime_contracts.md) |
| `core/agent/plugins/<site>/*` | Site-specific agent plugin behavior, object templates, action templates | [scenario_runner.md](../10_runtime_contracts/scenario_runner.md) |
| `core/browser/session.py` | Playwright wrapper, timeout strategy, deadline | [browser_contract.md](../10_runtime_contracts/browser_contract.md) |
| `core/browser/click.py`, `core/browser/fill.py`, `core/browser/wait.py`, `core/browser/verification_challenges.py` | Modular browser interaction/runtime concerns | [browser_contract.md](../10_runtime_contracts/browser_contract.md) |
| `core/extractor.py` | Extraction entrypoint, fallback ordering | [plugins.md](../10_runtime_contracts/plugins.md) |
| `core/plugins/runtime_extraction.py` | Plugin router, normalization, acceptance | [plugins.md](../10_runtime_contracts/plugins.md) |
| `core/site_recovery_dispatch.py` | Site-dispatched recovery contracts | [runtime_playbook.md](../20_decision_system/runtime_playbook.md) |
| `core/scenario_recovery_collab.py` | Bounded collaborative recovery (VLM/planner) | [runtime_playbook.md](../20_decision_system/runtime_playbook.md) |
| `llm/code_model.py` | LLM/VLM entrypoints, parse helpers | - |
| `llm/model_router.py` | Model routing with fallback | - |
| `llm/vlm_validation.py` | Vision validation, page kind detection | - |
| `storage/knowledge_store.py` | Per-user/per-service learning | - |
| `storage/adaptive_policy.py` | Runtime profile adjustment | - |
| `core/alerts.py` | Alert evaluation and dispatch | - |

---

## Extension Points

**Add Service**: Profile in `service_ui_profiles.json` + `services.yaml` config + (optional) plugin in `plugins/services/<service>.py`

**Add Agent UI Object/Action Catalog**: Implement site templates in `core/agent/plugins/<site>/objects.py` and `core/agent/plugins/<site>/actions.py`, and reuse shared catalog primitives in `core/agent/plugins/common/`

**Add Extraction Strategy**: Implement in `plugins/strategies/` + register in router + set feature flag in `thresholds.yaml`

**Add Pattern**: Document in `docs/kb/30_patterns/` + implement in `scenario/` or `agent/` + add StepResult with failure modes + add tests + `DOC:` comment reference

**Extend Recovery**: Site-specific probes in site module + register via `site_recovery_dispatch.py` + reuse `scenario_recovery_collab.py` orchestration

---

## Configuration Layers

**Runtime** (loaded per run):
- `run.yaml` — Trip defaults (origin, dest, dates)
- `services.yaml` — Enabled services, URLs
- `models.yaml` — LLM/VLM names
- `thresholds.yaml` — Feature flags, timeouts, budgets
- `alerts.yaml` — Alert policy, channels
- `service_ui_profiles.json` — Selectors/tokens
- `knowledge_rules.yaml` — Token lists

**Persistent** (learned between runs):
- `adaptive_policy.json` — Threshold deltas
- `knowledge_store.json` — Service signals
- `shared_knowledge_store.json` — Route/alias knowledge

---

## Plugin Interface

Feature flags:
- `FLIGHT_WATCHER_PLUGIN_STRATEGY_ENABLED`
- `FLIGHT_WATCHER_PLUGIN_EXTRACT_ROUTER_ENABLED`
- `FLIGHT_WATCHER_EXTRACT_STRATEGY_KEY`
- `FLIGHT_WATCHER_DISABLE_PLUGINS` (emergency off)

**Contract**: Router accepts only valid, scope-safe candidates. Malformed output returns `{}`. Legacy path remains fallback.

---

## State Management

**Per-run**: StepResult[] (in-memory); HTML/PNG in `storage/runs/<run_id>/`

**Persistent**: Run records, knowledge store, adaptive policy, alert history

**Query patterns**:
- `runs.get_latest_from_service(service, trip)` → Last 3 runs
- `knowledge_store.lookup_tokens(service)` → Domain tokens
- `adaptive_policy.get_delta(feature)` → Threshold adjustment

---

## Failure Propagation

Failures return structured data, never exceptions:
- Browser actions: `StepResult(ok=False, reason="...", evidence={...})`
- Extraction: `{}`  with logged rejection reason
- Recovery: Bounded by max attempts, circuit-open on persistent failure

**All failures observable**: No silent failures, no infinite retry loops.

Ref: [evidence.md](../10_runtime_contracts/evidence.md)

---

## Maintainability Pattern

Large coordinators split into:
- `scenario_runner` — orchestration, lifecycle
- Extracted modules — focused flows (recovery collab)
- Dispatch modules — site capability routing
- Site modules — provider-specific implementations

---

## Scenario Runner Architecture (Post-Refactor)

### `core/scenario_runner.py`

**Role**: Stable public entrypoint and orchestration boundary.

**Allowed**:
- High-level flow control
- State transitions
- Coordination between modules
- Calling service-specific modules

**Forbidden**:
- Site-specific selector banks
- Token definitions
- Form parsing heuristics
- Default plan definitions
- VLM implementation logic
- Large helper clusters

**Size expectation**:
- Remain orchestration-focused
- New feature logic MUST live in submodules

**Entrypoint split**:
- `core/scenario_runner.py::run_agentic_scenario` is the stable public entrypoint
- `core/scenario_runner/run_agentic_scenario.py::run_agentic_scenario` contains the implementation
- Extracted helpers should be consumed via direct imports from `core.scenario_runner.*` modules

### `core/browser/*`

Browser interactions are modularized by concern:
- session/lifecycle: `session.py`, `framework.py`, `page.py`
- user actions: `click.py`, `fill.py`, `wait.py`, `combobox.py`
- verification challenge behavior: `verification_challenges.py` and related browser hardening helpers
- timeout strategy: `timeouts.py`

### `core/scenario_runner/google_flights/*`

Contains all Google Flights site-specific logic. Must not leak into the orchestrator.

### `core/scenario_runner/skyscanner/*`

Contains all Skyscanner site-specific logic. Must not leak into the orchestrator.

### `core/scenario_runner/selectors/*`

Generic selector handling and fallback logic. Must not contain service-specific hardcoded selectors.

### `core/agent/plugins/common/*`

Shared, profile-driven catalog primitives for agent UI object/action definitions.

- Must remain service-agnostic
- Must not contain site tokens/selectors
- Must resolve selectors through profile keys and locale-aware helpers

### `core/agent/plugins/<site>/*`

Site-specific agent UI behavior and catalogs.

- Define site object/action templates only
- Reuse shared catalog primitives from `core/agent/plugins/common/*`
- Keep browser interaction execution in actor/browser layers, not in catalog definitions

### `core/plugins/services/*`

Pure extraction only. Must not import browser/session logic.

### `core/service_runners/*`

Deprecated layer (migration in progress). New code MUST NOT depend on this path.

### Legacy Runner Retirement Contract

`core/service_runners/*` is a compatibility layer with bounded scope and clear retirement gates:
- New feature logic MUST be implemented under `core/scenario_runner/<site>/` modules.
- Legacy runners SHOULD consume public bridge interfaces from `core/scenario_runner/<site>/service_runner_bridge.py` rather than direct scenario-runner internals.
- `core/scenario_runner.py` MUST remain orchestration-only and MUST NOT import `core/service_runners/*`.
- Import/test guardrails SHOULD enforce decreasing dependency edges from legacy runners into scenario-runner internals over time.

---

## Related

- [Design Doctrine](doctrine.md)
- [Architecture Invariants](architecture_invariants.md)
- [Runtime Contracts](../10_runtime_contracts/runtime_contracts.md)
- [Configuration Guide](../../CONFIG.md)
