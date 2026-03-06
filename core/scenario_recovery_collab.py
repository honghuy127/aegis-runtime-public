"""Bounded collaborative recovery orchestration helpers.

This module holds the heavy control flow for Phase B collaborative recovery so
`core.scenario_runner` can keep a thinner, more readable main loop.
"""

from typing import Any, Dict, Optional, Tuple


def try_recovery_collab_followup(
    *,
    current_html: str,
    failed_plan,
    route_core_failure: Optional[Dict[str, Any]],
    turn_index: int,
    env: Dict[str, Any],
    deps: Dict[str, Any],
) -> Tuple[Any, list]:
    """Run bounded collaborative recovery follow-up.

    `env` contains runtime state and limits; `deps` provides callbacks bound by
    `scenario_runner` so this module stays decoupled from browser/router internals.
    """
    log = deps["log"]
    is_valid_plan = deps["is_valid_plan"]
    run_vision_probe = deps["run_vision_probe"]
    apply_vision_hints = deps["apply_vision_hints"]
    refresh_html = deps["refresh_html"]
    reprobe_route_core = deps["reprobe_route_core"]
    make_base_plan = deps["make_base_plan"]
    compose_local_hint_with_notes = deps["compose_local_hint_with_notes"]
    call_repair_plan = deps["call_repair_plan"]
    call_generate_plan = deps["call_generate_plan"]
    postprocess_plan = deps["postprocess_plan"]

    site_key = str(env.get("site_key", "") or "")
    recovery_mode = bool(env.get("recovery_mode", False))
    limits = dict(env.get("limits", {}) or {})
    usage = env.get("usage", {})
    local_knowledge_hint = str(env.get("local_knowledge_hint", "") or "")
    planner_notes = list(env.get("planner_notes", []) or [])
    trace_memory_hint = str(env.get("trace_memory_hint", "") or "")

    if not recovery_mode:
        return None, []
    if not bool(limits.get("enabled", False)):
        return None, []

    collab_notes = []
    collab_new_notes = []
    vision_hint_payload: Dict[str, Any] = {}
    vision_hint_stage = ""
    vision_hints_applied = False
    trigger_reason = str(env.get("trigger_reason", "") or "")
    if not trigger_reason:
        trigger_reason = "route_core_before_date_fill_unverified"

    if usage.get("vlm", 0) < int(limits.get("max_vlm", 0)):
        try:
            usage["vlm"] = usage.get("vlm", 0) + 1
            for stage in ("last", "initial"):
                vision_hint_payload = run_vision_probe(
                    html_text=current_html,
                    screenshot_stage=stage,
                    trigger_reason=trigger_reason,
                )
                if isinstance(vision_hint_payload, dict) and vision_hint_payload:
                    vision_hint_stage = stage
                    break
        except Exception as collab_vlm_exc:
            log.warning(
                "scenario.plan.google_recovery_collab.vlm_failed error=%s",
                collab_vlm_exc,
            )
            vision_hint_payload = {}
            vision_hint_stage = ""

    base_plan = make_base_plan(failed_plan, vision_hint_payload)

    if isinstance(vision_hint_payload, dict) and vision_hint_payload:
        try:
            vision_hints_applied = bool(apply_vision_hints(vision_hint_payload))
        except Exception as collab_hint_exc:
            log.warning(
                "scenario.plan.google_recovery_collab.apply_vision_hints_failed error=%s",
                collab_hint_exc,
            )
            vision_hints_applied = False
        if vision_hints_applied:
            try:
                current_html = str(refresh_html() or current_html or "")
            except Exception as collab_html_exc:
                log.debug(
                    "scenario.plan.google_recovery_collab.refresh_html_failed error=%s",
                    collab_html_exc,
                )
            route_core_reprobe = reprobe_route_core(current_html)
            collab_notes.append(
                "RouteCoreReprobeAfterVLM: "
                f"ok={bool(route_core_reprobe.get('ok', False))} "
                f"reason={str(route_core_reprobe.get('reason', '') or '')}"
            )
            if bool(route_core_reprobe.get("ok", False)) and is_valid_plan(base_plan):
                deterministic_plan = postprocess_plan(base_plan)
                if is_valid_plan(deterministic_plan):
                    log.info(
                        "scenario.plan.google_recovery_collab valid=True source=vision_hint_reprobe vlm_used=%s vlm_stage=%s planner_calls=%s/%s repair_calls=%s/%s route_core_only=%s",
                        bool(vision_hint_payload),
                        vision_hint_stage or "none",
                        usage.get("planner", 0),
                        int(limits.get("max_planner", 0)),
                        usage.get("repair", 0),
                        int(limits.get("max_repair", 0)),
                        bool(limits.get("route_core_only_first", True)),
                    )
                    return deterministic_plan, list(collab_notes or [])

    route_core_evidence = (
        dict((route_core_failure or {}).get("evidence", {}) or {})
        if isinstance(route_core_failure, dict)
        else {}
    )
    if route_core_evidence:
        collab_notes.append(
            "RouteCoreGate: "
            + ", ".join(
                f"{k}={route_core_evidence.get(k)}"
                for k in (
                    "verify.route_core_probe_reason",
                    "verify.route_core_observed_origin",
                    "verify.route_core_observed_dest",
                )
                if route_core_evidence.get(k) not in (None, "")
            )[:400]
        )
    if isinstance(vision_hint_payload, dict) and vision_hint_payload:
        collab_notes.append(
            "VLMPageKind: "
            f"kind={vision_hint_payload.get('page_kind','unknown')} "
            f"confidence={vision_hint_payload.get('confidence','low')} "
            f"reason={vision_hint_payload.get('reason','')} "
            f"stage={vision_hint_stage or 'unknown'} "
            f"hints_applied={vision_hints_applied}"
        )
    collab_notes.append(
        "PhaseB: Route-core recovery only; prioritize origin/dest rebind before date fills."
    )

    local_hint = compose_local_hint_with_notes(
        local_knowledge_hint,
        planner_notes,
        trace_memory_hint,
    )
    if collab_notes:
        collab_blob = "\n".join([note for note in collab_notes if note])
        local_hint = f"{local_hint}\n{collab_blob}" if local_hint else collab_blob

    collab_plan = None
    timeout_sec = int(limits.get("planner_timeout_sec", 45) or 45)

    if is_valid_plan(base_plan) and usage.get("repair", 0) < int(limits.get("max_repair", 0)):
        try:
            collab_plan, collab_new_notes = call_repair_plan(
                base_plan=base_plan,
                current_html=current_html,
                turn_index=turn_index,
                timeout_sec=timeout_sec,
            )
            usage["repair"] = usage.get("repair", 0) + 1
        except Exception as collab_repair_exc:
            if isinstance(collab_repair_exc, (TimeoutError, KeyboardInterrupt)):
                raise
            log.warning(
                "scenario.plan.google_recovery_collab.repair_failed error=%s",
                collab_repair_exc,
            )
            collab_plan = None
            collab_new_notes = []

    if not is_valid_plan(collab_plan) and usage.get("planner", 0) < int(limits.get("max_planner", 0)):
        try:
            collab_plan, collab_new_notes = call_generate_plan(
                current_html=current_html,
                local_hint=local_hint,
                turn_index=turn_index,
                timeout_sec=timeout_sec,
            )
            usage["planner"] = usage.get("planner", 0) + 1
        except Exception as collab_plan_exc:
            if isinstance(collab_plan_exc, (TimeoutError, KeyboardInterrupt)):
                raise
            log.warning(
                "scenario.plan.google_recovery_collab.generate_failed error=%s",
                collab_plan_exc,
            )
            collab_plan = None
            collab_new_notes = []

    if is_valid_plan(collab_plan):
        collab_plan = postprocess_plan(collab_plan)

    plan_source = "collab"
    final_plan = collab_plan if is_valid_plan(collab_plan) else None
    if not is_valid_plan(final_plan) and is_valid_plan(base_plan):
        fallback_plan = postprocess_plan(base_plan)
        if is_valid_plan(fallback_plan):
            final_plan = fallback_plan
            plan_source = "deterministic_base_fallback"
            collab_notes.append(
                "PhaseBDeterministicFallback: using route-core-only base recovery plan after invalid collab output."
            )

    log.info(
        "scenario.plan.google_recovery_collab valid=%s source=%s vlm_used=%s vlm_stage=%s planner_calls=%s/%s repair_calls=%s/%s route_core_only=%s",
        is_valid_plan(final_plan),
        plan_source,
        bool(vision_hint_payload),
        vision_hint_stage or "none",
        usage.get("planner", 0),
        int(limits.get("max_planner", 0)),
        usage.get("repair", 0),
        int(limits.get("max_repair", 0)),
        bool(limits.get("route_core_only_first", True)),
    )
    merged_notes = list(collab_new_notes or [])
    if collab_notes:
        merged_notes.extend(collab_notes)
    return (final_plan if is_valid_plan(final_plan) else None), merged_notes
