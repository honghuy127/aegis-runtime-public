"""AgentEngine orchestrates observation, policy, and action execution loops."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from core.agent.actor.actor import Actor
from core.agent.plugins.base import RunContext, ServicePlugin
from core.agent.policy import ActionPolicy
from core.agent.state import AgentStateManager
from core.agent.types import ActionSpec, AgentState, Observation, TraceEvent


class AgentEngine:
    """Orchestrate agentic scenario loops: observe → rank → execute → repeat.

    Typical usage:
        plugin = GoogleFlightsPlugin()
        engine = AgentEngine(plugin)
        # Loop up to 3 turns
        for turn in range(3):
            html, obs, trace = engine.run_once(browser, html, ctx)
            if plugin.readiness(obs, ctx):
                break  # Ready to extract results

    Manages:
    - State (turn, attempt, action history, blocked actions)
    - Policy (weighted action ranking)
    - Actor (browser interaction)
    """

    def __init__(self, plugin: ServicePlugin, *, log=None):
        """Initialize engine.

        Args:
            plugin: ServicePlugin instance (e.g., GoogleFlightsPlugin).
            log: Optional logger.
        """
        self.plugin = plugin
        self.log = log
        self.state_manager = AgentStateManager(budget_ms=120000)
        self.policy = ActionPolicy()

    def run_once(
        self,
        browser: Any,
        html: str,
        ctx: RunContext,
    ) -> Tuple[str, Observation, List[TraceEvent]]:
        """Execute one agent turn: probe → ready check → policy → top_k actions.

        HARDENING: Changed semantics to try multiple actions per turn (top_k).
        Instead of executing only ranked_actions[0], tries up to 3 actions
        before incrementing turn counter. This improves robustness when
        first action fails due to transient DOM issues.

        Args:
            browser: BrowserSession instance.
            html: Current page HTML.
            ctx: RunContext.

        Returns:
            (html_after, observation, trace_events)
        """
        trace: List[TraceEvent] = []
        current_turn = self.state_manager.state.turn

        # Log: Turn start
        if self.log:
            self.log.info(f"agent.v0.turn.start turn={current_turn}")

        # Step 1: Probe current observation
        obs = self.plugin.dom_probe(html, ctx)

        # Step 2: Check readiness (fast exit if successful)
        if self.plugin.readiness(obs, ctx):
            if self.log:
                self.log.info(
                    f"agent.v0.turn.end turn={current_turn} ready=True reason=readiness_check_passed"
                )
            return html, obs, trace

        # Step 3: Get action catalog and rank by policy
        actions = self.plugin.action_catalog(ctx)
        if not actions:
            if self.log:
                self.log.debug(f"agent.turn={current_turn} no_actions_available")
            if self.log:
                self.log.info(f"agent.v0.turn.end turn={current_turn} ready=False reason=no_actions")
            return html, obs, trace

        ranked_actions = self.policy.rank_actions(
            actions,
            state=self.state_manager.get_state(),
            obs=obs,
        )

        if not ranked_actions:
            if self.log:
                self.log.debug(f"agent.turn={current_turn} all_actions_blocked")
            if self.log:
                self.log.info(f"agent.v0.turn.end turn={current_turn} ready=False reason=all_actions_blocked")
            return html, obs, trace

        # Step 4: HARDENING - Execute top_k actions (up to 3) per turn
        # Try each candidate action until one succeeds
        actor = Actor(browser, log=self.log)
        action_succeeded = False

        for attempt, candidate_action in enumerate(ranked_actions[:3]):
            action_id = candidate_action.action_id

            # Log: Action attempt
            if self.log:
                self.log.info(
                    f"agent.v0.action.try action_id={action_id} rank={attempt} "
                    f"type={candidate_action.type.value}"
                )

            event = actor.execute(candidate_action, timeout_ms=1500)
            trace.append(event)

            # Step 5: Record outcome and update state
            self.state_manager.record_action(action_id, event.status)

            # Log: Action result
            if self.log:
                self.log.info(
                    f"agent.v0.action.result action_id={action_id} status={event.status} "
                    f"elapsed_ms={event.elapsed_ms}"
                )

            # If action succeeds, break from top_k loop and move to next turn
            if event.status == "ok":
                if self.log:
                    self.log.debug(f"agent.turn={current_turn} action_succeeded={action_id}, breaking top_k loop")
                action_succeeded = True
                break

            # Block action on any non-ok result (fail or soft_fail)
            if event.status != "ok":
                self.state_manager.block_action_temporarily(action_id, cooldown_turns=1)

        # Step 6: Increment turn after trying top_k actions (whether succeeded or not)
        self.state_manager.increment_turn()

        # Step 7: Probe updated page and return
        html2 = browser.content()
        obs2 = self.plugin.dom_probe(html2, ctx)

        # Log: Turn end
        if self.log:
            self.log.info(
                f"agent.v0.turn.end turn={current_turn} ready={self.plugin.readiness(obs2, ctx)} "
                f"action_succeeded={action_succeeded} page_class={obs2.page_class}"
            )
