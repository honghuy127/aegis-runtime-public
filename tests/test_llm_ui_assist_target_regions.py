"""Tests for VLM UI-assist target region normalization."""

import json

import llm.code_model as cm


def test_analyze_page_ui_with_vlm_normalizes_target_regions(monkeypatch):
    """UI assist should return normalized target_regions bboxes for planner hints."""
    monkeypatch.setattr(cm, "_encode_image_base64_variants", lambda _p: ["fake_b64"])
    monkeypatch.setattr(cm, "_resolve_vision_model", lambda: "fake-vlm")
    monkeypatch.setattr(
        cm,
        "_llm_runtime_options",
        lambda _kind: {"num_ctx": 4096, "num_predict": 1024, "temperature": 0.0},
    )
    monkeypatch.setattr(cm, "_effective_vlm_endpoint_policy", lambda *args, **kwargs: "chat_only")
    monkeypatch.setattr(
        cm,
        "build_ui_language_hint_block",
        lambda **kwargs: ("LanguageHint: en", "en", "test"),
    )

    payload = {
        "page_scope": "international",
        "page_class": "flight_only",
        "trip_product": "flight_only",
        "blocked_by_modal": False,
        "fill_labels": {
            "origin": ["Where from"],
            "dest": ["Where to"],
            "depart": ["Departure"],
            "return": ["Return"],
            "search": ["Search"],
        },
        "target_regions": {
            "origin": [0.1, 0.2, 0.3, 0.05],
            "dest": [0.45, 0.2, 0.3, 0.05],
            "search": [0.8, 0.2, 0.12, 0.05],
            "modal_close": None,
            "depart": [2.0, -1.0, 0.2, 0.1],  # invalid => normalized to None
        },
        "reason": "flight_scope_detected",
    }
    monkeypatch.setattr(cm, "call_llm", lambda *args, **kwargs: json.dumps(payload))

    out = cm.analyze_page_ui_with_vlm(
        "/tmp/fake.png",
        site="google_flights",
        origin="HND",
        dest="ITM",
        depart="2026-03-01",
        return_date="2026-03-08",
        locale="ja-JP",
        html_context="<html></html>",
        stage="page_kind",
    )

    assert out["fill_labels"]["search"] == ["Search"]
    assert out["target_regions"]["origin"] == [0.1, 0.2, 0.3, 0.05]
    assert out["target_regions"]["dest"] == [0.45, 0.2, 0.3, 0.05]
    assert out["target_regions"]["search"] == [0.8, 0.2, 0.12, 0.05]
    assert out["target_regions"]["depart"] is None
    assert out["target_regions"]["modal_close"] is None
