# ADR 0002: Incremental Plugin Migration

## Status
Accepted

## Context
The codebase has mature, working logic in legacy modules.
A big-bang plugin rewrite would raise regression risk and increase rollback cost.

## Decision
Adopt plugin architecture in small stages:
1. introduce interfaces/registry
2. add adapters delegating to current logic
3. migrate one call site at a time behind feature flags
4. keep legacy modules as compatibility shims until parity is proven

## Consequences
- Small, reversible patches.
- Easier review and safer deployments.
- Temporary duplication/adapters are accepted during transition.

## Legacy Layer Retirement Milestones
Retirement of `core/service_runners/*` follows explicit completion gates:

1. **Milestone M1: Bridge-first coupling**
- Legacy runner call sites consume public bridge interfaces (`core/scenario_runner/<site>/service_runner_bridge.py`) instead of direct scenario-runner internals.
- New cross-module underscore-private imports from `core.scenario_runner.*` are blocked by tests.

2. **Milestone M2: Ownership isolation**
- Site-specific UI policy and recovery logic live under `core/scenario_runner/<site>/`.
- `core/scenario_runner.py` remains orchestration-only and does not import `core/service_runners/*`.

3. **Milestone M3: Runtime parity**
- Targeted scenario and timeout suites pass with bridge-first routing.
- Import-graph checks show shrinking `core/service_runners/* -> core.scenario_runner.*` dependency edges.

4. **Milestone M4: Legacy deactivation readiness**
- Legacy runner entrypoints are no longer required by active runtime routes.
- Documentation and triage references identify `core/scenario_runner/<site>/` as primary owners.

## Retirement Exit Criteria
`core/service_runners/*` may be removed when all milestones M1-M4 are satisfied and contract/invariant tests remain green.
