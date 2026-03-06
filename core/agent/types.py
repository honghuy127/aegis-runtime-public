from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set


class Confidence(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class ActionType(str, Enum):
    focus = "FOCUS"
    click = "CLICK"
    wait = "WAIT"
    clear = "CLEAR"
    type = "TYPE"
    pick_suggestion = "PICK_SUGGESTION"
    open_datepicker = "OPEN_DATEPICKER"
    pick_date = "PICK_DATE"
    submit = "SUBMIT"


class SignalKind(str, Enum):
    visible = "VISIBLE"
    clickable = "CLICKABLE"
    editable = "EDITABLE"
    value_contains = "VALUE_CONTAINS"
    value_empty = "VALUE_EMPTY"
    committed = "COMMITTED"
    page_class = "PAGE_CLASS"
    route_bound = "ROUTE_BOUND"


@dataclass(frozen=True)
class SignalSpec:
    kind: SignalKind
    target_object_id: Optional[str] = None
    selector: Optional[str] = None
    expected: Optional[str] = None
    source: str = "dom"              # dom|vlm|mixed
    confidence_min: Confidence = Confidence.low
    timeout_ms: int = 800


@dataclass(frozen=True)
class ObjectSpec:
    id: str                          # e.g. "RouteForm.dest"
    role: str                        # origin|dest|depart|return|search|consent...
    selector_families: List[str]     # candidates
    notes: str = ""


@dataclass(frozen=True)
class ActionSpec:
    action_id: str                   # REQUIRED: Stable action identifier (e.g., "fill_origin", "submit_search")
    type: ActionType
    target_object_id: Optional[str] = None
    selectors: List[str] = field(default_factory=list)
    params: Dict[str, Any] = field(default_factory=dict)
    preconditions: List[SignalSpec] = field(default_factory=list)
    postconditions: List[SignalSpec] = field(default_factory=list)
    cost_hint: str = "low"           # low|med|high
    allow_soft_fail: bool = False
    target_role: Optional[str] = None # Target object role (origin|dest|depart|return|search|...)
    debug: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Observation:
    page_class: str = "unknown"
    trip_product: str = "unknown"
    route_bound: Optional[bool] = None
    fields: Dict[str, str] = field(default_factory=dict)   # role -> text
    confidence: Confidence = Confidence.low
    reason: str = ""


@dataclass
class TraceEvent:
    step: int
    action: ActionSpec
    status: str                      # ok|soft_fail|fail
    elapsed_ms: int
    reason: str = ""
    observed: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentState:
    """Mutable state tracking agent progress through a scenario.

    Attributes:
        turn: Current iteration count (0-indexed).
        attempt: Attempt number within current turn.
        budget_ms: Total milliseconds budget remaining.
        last_fail_reason: Description of last failure, if any.
        action_history: List of action_ids executed in order.
        blocked_actions: Set of action_ids temporarily blocked (cooldown).
    """
    turn: int = 0
    attempt: int = 0
    budget_ms: int = 0
    last_fail_reason: Optional[str] = None
    action_history: List[str] = field(default_factory=list)
    blocked_actions: Set[str] = field(default_factory=set)


@dataclass(frozen=True)
class WeightScore:
    """Score and rationale for a candidate action.

    Attributes:
        action_id: Target action identifier.
        score: Numeric score (higher=better). Range typically [0.0, 1.0].
        reason: Explanation of score (e.g., "cost_hint:low blocked:false repeated:false").
    """
    action_id: str
    score: float
    reason: str
