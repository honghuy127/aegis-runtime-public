# Design Doctrine

**Scope**: Core principles for all system and code changes
**Defines**: Mandatory MUST/MUST NOT rules, 5 pillars, AI layer discipline
**Does NOT define**: API contracts, invariants, test structure

---

## Core Optimizations

- Structural robustness over quick fixes
- Deterministic commit logic over heuristic guessing
- Bounded retries over infinite loops
- Layered arbitration over single-source truth
- Observability over silent failures
- Adaptive collaboration over rigid single-agent scripts

---

## Agentic Principles

**KB-First: MUST consult canonical entrypoints before planning**
- Cite KB docs in solution design
- Update KB when introducing new reasons/evidence

**Bounded Collaboration: Multiple decision roles, single artifact protocol**
- Deterministic verifier sets gates; LLM/VLM propose adaptations
- Max 2 scope overrides per scenario
- Communication via HTML, screenshots, structured evidence
- API: [../10_runtime_contracts/runtime_contracts.md](../10_runtime_contracts/runtime_contracts.md)

---

## Structural Robustness Checklist

Every fix MUST:
- [ ] Introduce reusable pattern (not special case)
- [ ] Be threshold-driven, bounded, testable
- [ ] Be observable via structured logging
- [ ] Generalize behind dispatch/adapters

FORBIDDEN: direct selector fixes, blind timeout increases, unbounded retry layers, undocumented special cases

---

## Fail-Closed Doctrine

**Scope Guard Hierarchy**: VLM (high-confidence) > Heuristic > LLM (fallback)

MUST:
- No silent wrong data; no infinite retry storms; no fake confidence
- Max 2 scope overrides per scenario
- Allow controlled override when signal high

---

## Vision: Gated Semantic Layer

Vision used only in intentional stages:
- **Stage A**: Page kind probe (heuristic irrelevant)
- **Stage B**: Post-fill verification (route binding uncertain)

MUST: strict schema validation, screenshot cache, same-turn cooldown, language hint, no vision spam

---

## IATA Doctrine: Deterministic Commit

Autocomplete is probabilistic. Commit logic MUST be deterministic.

| Rank | Condition | Action |
|------|-----------|--------|
| 3 | Exact IATA match | Click |
| 2 | IATA in parentheses or alias token | Click |
| 1 | Partial match | Enter or fallback |
| 0 | No match | Fallback; max 2 retries |

MUST: Click iff rank >= 2; 150ms post-click wait. Ref: [../30_patterns/combobox_commit.md](../30_patterns/combobox_commit.md)

---

## Timeouts: Systematic Only

MUST use `apply_selector_timeout_strategy()` for all waits; min 800ms; per-site overrides supported.

FORBIDDEN: Hardcoded waits, magic numbers. Ref: [../10_runtime_contracts/budgets_timeouts.md](../10_runtime_contracts/budgets_timeouts.md)

---

## Recovery Bounds

MUST:
- Max 2 attempts per scenario
- Max 2 turns per attempt
- Circuit-open → fail fast

FORBIDDEN: Nested retry cascades, unbounded loops

---

## Maintainability Doctrine

Coordinator complexity grows. Keep it thin.

MUST:
- Prefer orchestration + dispatch helpers over nested branches
- Extract large helpers into focused modules
- Keep site-specific policy behind adapters
- Pass dependencies explicitly

---

## Locale Strategy

MUST:
- Try EN locale first for US-origin platforms (OCR quality)
- Provide language hint to VLM
- Cover JA+EN token coverage for text-based selectors

FORBIDDEN: Blind locale switching on native Japanese platforms; global locale config without site context

Ref: [../30_patterns/i18n_ja.md](../30_patterns/i18n_ja.md)

---

## Observability Doctrine

Logs MUST carry semantic meaning. Required signals:
- `action_budget_used`, `step_result.reason`, `commit_strategy`
- `scope_conflict_resolved`, `vision.page_kind`, `payload_hash` (never full payload)

---

## Testing Doctrine

MUST have:
- **Unit**: Ranking, timeout clamp, scope reconciliation, budget logic
- **Integration**: Scenario services, extraction, knowledge store
- **Smoke**: Real browser, locale-specific, screenshot capture

Unit passing ≠ bot stability. Smoke tests mandatory.

---

## Dead Branch Removal

When service unstable: MUST remove completely (profile, tests, references). FORBIDDEN: Zombie code, half-supported services.

---

## AI Layer Discipline

VLM MUST:
- Validate schema before returning
- Log payload hash only (never full payload)
- Reject invalid JSON early
- Respect cooldown and circuit-open

**AI is assistive only, not authority.**

---

## The Five Pillars (Stability Core)

1. **Deterministic Commit**: IATA ranking, bounded selection
2. **Hierarchical Scope Arbitration**: VLM > heuristic > LLM
3. **Threshold-Driven Timing**: Systematic timeouts
4. **Gated Vision Assist**: Staged VLM, cooldown, cache
5. **Bounded Retry**: Max 2 attempts, hard gates

If a change weakens any pillar, reconsider.

---

## Related

- [System Architecture](architecture.md)
- [Architecture Invariants](architecture_invariants.md)
- [Runtime Contracts](../10_runtime_contracts/runtime_contracts.md)
- [Patterns](../30_patterns/)
