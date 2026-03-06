# Documentation Index

**Updated**: 2026-03-02

This directory contains authoritative documentation for AegisRuntime.

---

## Directory Structure

```
docs/
├── kb/                  # Canonical knowledge base (authoritative)
│   ├── 00_foundation/   # Doctrine, architecture, system model
│   ├── 10_runtime_contracts/ # Runtime contracts and APIs
│   ├── 20_decision_system/   # Playbooks and triage runbook
│   ├── 30_patterns/     # Implementation patterns
│   ├── 40_cards/        # KB cards
│   ├── 50_governance/   # Governance + ADRs
│   └── ...
├── research/            # Research artifacts and case-study analyses
│   └── aegis-runtime.md # Governance-aware hybrid runtime technical report
└── playbooks/           # Developer integration guides
```

### Directory Purpose Guide

**kb/** — Canonical Knowledge Base (Authoritative)
- Core principles, architecture, contracts, patterns
- Runtime-queryable, test-verified documentation
- Read for: understanding system design and invariants
- Index: [kb/INDEX.md](kb/INDEX.md)

**playbooks/** — Developer Integration Guides
- Step-by-step integration instructions
- Checklists and configuration examples
- Read for: following established integration patterns

**research/** — Research Artifacts (Non-canonical)
- Run analyses and implementation case studies
- Provider-specific examples used for architecture evaluation
- Read for: historical context and experimental findings

---

## Quick Links

### Agent Workflow (KB-First)
- **Run before planning/coding**: `python -m utils.agent_preflight`
- **Task-scoped preflight**: `python -m utils.agent_preflight --path <file> --reason <reason_code>`
- **Purpose**: prints mandatory KB entrypoints and task-relevant KB docs to review first

### Getting Started
- [README](../README.md) - Project overview and installation
- [CONFIG.md](CONFIG.md) - Configuration file reference
- [DEVELOPMENT.md](DEVELOPMENT.md) - Development setup and testing

### Refactor Tooling
- `python scripts/extract_import_graph.py <path.py>` - import dependency snapshot
- `python scripts/extract_headers.py <path.py> [<path.py> ...]` - top-level headers/functions snapshot
- `python scripts/list_runtime_exports.py <path.py>` - runtime-export check
- `python scripts/list_nested_functions.py <path.py> --max-lines 30` - nested-helper bloat scanner
- `python scripts/init_refactor_journal.py` - initialize refactor journal template
- `bash scripts/refactor_gate.sh --file <path.py> --entrypoints <fn...> --tests \"pytest -q ...\"` - signature + stale import + test gate

### Authoritative Knowledge Base (kb/)
- **[kb/00_foundation/doctrine.md](kb/00_foundation/doctrine.md)** - Core design principles and invariants
- **[kb/00_foundation/architecture.md](kb/00_foundation/architecture.md)** - System components and data flow
- **[kb/20_decision_system/runtime_playbook.md](kb/20_decision_system/runtime_playbook.md)** - Troubleshooting by symptoms/logs
- **[kb/00_foundation/architecture_invariants.md](kb/00_foundation/architecture_invariants.md)** - Test-grounded architectural contracts
- **[kb/INDEX.md](kb/INDEX.md)** - Full KB reading order
- **[kb/kb_index.yaml](kb/kb_index.yaml)** - Machine-readable topic index

### Patterns (kb/30_patterns/)
- [kb/30_patterns/date_picker.md](kb/30_patterns/date_picker.md) - Date picker with ActionBudget
- [kb/30_patterns/combobox_commit.md](kb/30_patterns/combobox_commit.md) - IATA-based combobox commit
- [kb/30_patterns/selectors.md](kb/30_patterns/selectors.md) - Selector strategies
- [kb/30_patterns/i18n_ja.md](kb/30_patterns/i18n_ja.md) - Japanese locale handling

### Contracts (kb/10_runtime_contracts/)
- [kb/10_runtime_contracts/budgets_timeouts.md](kb/10_runtime_contracts/budgets_timeouts.md) - ActionBudget & timeouts
- [kb/10_runtime_contracts/evidence.md](kb/10_runtime_contracts/evidence.md) - Evidence fields reference
- [kb/10_runtime_contracts/scenario_runner.md](kb/10_runtime_contracts/scenario_runner.md) - Scenario runner API
- [kb/10_runtime_contracts/browser_contract.md](kb/10_runtime_contracts/browser_contract.md) - Browser contract
- [kb/10_runtime_contracts/plugins.md](kb/10_runtime_contracts/plugins.md) - Plugin extraction contract

### ADRs (kb/50_governance/adr/)
- [kb/50_governance/adr/0001-soft-validators-tolerant-parsing.md](kb/50_governance/adr/0001-soft-validators-tolerant-parsing.md)
- [kb/50_governance/adr/0002-incremental-plugin-migration.md](kb/50_governance/adr/0002-incremental-plugin-migration.md)

### Developer Playbooks (playbooks/)
- _(Playbooks added as needed for specific integration patterns)_

### Research Reports (research/)
- [research/aegis-runtime.md](research/aegis-runtime.md) - Governance-aware hybrid runtime technical systems report
- [research/formal_math_modeling.md](research/formal_math_modeling.md) - Formal mathematical modeling addendum for AegisRuntime

---

## Documentation Philosophy

### Knowledge Base (kb/) is Authoritative
The `kb/` directory contains **authoritative, runtime-queryable documentation** designed for both humans and AI agents. These docs:
- Use stable headings and structured sections
- Include "Purpose / When to read / Key invariants" headers
- Document explicit failure modes and evidence fields
- Are indexed in `kb_index.yaml` for semantic lookup

For coding agents, KB consultation is a **planning gate**. Agents should read KB
entrypoints and task-relevant KB docs before writing a plan or patching code.

### Playbooks (playbooks/) Guide Integration
The `playbooks/` directory contains **developer integration guides**:
- Step-by-step instructions for common tasks
- Checklists and configuration examples
- Living documents updated as patterns evolve

### Note on Preserved Documentation
Historical documentation is preserved in git history only. For current authoritative guidance, always consult `docs/kb/`.

### Normalized and Verified
All kb/ docs have been **git-normalized** to ensure they are:
- **Timeless**: No temporal language ("recently", "we added", "Phase X")
- **Declarative**: Use imperative/prescriptive language ("The system MUST...")
- **Code-verified**: Invariants match actual implementation (timeouts, budgets, etc.)
- **Stable**: No brittle line number references

---

## Where Did X Go? Migration Guide

Authoritative docs now live under `docs/kb/`:
- **Design principles**: `docs/kb/00_foundation/doctrine.md`
- **Architecture**: `docs/kb/00_foundation/architecture.md`
- **Runtime contracts**: `docs/kb/10_runtime_contracts/`
- **Patterns**: `docs/kb/30_patterns/`
- **ADRs**: `docs/kb/50_governance/adr/`

Legacy summaries are preserved in git history only.

---

## Finding Documentation as an Agent

### Use kb_index.yaml
```yaml
# Load index
index = yaml.safe_load(open('docs/kb/kb_index.yaml'))

# Lookup by topic
date_picker_docs = [f for f in index['topics'] if f['name'] == 'date_picker']

# Lookup by symptom
timeout_docs = [s for s in index['symptom_index'] if s['symptom'] == 'timeout_error']

# Lookup by tag
agentic_docs = [t for t in index['tags']['agentic']]
```

### Follow DOC: Comments in Code
Code hotspots include `DOC:` comments pointing to relevant kb sections:
```python
# core/scenario_runner/google_flights/route_recovery.py
# core/scenario/gf_helpers/date_picker_orchestrator.py
"""
DOC: See docs/kb/30_patterns/date_picker.md for complete pattern documentation.
"""

# core/scenario/types.py:ActionBudget
"""
DOC: See docs/kb/10_runtime_contracts/budgets_timeouts.md for complete contract.
"""
```

### Start with Entrypoints
Always start with these three docs:
1. [kb/00_foundation/doctrine.md](kb/00_foundation/doctrine.md) - Core principles
2. [kb/00_foundation/architecture.md](kb/00_foundation/architecture.md) - System overview
3. [kb/20_decision_system/runtime_playbook.md](kb/20_decision_system/runtime_playbook.md) - Troubleshooting

---

## Architecture Invariants: Test-Grounded Contracts

The **Architecture Invariants** documents provide test-grounded architectural contracts enforced by the test suite. Unlike aspirational design docs, these invariants reflect **actual enforced behavior**.

### Key Document

**[kb/00_foundation/architecture_invariants.md](kb/00_foundation/architecture_invariants.md)** - Main invariants document
- Test-grounded architectural contracts and constraints

### Usage Scenarios

**Before Refactoring**:
```bash
# Check which invariants your change affects
grep "core/scenario_runner" docs/kb/00_foundation/architecture_invariants.md

# Run those tests to verify no regression
pytest tests/test_scenario_runner_timeouts.py -v
```

**When Adding Features**:
1. Check if new behavior creates an architectural contract
2. Add test(s) validating the contract
3. Document as new INV-* in docs/kb/00_foundation/architecture_invariants.md
4. Update trace map with test references

**When Tests Fail**:
1. Check if failure indicates invariant violation
2. Review invariant documentation for expected behavior
3. Either fix code to satisfy invariant OR update invariant if requirements changed

### Invariant ID Scheme

```
INV-<SUBSYSTEM>-<NUMBER>

Examples:
- INV-SCENARIO-001: Global wall-clock timeouts MUST abort immediately
- INV-BUDGET-003: Wall-clock cap helper MUST only trigger when enabled
- INV-SELECTOR-001: IATA ranking MUST prioritize parenthesized codes
```

---

## Runtime KB Loader (Python API)

For programmatic access within the codebase, use the **runtime KB loader** at [`utils/kb.py`](../utils/kb.py).

### Basic Usage

```python
from utils.kb import get_kb, get_docs_for_topic, get_docs_for_reason

# Load KB index (cached after first call)
kb = get_kb()

# Get docs for a specific topic
date_picker_docs = get_docs_for_topic(kb, "date_picker")
for doc in date_picker_docs:
    print(f"{doc.topic}: {doc.path} (priority={doc.priority})")

# Get docs for a failure reason
docs = get_docs_for_reason(kb, "calendar_not_open")
# Returns: [DocRef for date_picker, ...]
```

### API Reference

#### Core Functions

- **`get_kb(root_dir=None, force_reload=False) -> KBIndex`**
  - Get cached KB index, loading if necessary
  - Auto-detects repo root if `root_dir` is None
  - Use `force_reload=True` to skip cache

- **`get_entrypoints(kb) -> list[DocRef]`**
  - Returns core entrypoint docs (doctrine, architecture, runtime_playbook)

- **`get_docs_for_topic(kb, topic) -> list[DocRef]`**
  - Lookup docs by topic name (case-insensitive)
  - Returns `[]` if topic not found

- **`search_topics(kb, query) -> list[str]`**
  - Substring search on topic names
  - Returns sorted list of matching topic names

- **`get_docs_for_reason(kb, reason) -> list[DocRef]`**
  - Map `StepResult.reason` code to relevant docs
  - Deduplicates and sorts by priority
  - Example: `"calendar_not_open"` → date_picker docs

#### Data Types

```python
@dataclass
class DocRef:
    path: str           # e.g., "kb/30_patterns/date_picker.md"
    priority: int       # Lower = higher priority
    topic: str          # e.g., "date_picker"
    kind: str          # "entrypoint" | "topic" | "configuration"
```

### Reason → Topic Mapping

The loader includes built-in mappings from `StepResult.reason` codes to documentation topics:

| Reason Code | Topics |
|-------------|--------|
| `calendar_not_open` | date_picker |
| `month_nav_exhausted` | date_picker, budgets_timeouts |
| `budget_hit` | budgets_timeouts, evidence |
| `verify_mismatch` | date_picker, selectors |
| `action_deadline_exceeded_before_click` | budgets_timeouts, selectors |
| `timeout_error` | budgets_timeouts, scenario_runner |
| `selector_not_found` | selectors, budgets_timeouts |
| `iata_mismatch` | combobox_commit, evidence |
| `price_extraction_failed` | plugins, evidence |
| `scope_conflict` | plugins, scenario_runner |
| `selector_spam` | selectors, budgets_timeouts |
| `infinite_recovery` | scenario_runner, budgets_timeouts |

### Adding New Topics

To add a new topic to kb_index.yaml:

1. **Add topic entry**:
```yaml
topics:
  - name: new_pattern
    description: "Brief description"
    files:
      - path: kb/30_patterns/new_pattern.md
        priority: 1
        tags: [relevant, tags]
```

2. **Add reason mapping** (if applicable):
```yaml
symptom_index:
  - symptom: "new_failure_reason"
    docs:
      - kb/30_patterns/new_pattern.md
```

3. **Update reason mapping in code** (if needed):

Edit [`utils/kb.py`](../utils/kb.py) and add to `REASON_TO_TOPICS`:
```python
REASON_TO_TOPICS = {
    # ... existing mappings
    "new_failure_reason": ["new_pattern", "related_topic"],
}
```

### Graceful Degradation

The KB loader is designed to never crash:
- Missing YAML → tries JSON fallback
- Missing JSON → returns empty `KBIndex(version="0")`
- Unknown topic → returns `[]`
- Unknown reason → returns `[]`

All file I/O errors are logged at WARNING level.

---

## Contributing to Documentation

### When to update kb/
- Core design principles changed
- New pattern implemented (date picker, combobox, etc.)
- New contract added (browser, plugins, etc.)
- Failure mode or evidence field added
- Troubleshooting procedure documented

### When to archive legacy notes
- Historical implementation reports (preserve in git history)
- Post-mortem analysis
- Audit findings
- Migration notes

### When to update kb_index.yaml
- New topic added to kb/
- New symptom documented in runtime_playbook
- New code hotspot with DOC: comment
- New tag for semantic search

---

## Documentation Standards

### Every kb/ document must have:
1. **Header block**:
   ```markdown
   **Purpose**: What this doc covers
   **When to read**: Use cases for reading this doc
   **Key invariants**: Core guarantees/principles
   ```

2. **Stable headings**: Use consistent heading structure for semantic lookup

3. **Failure modes section** (where relevant): Document explicit failure modes with evidence fields

4. **Related docs**: Link to related kb/ documents at bottom

5. **Code hotspots**: Reference code locations where pattern is implemented

### Avoid in kb/ documents:
- Long narrative storytelling (use git history for that)
- Temporary implementation notes
- "As of [date]" temporal markers (keep docs evergreen)
- Copy-pasted code blocks (reference code locations instead)

---

## Questions?

- **"Where's the date picker documentation?"** → `docs/kb/30_patterns/date_picker.md` (authoritative)
- **"Where are the ADRs?"** → `docs/kb/50_governance/adr/` (architecture decision records)
- **"How do I troubleshoot timeout errors?"** → `docs/kb/20_decision_system/runtime_playbook.md#symptom-timeout-error`
- **"What are the core principles?"** → `docs/kb/00_foundation/doctrine.md`

---

## Note on Historical Documentation

Historical implementation reports and phase analysis documents have been archived in git for reference but are **NOT authoritative**. They preserve context about how features evolved but may contain:
- Outdated implementation details
- Superseded approaches
- Temporary analysis notes

Always check `docs/kb/` for current, authoritative guidance.
