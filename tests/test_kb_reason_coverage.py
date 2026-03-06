"""Guardrails for canonical reason coverage across KB artifacts."""

from pathlib import Path

import yaml

from core.scenario.reasons import REASON_REGISTRY, normalize_reason


REPO_ROOT = Path(__file__).resolve().parent.parent
TRIAGE_DECISION_TABLE = REPO_ROOT / "docs/kb/20_decision_system/triage_decision_table.yaml"
EVIDENCE_FIELDS = REPO_ROOT / "docs/kb/10_runtime_contracts/evidence_fields.yaml"


# Canonical reasons intentionally excluded from triage decision table coverage.
# Keep this list explicit and small; revisit exclusions when triage guidance is added.
TRIAGE_COVERAGE_EXCLUSIONS = {
    "deadline_hit",
    "deeplink_recovery_activation_unverified",
    "deeplink_recovery_rebind_unverified",
    "route_core_before_date_fill_unverified",
}

# Canonical reasons intentionally excluded from evidence map coverage.
# These are orchestration-state reasons with scenario-level artifacts rather than
# dedicated StepResult field contracts in reason_evidence_map.
EVIDENCE_COVERAGE_EXCLUSIONS = {
    "deeplink_recovery_activation_unverified",
    "deeplink_recovery_rebind_unverified",
    "route_core_before_date_fill_unverified",
}


def _load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _extract_triage_reason_codes() -> set[str]:
    data = _load_yaml(TRIAGE_DECISION_TABLE)
    reason_tree = data.get("reason_tree", {})
    codes: set[str] = set()

    if not isinstance(reason_tree, dict):
        return codes

    for key, value in reason_tree.items():
        if isinstance(value, dict) and "code" not in value and isinstance(key, str):
            normalized = normalize_reason(key)
            codes.add(normalized if normalized != "unknown" else key)
            continue
        if isinstance(value, list):
            for item in value:
                if not isinstance(item, dict):
                    continue
                code = str(item.get("code", "")).strip()
                if not code:
                    continue
                normalized = normalize_reason(code)
                codes.add(normalized if normalized != "unknown" else code)
    return codes


def _extract_evidence_reason_codes() -> set[str]:
    data = _load_yaml(EVIDENCE_FIELDS)
    reason_map = data.get("reason_evidence_map", {})
    if not isinstance(reason_map, dict):
        return set()

    codes: set[str] = set()
    for code in reason_map:
        normalized = normalize_reason(code)
        codes.add(normalized if normalized != "unknown" else code)
    return codes


def test_canonical_reason_registry_has_triage_coverage_or_explicit_exclusion():
    canonical = set(REASON_REGISTRY.keys())
    triage_codes = _extract_triage_reason_codes()
    missing = sorted(canonical - triage_codes - TRIAGE_COVERAGE_EXCLUSIONS)
    assert not missing, (
        "Canonical reasons missing triage coverage. "
        f"Missing: {missing}. "
        "Add triage_decision_table entries or add explicit exclusions in this test."
    )


def test_canonical_reason_registry_has_evidence_coverage_or_explicit_exclusion():
    canonical = set(REASON_REGISTRY.keys())
    evidence_codes = _extract_evidence_reason_codes()
    missing = sorted(canonical - evidence_codes - EVIDENCE_COVERAGE_EXCLUSIONS)
    assert not missing, (
        "Canonical reasons missing reason_evidence_map coverage. "
        f"Missing: {missing}. "
        "Add evidence_fields reason_evidence_map entries or explicit exclusions in this test."
    )
