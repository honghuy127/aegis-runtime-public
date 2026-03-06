"""Governance: disallow fixed calendar dates in tests except explicit parsing cases.

Policy intent:
- Runtime-flow/integration tests must use deterministic dynamic dates from
  `tests.utils.dates`.
- Fixed dates are allowed only for parsing/formatting subject tests, or with an
  explicit inline marker:
    # allow-fixed-date: parsing-test

This test enforces policy for new/edited tests while preserving a small
legacy grandfather list to avoid destabilizing unrelated test intent in one
sweep.
"""

from __future__ import annotations

import re
from pathlib import Path

DATE_LITERAL_RE = re.compile(r"\b(19|20)\d{2}-\d{2}-\d{2}\b")
DATE_CTOR_RE = re.compile(r"\bdate\s*\(\s*(19|20)\d{2}\s*,")
DATETIME_CTOR_RE = re.compile(r"\bdatetime\s*\(\s*(19|20)\d{2}\s*,")
ALLOW_MARKER = "allow-fixed-date: parsing-test"

PARSE_FILE_HINTS = ("parse", "parsing", "date_text", "format")

# Legacy baseline captured during stabilization. New files with fixed dates must
# not be added to this set without explicit governance review.
LEGACY_GRANDFATHERED = {
    "tests/manual/demo_debug_mode.py",
    "tests/test_adapter_fallback_observability.py",
    "tests/test_audit_extraction_pipeline.py",
    "tests/test_audit_site_adapter_fallback.py",
    "tests/test_calendar_driver_unit.py",
    "tests/test_calendar_snapshot.py",
    "tests/test_date_text_utils.py",
    "tests/test_evidence_dump.py",
    "tests/test_extraction_coordination_integration.py",
    "tests/test_extraction_router_llm_gating.py",
    "tests/test_extractor.py",
    "tests/test_extractor_contract.py",
    "tests/test_extractor_multimodal_judge_mode.py",
    "tests/test_flight_plan.py",
    "tests/test_gf_set_date.py",
    "tests/test_gf_set_date_enhanced.py",
    "tests/test_google_deeplink_page_state_recovery.py",
    "tests/test_google_flights_agent_profile_selectors.py",
    "tests/test_google_flights_date_picker_commit.py",
    "tests/test_google_flights_deeplink.py",
    "tests/test_google_recovery_collab.py",
    "tests/test_google_recovery_route_core_gate.py",
    "tests/test_kb_cards_loader.py",
    "tests/test_kb_loader.py",
    "tests/test_llm_multimodal_context.py",
    "tests/test_llm_ui_assist_target_regions.py",
    "tests/test_main_runtime_args.py",
    "tests/test_plugin_extraction_router.py",
    "tests/test_prompt_formatting.py",
    "tests/test_prompt_templates_contract.py",
    "tests/test_route_binding_gate.py",
    "tests/test_run_input_config.py",
    "tests/test_scenario_plan_binding.py",
    "tests/test_scenario_plan_generation.py",
    "tests/test_scenario_return_route_state_fallback.py",
    "tests/test_scenario_runner_timeouts.py",
    "tests/test_selector_hints.py",
    "tests/test_site_adapter_registry.py",
    "tests/test_skyscanner_agent_profile_selectors.py",
    "tests/test_skyscanner_minimal_integration.py",
    "tests/test_ui_snapshot_schema.py",
    "tests/test_vlm_deeplink_skip.py",
}


def _is_parse_context(path: Path, lines: list[str], lineno: int) -> bool:
    name = path.name.lower()
    if any(h in name for h in PARSE_FILE_HINTS):
        return True

    # Allow within parsing/format-focused test names.
    for idx in range(lineno - 1, -1, -1):
        candidate = lines[idx].strip().lower()
        if candidate.startswith("def test_"):
            return any(h in candidate for h in PARSE_FILE_HINTS)
    return False


def _iter_scan_targets(repo_root: Path):
    patterns = [
        "tests/**/*.py",
        "tests/fixtures/**/*.json",
        "tests/fixtures/**/*.meta.json",
        "tests/fixtures/**/*.html",
        "tests/**/*.md",
    ]
    seen: set[Path] = set()
    for pattern in patterns:
        for path in repo_root.glob(pattern):
            if path.is_file() and path not in seen:
                seen.add(path)
                yield path


def _find_violations(repo_root: Path):
    violations: list[tuple[str, int, str]] = []

    for path in _iter_scan_targets(repo_root):
        rel = path.relative_to(repo_root).as_posix()
        if rel in LEGACY_GRANDFATHERED:
            continue

        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()

        for lineno, line in enumerate(lines, start=1):
            if ALLOW_MARKER in line:
                continue

            if not (
                DATE_LITERAL_RE.search(line)
                or DATE_CTOR_RE.search(line)
                or DATETIME_CTOR_RE.search(line)
            ):
                continue

            if _is_parse_context(path, lines, lineno):
                continue

            snippet = line.strip()
            violations.append((rel, lineno, snippet))

    return violations


def test_no_fixed_dates_in_non_parsing_tests():
    repo_root = Path(__file__).resolve().parents[1]
    violations = _find_violations(repo_root)

    if not violations:
        return

    lines = [
        "Found prohibited fixed-date usage in tests.",
        "Use tests.utils.dates (future_date/trip_dates/iso) for runtime-flow tests.",
        "If this is truly a parsing/format subject test, use:",
        f"  # {ALLOW_MARKER}",
        "",
    ]
    for rel, lineno, snippet in violations[:200]:
        lines.append(f"- {rel}:{lineno}: {snippet}")

    raise AssertionError("\n".join(lines))
