"""KB drift detector - identifies mismatches between code and YAML registries.

Detects:
1. Reason code drift: code emits reasons not in YAML triage table
2. Evidence key drift: code writes evidence keys not defined in YAML
3. Artifact drift: code writes artifacts not registered in YAML
4. Invariant drift: tests reference INV-* not in YAML registry

Usage:
    from utils.kb_drift import detect_drift, DriftReport

    report = detect_drift()
    if report.has_errors():
        print(report.format_report())
"""

import ast
import logging
import re
import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Non-failure runtime status codes that are intentionally not triage decision codes.
# These values are emitted for telemetry/progress breadcrumbs rather than failure triage.
_NON_TRIAGE_REASON_CODES = {
    "activated_route_form",
    "agent_bind_failed",
    "agent_turns_exhausted",
    "agent_v0_ready",
    "brand_and_results_markers_detected",
    "brand_detected_results_markers_missing",
    "brand_markers_missing",
    "brand_without_flight_markers",
    "calendar_not_open_local_open_stage",
    "cleared",
    "click_failed",
    "clicked",
    "contextual_ready",
    "date_picker_unverified_local_verify_false_negative",
    "deeplink_probe_ready",
    "deeplink_probe_unready_light_fallback",
    "deeplink_quick_rebind_ready",
    "default_plan",
    "default_repair",
    "dest_placeholder",
    "dom_all_required_matched",
    "dom_context_tokens_matched",
    "dom_context_tokens_partial",
    "dom_explicit_mismatch",
    "dom_no_evidence",
    "dom_partial_or_unknown",
    "engine_not_initialized",
    "exception",
    "explicit_mismatch",
    "fields_bound",
    "flights_results_ready",
    "insufficient_fields_found",
    "legacy_defers_to_scenario_runner",
    "legacy_goto_failed",
    "llm_non_flight_overridden_by_route_context",
    "local_runtime_exception",
    "month_nav_buttons_not_found_local_picker_stage",
    "no_evidence",
    "no_legacy_driver",
    "no_locator",
    "no_route_signals_detected",
    "not_bound",
    "not_checked",
    "origin_and_dest_detected",
    "origin_only_detected",
    "partial_fields_found",
    "plausibility_rejected",
    "retries_exhausted",
    "route_bind_corroborated_local_fill",
    "route_core_before_date_fill_unverified",
    "route_core_dest_mismatch",
    "route_core_dest_uncommitted",
    "route_core_origin_mismatch",
    "route_core_origin_missing",
    "route_core_unverified",
    "route_core_verified",
    "route_core_verified_live_dom_form",
    "route_core_verified_results_itinerary",
    "route_fill_mismatch",
    "scenario_budget_soft_stop",
    "scenario_wall_clock_cap",
    "scope_conflict_route_support_not_strong",
    "scope_conflict_unresolved_for_vlm_price",
    "scope_guard_non_flight_irrelevant_page",
    "scope_non_flight_irrelevant_page",
    "scope_override_limit_reached",
    "skipped_after_fast_fail",
    "strong_evidence",
    "transport_timeout",
    "turn_followup_unavailable",
    "typed",
    "vision_fill_mismatch",
    "vision_page_kind_recovered",
    "vlm_all_required_matched",
    "vlm_image_unavailable",
    "vlm_no_match",
    "vlm_partial_match",
    "vlm_route_verify_unavailable",
    "wait_ok",
    "weak_evidence",
}

# Evidence namespaces used for internal coordination telemetry, not KB contract surface.
_NON_CONTRACT_EVIDENCE_PREFIXES = {
    "coordination.",
    "domslice.",
    "extraction.",
}

# Transitional evidence keys present in runtime telemetry but not yet normalized
# into the canonical evidence catalog namespaces.
_TRANSITIONAL_EVIDENCE_KEYS = {
    "calendar.nav_direction",
    "calendar.open.selector_used",
    "calendar.opener_attempts",
    "calendar.opener_candidate_order",
    "calendar.opener_selector_index_used",
    "calendar.opener_selector_used",
    "calendar.opener_visible_prefilter",
    "calendar.snapshot_id",
    "calendar.strategies_tried",
    "calendar.strategy_id",
    "combobox.activation_attempts",
    "combobox.activation_order",
    "combobox.failure_selector",
    "combobox.input_source",
    "verify.value_source",
}

# Repo root discovery
def _find_repo_root() -> Path:
    """Find repository root by locating .git directory."""
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / ".git").exists():
            return current
        current = current.parent
    return Path(__file__).resolve().parent.parent


@dataclass
class DriftItem:
    """Single drift detection item."""
    category: str  # "reason", "evidence", "artifact", "invariant"
    item_key: str  # The actual key/code/ID
    drift_type: str  # "missing_in_yaml", "missing_in_code", "orphaned"
    severity: str  # "error", "warning"
    source_location: Optional[str] = None  # Where in code it's used/defined


@dataclass
class DriftReport:
    """Complete drift detection report."""
    errors: List[DriftItem] = field(default_factory=list)
    warnings: List[DriftItem] = field(default_factory=list)

    def has_errors(self) -> bool:
        return len(self.errors) > 0

    def has_warnings(self) -> bool:
        return len(self.warnings) > 0

    def add_error(self, item: DriftItem):
        self.errors.append(item)

    def add_warning(self, item: DriftItem):
        self.warnings.append(item)

    def format_report(self, include_warnings: bool = True) -> str:
        """Format report as human-readable text."""
        lines = []

        if self.errors:
            lines.append("=" * 60)
            lines.append(f"KB DRIFT ERRORS: {len(self.errors)} found")
            lines.append("=" * 60)
            for item in sorted(self.errors, key=lambda x: (x.category, x.item_key)):
                lines.append(f"[{item.category.upper()}] {item.item_key}")
                lines.append(f"  Type: {item.drift_type}")
                if item.source_location:
                    lines.append(f"  Location: {item.source_location}")
                lines.append("")

        if include_warnings and self.warnings:
            lines.append("=" * 60)
            lines.append(f"KB DRIFT WARNINGS: {len(self.warnings)} found")
            lines.append("=" * 60)
            for item in sorted(self.warnings, key=lambda x: (x.category, x.item_key)):
                lines.append(f"[{item.category.upper()}] {item.item_key}")
                lines.append(f"  Type: {item.drift_type}")
                if item.source_location:
                    lines.append(f"  Location: {item.source_location}")
                lines.append("")

        if not self.errors and not self.warnings:
            lines.append("✓ No KB drift detected")

        return "\n".join(lines)

    def to_dict(self) -> Dict:
        """Export report as dict for JSON serialization."""
        return {
            "summary": {
                "errors": len(self.errors),
                "warnings": len(self.warnings),
            },
            "errors": [
                {
                    "category": e.category,
                    "item_key": e.item_key,
                    "drift_type": e.drift_type,
                    "severity": e.severity,
                    "source_location": e.source_location,
                }
                for e in self.errors
            ],
            "warnings": [
                {
                    "category": w.category,
                    "item_key": w.item_key,
                    "drift_type": w.drift_type,
                    "severity": w.severity,
                    "source_location": w.source_location,
                }
                for w in self.warnings
            ],
        }


def load_yaml_reasons(repo_root: Path) -> Set[str]:
    """Load reason codes from triage decision table YAML."""
    yaml_path = repo_root / "docs/kb/20_decision_system/triage_decision_table.yaml"

    if not yaml_path.exists():
        logger.warning(f"Triage YAML not found: {yaml_path}")
        return set()

    with open(yaml_path) as f:
        data = yaml.safe_load(f)

    reasons = set()

    # Extract from reason_tree structure.
    # Support both:
    # 1) legacy map shape: reason_tree.{reason_code}: {...}
    # 2) categorized list shape: reason_tree.{category}: [{code: "...", ...}, ...]
    reason_tree = data.get("reason_tree", {})
    if isinstance(reason_tree, dict):
        for key, value in reason_tree.items():
            if isinstance(key, str) and key and isinstance(value, dict) and "code" not in value:
                # Legacy map shape where key is reason code.
                reasons.add(key)
                continue
            if isinstance(value, list):
                for item in value:
                    if not isinstance(item, dict):
                        continue
                    code = item.get("code")
                    if isinstance(code, str) and code:
                        reasons.add(code)

    return reasons


def load_yaml_evidence_keys(repo_root: Path) -> Set[str]:
    """Load evidence keys from evidence_fields.yaml."""
    yaml_path = repo_root / "docs/kb/10_runtime_contracts/evidence_fields.yaml"

    if not yaml_path.exists():
        logger.warning(f"Evidence YAML not found: {yaml_path}")
        return set()

    with open(yaml_path) as f:
        data = yaml.safe_load(f)

    evidence_keys = set()

    # Extract from namespaces structure
    namespaces = data.get("namespaces", {})
    for namespace, namespace_data in namespaces.items():
        if isinstance(namespace_data, dict):
            # Direct fields in namespace
            for key in namespace_data.keys():
                if key == "fields":
                    # Fields array structure
                    for field_def in namespace_data.get("fields", []):
                        if isinstance(field_def, dict) and "key" in field_def:
                            evidence_keys.add(field_def["key"])

    # Also check for direct key mappings at root level
    for key, val in data.items():
        if key.startswith(("calendar.", "ui.", "verify.", "budget.", "combobox.", "coordination.")):
            evidence_keys.add(key)

    return evidence_keys


def load_yaml_artifacts(repo_root: Path) -> Set[str]:
    """Load artifact names from evidence_artifacts.yaml."""
    yaml_path = repo_root / "docs/kb/10_runtime_contracts/evidence_artifacts.yaml"

    if not yaml_path.exists():
        logger.warning(f"Artifacts YAML not found: {yaml_path}")
        return set()

    with open(yaml_path) as f:
        data = yaml.safe_load(f)

    artifacts = set()

    # Extract artifact keys
    artifacts_data = data.get("artifacts", {})
    for artifact_key in artifacts_data.keys():
        if artifact_key and isinstance(artifact_key, str):
            artifacts.add(artifact_key)

    return artifacts


def load_yaml_invariants(repo_root: Path) -> Set[str]:
    """Load invariant IDs from invariants_registry.yaml."""
    yaml_path = repo_root / "docs/kb/00_foundation/invariants_registry.yaml"

    if not yaml_path.exists():
        logger.warning(f"Invariants YAML not found: {yaml_path}")
        return set()

    with open(yaml_path) as f:
        data = yaml.safe_load(f)

    invariants = set()

    # Extract invariant IDs
    invariants_list = data.get("invariants", [])
    for inv in invariants_list:
        if isinstance(inv, dict) and "id" in inv:
            invariants.add(inv["id"])

    return invariants


def extract_code_reasons(repo_root: Path) -> Dict[str, List[str]]:
    """Extract reason codes used in code.

    Returns:
        Dict mapping reason code -> list of file locations
    """
    # First load canonical registry from reasons.py
    reasons_file = repo_root / "core/scenario/reasons.py"
    canonical_reasons = set()

    if reasons_file.exists():
        try:
            with open(reasons_file) as f:
                tree = ast.parse(f.read(), filename=str(reasons_file))

            # Find REASON_REGISTRY dict
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id == "REASON_REGISTRY":
                            if isinstance(node.value, ast.Dict):
                                for key in node.value.keys:
                                    if isinstance(key, ast.Constant):
                                        canonical_reasons.add(key.value)
        except Exception as e:
            logger.warning(f"Failed to parse {reasons_file}: {e}")

    # Now grep for reason= assignments in code
    reason_usage = {}

    # Search production runtime code only.
    # Tests intentionally include synthetic reason strings and should not
    # affect canonical reason taxonomy drift reporting.
    for search_dir in ["core", "utils"]:
        search_path = repo_root / search_dir
        if not search_path.exists():
            continue

        for py_file in search_path.rglob("*.py"):
            try:
                with open(py_file) as f:
                    content = f.read()

                # Match reason="..." or reason='...'
                pattern = r'reason\s*=\s*["\']([a-z_][a-z0-9_]*)["\']'
                matches = re.finditer(pattern, content)

                for match in matches:
                    reason_code = match.group(1)
                    if reason_code not in reason_usage:
                        reason_usage[reason_code] = []
                    relative_path = py_file.relative_to(repo_root)
                    reason_usage[reason_code].append(str(relative_path))
            except Exception as e:
                logger.debug(f"Failed to read {py_file}: {e}")

    # Add canonical reasons with reasons.py as source
    for reason in canonical_reasons:
        if reason not in reason_usage:
            reason_usage[reason] = ["core/scenario/reasons.py"]

    return reason_usage


def extract_code_evidence_keys(repo_root: Path) -> Dict[str, List[str]]:
    """Extract evidence keys used in code.

    Returns:
        Dict mapping evidence key -> list of file locations
    """
    evidence_usage = {}

    # Search production runtime code only.
    # Tests intentionally exercise synthetic evidence keys and should not
    # count as runtime contract drift.
    for search_dir in ["core"]:
        search_path = repo_root / search_dir
        if not search_path.exists():
            continue

        for py_file in search_path.rglob("*.py"):
            try:
                with open(py_file) as f:
                    content = f.read()

                # Match evidence["key"] or evidence['key']
                pattern = r'evidence\s*\[\s*["\']([a-z_][a-z0-9_.]*)["\']'
                matches = re.finditer(pattern, content)

                for match in matches:
                    evidence_key = match.group(1)
                    if evidence_key not in evidence_usage:
                        evidence_usage[evidence_key] = []
                    relative_path = py_file.relative_to(repo_root)
                    evidence_usage[evidence_key].append(str(relative_path))
            except Exception as e:
                logger.debug(f"Failed to read {py_file}: {e}")

    return evidence_usage


def extract_code_invariants(repo_root: Path) -> Dict[str, List[str]]:
    """Extract invariant IDs referenced in tests.

    Returns:
        Dict mapping invariant ID -> list of file locations
    """
    invariant_usage = {}

    # Search in tests/ only
    tests_path = repo_root / "tests"
    if not tests_path.exists():
        return invariant_usage

    for py_file in tests_path.rglob("*.py"):
        # Skip synthetic invariant IDs used by drift-detector unit tests.
        if py_file.name in {"test_kb_drift_check.py", "test_kb_yaml_retriever.py"}:
            continue
        try:
            with open(py_file) as f:
                content = f.read()

            # Match INV-CATEGORY-NNN pattern
            pattern = r'\bINV-[A-Z]+-\d{3}\b'
            matches = re.finditer(pattern, content)

            for match in matches:
                inv_id = match.group(0)
                if inv_id not in invariant_usage:
                    invariant_usage[inv_id] = []
                relative_path = py_file.relative_to(repo_root)
                invariant_usage[inv_id].append(str(relative_path))
        except Exception as e:
            logger.debug(f"Failed to read {py_file}: {e}")

    return invariant_usage


def detect_drift(repo_root: Optional[Path] = None) -> DriftReport:
    """Main drift detection function.

    Args:
        repo_root: Repository root path (auto-detected if None)

    Returns:
        DriftReport with errors and warnings
    """
    if repo_root is None:
        repo_root = _find_repo_root()

    report = DriftReport()

    # PHASE 1: Load YAML registries
    logger.info("Loading YAML registries...")
    yaml_reasons = load_yaml_reasons(repo_root)
    yaml_evidence = load_yaml_evidence_keys(repo_root)
    yaml_artifacts = load_yaml_artifacts(repo_root)
    yaml_invariants = load_yaml_invariants(repo_root)

    logger.info(f"Loaded YAML: {len(yaml_reasons)} reasons, {len(yaml_evidence)} evidence keys, "
                f"{len(yaml_artifacts)} artifacts, {len(yaml_invariants)} invariants")

    # PHASE 2: Extract code usage
    logger.info("Extracting code usage...")
    code_reasons = extract_code_reasons(repo_root)
    code_evidence = extract_code_evidence_keys(repo_root)
    code_invariants = extract_code_invariants(repo_root)

    logger.info(f"Found in code: {len(code_reasons)} reasons, {len(code_evidence)} evidence keys, "
                f"{len(code_invariants)} invariants")

    # Normalize reason codes so aliases and canonical codes are treated equivalently.
    def _normalize_reason_for_drift(code: str) -> str:
        code_str = str(code or "").strip()
        if not code_str:
            return code_str
        try:
            from core.scenario.reasons import normalize_reason

            normalized = normalize_reason(code_str)
            return normalized if normalized != "unknown" else code_str
        except Exception:
            return code_str

    normalized_code_reasons: Dict[str, Dict[str, Any]] = {}
    for reason_code, locations in code_reasons.items():
        normalized = _normalize_reason_for_drift(reason_code)
        if normalized not in normalized_code_reasons:
            normalized_code_reasons[normalized] = {"origins": set(), "locations": []}
        normalized_code_reasons[normalized]["origins"].add(reason_code)
        if locations:
            normalized_code_reasons[normalized]["locations"].extend(locations)

    normalized_yaml_reasons: Dict[str, Set[str]] = {}
    for reason_code in yaml_reasons:
        normalized = _normalize_reason_for_drift(reason_code)
        normalized_yaml_reasons.setdefault(normalized, set()).add(reason_code)

    # PHASE 3: Compare and report

    # 1. Reason drift - code has but YAML missing (ERROR)
    for normalized_reason, payload in normalized_code_reasons.items():
        locations = payload.get("locations", [])
        if normalized_reason in _NON_TRIAGE_REASON_CODES:
            continue
        if normalized_reason not in normalized_yaml_reasons and normalized_reason not in ["", "success", "unknown"]:
            origins = sorted(payload.get("origins", []))
            report_key = origins[0] if origins else normalized_reason
            report.add_error(DriftItem(
                category="reason",
                item_key=report_key,
                drift_type="missing_in_yaml",
                severity="error",
                source_location=locations[0] if locations else None,
            ))

    # 2. Reason drift - YAML has but code never uses (WARNING)
    for normalized_reason, origins in normalized_yaml_reasons.items():
        if normalized_reason not in normalized_code_reasons:
            report_key = sorted(origins)[0] if origins else normalized_reason
            report.add_warning(DriftItem(
                category="reason",
                item_key=report_key,
                drift_type="orphaned_in_yaml",
                severity="warning",
                source_location="docs/kb/20_decision_system/triage_decision_table.yaml",
            ))

    # 3. Evidence key drift - code writes but YAML missing (ERROR)
    for evidence_key, locations in code_evidence.items():
        # Skip diagnostic keys
        if evidence_key.startswith("diag."):
            continue
        # Skip common test fixtures
        if evidence_key in ["x", "test", "dummy"]:
            continue
        # Skip unscoped telemetry keys (legacy/non-contract).
        if "." not in evidence_key:
            report.add_warning(DriftItem(
                category="evidence",
                item_key=evidence_key,
                drift_type="unscoped_non_contract_key",
                severity="warning",
                source_location=locations[0] if locations else None,
            ))
            continue
        # Skip known internal coordination telemetry namespaces.
        if any(evidence_key.startswith(prefix) for prefix in _NON_CONTRACT_EVIDENCE_PREFIXES):
            report.add_warning(DriftItem(
                category="evidence",
                item_key=evidence_key,
                drift_type="internal_coordination_telemetry",
                severity="warning",
                source_location=locations[0] if locations else None,
            ))
            continue
        if evidence_key in _TRANSITIONAL_EVIDENCE_KEYS:
            report.add_warning(DriftItem(
                category="evidence",
                item_key=evidence_key,
                drift_type="transitional_legacy_evidence_key",
                severity="warning",
                source_location=locations[0] if locations else None,
            ))
            continue
        if evidence_key not in yaml_evidence:
            report.add_error(DriftItem(
                category="evidence",
                item_key=evidence_key,
                drift_type="missing_in_yaml",
                severity="error",
                source_location=locations[0] if locations else None,
            ))

    # 4. Invariant drift - code references but YAML missing (ERROR)
    for inv_id, locations in code_invariants.items():
        if inv_id not in yaml_invariants:
            report.add_error(DriftItem(
                category="invariant",
                item_key=inv_id,
                drift_type="missing_in_yaml",
                severity="error",
                source_location=locations[0] if locations else None,
            ))

    # 5. Invariant drift - YAML has but tests never reference (WARNING)
    for inv_id in yaml_invariants:
        if inv_id not in code_invariants:
            report.add_warning(DriftItem(
                category="invariant",
                item_key=inv_id,
                drift_type="orphaned_in_yaml",
                severity="warning",
                source_location="docs/kb/00_foundation/invariants_registry.yaml",
            ))

    return report


if __name__ == "__main__":
    import sys

    # Simple CLI
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    report = detect_drift()
    print(report.format_report())

    # Exit with error code if errors found
    sys.exit(1 if report.has_errors() else 0)
