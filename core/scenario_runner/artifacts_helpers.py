"""Re-export artifact helper functions used by the scenario runner.

Expose snapshot and artifact-writing helpers so the extracted
`run_agentic_scenario` implementation can import them explicitly while we
move implementations out of the monolith.
"""
from core import scenario_runner as sr
from core.scenario_runner import artifacts as artifacts_module


def _call_on_sr_or_original(name, *args, **kwargs):
    """Call the attribute `name` on `sr` if it's been overridden; otherwise
    fall back to the original implementation in `core.scenario_runner.artifacts`.
    """
    sr_attr = getattr(sr, name, None)
    # If sr.<name> resolves to this module's wrapper, avoid recursion by
    # calling the original implementation from artifacts_module.
    wrapper = globals().get(name)
    if sr_attr is None or sr_attr is wrapper:
        orig = getattr(artifacts_module, name.lstrip("_"), None)
        if orig is None:
            raise AttributeError(f"original artifact helper not found: {name}")
        return orig(*args, **kwargs)
    return sr_attr(*args, **kwargs)


def _write_debug_snapshot(payload, run_id=None, **kwargs):
    return _call_on_sr_or_original("_write_debug_snapshot", payload, run_id, **kwargs)


def _write_progress_snapshot(*args, **kwargs):
    return _call_on_sr_or_original("_write_progress_snapshot", *args, **kwargs)


def _write_html_snapshot(*args, **kwargs):
    return _call_on_sr_or_original("_write_html_snapshot", *args, **kwargs)


def _write_json_artifact_snapshot(*args, **kwargs):
    return _call_on_sr_or_original("_write_json_artifact_snapshot", *args, **kwargs)


def _write_image_snapshot(*args, **kwargs):
    return _call_on_sr_or_original("_write_image_snapshot", *args, **kwargs)


def _write_route_state_debug(*args, **kwargs):
    return _call_on_sr_or_original("_write_route_state_debug", *args, **kwargs)


def _append_jsonl_artifact(*args, **kwargs):
    return _call_on_sr_or_original("_append_jsonl_artifact", *args, **kwargs)


__all__ = [
    "_write_debug_snapshot",
    "_write_progress_snapshot",
    "_write_html_snapshot",
    "_write_json_artifact_snapshot",
    "_write_image_snapshot",
    "_write_route_state_debug",
    "_append_jsonl_artifact",
]
