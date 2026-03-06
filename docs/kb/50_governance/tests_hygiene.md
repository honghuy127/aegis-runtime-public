# Tests Hygiene Policy

## Purpose
Tests in this repository enforce runtime contracts, architecture invariants, and deterministic correctness checks. The default suite is designed to be fast, offline, and stable, while opt-in suites cover model-dependent or environment-specific behavior.

## Categories
- **core_contract**: Always-run contract tests for public interfaces and runtime contracts.
- **invariants**: Always-run architecture and layering invariants.
- **unit_fast**: Fast unit tests without external I/O or model calls.
- **integration_light**: Tests that touch filesystem or local state with fakes/fixtures; still fast and deterministic.
- **llm_or_vlm**: Tests that exercise LLM/VLM code paths; opt-in only.
- **one_shot_or_migration**: Historical or incident-repro tests; quarantined by default.
- **redundant_duplicates**: Tests that duplicate coverage; merge or remove when safe.

## Naming Convention
Use file names that reflect scope and intent:
- `test_*_contract.py` for contract-level expectations.
- `test_*_invariants.py` for architecture and layering invariants.
- `test_*_unit.py` for fast unit tests.
- `test_*_integration.py` for lightweight integration tests with local fixtures only.
- `test_*_manual.py` for tests not intended for default pytest discovery.

## Quarantine Policy
Historical or one-shot tests may be temporarily placed under `tests/quarantine/` and excluded from default test discovery. Quarantined tests must include a short header comment stating why they are quarantined and how to run them explicitly.

### Quarantine Entry Criteria
A test should be quarantined (not deleted) when:
- It validates a rare but critical incident reproduction and cannot yet be made stable without runtime changes.
- It targets a feature not yet implemented but provides specification value.
- It requires external dependencies (network, services) not available in CI but has long-term value.

### Quarantine Exit Criteria
A quarantined test should be promoted to the main suite when:
- **Uniqueness**: It provides coverage not present elsewhere in the suite.
- **Stability**: It can run deterministically without external services, time dependence, or local machine state.
- **Timelessness**: It asserts a durable rule rather than an incident snapshot.
- **Value**: Regression would be painful or expensive to rediscover.

Promotion process:
1. Rewrite the test to remove brittleness (use fakes/fixtures, remove hardcoded timestamps).
2. Rename to follow naming conventions (`test_*_contract.py`, `test_*_invariants.py`, etc.).
3. Move to `tests/` and remove quarantine header.
4. Verify it passes in `pytest -q`.

### Deletion Criteria
A quarantined test should be deleted when:
- **Redundant**: Full coverage already exists in the main suite.
- **Obsolete**: It targets code paths or features that no longer exist.
- **Transient**: It depends on transient artifacts (specific run IDs, timestamps, local machine state) and cannot be made stable within tests-only changes.
- **Unimplementable**: It describes a feature explicitly marked as not implemented with no timeline.

## Marker Policy
- `@pytest.mark.llm` and `@pytest.mark.vlm` indicate model-dependent tests and are skipped by default unless the corresponding CLI flag is provided.
- `@pytest.mark.manual` indicates a test intended for explicit invocation only.
- `@pytest.mark.integration` is reserved for deterministic local I/O with temp fixtures.
- Avoid network, browser, or external service dependencies in the default suite.

## Dynamic Date Policy
- Fixed calendar dates rot and create future-sensitive false failures in runtime-flow tests.
- For runtime scenario tests, integration-ish tests, and flow fixtures, use deterministic UTC helpers from `tests/utils/dates.py`:
  - `future_date(...)`
  - `trip_dates(...)`
  - `iso(...)`
- Use one deterministic test-session RNG seed only (provided by `tests/utils/dates.py`); do not create ad-hoc random date generators in test files.
- Fixed dates are allowed only when date parsing/formatting is the direct subject under test.
- Any fixed-date exception must include an inline marker:
  - `# allow-fixed-date: parsing-test`
- Governance enforcement:
  - `tests/test_governance_no_fixed_dates.py` scans tests and fails on prohibited fixed dates.

## Definition of Done for a New Test
A new test is complete when it:
- Is deterministic and offline by default.
- Uses the correct file naming convention and category.
- Declares the correct marker(s), if any.
- Does not require network or model access in the default run.
- Includes a clear docstring that explains what invariant or contract is being validated.

## Deletions
- None.
