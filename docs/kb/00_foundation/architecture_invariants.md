# Architecture Invariants

**Machine-Readable Catalog**: [architecture_invariants.yaml](./architecture_invariants.yaml)

This index provides quick navigation to architectural invariants. For complete details (statements, rationale, test references, module mappings), consult the YAML file.

**Last verified**: 2026-02-26

---

## A. Scenario Runner & Step Execution (5 invariants)

- **INV-SCENARIO-001**: Global wall-clock timeouts MUST abort immediately
- **INV-SCENARIO-002**: Selector timeouts MUST be bounded and clamped
- **INV-SCENARIO-003**: Optional fill actions MUST skip expensive recovery chains
- **INV-SCENARIO-004**: Depart date fill MUST soft-pass when date already bound in DOM
- **INV-SCENARIO-005**: Plugin readiness probes MUST be consumed when actionable

[All details → architecture_invariants.yaml: categories.SCENARIO]

---

## B. Budgeting & Watchdog Caps (5 invariants)

- **INV-BUDGET-001**: ActionBudget MUST track and enforce action limits
- **INV-BUDGET-002**: Wall-clock extraction cap MUST skip legacy extraction when hit
- **INV-BUDGET-003**: Wall-clock cap helper MUST only trigger when enabled and exceeded
- **INV-BUDGET-004**: Browser commit timeout MUST NOT exceed goto timeout
- **INV-BUDGET-005**: Remaining browser timeout MUST apply floor to prevent zero timeout

[All details → architecture_invariants.yaml: categories.BUDGET]

---

## C. Recovery & Fallback (5 invariants)

- **INV-RECOVERY-001**: Heuristic fallback MUST extract minimum visible fare when LLM misses price
- **INV-RECOVERY-002**: Route context MUST filter heuristic extraction
- **INV-RECOVERY-003**: Short metro codes MUST NOT match city name substrings
- **INV-RECOVERY-004**: Irrelevant page downgrade MUST require medium+ confidence VLM
- **INV-RECOVERY-005**: Error handling MUST NOT expose exceptions to callers

[All details → architecture_invariants.yaml: categories.RECOVERY]

---

## D. Selector Strategy (5 invariants)

- **INV-SELECTOR-001**: IATA ranking MUST prioritize parenthesized codes highest
- **INV-SELECTOR-002**: IATA validation MUST enforce 3-letter alphabetic format
- **INV-SELECTOR-003**: Substring IATA matches MUST require explicit click decision
- **INV-SELECTOR-004**: Button text selectors MUST NEVER emit bare text= patterns
- **INV-SELECTOR-005**: Timeout for Google Flights fill MUST NEVER be tiny

[All details → architecture_invariants.yaml: categories.SELECTOR]

---

## E. Internationalization (3 invariants)

- **INV-I18N-001**: Token prioritization MUST reorder by locale hint
- **INV-I18N-002**: Text normalization MUST fold full-width characters and spacing
- **INV-I18N-003**: Placeholder matching MUST use normalized exact match

[All details → architecture_invariants.yaml: categories.I18N]

---

## F. Plugin Architecture (5 invariants)

- **INV-PLUGIN-001**: Route binding gate MUST accept weak support when strong not required
- **INV-PLUGIN-002**: Route binding gate MUST reject weak support when strong required
- **INV-PLUGIN-003**: DOM/VLM fusion MUST promote weak to strong when both affirm
- **INV-PLUGIN-004**: Scope conflict resolution MUST require strong route support to override
- **INV-PLUGIN-005**: Explicit DOM mismatch MUST block route binding support

[All details → architecture_invariants.yaml: categories.PLUGIN]

---

## G. Evidence & Results (3 invariants)

- **INV-EVIDENCE-001**: StepResult MUST support success and failure factory methods
- **INV-EVIDENCE-002**: gf_set_date MUST validate role and date format before execution
- **INV-EVIDENCE-003**: Extraction result schema MUST include required fields

[All details → architecture_invariants.yaml: categories.EVIDENCE]

---

## H. Storage Layer (2 invariants)

- **INV-STORAGE-001**: Storage layer MUST NOT import Playwright or browser modules
- **INV-STORAGE-002**: LLM layer MUST NOT import scenario modules

[All details → architecture_invariants.yaml: categories.STORAGE]

---

## I. Prompt Engineering (4 invariants)

- **INV-PROMPT-001**: Prompt templates MUST retain JSON schema blocks
- **INV-PROMPT-002**: Prompt registry MUST resolve existing template IDs
- **INV-PROMPT-003**: Validators MUST accept existing valid payload shapes
- **INV-PROMPT-004**: Scenario/repair prompts MUST constrain action vocabulary

[All details → architecture_invariants.yaml: categories.PROMPT]

---

## J. Parsing & Validation (3 invariants)

- **INV-PARSE-001**: JSON parser MUST tolerate fenced code blocks
- **INV-PARSE-002**: Price coercion MUST preserve null with reason
- **INV-PARSE-003**: Base domain extraction MUST handle public suffixes

[All details → architecture_invariants.yaml: categories.PARSE]

---

## K. Image Handling (2 invariants)

- **INV-IMAGE-001**: VLM image variants MUST deduplicate identical blobs
- **INV-IMAGE-002**: VLM image variants MUST apply byte cap

[All details → architecture_invariants.yaml: categories.IMAGE]

---

## L. Service URLs (1 invariant)

- **INV-SERVICE-001**: Preferred URL MUST stay first when split-flow inactive

[All details → architecture_invariants.yaml: categories.SERVICE]

---

## M. Site Adapter & UI Driver Ownership (5 invariants)

- **INV-ADAPTER-001**: Exactly ONE site UI driver is active per run (agent OR legacy, never both)
- **INV-ADAPTER-002**: UI driver selection is config-driven with safe fallback
- **INV-ADAPTER-003**: Service extraction MUST be extraction-only (no browser interactions)
- **INV-ADAPTER-004**: Scenario site modules must not accumulate new UI logic
- **INV-ADAPTER-005**: Fallback events MUST be logged with diagnostic context
- **INV-ADAPTER-006**: Site-specific plan generation and utilities MUST live under core/scenario_runner/<site>/ (e.g. Google Flights under core/scenario_runner/google_flights/, Skyscanner under core/scenario_runner/skyscanner/)

[All details → architecture_invariants.yaml: categories.ADAPTER]

---

## N. Scenario Runner Orchestrator Discipline (4 invariants)

- **INV-SRUN-001**: `scenario_runner.py` MUST NOT import `core/service_runners/*`
- **INV-SRUN-002**: `scenario_runner.py` SHOULD only import from:
	- `core.scenario_runner.*`
	- `core.adapters.*`
	- `core.agent.*`
	- `core.browser`
	- `core.route_binding`
	- `core.scope_reconciliation`
	- `core.services`
	- `storage.*`
	- `utils.*`
	- `llm.*`
- **INV-SRUN-003**: Site-specific logic MUST live under `core/scenario_runner/<site>/`
- **INV-SRUN-004**: New service integration MUST:
	- Add a site module under `core/scenario_runner/<site>/`
	- Add a selector bank
	- Add a plan preset
	- NOT modify `scenario_runner.py` except for minimal wiring

---

## O. Documentation (1 invariant)

- **INV-DOCS-001**: Coding agents MUST use KB-first planning before patching behavior

[All details → architecture_invariants.yaml: categories.DOCS]

---

## Where to Patch: Decision Tree

For complete decision tree with symptoms, diagnostics, and relevant invariants, see [architecture_invariants.yaml: decision_tree](./architecture_invariants.yaml).

**Common scenarios covered**:
- Date picker not opening → INV-SCENARIO-004, INV-RECOVERY-005
- Route not binding → INV-PLUGIN-001, INV-SELECTOR-002
- Extraction timeouts → INV-BUDGET-002, INV-SELECTOR-005
- Unbounded retries → INV-BUDGET-004, INV-BUDGET-005, INV-RECOVERY-005
