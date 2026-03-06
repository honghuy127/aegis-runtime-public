from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from core.agent.types import ActionSpec, ObjectSpec, Observation


@dataclass(frozen=True)
class RunContext:
    site_key: str
    url: str
    locale: str = ""
    region: str = ""
    currency: str = ""
    is_domestic: bool = False
    inputs: Dict[str, Any] = None


class ServicePlugin:
    service_key: str = "unknown"

    def objects(self, ctx: RunContext) -> List[ObjectSpec]:
        raise NotImplementedError

    def action_catalog(self, ctx: RunContext) -> List[ActionSpec]:
        """Return base action templates (unbound or loosely bound)."""
        return []

    def dom_probe(self, html: str, ctx: RunContext) -> Observation:
        """Fast observation from DOM/HTML only."""
        return Observation()

    def readiness(self, obs: Observation, ctx: RunContext) -> bool:
        """Stop condition based on obs."""
        return False
