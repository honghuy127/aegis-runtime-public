"""Tests for bounded multimodal VLM context-pack generation."""

from llm.code_model import _build_multimodal_context_pack, _compact_hint_dict


def test_multimodal_context_pack_includes_code_judge_context_in_judge_mode():
    """Judge mode should embed bounded code/judgment context alongside DOM summary."""
    pack = _build_multimodal_context_pack(
        "<html><body><div>Flights HND ITM</div></body></html>",
        site="google_flights",
        origin="HND",
        dest="ITM",
        depart="2026-03-01",
        return_date="2026-03-08",
        multimodal_mode="judge",
        code_judge_context={
            "judge_mode": "vlm_plus_code_model",
            "quality_probe": {"quality": "uncertain", "route_score": 4},
            "route_bind_verdict": {"route_bound": True, "support": "strong"},
        },
    )
    assert "MultimodalMode: judge" in pack
    assert "JudgingPolicy:" in pack
    assert "CodeJudgeContext:" in pack
    assert "judge_mode" in pack
    assert "DOMSummary:" in pack


def test_multimodal_context_pack_omits_code_judge_context_outside_judge_mode():
    """Assist mode should keep the original lightweight context shape."""
    pack = _build_multimodal_context_pack(
        "<html><body><div>Flights</div></body></html>",
        site="google_flights",
        multimodal_mode="assist",
        code_judge_context={"judge_mode": "vlm_plus_code_model"},
    )
    assert "MultimodalMode: assist" in pack
    assert "CodeJudgeContext:" not in pack


def test_multimodal_context_pack_includes_code_judge_context_in_judge_primary_mode():
    """judge_primary should preserve the judge context semantics in the prompt pack."""
    pack = _build_multimodal_context_pack(
        "<html><body><div>Flights</div></body></html>",
        site="google_flights",
        multimodal_mode="judge_primary",
        code_judge_context={"judge_mode": "vlm_plus_code_model"},
    )
    assert "MultimodalMode: judge_primary" in pack
    assert "CodeJudgeContext:" in pack


def test_compact_hint_dict_includes_target_regions_summary():
    """Planner multimodal hint compactor should preserve bounded target location hints."""
    text = _compact_hint_dict(
        {
            "reason": "flight_scope_detected",
            "target_regions": {
                "origin": [0.1, 0.2, 0.3, 0.04],
                "dest": [0.45, 0.2, 0.3, 0.04],
                "search": [0.82, 0.2, 0.1, 0.05],
            },
        },
        max_chars=500,
    )
    assert "regions=" in text
    assert "origin@" in text
    assert "search@" in text
