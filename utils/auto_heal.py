#!/usr/bin/env python3
"""
Auto-Heal Sandbox (Tier 2 Lite) - Bounded, gated, debug-only self-healing.

Provides lightweight recovery for common agent failures after debug runs.
MUST be off by default. Respects ActionBudget constraints.

CLI:
    python -m utils.auto_heal --run-id <id> [--log-file <path>] [--apply] [--verbose]
    python -m utils.auto_heal --latest [--log-file <path>] [--apply] [--verbose]

Typical flow:
    1. Debug run fails → scenario_last_error.json captured
    2. User retrieves error: `python -m utils.triage --with-cards | tail -50`
    3. Attempts auto-heal: `python -m utils.auto_heal --latest --log-file debug.log --verbose`
    4. If recovery viable: `python -m utils.auto_heal --latest --apply`
    5. Otherwise: escalate to Stage 2 (selector learning, deeper refactoring)
"""

import argparse
import json
import logging
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from utils.run_paths import get_run_dir, read_latest_run_id

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


# ============================================================================
# DATA STRUCTURES
# ============================================================================


@dataclass
class AutoHealConfig:
    """Configuration for auto-healing."""

    enabled: bool = False
    apply_patch: bool = False  # Dry-run by default
    max_files: int = 2
    max_changed_lines: int = 80
    test_cmd: str = "pytest -q tests/test_architecture_invariants.py"
    llm_enabled: bool = False  # Heuristic default, not LLM-based
    verbose: bool = False

    @classmethod
    def from_config_dict(cls, cfg: Dict[str, Any]) -> "AutoHealConfig":
        """Parse from config dict (yaml_dict)."""
        return cls(
            enabled=cfg.get("auto_heal_enabled", False),
            apply_patch=cfg.get("auto_heal_apply_patch", False),
            max_files=cfg.get("auto_heal_max_files", 2),
            max_changed_lines=cfg.get("auto_heal_max_changed_lines", 80),
            test_cmd=cfg.get(
                "auto_heal_test_cmd", "pytest -q tests/test_architecture_invariants.py"
            ),
            llm_enabled=cfg.get("auto_heal_llm_enabled", False),
        )

    @classmethod
    def from_yaml(cls, yaml_dict: Dict[str, Any]) -> "AutoHealConfig":
        """Parse from YAML dict (legacy)."""
        return cls.from_config_dict(yaml_dict)

    def to_dict(self) -> Dict[str, Any]:
        """Export to dict."""
        return asdict(self)



@dataclass
class ReasonRecord:
    """Single reason code extracted from triage."""

    code: str
    count: int = 1
    evidence_keys: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Export to dict."""
        return {
            "code": self.code,
            "count": self.count,
            "evidence_keys": self.evidence_keys,
        }


@dataclass
class CardRecord:
    """KB Card record."""

    id: str
    title: str
    match: Dict[str, str] = field(default_factory=dict)
    actions_allowed: List[str] = field(default_factory=list)
    best_patch_bullets: List[str] = field(default_factory=list)
    links: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Export to dict."""
        return {
            "id": self.id,
            "title": self.title,
            "match": self.match,
            "actions_allowed": self.actions_allowed,
            "best_patch_bullets": self.best_patch_bullets,
            "links": self.links,
        }


@dataclass
class PatchAction:
    """Single patch action to attempt."""

    type: str
    path: str
    summary: str
    max_changed_lines: int = 20

    def to_dict(self) -> Dict[str, Any]:
        """Export to dict."""
        return {
            "type": self.type,
            "path": self.path,
            "summary": self.summary,
            "max_changed_lines": self.max_changed_lines,
        }


@dataclass
class Proposal:
    """Recovery proposal with actions."""

    intent: str
    actions: List[PatchAction] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Export to dict."""
        return {
            "intent": self.intent,
            "actions": [a.to_dict() for a in self.actions],
        }


@dataclass
class AutoHealReport:
    """Result of auto-heal analysis."""

    version: str = "1"
    run_id: str = ""
    site: str = "unknown"
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    reasons: List[ReasonRecord] = field(default_factory=list)
    cards: List[CardRecord] = field(default_factory=list)
    proposal: Optional[Proposal] = None
    bounds: Dict[str, Any] = field(default_factory=dict)
    safety: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    def __post_init__(self):
        """Initialize bounds and safety after construction."""
        if not self.bounds:
            self.bounds = {
                "max_files": 2,
                "max_changed_lines": 80,
                "max_attempts": 1,
            }
        if not self.safety:
            self.safety = {
                "apply_mode": False,
                "llm_used": False,
                "passed_tests": False,
                "rollback_needed": False,
            }

    def to_dict(self) -> Dict[str, Any]:
        """Export to dict."""
        return {
            "version": self.version,
            "run_id": self.run_id,
            "site": self.site,
            "timestamp": self.timestamp,
            "reasons": [r.to_dict() for r in self.reasons],
            "cards": [c.to_dict() for c in self.cards],
            "proposal": self.proposal.to_dict() if self.proposal else None,
            "bounds": self.bounds,
            "safety": self.safety,
            "notes": self.notes,
        }

    def to_json(self) -> str:
        """Export to JSON string."""
        return json.dumps(self.to_dict(), indent=2)

    def save(self, path: Path) -> None:
        """Save report to file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json())



# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def load_config() -> Dict[str, Any]:
    """Load run.yaml config (auto_heal_* settings)."""
    config_file = Path("configs/run.yaml")
    if not config_file.exists():
        return {}

    try:
        import yaml

        return yaml.safe_load(config_file.read_text()) or {}
    except Exception as e:
        logger.debug(f"Failed to load config: {e}")
        return {}


def load_events_from_log_file(log_file: Path) -> Dict[str, int]:
    """Parse reason codes and counts from a log file.

    Looks for patterns like reason= and error= to extract reason codes.

    Returns:
        Dict mapping reason code -> count
    """
    reason_counts: Dict[str, int] = {}

    try:
        log_text = log_file.read_text(encoding='utf-8', errors='ignore')

        for line in log_text.split('\n'):
            # Pattern 1: reason=REASON_CODE
            reason_match = re.search(r'reason=([a-z_0-9]+)', line)
            if reason_match:
                reason_code = reason_match.group(1)
                reason_counts[reason_code] = reason_counts.get(reason_code, 0) + 1
                continue

            # Pattern 2: error=REASON_CODE
            error_match = re.search(r'error=([a-z_0-9]+)', line)
            if error_match:
                reason_code = error_match.group(1)
                reason_counts[reason_code] = reason_counts.get(reason_code, 0) + 1
                continue

    except Exception as e:
        logger.debug(f"Failed to parse log file {log_file}: {e}")

    return reason_counts


def build_heuristic_patch_plan(
    by_reason: Dict[str, List[Any]]
) -> Proposal:
    """
    Build heuristic patch plan based on reason codes.
    Determines intent and suggests actions based on failure patterns.

    Args:
        by_reason: Dict mapping reason code to list of triage events

    Returns:
        Proposal with intent and actions
    """
    # Check for timeout issues
    if "timeout_error" in by_reason:
        return Proposal(
            intent="timeout_hardening",
            actions=[
                PatchAction(
                    type="edit_file",
                    path="core/scenario/gf_helpers/date_picker_orchestrator.py",
                    summary="Increase timeout for calendar interactions",
                    max_changed_lines=8,
                )
            ],
        )

    # Check for selector/calendar issues
    if (
        "calendar_not_open" in by_reason
        or "selector_not_found" in by_reason
        or "month_nav_exhausted" in by_reason
    ):
        return Proposal(
            intent="selector_fix",
            actions=[
                PatchAction(
                    type="edit_file",
                    path="core/scenario/gf_helpers/date_picker_orchestrator.py",
                    summary="Update selectors for calendar dialog",
                    max_changed_lines=12,
                )
            ],
        )

    # Default: logging only
    return Proposal(
        intent="logging_only",
        actions=[
            PatchAction(
                type="add_logging",
                path="core/scenario/types.py",
                summary="Add debug logging for unknown failures",
                max_changed_lines=5,
            )
        ],
    )


def get_kb_cards_for_reasons(
    reason_codes: List[str],
    site: str = "unknown",
    cards_root: str = "",
) -> Dict[str, List[CardRecord]]:
    """
    Retrieve KB cards matching reason codes.
    Graceful fallback if KB cards unavailable.

    Args:
        reason_codes: List of reason code strings
        site: Site name (e.g., 'google_flights')
        cards_root: Path to KB cards root

    Returns:
        Dict mapping reason code -> list of CardRecord objects
    """
    result = {}

    # Graceful fallback: if no cards_root provided or doesn't exist, return empty
    if not cards_root:
        cards_root = "docs/kb/cards"

    cards_path = Path(cards_root)
    if not cards_path.exists():
        return result

    try:
        from utils.kb_cards import load_kb_cards, filter_cards

        # Load all cards from the root
        all_cards = load_kb_cards(root_dir=cards_root)

        # Filter by reason code for each
        for reason_code in reason_codes:
            filtered = filter_cards(
                all_cards,
                site=site,
                reason_code=reason_code,
                limit=3,
            )
            if filtered:
                result[reason_code] = [
                    CardRecord(
                        id=f"{reason_code}-card-{i}",
                        title=c.title,
                        match={"site": site, "reason_code": reason_code},
                        actions_allowed=c.actions_allowed or [],
                        links=c.kb_links or [],
                    )
                    for i, c in enumerate(filtered)
                ]
    except ImportError:
        logger.debug("KB Cards module not available; skipping card retrieval")

    return result


# ============================================================================
# AUTO-HEAL MAIN LOGIC
# ============================================================================


def run_auto_heal(
    run_id: str = "",
    config: Optional[AutoHealConfig] = None,
    log_file: Optional[Path] = None,
) -> AutoHealReport:
    """
    Main auto-heal logic: analyze failure, generate report.
    When disabled, returns minimal report without processing.

    Args:
        run_id: Run ID to analyze
        config: AutoHealConfig (uses default if not provided)
        log_file: Optional log file to parse reason codes from

    Returns:
        AutoHealReport with status and recommendations
    """
    if config is None:
        yaml_config = load_config()
        config = AutoHealConfig.from_yaml(yaml_config)

    report = AutoHealReport(run_id=run_id)

    # When disabled, return early
    if not config.enabled:
        report.notes.append("Auto-heal is disabled; no analysis performed")
        return report

    # Parse reasons from log file if provided
    if log_file and log_file.exists():
        reason_counts = load_events_from_log_file(log_file)
        for reason_code, count in reason_counts.items():
            reason_record = ReasonRecord(code=reason_code, count=count)
            report.reasons.append(reason_record)

        if config.verbose:
            logger.info(f"Parsed {len(reason_counts)} reason codes from log file")
    else:
        # Load error data (for demonstration)
        chosen_run_id = str(run_id or "").strip()
        if not chosen_run_id or chosen_run_id.lower() == "unknown":
            chosen_run_id = read_latest_run_id()
        error_file = get_run_dir(chosen_run_id) / "scenario_last_error.json" if chosen_run_id else Path("")
        if not error_file.exists():
            report.notes.append("No error file found")
            return report

        try:
            error_data = json.loads(error_file.read_text())
            reason_code = error_data.get("reason", "unknown")

            # Build reason record
            reason_record = ReasonRecord(code=reason_code, count=1)
            report.reasons.append(reason_record)

        except (json.JSONDecodeError, IOError) as e:
            report.notes.append(f"Failed to analyze error: {e}")
            return report

    # Try to retrieve KB cards if we have reasons
    if report.reasons:
        reason_codes = [r.code for r in report.reasons]
        kb_cards = get_kb_cards_for_reasons(reason_codes, site=report.site)
        for reason_code in reason_codes:
            if reason_code in kb_cards:
                report.cards.extend(kb_cards[reason_code])

        # Build heuristic proposal based on first reason
        primary_reason = report.reasons[0].code if report.reasons else None
        if primary_reason:
            by_reason = {primary_reason: []}
            proposal = build_heuristic_patch_plan(by_reason)
            report.proposal = proposal

    return report


# ============================================================================
# CLI
# ============================================================================


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Auto-Heal Sandbox: Bounded, gated, debug-only self-healing."
    )
    parser.add_argument(
        "--run-id",
        type=str,
        default=None,
        help="Specific run ID to analyze",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="Parse reason codes from log file instead of stored errors",
    )
    parser.add_argument(
        "--episode-dir",
        type=Path,
        default=None,
        help="Directory to save report.json and other artifacts",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply patches (default: dry-run)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Verbose output",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON report",
    )

    args = parser.parse_args()

    # Load config
    yaml_config = load_config()
    config = AutoHealConfig.from_yaml(yaml_config)
    config.apply_patch = args.apply
    config.verbose = args.verbose

    # Run auto-heal
    run_id = (args.run_id or "").strip() or read_latest_run_id() or "unknown"
    report = run_auto_heal(
        run_id=run_id,
        config=config,
        log_file=args.log_file,
    )

    # Save to episode directory if provided
    if args.episode_dir:
        episode_path = Path(args.episode_dir)
        episode_path.mkdir(parents=True, exist_ok=True)
        report_path = episode_path / "report.json"
        report.save(report_path)
        if args.verbose:
            logger.info(f"Saved report to {report_path}")

    # Output
    if args.json:
        print(report.to_json())
    else:
        print(f"Run ID: {report.run_id}")
        print(f"Site: {report.site}")
        if report.reasons:
            print(f"Reasons: {len(report.reasons)}")
            for reason in report.reasons:
                print(f"  - {reason.code} (count={reason.count})")
        if report.cards:
            print(f"KB Cards: {len(report.cards)}")
        if report.proposal:
            print(f"Proposal: {report.proposal.intent}")
        if report.notes:
            print("Notes:")
            for note in report.notes:
                print(f"  - {note}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
