from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from core.agent.types import ActionSpec, ActionType, ObjectSpec
from core.service_ui_profiles import (
    get_service_ui_profile,
    profile_localized_list,
    profile_role_list,
)


@dataclass(frozen=True)
class ObjectTemplate:
    """Template for one ObjectSpec resolved from service profile selectors."""

    object_id: str
    role: str
    profile_key: str
    profile_scope: str = "role"
    notes: str = ""
    fallback_selectors: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class ActionTemplate:
    """Template for one ActionSpec resolved from service profile selectors."""

    action_id: str
    action_type: ActionType
    target_object_id: str
    target_role: str
    profile_key: str
    profile_scope: str = "role"
    params_key: str = ""
    allow_soft_fail: bool = True
    cost_hint: str = "low"
    debug: Dict[str, Any] = field(default_factory=dict)
    fallback_selectors: List[str] = field(default_factory=list)


class BaseObjectCatalog:
    """Base object catalog with profile-driven selector lookup."""

    site_key: str = "unknown"

    def templates(self) -> List[ObjectTemplate]:
        raise NotImplementedError

    def objects(self, *, locale: str = "") -> List[ObjectSpec]:
        out: List[ObjectSpec] = []
        profile = get_service_ui_profile(self.site_key)
        for template in self.templates():
            selectors = self._resolve_selectors(profile, template.profile_key, template.role, template.profile_scope, locale)
            if not selectors:
                selectors = list(template.fallback_selectors)
            out.append(
                ObjectSpec(
                    id=template.object_id,
                    role=template.role,
                    selector_families=selectors,
                    notes=template.notes,
                )
            )
        return out

    def _resolve_selectors(
        self,
        profile: Dict[str, Any],
        key: str,
        role: str,
        scope: str,
        locale: str,
    ) -> List[str]:
        if scope == "list":
            return profile_localized_list(profile, key, locale=locale)
        return profile_role_list(profile, key, role, locale=locale)


class BaseActionCatalog:
    """Base action catalog with profile-driven selector lookup."""

    site_key: str = "unknown"

    def templates(self, *, inputs: Dict[str, Any]) -> List[ActionTemplate]:
        raise NotImplementedError

    def actions(self, *, inputs: Dict[str, Any], locale: str = "") -> List[ActionSpec]:
        out: List[ActionSpec] = []
        profile = get_service_ui_profile(self.site_key)
        for index, template in enumerate(self.templates(inputs=inputs or {})):
            selectors = self._resolve_selectors(
                profile=profile,
                key=template.profile_key,
                role=template.target_role,
                scope=template.profile_scope,
                locale=locale,
            )
            if not selectors:
                selectors = list(template.fallback_selectors)
            params: Dict[str, Any] = {}
            if template.params_key:
                params["text"] = str((inputs or {}).get(template.params_key, "") or "")
            out.append(
                ActionSpec(
                    action_id=template.action_id,
                    type=template.action_type,
                    target_object_id=template.target_object_id,
                    target_role=template.target_role,
                    selectors=selectors,
                    params=params,
                    allow_soft_fail=template.allow_soft_fail,
                    cost_hint=template.cost_hint,
                    debug={"step": index, **dict(template.debug)},
                )
            )
        return out

    def _resolve_selectors(
        self,
        *,
        profile: Dict[str, Any],
        key: str,
        role: str,
        scope: str,
        locale: str,
    ) -> List[str]:
        if scope == "list":
            return profile_localized_list(profile, key, locale=locale)
        return profile_role_list(profile, key, role, locale=locale)
