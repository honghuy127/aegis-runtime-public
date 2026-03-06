from __future__ import annotations

from typing import List

from core.agent.plugins.common import BaseObjectCatalog, ObjectTemplate
from core.agent.types import ObjectSpec


class SkyscannerObjectCatalog(BaseObjectCatalog):
    site_key = "skyscanner"

    def templates(self) -> List[ObjectTemplate]:
        return [
            ObjectTemplate(
                object_id="Page.main",
                role="main",
                profile_key="wait_selectors",
                profile_scope="list",
                notes="Skyscanner v0 agent path only waits for main content before extraction.",
                fallback_selectors=["[role='main']", "main", "body"],
            )
        ]


def objects_minimal(*, locale: str = "") -> List[ObjectSpec]:
    """Minimal object catalog for Skyscanner deeplink/open flows."""
    return SkyscannerObjectCatalog().objects(locale=locale)
