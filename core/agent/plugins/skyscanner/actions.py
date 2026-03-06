from __future__ import annotations

from typing import List, Tuple

from core.agent.plugins.common import ActionTemplate, BaseActionCatalog
from core.agent.types import ActionSpec, ActionType


# OPEN_URL is adapter-owned during bind_route(); WAIT_MAIN remains the only agent action.
ALLOWED_SKYSCANNER_ACTIONS: Tuple[str, ...] = ("OPEN_URL", "WAIT_MAIN")


class SkyscannerActionCatalog(BaseActionCatalog):
    site_key = "skyscanner"

    def templates(self, *, inputs: dict) -> List[ActionTemplate]:
        _ = inputs
        return [
            ActionTemplate(
                action_id="wait_main",
                action_type=ActionType.wait,
                target_object_id="Page.main",
                target_role="main",
                profile_key="wait_selectors",
                profile_scope="list",
                allow_soft_fail=True,
                cost_hint="low",
                debug={"allowed_actions": list(ALLOWED_SKYSCANNER_ACTIONS)},
                fallback_selectors=["[role='main']", "main", "body"],
            )
        ]


def minimal_wait_actions(*, locale: str = "") -> List[ActionSpec]:
    """Return bounded wait action catalog for Skyscanner deeplink flow."""
    return SkyscannerActionCatalog().actions(inputs={}, locale=locale)
