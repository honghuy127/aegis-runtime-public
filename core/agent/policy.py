"""Basic weighted action selection policy.

This module implements a deterministic heuristic for ranking candidate actions
based on state and observation signals. It is designed as a foundation for
evolution to ML/RL-based scoring, but remains simple and interpretable for now.

Key heuristics:
  - Avoid blocked actions
  - Avoid repeating the same action consecutively
  - Prefer lower-cost actions (cost_hint: "low" > "med" > "high")
  - Boost route-related actions when route binding is incomplete
"""

from __future__ import annotations

from typing import List

from core.agent.state import AgentStateManager
from core.agent.types import ActionSpec, AgentState, Observation, WeightScore, Confidence


class ActionPolicy:
    """Deterministic policy for ranking candidate actions.

    Example usage:
        policy = ActionPolicy()
        ranked = policy.rank_actions(
            actions=[action1, action2, action3],
            state=manager.get_state(),
            obs=current_observation,
        )
        # ranked is sorted by score, highest first
        next_action = ranked[0] if ranked else None
    """

    def rank_actions(
        self,
        actions: List[ActionSpec],
        state: AgentState,
        obs: Observation,
    ) -> List[ActionSpec]:
        """Rank actions by computed weight score.

        Args:
            actions: Candidate action specs to score.
            state: Current AgentState (turn, history, blocked_actions, etc).
            obs: Latest Observation from DOM probe (route_bound, page_class, etc).

        Returns:
            Actions sorted by score descending (highest score first).
            If no actions, returns empty list.
        """
        if not actions:
            return []

        scores: List[tuple[ActionSpec, float, str]] = []
        for action in actions:
            score, reason = self._score_action(action, state, obs)
            scores.append((action, score, reason))

        # Sort by score descending
        scores.sort(key=lambda x: x[1], reverse=True)
        return [action for action, _score, _reason in scores]

    def _score_action(
        self,
        action: ActionSpec,
        state: AgentState,
        obs: Observation,
    ) -> tuple[float, str]:
        """Compute a numeric score and reason string for a single action.

        Args:
            action: Action to score.
            state: Current agent state.
            obs: Observation from latest DOM probe.

        Returns:
            Tuple of (score, reason_string).
            Score typically in [0.0, 1.0], higher is better.
        """
        score = 0.5  # Base score

        reasons = []

        # Rule 1: Penalize blocked actions
        action_id = action.target_object_id or action.type.value
        if action_id in state.blocked_actions:
            score -= 0.4
            reasons.append("blocked:true")
        else:
            reasons.append("blocked:false")

        # Rule 2: Penalize repeating same action (if history exists)
        if state.action_history and state.action_history[-1] == action_id:
            score -= 0.2
            reasons.append("repeated:true")
        else:
            reasons.append("repeated:false")

        # Rule 3: Boost low-cost actions
        cost_boost = {
            "low": 0.15,
            "med": 0.05,
            "high": -0.1,
        }.get(action.cost_hint, 0.0)
        score += cost_boost
        reasons.append(f"cost_hint:{action.cost_hint}")

        # Rule 4: Boost route-related actions if route is not bound
        if obs.route_bound is False:
            target_role = action.debug.get("target_role", "")
            if target_role in {"origin", "dest"}:
                score += 0.15
                reasons.append("route_incomplete:boosted")
        else:
            reasons.append("route_complete")

        # Rule 5: Clamp score to valid range [0, 1]
        score = max(0.0, min(1.0, score))

        reason = " ".join(reasons)
        return score, reason

    def get_weight_scores(
        self,
        actions: List[ActionSpec],
        state: AgentState,
        obs: Observation,
    ) -> List[WeightScore]:
        """Return detailed WeightScore objects for actions (for debugging/audit).

        Args:
            actions: Actions to score.
            state: Current agent state.
            obs: Current observation.

        Returns:
            List of WeightScore objects sorted by score descending.
        """
        scores: List[tuple[ActionSpec, float, str]] = []
        for action in actions:
            score, reason = self._score_action(action, state, obs)
            scores.append((action, score, reason))

        # Sort by score descending
        scores.sort(key=lambda x: x[1], reverse=True)

        return [
            WeightScore(
                action_id=action.target_object_id or action.type.value,
                score=score,
                reason=reason,
            )
            for action, score, reason in scores
        ]
