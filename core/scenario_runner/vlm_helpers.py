"""Re-export of VLM/runtime-hints helpers used by the scenario runner.

This module provides a stable import path for a small group of VLM-related
helpers so they can be imported directly by the extracted
`run_agentic_scenario` implementation while we incrementally move helpers
out of the monolith.
"""
from core.scenario_runner.vlm.runtime_hints import (
    _apply_vlm_runtime_hints,
    _clear_vlm_runtime_hints,
    _compose_vlm_knowledge_hint,
    _sanitize_vlm_label,
    _sanitize_vlm_labels,
)

__all__ = [
    "_apply_vlm_runtime_hints",
    "_clear_vlm_runtime_hints",
    "_compose_vlm_knowledge_hint",
    "_sanitize_vlm_label",
    "_sanitize_vlm_labels",
]
