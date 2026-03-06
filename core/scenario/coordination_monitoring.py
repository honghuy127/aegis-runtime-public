"""Monitoring and observability for model coordination layer.

Collects metrics on:
- Gate decisions (which gates block extraction)
- Budget consumption patterns
- Model effectiveness (price extraction success rates)
- DomSlice statistics (selector usage, text size distribution)
- Evidence aggregation for debugging
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional
from collections import defaultdict
import time

log = logging.getLogger(__name__)


@dataclass
class GateDecisionMetrics:
    """Metrics for a single gate decision."""

    gate_name: str
    """Gate identifier (e.g., 'route_bound', 'is_flight', 'budget')."""

    passed: bool
    """True if gate passed; False if blocked."""

    reason: Optional[str] = None
    """Reason for block (e.g., 'route_mismatch', 'non_flight_scope')."""

    timestamp: float = field(default_factory=time.time)
    """ISO timestamp of decision."""

    context: Dict[str, Any] = field(default_factory=dict)
    """Additional context (route_support, remaining_budget, etc)."""


@dataclass
class ExtractionMetrics:
    """Metrics for a single extraction attempt."""

    extraction_id: str
    """Unique identifier for this extraction."""

    timestamp: float = field(default_factory=time.time)
    """Start time of extraction."""

    gated_at: Optional[str] = None
    """Gate where extraction was blocked (e.g., 'route_mismatch')."""

    gates_passed: List[str] = field(default_factory=list)
    """List of gates that passed."""

    domslice_built: bool = False
    """True if DomSlice was successfully built."""

    domslice_selector: Optional[str] = None
    """Which selector was used for DomSlice."""

    domslice_text_len: int = 0
    """Character count of DomSlice."""

    llm_called: bool = False
    """True if LLM was actually called."""

    price_extracted: bool = False
    """True if price was successfully extracted."""

    price_value: Optional[float] = None
    """Extracted price value."""

    evidence: Dict[str, Any] = field(default_factory=dict)
    """Full evidence dict from extraction."""

    duration_ms: int = 0
    """Total extraction time in milliseconds."""


@dataclass
class BudgetMetrics:
    """Budget consumption metrics."""

    budget_total_s: float
    """Initial budget in seconds."""

    budget_consumed_s: float
    """Total budget consumed."""

    budget_remaining_s: float
    """Remaining budget at measurement."""

    extraction_count: int = 0
    """Number of extractions in this budget period."""

    gated_count: int = 0
    """Number of extractions blocked by budget gate."""

    circuit_opens: int = 0
    """Number of models that opened circuit due to timeout."""

    timestamp: float = field(default_factory=time.time)
    """Measurement timestamp."""


class CoordinationMetricsCollector:
    """Collects and aggregates coordination metrics."""

    def __init__(self):
        """Initialize metrics collector."""
        self.gate_decisions: List[GateDecisionMetrics] = []
        self.extractions: List[ExtractionMetrics] = []
        self.budget_measurements: List[BudgetMetrics] = []

        # Aggregates
        self.gate_pass_rate: Dict[str, float] = defaultdict(float)
        self.blocking_reasons: Dict[str, int] = defaultdict(int)
        self.domslice_selectors: Dict[str, int] = defaultdict(int)

    def record_gate_decision(
        self,
        gate_name: str,
        passed: bool,
        reason: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record a gate decision.

        Args:
            gate_name: Gate identifier
            passed: True if gate passed
            reason: Block reason if not passed
            context: Additional context
        """
        decision = GateDecisionMetrics(
            gate_name=gate_name,
            passed=passed,
            reason=reason,
            context=context or {},
        )
        self.gate_decisions.append(decision)

        if not passed:
            self.blocking_reasons[reason or "unknown"] += 1

    def record_extraction(
        self,
        extraction_id: str,
        gated_at: Optional[str] = None,
        gates_passed: Optional[List[str]] = None,
        domslice_selector: Optional[str] = None,
        domslice_text_len: int = 0,
        llm_called: bool = False,
        price_extracted: bool = False,
        price_value: Optional[float] = None,
        evidence: Optional[Dict[str, Any]] = None,
        duration_ms: int = 0,
    ) -> None:
        """Record extraction metrics.

        Args:
            extraction_id: Unique extraction ID
            gated_at: Gate where blocked (if any)
            gates_passed: List of gates that passed
            domslice_selector: Selector used for DomSlice
            domslice_text_len: Character count of DomSlice
            llm_called: True if LLM was called
            price_extracted: True if price extracted
            price_value: Extracted price
            evidence: Full evidence dict
            duration_ms: Extraction duration
        """
        metric = ExtractionMetrics(
            extraction_id=extraction_id,
            gated_at=gated_at,
            gates_passed=gates_passed or [],
            domslice_built=domslice_text_len > 0,
            domslice_selector=domslice_selector,
            domslice_text_len=domslice_text_len,
            llm_called=llm_called,
            price_extracted=price_extracted,
            price_value=price_value,
            evidence=evidence or {},
            duration_ms=duration_ms,
        )
        self.extractions.append(metric)

        if domslice_selector:
            self.domslice_selectors[domslice_selector] += 1

    def record_budget_measurement(
        self,
        budget_total_s: float,
        budget_consumed_s: float,
        budget_remaining_s: float,
        extraction_count: int = 0,
        gated_count: int = 0,
        circuit_opens: int = 0,
    ) -> None:
        """Record budget measurement.

        Args:
            budget_total_s: Total budget
            budget_consumed_s: Consumed budget
            budget_remaining_s: Remaining budget
            extraction_count: Total extractions
            gated_count: Extractions gated by budget
            circuit_opens: Number of circuit opens
        """
        metric = BudgetMetrics(
            budget_total_s=budget_total_s,
            budget_consumed_s=budget_consumed_s,
            budget_remaining_s=budget_remaining_s,
            extraction_count=extraction_count,
            gated_count=gated_count,
            circuit_opens=circuit_opens,
        )
        self.budget_measurements.append(metric)

    def get_summary(self) -> Dict[str, Any]:
        """Get summary metrics.

        Returns:
            Dict with aggregated metrics
        """
        total_extractions = len(self.extractions)
        gated_extractions = len([e for e in self.extractions if e.gated_at])
        llm_called = len([e for e in self.extractions if e.llm_called])
        price_extracted = len([e for e in self.extractions if e.price_extracted])

        avg_duration = (
            sum(e.duration_ms for e in self.extractions) / len(self.extractions)
            if self.extractions
            else 0
        )

        return {
            "total_extractions": total_extractions,
            "gated_extractions": gated_extractions,
            "gating_rate": gated_extractions / total_extractions if total_extractions > 0 else 0,
            "llm_called": llm_called,
            "llm_call_rate": llm_called / total_extractions if total_extractions > 0 else 0,
            "price_extracted": price_extracted,
            "extraction_success_rate": price_extracted / llm_called if llm_called > 0 else 0,
            "avg_duration_ms": avg_duration,
            "blocking_reasons": dict(self.blocking_reasons),
            "domslice_selectors": dict(self.domslice_selectors),
            "gate_decisions": len(self.gate_decisions),
            "budget_measurements": len(self.budget_measurements),
        }

    def log_summary(self) -> None:
        """Log summary metrics."""
        summary = self.get_summary()
        log.info(
            "coordination.metrics_summary total_extractions=%d gated_rate=%.2f "
            "llm_call_rate=%.2f success_rate=%.2f avg_duration_ms=%.1f",
            summary["total_extractions"],
            summary["gating_rate"],
            summary["llm_call_rate"],
            summary["extraction_success_rate"],
            summary["avg_duration_ms"],
        )


class ExtractionObserver:
    """Observes extraction pipeline for metrics collection.

    Provides hook methods to integrate with extraction functions
    for automatic metrics collection without code changes.
    """

    def __init__(self, metrics_collector: Optional[CoordinationMetricsCollector] = None):
        """Initialize observer.

        Args:
            metrics_collector: Optional metrics collector (creates if None)
        """
        self.metrics = metrics_collector or CoordinationMetricsCollector()
        self.current_extraction_id: Optional[str] = None
        self.extraction_start_time: Optional[float] = None

    def on_extraction_start(self, extraction_id: str) -> None:
        """Called at extraction start.

        Args:
            extraction_id: Unique ID for this extraction
        """
        self.current_extraction_id = extraction_id
        self.extraction_start_time = time.time()

    def on_gate_evaluation(
        self,
        gate_name: str,
        passed: bool,
        reason: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Called when gate is evaluated.

        Args:
            gate_name: Gate identifier
            passed: True if passed
            reason: Reason if failed
            context: Additional context
        """
        self.metrics.record_gate_decision(gate_name, passed, reason, context)

    def on_extraction_complete(
        self,
        gated_at: Optional[str] = None,
        gates_passed: Optional[List[str]] = None,
        domslice_selector: Optional[str] = None,
        domslice_text_len: int = 0,
        llm_called: bool = False,
        price_extracted: bool = False,
        price_value: Optional[float] = None,
        evidence: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Called when extraction completes.

        Args:
            gated_at: Gate where blocked (if any)
            gates_passed: Gates that passed
            domslice_selector: DomSlice selector
            domslice_text_len: DomSlice size
            llm_called: True if LLM called
            price_extracted: True if price extracted
            price_value: Extracted price
            evidence: Full evidence dict
        """
        duration_ms = int((time.time() - self.extraction_start_time) * 1000) if self.extraction_start_time else 0

        self.metrics.record_extraction(
            extraction_id=self.current_extraction_id or "unknown",
            gated_at=gated_at,
            gates_passed=gates_passed,
            domslice_selector=domslice_selector,
            domslice_text_len=domslice_text_len,
            llm_called=llm_called,
            price_extracted=price_extracted,
            price_value=price_value,
            evidence=evidence,
            duration_ms=duration_ms,
        )

    def get_metrics(self) -> CoordinationMetricsCollector:
        """Get accumulated metrics.

        Returns:
            Metrics collector with all recorded metrics
        """
        return self.metrics


# ============================================================================
# MONITORING DASHBOARD (for real-time metrics)
# ============================================================================


def format_metrics_report(metrics: Dict[str, Any]) -> str:
    """Format metrics dict into readable report.

    Args:
        metrics: Metrics dict from get_summary()

    Returns:
        Formatted string report
    """
    return f"""
COORDINATION LAYER METRICS REPORT
=====================================

Extraction Statistics:
  Total Extractions:      {metrics['total_extractions']}
  Gated Extractions:      {metrics['gated_extractions']}
  Gating Rate:            {metrics['gating_rate']:.1%}

LLM Call Statistics:
  LLM Called:             {metrics['llm_called']}
  LLM Call Rate:          {metrics['llm_call_rate']:.1%}
  Price Extraction Rate:  {metrics['extraction_success_rate']:.1%}

Performance:
  Avg Duration (ms):      {metrics['avg_duration_ms']:.1f}
  Gate Decisions:         {metrics['gate_decisions']}
  Budget Measurements:    {metrics['budget_measurements']}

Blocking Reasons:
{_format_dict_report(metrics['blocking_reasons'], indent=2)}

DomSlice Selectors (by usage):
{_format_dict_report(metrics['domslice_selectors'], indent=2)}
"""


def _format_dict_report(data: Dict[str, int], indent: int = 2) -> str:
    """Format dict into indented report lines.

    Args:
        data: Dict to format
        indent: Indentation level

    Returns:
        Formatted string
    """
    if not data:
        return " " * indent + "(none)"

    lines = []
    for key, value in sorted(data.items(), key=lambda x: x[1], reverse=True):
        lines.append(f"{' ' * indent}{key}: {value}")
    return "\n".join(lines)
