from __future__ import annotations

from typing import List

from core.agent.plugins.common import ActionTemplate, BaseActionCatalog
from core.agent.types import ActionSpec, ActionType


class GoogleFlightsActionCatalog(BaseActionCatalog):
    site_key = "google_flights"

    def templates(self, *, inputs: dict) -> List[ActionTemplate]:
        _ = inputs
        return [
            ActionTemplate(
                action_id="fill_origin",
                action_type=ActionType.type,
                target_object_id="RouteForm.origin",
                target_role="origin",
                profile_key="activation_clicks",
                profile_scope="role",
                params_key="origin",
                allow_soft_fail=True,
                cost_hint="low",
            ),
            ActionTemplate(
                action_id="fill_dest",
                action_type=ActionType.type,
                target_object_id="RouteForm.dest",
                target_role="dest",
                profile_key="activation_clicks",
                profile_scope="role",
                params_key="dest",
                allow_soft_fail=True,
                cost_hint="low",
            ),
            ActionTemplate(
                action_id="submit_search",
                action_type=ActionType.submit,
                target_object_id="RouteForm.search",
                target_role="search",
                profile_key="search_selectors",
                profile_scope="list",
                allow_soft_fail=True,
                cost_hint="med",
            ),
        ]


def base_actions(inputs: dict, *, locale: str = "") -> List[ActionSpec]:
    """Generate base action catalog for Google Flights route binding.

    Args:
        inputs: Dict with "origin", "dest" keys.
        locale: Optional locale hint for selector ordering (JA vs default).

    Returns:
        List of ActionSpec in execution order.
    """
    return GoogleFlightsActionCatalog().actions(inputs=inputs or {}, locale=locale)
