"""Triage helper for analyzing failure patterns in Flight Price Watcher.

Usage:
    python -m utils.triage
    python -m utils.triage --top-n 10 --lookback 48 --json
    python -m utils.triage --reason "calendar_not_open" --show-evidence
    python -m utils.triage --with-cards --cards-limit 3
    python -m utils.triage --log-file debug.log --json
"""

import argparse
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.scenario.reasons import FAILURE_REASONS, get_reason, normalize_reason
from utils.graph_policy_stats import GraphPolicyStats, load_graph_stats_for_run
from utils.kb import get_docs_for_reason, get_kb
from utils.kb_cards import Card, load_kb_cards, filter_cards
from utils.run_paths import get_run_dir, read_latest_run_id

logger = logging.getLogger(__name__)


def read_kb_cards_config() -> bool:
    """Try to read kb_cards_enabled from configs/run.yaml.

    Returns:
        True if kb_cards_enabled is set to true in config, False otherwise.
    """
    try:
        config_path = Path.cwd() / "configs" / "run.yaml"
        if not config_path.exists():
            return False

        # Try to use yaml library if available
        try:
            import yaml

            with open(config_path) as f:
                config = yaml.safe_load(f) or {}
                return config.get("kb_cards_enabled", False)
        except ImportError:
            # Fallback: simple string search
            with open(config_path) as f:
                content = f.read()
                if "kb_cards_enabled: true" in content:
                    return True
        return False
    except Exception as e:
        logger.debug(f"Failed to read KB cards config: {e}")
        return False


@dataclass
class TriageEvent:
    """Single failure event parsed from logs/errors."""

    timestamp: datetime
    reason: str
    evidence: Dict[str, Any]
    module: str = ""
    severity: str = ""


def read_last_run_pointer(pointer_path: Path) -> Optional[Dict[str, str]]:
    """Read LAST_RUN.txt pointer file to get canonical run directory.

    Returns dict with keys: run_id, canonical_dir, artifacts_dir, etc.
    Returns None if pointer file doesn't exist or can't be parsed.
    """
    if not pointer_path.exists():
        return None

    try:
        content = pointer_path.read_text(encoding="utf-8")
        pointer_data = {}
        for line in content.split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, value = line.split("=", 1)
                pointer_data[key.strip()] = value.strip()
        return pointer_data if pointer_data else None
    except Exception as e:
        logger.debug(f"Failed to read LAST_RUN.txt pointer at {pointer_path}: {e}")
        return None


def find_debug_dir() -> Path:
    """Locate storage/debug directory."""
    current = Path.cwd()
    for parent in [current] + list(current.parents):
        debug_dir = parent / "storage" / "debug"
        if debug_dir.exists():
            return debug_dir
    return Path.cwd() / "storage" / "debug"


def find_storage_root() -> Path:
    """Locate repository storage/ directory without creating it."""
    current = Path.cwd()
    for parent in [current] + list(current.parents):
        storage_dir = parent / "storage"
        if storage_dir.exists():
            return storage_dir
    return Path.cwd() / "storage"


def find_canonical_run_dir_from_latest() -> Optional[Path]:
    """Resolve canonical run directory via storage/latest_run_id.txt."""
    storage_root = find_storage_root()
    run_id = read_latest_run_id(storage_root=storage_root)
    if not run_id:
        return None
    run_dir = get_run_dir(run_id, base_dir=storage_root / "runs")
    return run_dir if run_dir.exists() else None


def find_canonical_artifacts_dir() -> Optional[Path]:
    """Try to find canonical run directory from latest_run_id (then legacy pointers)."""
    canonical_dir = find_canonical_run_dir_from_latest()
    if canonical_dir and (canonical_dir / "scenario_last_error.json").exists():
        return canonical_dir

    current = Path.cwd()
    search_roots = [current] + list(current.parents)

    for root in search_roots:
        # Check legacy pointer locations
        for debug_subdir in ["debug", "debug_html"]:
            pointer_path = root / "storage" / debug_subdir / "LAST_RUN.txt"
            pointer_data = read_last_run_pointer(pointer_path)
            if pointer_data and "canonical_dir" in pointer_data:
                canonical_dir = Path(pointer_data["canonical_dir"])
                # Resolve to absolute if needed
                if not canonical_dir.is_absolute():
                    canonical_dir = root / canonical_dir
                if canonical_dir.exists():
                    scenario_error_path = canonical_dir / "scenario_last_error.json"
                    if scenario_error_path.exists():
                        return canonical_dir
    return None


def parse_error_json_file(json_path: Path) -> Optional[TriageEvent]:
    """Parse a single error JSON file.

    Expects format like:
    {
        "timestamp": "2026-02-21T14:30:45",
        "reason": "calendar_not_open",
        "evidence": {...}
    }
    """
    try:
        if not json_path.exists():
            return None

        with open(json_path) as f:
            data = json.load(f)

        timestamp_str = data.get("timestamp", "")
        try:
            timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            timestamp = datetime.now()

        reason = str(data.get("reason", "unknown")).strip()
        evidence = data.get("evidence", {})
        module = data.get("module", "")
        severity = data.get("severity", "error")

        return TriageEvent(
            timestamp=timestamp,
            reason=reason,
            evidence=evidence,
            module=module,
            severity=severity,
        )
    except Exception as e:
        logger.debug(f"Failed to parse {json_path}: {e}")
        return None


def collect_error_events(
    debug_dir: Path = None,
    lookback_hours: int = 24,
) -> List[TriageEvent]:
    """Collect recent error events from debug directory or canonical artifacts.

    First tries to find canonical artifacts via LAST_RUN.txt pointer.
    Falls back to legacy storage/debug directory.
    Looks for files matching pattern: *_error.json or scenario_last_error.json
    """
    import os
    from datetime import timezone

    events: List[TriageEvent] = []
    # Use naive datetime for comparison like the rest of the codebase
    cutoff_time = datetime.now() - timedelta(hours=lookback_hours)

    # Try canonical location first
    canonical_dir = find_canonical_artifacts_dir()
    if canonical_dir:
        scenario_error_path = canonical_dir / "scenario_last_error.json"
        if scenario_error_path.exists():
            event = parse_error_json_file(scenario_error_path)
            if event:
                # Normalize timestamps for comparison (convert to naive if needed)
                event_ts = event.timestamp
                if event_ts.tzinfo is not None:
                    event_ts = event_ts.replace(tzinfo=None)
                if event_ts >= cutoff_time:
                    events.append(event)
                    logger.debug(f"Loaded canonical error from {scenario_error_path}")

    # Fall back to legacy debug directory
    if debug_dir is None:
        debug_dir = find_debug_dir()

    # Look for error JSON files in legacy directory (read-only fallback)
    if debug_dir.exists():
        for json_file in sorted(debug_dir.glob("*_error.json"), reverse=True):
            event = parse_error_json_file(json_file)
            if event:
                # Normalize timestamps for comparison
                event_ts = event.timestamp
                if event_ts.tzinfo is not None:
                    event_ts = event_ts.replace(tzinfo=None)
                if event_ts >= cutoff_time:
                    # Avoid duplicates if we already loaded from canonical
                    if not any(e.timestamp == event.timestamp and e.reason == event.reason for e in events):
                        events.append(event)

    return events


def aggregate_by_reason(events: List[TriageEvent]) -> Dict[str, List[TriageEvent]]:
    """Group events by canonical reason code while preserving raw event reasons."""
    by_reason: Dict[str, List[TriageEvent]] = {}
    for event in events:
        raw_reason = str(event.reason or "").strip()
        canonical_reason = normalize_reason(raw_reason)
        reason_key = canonical_reason if canonical_reason != "unknown" else raw_reason or "unknown"
        if reason_key not in by_reason:
            by_reason[reason_key] = []
        by_reason[reason_key].append(event)
    return by_reason


def load_events_from_log_text(log_text: str, run_id: Optional[str] = None) -> Tuple[List[TriageEvent], Optional[str]]:
    """Parse TriageEvents from raw log text.

    Looks for log lines matching patterns like:
    - "scenario.date_fill_failure.exit reason=calendar_not_open"
    - "scenario.google_date.soft_fail ... error=calendar_not_open"
    - "scenario.step.fill_optional_soft_fail ... error=month_header_not_found"
    - "scenario.span ... run_id=20260220_173410_417177"

    Args:
        log_text: Raw log file content
        run_id: Optional run_id to use if not found in logs

    Returns:
        Tuple of (events list, extracted run_id or None)
    """
    events: List[TriageEvent] = []
    extracted_run_id = run_id
    reason_counts: Dict[str, int] = {}

    for line in log_text.split('\n'):
        # Extract run_id if present
        if extracted_run_id is None and 'run_id=' in line:
            match = re.search(r'run_id=([a-zA-Z0-9\-._]+)', line)
            if match:
                extracted_run_id = match.group(1)

        # Look for reason codes in log lines
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

    # Convert reason counts to events
    for reason_code, count in reason_counts.items():
        # Create event with current timestamp
        event = TriageEvent(
            timestamp=datetime.now(),
            reason=reason_code,
            evidence={},
            module="log_parser",
            severity="error"
        )
        # Add count as evidence if count > 1
        if count > 1:
            event.evidence["count"] = count
        events.append(event)

    return events, extracted_run_id



def format_reason_html(reason_code: str, cards: Optional[List[Card]] = None) -> str:
    """Format a single reason for human output, optionally including KB cards."""
    reason = get_reason(reason_code)
    if not reason:
        output = f"Unknown reason: {reason_code}"
    else:
        lines = [
            f"**{reason_code}**",
            f"  Summary: {reason.summary}",
            f"  Severity: {reason.severity}",
            f"  Emitter: {reason.emitter}",
        ]

        if reason.kb_links:
            lines.append(f"  References:")
            for link in reason.kb_links:
                lines.append(f"    - {link}")

        output = "\n".join(lines)

    # Append KB cards if provided
    if cards:
        output += "\n  KB Cards (top results):"
        for card in cards[:3]:  # Show top 3 cards
            output += f"\n    - {card.title} ({card.path})"
            if card.actions_allowed:
                output += f"\n      actions_allowed: {', '.join(card.actions_allowed)}"
            if card.evidence_required:
                output += f"\n      evidence_required: {', '.join(card.evidence_required)}"
            if card.kb_links:
                for link in card.kb_links[:2]:  # Show top 2 links
                    output += f"\n      reference: {link}"
            if card.code_refs:
                for ref in card.code_refs[:2]:  # Show top 2 code refs
                    output += f"\n      code: {ref}"

    return output



def format_human_report(
    by_reason: Dict[str, List[TriageEvent]],
    kb_available: bool = False,
    cards_by_reason: Optional[Dict[str, List[Card]]] = None,
    graph_stats: Optional[GraphPolicyStats] = None,
) -> str:
    """Format triage report for human reading."""
    lines = [
        "=== Flight Price Watcher Triage Report ===",
        "",
        f"Generated: {datetime.now().isoformat()}",
        "",
    ]

    if not by_reason:
        lines.append("No failure events found in lookback period.")
        # Still show graph stats if available
        if graph_stats and graph_stats.transitions:
            lines.append("")
            lines.append("=== Graph-lite Stats: Top Failing Transitions ===")
            lines.append("")
            top_failures = graph_stats.summarize_top_failures(limit=5)
            if top_failures:
                for i, failure in enumerate(top_failures, 1):
                    sig = failure["state_signature"]
                    lines.append(f"{i}. {sig.get('site', 'unknown')} | {sig.get('page_kind', 'unknown')} | {sig.get('role', 'none')} → {sig.get('action', 'unknown')}")
                    lines.append(f"   Selector: {sig.get('selector_family', 'unknown')}")
                    lines.append(f"   Failures: {failure['total_failures']}, Avg elapsed: {failure['avg_elapsed_ms']}ms")
                    top_reasons = failure.get("top_reasons", [])
                    if top_reasons:
                        reasons_str = ", ".join(f"{r['reason_code']} ({r['count']})" for r in top_reasons[:3])
                        lines.append(f"   Top reasons: {reasons_str}")
                    lines.append("")
            else:
                lines.append("No failures found in graph stats")
                lines.append("")
        return "\n".join(lines)

    # Sort reasons by frequency
    sorted_reasons = sorted(
        by_reason.items(),
        key=lambda x: len(x[1]),
        reverse=True,
    )

    lines.append(f"Top Failure Reasons ({len(sorted_reasons)} unique):")
    lines.append("")

    total_events = sum(len(events) for events in by_reason.values())

    for i, (reason_code, events) in enumerate(sorted_reasons, 1):
        count = len(events)
        percentage = 100 * count / total_events if total_events > 0 else 0

        lines.append(f"{i}. {reason_code} ({count} occurrences, {percentage:.1f}% of failures)")
        raw_counts = Counter(str(e.reason or "").strip() or "unknown" for e in events)
        raw_counts_str = ", ".join(f"{raw} ({raw_count})" for raw, raw_count in raw_counts.most_common())
        lines.append(f"   Raw reasons: {raw_counts_str}")

        # Get reason metadata and cards
        cards = None
        if cards_by_reason and reason_code in cards_by_reason:
            cards = cards_by_reason[reason_code]

        lines.append(format_reason_html(reason_code, cards=cards))
        lines.append("")

    # Graph-lite stats section (if available)
    if graph_stats and graph_stats.transitions:
        lines.append("=== Graph-lite Stats: Top Failing Transitions ===")
        lines.append("")
        top_failures = graph_stats.summarize_top_failures(limit=5)
        if top_failures:
            for i, failure in enumerate(top_failures, 1):
                sig = failure["state_signature"]
                lines.append(f"{i}. {sig.get('site', 'unknown')} | {sig.get('page_kind', 'unknown')} | {sig.get('role', 'none')} → {sig.get('action', 'unknown')}")
                lines.append(f"   Selector: {sig.get('selector_family', 'unknown')}")
                lines.append(f"   Failures: {failure['total_failures']}, Avg elapsed: {failure['avg_elapsed_ms']}ms")
                top_reasons = failure.get("top_reasons", [])
                if top_reasons:
                    reasons_str = ", ".join(f"{r['reason_code']} ({r['count']})" for r in top_reasons[:3])
                    lines.append(f"   Top reasons: {reasons_str}")
                lines.append("")
        else:
            lines.append("No failures found in graph stats")
            lines.append("")

    lines.append("---")
    lines.append(f"Total Events: {total_events}")
    lines.append(f"Unique Reasons: {len(by_reason)}")

    if kb_available:
        lines.append("")
        lines.append("To learn more about a reason, see:")
        lines.append("  - docs/kb/10_runtime_contracts/evidence.md (detailed runbook)")
        lines.append("  - docs/kb/20_decision_system/triage_runbook.md (decision trees)")

    return "\n".join(lines)


def format_json_report(
    by_reason: Dict[str, List[TriageEvent]],
    cards_by_reason: Optional[Dict[str, List[Card]]] = None,
    graph_stats: Optional[GraphPolicyStats] = None,
) -> str:
    """Format triage report as JSON."""
    report = {
        "timestamp": datetime.now().isoformat(),
        "total_events": sum(len(events) for events in by_reason.values()),
        "unique_reasons": len(by_reason),
        "reasons": [],
    }

    sorted_reasons = sorted(
        by_reason.items(),
        key=lambda x: len(x[1]),
        reverse=True,
    )

    for reason_code, events in sorted_reasons:
        reason = get_reason(reason_code)
        raw_counts = Counter(str(e.reason or "").strip() or "unknown" for e in events)
        reason_obj = {
            "code": reason_code,
            "count": len(events),
            "summary": reason.summary if reason else "Unknown",
            "severity": reason.severity if reason else "unknown",
            "latest_timestamp": max(e.timestamp for e in events).isoformat(),
            "raw_reason_counts": dict(raw_counts),
        }

        # Add cards if available
        if cards_by_reason and reason_code in cards_by_reason:
            cards = cards_by_reason[reason_code]
            reason_obj["cards"] = [
                {
                    "title": card.title,
                    "path": card.path,
                    "actions_allowed": card.actions_allowed,
                    "evidence_required": card.evidence_required,
                    "kb_links": card.kb_links,
                    "code_refs": card.code_refs,
                }
                for card in cards[:3]  # Top 3 cards
            ]

        report["reasons"].append(reason_obj)

    # Add graph-lite stats if available
    if graph_stats and graph_stats.transitions:
        top_failures = graph_stats.summarize_top_failures(limit=10)
        report["graph_stats"] = {
            "total_transitions": len(graph_stats.transitions),
            "outcome_counts": graph_stats.count_by_outcome(),
            "selector_family_counts": graph_stats.count_by_selector_family(),
            "top_failures": top_failures,
        }

    return json.dumps(report, indent=2)


def main():
    parser = argparse.ArgumentParser(
        description="Triage failure patterns in Flight Price Watcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m utils.triage
  python -m utils.triage --top-n 10
  python -m utils.triage --lookback 48 --json
  python -m utils.triage --reason calendar_not_open --show-evidence
  python -m utils.triage --with-cards --cards-limit 3
  python -m utils.triage --log-file debug.log --json
        """,
    )

    parser.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="Parse reason codes from raw log file instead of stored errors",
    )
    parser.add_argument(
        "--run-id",
        type=str,
        default=None,
        help="Run ID for loading graph policy stats",
    )
    parser.add_argument(
        "--lookback",
        type=int,
        default=24,
        help="Hours to look back (default: 24) [ignored if --log-file provided]",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=5,
        help="Show top N reasons (default: 5)",
    )
    parser.add_argument(
        "--reason",
        type=str,
        default=None,
        help="Filter to specific reason code",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON instead of human text",
    )
    parser.add_argument(
        "--show-evidence",
        action="store_true",
        help="Include evidence dicts in output",
    )
    parser.add_argument(
        "--with-cards",
        action="store_true",
        help="Include KB cards in output",
    )
    parser.add_argument(
        "--cards-root",
        type=str,
        default="docs/kb/cards",
        help="Root directory for KB cards (default: docs/kb/cards)",
    )
    parser.add_argument(
        "--cards-limit",
        type=int,
        default=3,
        help="Max cards per reason code (default: 3)",
    )
    parser.add_argument(
        "--cards-strict",
        action="store_true",
        help="Fail if KB cards cannot be loaded properly",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Verbose logging",
    )

    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    # Collect events
    events = []
    extracted_run_id = None

    if args.log_file:
        # Parse from log file if provided
        if not args.log_file.exists():
            logging.error(f"Log file not found: {args.log_file}")
            return 1

        log_text = args.log_file.read_text(encoding='utf-8', errors='ignore')
        events, extracted_run_id = load_events_from_log_text(log_text)
        logging.info(f"Parsed {len(events)} unique reason codes from {args.log_file.name}")
    else:
        # Parse from stored error files (tries canonical first via pointer)
        events = collect_error_events(lookback_hours=args.lookback)

        # Try to extract run_id from canonical pointer if available
        if extracted_run_id is None:
            canonical_dir = find_canonical_artifacts_dir()
            if canonical_dir:
                # Extract run_id from path like storage/runs/run_20260223_143022
                run_id_match = canonical_dir.name
                if run_id_match and run_id_match.startswith("run_"):
                    extracted_run_id = run_id_match
                    logging.info(f"Extracted run_id from canonical path: {extracted_run_id}")

    # Filter by reason if specified
    if args.reason:
        filter_raw = str(args.reason or "").strip()
        filter_canonical = normalize_reason(filter_raw)
        events = [
            e for e in events
            if (
                str(e.reason or "").strip() == filter_raw
                or normalize_reason(str(e.reason or "").strip()) == filter_canonical
            )
        ]

    # Aggregate
    by_reason = aggregate_by_reason(events)

    # Limit to top N
    if args.top_n and len(by_reason) > args.top_n:
        sorted_reasons = sorted(
            by_reason.items(),
            key=lambda x: len(x[1]),
            reverse=True,
        )
        by_reason = dict(sorted_reasons[: args.top_n])

    # Load KB cards if enabled
    cards_by_reason = None
    cards_enabled = args.with_cards or read_kb_cards_config()

    if cards_enabled:
        try:
            all_cards = load_kb_cards(
                root_dir=args.cards_root,
                strict=args.cards_strict,
            )
            # Filter cards by reason_code for each unique reason
            cards_by_reason = {}
            for reason_code in by_reason.keys():
                filtered = filter_cards(
                    all_cards,
                    reason_code=reason_code,
                    limit=args.cards_limit,
                )
                if filtered:
                    cards_by_reason[reason_code] = filtered
        except Exception as e:
            if args.cards_strict:
                raise
            else:
                logger.warning(f"Failed to load KB cards: {e}")
                cards_by_reason = None

    # Load graph policy stats if run_id is available
    graph_stats = None
    run_id = args.run_id or extracted_run_id
    if run_id:
        try:
            graph_stats = load_graph_stats_for_run(run_id)
            if graph_stats:
                logging.info(f"Loaded graph stats with {len(graph_stats.transitions)} transitions")
        except Exception as e:
            logging.debug(f"Failed to load graph stats for run_id {run_id}: {e}")

    # Format and output
    if args.json:
        output = format_json_report(by_reason, cards_by_reason=cards_by_reason, graph_stats=graph_stats)
    else:
        # Try to load KB for linking
        kb_available = False
        try:
            _kb = get_kb()
            kb_available = True
        except Exception:
            pass

        output = format_human_report(
            by_reason,
            kb_available=kb_available,
            cards_by_reason=cards_by_reason,
            graph_stats=graph_stats,
        )

    print(output)


if __name__ == "__main__":
    main()
