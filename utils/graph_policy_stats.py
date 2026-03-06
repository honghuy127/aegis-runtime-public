"""Graph-lite policy statistics for tracking state transitions and selector performance.

OFF by default. Enable via configs/run.yaml: graph_policy_stats_enabled: true

Captures transition records (state signature + outcome) to support:
- Future bounded exploration (which selectors/strategies succeed in which states)
- Selector/strategy ordering based on historical performance
- Diagnostic triage insights (top failing transitions)

No behavior change when disabled. Minimal overhead when enabled.
"""

import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, UTC
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class StateSignature:
    """Normalized representation of UI state before a transition.

    Used to identify similar contexts where selectors/strategies are tried.
    """
    site: str
    page_kind: str  # e.g., "search_form", "search_results", "details"
    locale: str  # e.g., "ja-JP", "en-US"
    role: str  # e.g., "origin", "dest", "depart", "search_button"
    action: str  # e.g., "fill", "click", "wait"
    selector_family: str  # normalized selector pattern (e.g., "role=textbox", "aria-label=...", "data-attr")
    strategy_id: str  # optional; e.g., "vlm_guided", "dom_probe", "direct_selector"

    def to_dict(self) -> Dict[str, str]:
        """Convert to dict for serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, str]) -> "StateSignature":
        """Deserialize from dict."""
        return cls(**data)

    def key(self) -> str:
        """Return a string key for grouping transitions."""
        return f"{self.site}|{self.page_kind}|{self.locale}|{self.role}|{self.action}|{self.selector_family}|{self.strategy_id}"


@dataclass
class TransitionRecord:
    """Single state transition record with timing and outcome.

    Captures one step execution: state before, action taken, outcome.
    """
    timestamp: str  # ISO8601 UTC
    run_id: str
    attempt: int  # scenario retry attempt number
    turn: int  # plan generation turn within attempt
    step_index: int
    state_signature: Dict[str, str]  # StateSignature as dict
    outcome: str  # "ok", "soft_fail", "hard_fail"
    reason_code: str  # e.g., "success", "selector_not_found", "calendar_not_open"
    elapsed_ms: int

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TransitionRecord":
        """Deserialize from dict."""
        return cls(**data)


def normalize_selector_family(selector: str) -> str:
    """Normalize a selector into a family/pattern for grouping similar selectors.

    Buckets selectors by strategy rather than exact value, to avoid overfitting.

    Examples:
        "[role='textbox'][aria-label='Origin']" -> "role+aria-label"
        "input[name='origin']" -> "tag+attr"
        "[data-testid='search-btn']" -> "data-attr"
        ".some-class-name" -> "class"
        "#some-id" -> "id"
        "button:has-text('Search')" -> "tag+text"

    Args:
        selector: Raw CSS/playwright selector string

    Returns:
        Normalized family string
    """
    if not selector or not isinstance(selector, str):
        return "unknown"

    s = selector.strip()

    # Prioritize semantic selector patterns
    if re.search(r"\[role\s*[=~]", s, re.IGNORECASE):
        if re.search(r"\[aria-label\s*[=~]", s, re.IGNORECASE):
            return "role+aria-label"
        if re.search(r"\[aria-", s, re.IGNORECASE):
            return "role+aria"
        return "role"

    if re.search(r"\[aria-label\s*[=~]", s, re.IGNORECASE):
        return "aria-label"

    if re.search(r"\[aria-", s, re.IGNORECASE):
        return "aria"

    if re.search(r"\[data-", s, re.IGNORECASE):
        return "data-attr"

    # Check for text-based selectors
    if ":has-text(" in s or "text=" in s or ':text("' in s:
        if re.match(r"^\w+", s):  # starts with tag name
            return "tag+text"
        return "text"

    # Tag + attribute
    if re.match(r"^\w+\[", s):
        return "tag+attr"

    # Simple patterns
    if s.startswith("#"):
        return "id"

    if s.startswith("."):
        return "class"

    # Tag only
    if re.match(r"^\w+$", s):
        return "tag"

    # Position-based (nth-child, etc.) - less desirable
    if ":nth-child(" in s or ":nth-of-type(" in s:
        return "positional"

    return "complex"


class GraphPolicyStats:
    """Collects and summarizes state transition records for graph-lite policy learning.

    Usage:
        stats = GraphPolicyStats()
        stats.record_transition(
            run_id="20260222_120000_abc",
            attempt=0,
            turn=0,
            step_index=0,
            site="google_flights",
            page_kind="search_form",
            locale="ja-JP",
            role="origin",
            action="fill",
            selector="[role='textbox'][aria-label='Origin']",
            strategy_id="direct_selector",
            outcome="ok",
            reason_code="success",
            elapsed_ms=150,
        )

        # Serialize to JSON
        json_data = stats.to_json()

        # Deserialize from JSON
        stats2 = GraphPolicyStats.from_json(json_data)

        # Get top failures for triage
        failures = stats2.summarize_top_failures(limit=10)
    """

    def __init__(self, transitions: Optional[List[TransitionRecord]] = None):
        """Initialize stats collector.

        Args:
            transitions: Optional list of pre-existing transitions (for deserialization)
        """
        self.transitions: List[TransitionRecord] = transitions or []

    def record_transition(
        self,
        *,
        run_id: str,
        attempt: int,
        turn: int,
        step_index: int,
        site: str,
        page_kind: str,
        locale: str,
        role: str,
        action: str,
        selector: str,
        strategy_id: str = "",
        outcome: str,
        reason_code: str,
        elapsed_ms: int,
    ) -> None:
        """Record a single state transition.

        Args:
            run_id: Scenario run identifier
            attempt: Scenario retry attempt number (0-indexed)
            turn: Plan generation turn within attempt (0-indexed)
            step_index: Step index within plan (0-indexed)
            site: Site key (e.g., "google_flights")
            page_kind: Page classification (e.g., "search_form", "search_results")
            locale: Locale string (e.g., "ja-JP")
            role: Field/element role (e.g., "origin", "dest", "search_button")
            action: Action type (e.g., "fill", "click", "wait")
            selector: Raw selector string used
            strategy_id: Optional strategy identifier (e.g., "vlm_guided", "dom_probe")
            outcome: Outcome category ("ok", "soft_fail", "hard_fail")
            reason_code: Reason code (e.g., "success", "selector_not_found")
            elapsed_ms: Step elapsed time in milliseconds
        """
        state_sig = StateSignature(
            site=site,
            page_kind=page_kind,
            locale=locale,
            role=role,
            action=action,
            selector_family=normalize_selector_family(selector),
            strategy_id=strategy_id,
        )

        record = TransitionRecord(
            timestamp=datetime.now(UTC).isoformat(),
            run_id=run_id,
            attempt=attempt,
            turn=turn,
            step_index=step_index,
            state_signature=state_sig.to_dict(),
            outcome=outcome,
            reason_code=reason_code,
            elapsed_ms=elapsed_ms,
        )

        self.transitions.append(record)

    def merge(self, other: "GraphPolicyStats") -> None:
        """Merge transitions from another stats instance.

        Args:
            other: Another GraphPolicyStats instance
        """
        if other and other.transitions:
            self.transitions.extend(other.transitions)

    def to_json(self, *, indent: Optional[int] = None) -> str:
        """Serialize to JSON string.

        Args:
            indent: Optional JSON indentation for pretty-printing

        Returns:
            JSON string
        """
        data = {
            "version": "1.0",
            "transitions": [t.to_dict() for t in self.transitions],
        }
        return json.dumps(data, indent=indent, ensure_ascii=False)

    @classmethod
    def from_json(cls, json_str: str) -> "GraphPolicyStats":
        """Deserialize from JSON string.

        Args:
            json_str: JSON string

        Returns:
            GraphPolicyStats instance
        """
        data = json.loads(json_str)
        transitions = [TransitionRecord.from_dict(t) for t in data.get("transitions", [])]
        return cls(transitions=transitions)

    @classmethod
    def from_file(cls, path: Path) -> "GraphPolicyStats":
        """Load from JSON file.

        Args:
            path: Path to JSON file

        Returns:
            GraphPolicyStats instance
        """
        if not path.exists():
            return cls()

        with open(path, "r", encoding="utf-8") as f:
            return cls.from_json(f.read())

    def save_to_file(self, path: Path, *, indent: int = 2) -> None:
        """Save to JSON file.

        Args:
            path: Path to JSON file
            indent: JSON indentation
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.to_json(indent=indent))

    def summarize_top_failures(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Summarize top failing transitions for triage.

        Groups by state signature and counts failures by reason code.

        Args:
            limit: Maximum number of failure groups to return

        Returns:
            List of failure summaries, sorted by count descending:
            [
                {
                    "state_signature": {...},
                    "total_failures": 15,
                    "top_reasons": [
                        {"reason_code": "selector_not_found", "count": 10},
                        {"reason_code": "timeout_error", "count": 5},
                    ],
                    "avg_elapsed_ms": 2500,
                },
                ...
            ]
        """
        # Group by state signature key
        sig_groups: Dict[str, List[TransitionRecord]] = defaultdict(list)

        for t in self.transitions:
            if t.outcome in ("soft_fail", "hard_fail"):
                sig = StateSignature.from_dict(t.state_signature)
                sig_groups[sig.key()].append(t)

        # Build summaries
        summaries = []
        for sig_key, records in sig_groups.items():
            if not records:
                continue

            reason_counts = Counter(r.reason_code for r in records)
            top_reasons = [
                {"reason_code": code, "count": count}
                for code, count in reason_counts.most_common(5)
            ]

            avg_elapsed = sum(r.elapsed_ms for r in records) // len(records) if records else 0

            summaries.append({
                "state_signature": records[0].state_signature,  # use first as representative
                "total_failures": len(records),
                "top_reasons": top_reasons,
                "avg_elapsed_ms": avg_elapsed,
            })

        # Sort by total failures descending
        summaries.sort(key=lambda x: x["total_failures"], reverse=True)

        return summaries[:limit]

    def count_by_outcome(self) -> Dict[str, int]:
        """Count transitions by outcome.

        Returns:
            {"ok": 100, "soft_fail": 20, "hard_fail": 5}
        """
        return dict(Counter(t.outcome for t in self.transitions))

    def count_by_selector_family(self) -> Dict[str, int]:
        """Count transitions by selector family.

        Returns:
            {"role+aria-label": 50, "data-attr": 30, ...}
        """
        families = [
            StateSignature.from_dict(t.state_signature).selector_family
            for t in self.transitions
        ]
        return dict(Counter(families))


def load_graph_stats_for_run(run_id: str, *, runs_root: Path = None) -> Optional[GraphPolicyStats]:
    """Load graph policy stats for a specific run_id.

    Args:
        run_id: Run identifier
        runs_root: Optional custom runs root (defaults to storage/runs)

    Returns:
        GraphPolicyStats instance or None if not found
    """
    if not runs_root:
        runs_root = Path("storage/runs")

    stats_path = runs_root / run_id / "artifacts" / "graph_policy_stats.json"

    if not stats_path.exists():
        return None

    return GraphPolicyStats.from_file(stats_path)
