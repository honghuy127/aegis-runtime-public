"""AgentState manager for deterministic scenario state tracking.

This module provides simple, deterministic mutation of agent state
without randomness or async. It is intended as a foundation for
evolution to RL-like trial selection, but maintains simplicity for now.
"""

from __future__ import annotations

from core.agent.types import AgentState


class AgentStateManager:
    """Simple manager for AgentState with deterministic mutations.

    Typical usage:
        state = AgentStateManager()
        state.increment_turn()
        state.record_action("fill_origin", "ok")
        state.block_action_temporarily("fill_origin", cooldown_turns=2)
        if not state.is_blocked("fill_origin"):  # will be True for 2 turns
            ...
    """

    def __init__(self, *, budget_ms: int = 120000):
        """Initialize state manager with optional budget.

        Args:
            budget_ms: Total budget in milliseconds. 0 = unlimited.
        """
        self.state = AgentState(budget_ms=budget_ms)
        self._block_until_turn: dict[str, int] = {}  # action_id -> unblock_at_turn

    def increment_turn(self) -> None:
        """Advance to next turn and reset attempt counter."""
        self.state.turn += 1
        self.state.attempt = 0
        # Unblock any actions whose cooldown window has passed
        self._unblock_expired(self.state.turn)

    def increment_attempt(self) -> None:
        """Increment attempt counter within current turn."""
        self.state.attempt += 1

    def record_action(self, action_id: str, status: str) -> None:
        """Record an executed action in history.

        Args:
            action_id: Identifier of the action (e.g., "fill_origin").
            status: Outcome ("ok", "soft_fail", "fail").
        """
        self.state.action_history.append(action_id)
        if status == "fail":
            self.state.last_fail_reason = f"action_failed:{action_id}"

    def block_action_temporarily(self, action_id: str, cooldown_turns: int) -> None:
        """Prevent an action from executing for a specified number of turns.

        Args:
            action_id: Action to block.
            cooldown_turns: Number of turns before re-enabling.
        """
        unblock_turn = self.state.turn + cooldown_turns
        self._block_until_turn[action_id] = unblock_turn
        self.state.blocked_actions.add(action_id)

    def is_blocked(self, action_id: str) -> bool:
        """Check if an action is currently blocked due to cooldown.

        Args:
            action_id: Action to check.

        Returns:
            True if blocked, False if available.
        """
        if action_id not in self._block_until_turn:
            return False
        unblock_turn = self._block_until_turn[action_id]
        if self.state.turn < unblock_turn:
            return True
        # Cooldown has expired
        self._block_until_turn.pop(action_id, None)
        self.state.blocked_actions.discard(action_id)
        return False

    def get_state(self) -> AgentState:
        """Return current immutable state snapshot."""
        return self.state

    def _unblock_expired(self, current_turn: int) -> None:
        """Internal: unblock actions whose cooldown window has ended."""
        expired = [
            action_id
            for action_id, unblock_turn in self._block_until_turn.items()
            if current_turn >= unblock_turn
        ]
        for action_id in expired:
            self._block_until_turn.pop(action_id, None)
            self.state.blocked_actions.discard(action_id)
