from __future__ import annotations

from typing import List

from core.agent.plugins.common import BaseObjectCatalog, ObjectTemplate
from core.agent.types import ObjectSpec


class GoogleFlightsObjectCatalog(BaseObjectCatalog):
    site_key = "google_flights"

    def templates(self) -> List[ObjectTemplate]:
        return [
            ObjectTemplate(
                object_id="RouteForm.origin",
                role="origin",
                profile_key="activation_clicks",
                profile_scope="role",
            ),
            ObjectTemplate(
                object_id="RouteForm.dest",
                role="dest",
                profile_key="activation_clicks",
                profile_scope="role",
            ),
            ObjectTemplate(
                object_id="RouteForm.search",
                role="search",
                profile_key="search_selectors",
                profile_scope="list",
            ),
        ]


def objects_for_locale(locale: str = "") -> List[ObjectSpec]:
    return GoogleFlightsObjectCatalog().objects(locale=locale)


def objects_ja() -> List[ObjectSpec]:
    """Backward-compatible alias used by early plugin call sites."""
    return objects_for_locale("ja-JP")
