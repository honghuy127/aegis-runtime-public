# Test Suite Organization

This directory contains the flight price watcher test suite, organized by speed and dependencies.

## Test Markers

Tests are categorized using pytest markers for flexible CI/CD pipelines:

### `@pytest.mark.llm`
Tests that depend on LLM client, model router, or LLM-based extraction logic.
These tests are **skipped by default** and require `--run-llm` flag to execute.

Examples:
- LLM client timeout/fallback tests
- Model router decision tests
- Prompt validation and template tests
- LLM extraction with mocked/fake clients

**Runtime**: Variable (fast if using mocks)
**Run command**: `pytest --run-llm -m llm -q`

### `@pytest.mark.vlm`
Tests that depend on VLM (vision LLM) client, image preprocessing, or vision-based extraction.
These tests are **skipped by default** and require `--run-vlm` flag to execute.

Examples:
- VLM validation and schema tests
- VLM adaptive retry tests
- VLM image preprocessing tests
- Route binding with VLM probes

**Runtime**: Variable (fast if using mocks)
**Run command**: `pytest --run-vlm -m vlm -q`

### `@pytest.mark.integration`
Tests that touch real filesystem, databases, or external services (with mocks/temp files).

Examples:
- Database operations (SQLite with tmp_path)
- File I/O tests (using tmp_path fixtures)
- Tests requiring actual LLM/VLM calls (manual/nightly only)

**Runtime**: Variable (0.1s - 10s per test)
**Run command**: `pytest -m integration -q`

### `@pytest.mark.e2e`
End-to-end tests requiring real browser automation or network calls.

**Runtime**: Slow (>10s per test)
**Run command**: `pytest -m e2e -q`

### `@pytest.mark.slow`
Tests with >1s execution time (computation-heavy, retries, timeouts).

**Runtime**: >1s per test
**Run command**: `pytest -m slow -q`

### `@pytest.mark.heavy`
Internal marker for tests with significant computation or mocking overhead.
Tests marked `heavy` may also be marked `llm` or `vlm`.

**Runtime**: >0.5s per test
**Run command**: `pytest -m heavy -q`

### Unit tests (no marker)
Fast, offline tests with no I/O dependencies and no external model calls. Use mocks/monkeypatch/fakes.

**Runtime**: <100ms per test
**Run command**: `pytest -m "not integration and not e2e and not slow and not llm and not vlm" -q`

## Running Tests

### Fast suite (default, no LLM/VLM)
```bash
pytest -q
```
**Summary**: Skips LLM/VLM tests by default. Fast unit tests run immediately.
**Expected**: Fast, offline, and deterministic.

### LLM tests only
```bash
pytest --run-llm -m llm -q
```
**Summary**: Runs all tests marked `@pytest.mark.llm` with fake/mocked clients.
**Expected**: Runtime varies based on mocks and environment.

### VLM tests only
```bash
pytest --run-vlm -m vlm -q
```
**Summary**: Runs all tests marked `@pytest.mark.vlm` with fake/mocked clients.
**Expected**: Runtime varies based on mocks and environment.

### Both LLM and VLM tests
```bash
pytest --run-llm --run-vlm -m "llm or vlm" -q
```
**Summary**: Runs all tests with both markers using mocked clients.
**Expected**: Runtime varies based on mocks and environment.

### Integration suite (filesystem/DB tests)
```bash
pytest -q -m integration
```
**Expected**: Runtime varies based on fixture complexity.

### Full suite (all tests, including LLM/VLM)
```bash
pytest --run-llm --run-vlm -q
```
**Expected**: Runtime varies based on model availability and environment.

### Quarantined tests (explicit only)
```bash
pytest -q tests/quarantine -o norecursedirs=
```

### Manual scripts (explicit only)
```bash
python tests/manual/quick_test.py
python tests/manual/ollama_server_smoke.py --model <model>
python tests/manual/demo_debug_mode.py
```

### Specific test file
```bash
pytest tests/test_extractor_contract.py -v
```

### With coverage
```bash
pytest --cov=core --cov=llm --cov-report=term-missing
```

## Test Organization by File

### Extraction & LLM (marked @pytest.mark.llm and/or @pytest.mark.vlm)
- **test_extractor.py** - Extraction tests with mocked LLM/VLM calls
- **test_extractor_contract.py** - Fast contract tests using fake LLM/VLM
- **test_audit_extraction_pipeline.py** - Plugin extraction router audit tests
- **test_extraction_router_llm_gating.py** - LLM gating behavior tests
- **test_llm_client.py** - LLM client circuit breaker and timeout tests (@pytest.mark.llm)
- **test_llm_diagnostics.py** - LLM error diagnostics classification (@pytest.mark.llm)
- **test_llm_attempt_policy.py** - LLM budget management tests (@pytest.mark.llm)
- **test_llm_client_error_normalization.py** - LLM error categorization (@pytest.mark.llm)
- **test_llm_metrics_store.py** - LLM metrics persistence (@pytest.mark.llm + @pytest.mark.integration)
- **test_model_router.py** - Model router decision logic (@pytest.mark.llm)
- **test_vlm_validation.py** - VLM schema validation (@pytest.mark.vlm)
- **test_vlm_adaptive_retry.py** - VLM retry logic (@pytest.mark.vlm)
- **test_vlm_image_preprocess.py** - VLM image preprocessing (@pytest.mark.vlm)
- **test_vlm_deeplink_skip.py** - VLM deferral for deeplinks (@pytest.mark.vlm)
- **test_prompt_templates_contract.py** - LLM prompt templates (@pytest.mark.llm)
- **test_prompt_validators.py** - LLM prompt validators (@pytest.mark.llm)
- **test_selector_quality.py** - Selector stability tests (@pytest.mark.llm + @pytest.mark.vlm)
- **test_route_binding_gate.py** - Route binding with VLM probes (@pytest.mark.vlm)
- **test_refactor_safety_stage0.py** - Safety-net tests for LLM code (@pytest.mark.llm)
- **test_ui_language_hinting.py** - Language detection for LLM hints (@pytest.mark.llm)

### Coordination & Routing (marked @pytest.mark.llm and/or @pytest.mark.vlm)
- **test_coordination_gates.py** - Coordination gating logic (@pytest.mark.llm + @pytest.mark.vlm)
- **test_coordination_router_integration.py** - Coordination router integration (@pytest.mark.llm + @pytest.mark.vlm)
- **test_coordination_monitoring.py** - Coordination monitoring tests
- **test_coordination_integration.py** - Full coordination integration tests

### Scenario & Planning
- **test_scenario_runner_timeouts.py** - Scenario timeout logic
- **test_scenario_plan_binding.py** - Plan-route binding tests
- **test_scenario_debug_snapshot.py** - Debug snapshot tests

### Configuration
- **test_thresholds.py** - Threshold config smoke tests
- **test_alerts_config.py** - Alert config tests
- **test_services_config.py** - Service config tests
- **test_run_input_config.py** - Runtime input validation

### Storage
- **test_storage_maintenance.py** - DB pruning/log trimming (@pytest.mark.integration)
- **test_evidence_dump.py** - Evidence checkpoint I/O (@pytest.mark.integration)
- **test_plan_store.py** - Plan persistence tests
- **test_knowledge_store.py** - Knowledge store tests
- **test_shared_knowledge_store.py** - Shared knowledge store tests
- **test_google_flights_deeplink.py** - Deeplink generation tests
- **test_skyscanner_activation.py** - Skyscanner activation tests

### Utilities
- **test_drift.py** - Drift detection tests (legacy + modern)
- **test_route_binding_gate.py** - Route binding validation
- **test_selector_quality.py** - Selector stability tests
- **test_ui_tokens.py** - UI token normalization
- **test_ui_language_hinting.py** - Language detection tests

## Fake Clients (tests/fakes/)

### fake_llm_client.py
Provides deterministic LLM responses for contract testing:
- `make_fake_llm_missing_price()` - Simulates LLM missing price
- `make_fake_llm_circuit_open()` - Simulates circuit breaker open
- `make_fake_llm_timeout()` - Simulates timeout
- `make_fake_llm_successful()` - Returns successful extraction

**Usage**:
```python
from tests.fakes.fake_llm_client import patch_parse_html_with_llm, MISSING_PRICE_RESPONSE

def test_heuristic_fallback(monkeypatch):
    patch_parse_html_with_llm(monkeypatch, MISSING_PRICE_RESPONSE)
    result = extract_with_llm(html="...", site="google_flights", task="price")
    assert result["source"] == "heuristic_html"
```

### fake_vlm_client.py
Provides deterministic VLM responses for contract testing:
- `make_fake_vlm_non_flight_scope()` - Simulates non-flight scope detection
- `make_fake_vlm_route_mismatch()` - Simulates route mismatch
- `make_fake_vlm_missing_price()` - Simulates VLM missing price
- `make_fake_vlm_successful()` - Returns successful extraction

**Usage**:
```python
from tests.fakes.fake_vlm_client import patch_analyze_page_ui_with_vlm, NON_FLIGHT_SCOPE_RESPONSE

def test_scope_guard(monkeypatch):
    patch_analyze_page_ui_with_vlm(monkeypatch, NON_FLIGHT_SCOPE_RESPONSE)
    result = extract_with_llm(html="...", screenshot_path="/path.png")
    assert result["scope"] == "package_bundle"
```

## Manual/Nightly Integration Tests

For tests requiring real LLM/VLM (not currently automated):

### Ollama smoke test
```bash
python tests/manual/ollama_server_smoke.py --model qwen3:8b
python tests/manual/ollama_server_smoke.py --model qwen3-vl:8b --vlm-image storage/debug_html/screenshot.png
```

### Quick smoke test
```bash
python tests/manual/quick_test.py
```

## CI Recommendations

### Pre-commit hook (fast suite)
```bash
pytest -q -m "not integration and not e2e and not slow" --maxfail=1
```

### Pull request (full suite)
```bash
pytest -q --cov=core --cov=llm --cov-report=term-missing
```

### Nightly (with real LLM/VLM)
```bash
# Not yet automated - requires Ollama server
python tests/manual/ollama_server_smoke.py --model qwen3:8b
```

## Debugging Tips

### Run single test with verbose output
```bash
pytest tests/test_extractor_contract.py::TestHeuristicFallbackRouting::test_heuristic_fallback_extracts_minimum_visible_fare -vv
```

### Show print statements
```bash
pytest tests/test_extractor.py -s
```

### Debug with pdb
```bash
pytest tests/test_extractor.py --pdb
```

### List all tests without running
```bash
pytest --collect-only -q
```

### Show which tests match a marker
```bash
pytest -m integration --collect-only -q
```

## Adding New Tests

### Fast unit test (preferred)
```python
def test_extraction_routing(monkeypatch):
    """Use fake clients for fast, offline testing."""
    from tests.fakes.fake_llm_client import patch_parse_html_with_llm, MISSING_PRICE_RESPONSE

    patch_parse_html_with_llm(monkeypatch, MISSING_PRICE_RESPONSE)
    result = extract_with_llm(html="...", site="google_flights", task="price")
    assert result["source"] == "heuristic_html"
```

### Integration test (I/O required)
```python
import pytest

@pytest.mark.integration
def test_evidence_dump(tmp_path):
    """Use tmp_path for filesystem tests."""
    output_file = tmp_path / "evidence.json"
    write_evidence_checkpoint(output_file, {"price": 25986})
    assert output_file.exists()
```

### Real LLM/VLM test (manual only)
```python
import pytest

@pytest.mark.integration
@pytest.mark.slow
def test_real_llm_extraction():
    """Requires Ollama server running."""
    result = parse_html_with_llm(html="...", site="google_flights", task="price")
    assert result["price"] is not None
```

## Troubleshooting

### Tests are slow despite using fakes
- Check monkeypatch target path matches import in the module under test
- Verify `pytest -v` shows tests completing in <1s each

### Integration tests fail in CI
- Ensure tmp_path fixture is used for all file operations
- Check that no tests rely on external state or network

### Coverage gaps
```bash
pytest --cov=core.extractor --cov-report=html
open htmlcov/index.html
```
