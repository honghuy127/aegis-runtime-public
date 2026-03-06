"""Unit tests for multimodal judge mode in the extractor."""

from core.extractor import extract_with_llm


def test_extract_with_llm_multimodal_judge_mode_verifies_candidate(monkeypatch):
    """Judge mode should route multimodal candidates through code-model verification."""
    monkeypatch.setenv("FLIGHT_WATCHER_AGENTIC_MULTIMODAL_MODE", "judge")
    monkeypatch.setenv("FLIGHT_WATCHER_VLM_EXTRACT_ENABLED", "0")
    monkeypatch.setenv("FLIGHT_WATCHER_EXTRACT_VISION_PRICE_ASSIST_ENABLED", "0")
    monkeypatch.setattr("core.extractor._extract_with_heuristics", lambda **kwargs: None)
    monkeypatch.setattr("core.extractor._semantic_html_chunks", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        "core.extractor.parse_html_with_llm",
        lambda html, site, task, timeout_sec=None, budget=None: {
            "price": None,
            "currency": None,
            "confidence": "low",
            "selector_hint": None,
            "reason": "price_not_found",
        },
    )
    seen = {}

    def _fake_multimodal(**kwargs):
        seen["multimodal_kwargs"] = dict(kwargs)
        return {
            "price": 23456.0,
            "currency": "JPY",
            "confidence": "medium",
            "selector_hint": None,
            "site": kwargs.get("site"),
            "task": kwargs.get("task"),
            "source": "vlm_multimodal",
            "reason": "price_found",
            "route_bound": True,
        }

    def _fake_judge(*args, **kwargs):  # noqa: ARG001
        seen["judge_called"] = True
        return {"accept": True, "support": "strong", "reason": "candidate_grounded"}

    monkeypatch.setattr("core.extractor.parse_page_multimodal_with_vlm", _fake_multimodal)
    monkeypatch.setattr("core.extractor.assess_vlm_price_candidate_with_llm", _fake_judge)

    result = extract_with_llm(
        html="<html><body>no deterministic price</body></html>",
        site="test",
        task="price",
        origin="HND",
        dest="ITM",
        depart="2026-03-01",
        return_date="2026-03-08",
        screenshot_path="/tmp/fake.png",
        page_url="https://www.google.com/travel/flights?hl=en",
    )

    assert result["price"] == 23456.0
    assert result["source"] == "vlm_multimodal"
    assert result["multimodal_judge_support"] == "strong"
    assert seen.get("judge_called") is True
    mm_kwargs = seen["multimodal_kwargs"]
    assert mm_kwargs["multimodal_mode"] == "judge"
    assert isinstance(mm_kwargs.get("code_judge_context"), dict)
    assert mm_kwargs["code_judge_context"].get("judge_mode") == "vlm_plus_code_model"


def test_extract_with_llm_multimodal_judge_mode_rejects_candidate(monkeypatch):
    """Judge mode should reject multimodal candidate when code-model verification rejects."""
    monkeypatch.setenv("FLIGHT_WATCHER_AGENTIC_MULTIMODAL_MODE", "judge")
    monkeypatch.setenv("FLIGHT_WATCHER_VLM_EXTRACT_ENABLED", "0")
    monkeypatch.setenv("FLIGHT_WATCHER_EXTRACT_VISION_PRICE_ASSIST_ENABLED", "0")
    monkeypatch.setattr("core.extractor._extract_with_heuristics", lambda **kwargs: None)
    monkeypatch.setattr("core.extractor._semantic_html_chunks", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        "core.extractor.parse_html_with_llm",
        lambda html, site, task, timeout_sec=None, budget=None: {
            "price": None,
            "currency": None,
            "confidence": "low",
            "selector_hint": None,
            "reason": "price_not_found",
        },
    )
    monkeypatch.setattr(
        "core.extractor.parse_page_multimodal_with_vlm",
        lambda **kwargs: {
            "price": 23456.0,
            "currency": "JPY",
            "confidence": "medium",
            "selector_hint": None,
            "site": kwargs.get("site"),
            "task": kwargs.get("task"),
            "source": "vlm_multimodal",
            "reason": "price_found",
        },
    )
    monkeypatch.setattr(
        "core.extractor.assess_vlm_price_candidate_with_llm",
        lambda *args, **kwargs: {
            "accept": False,
            "support": "none",
            "reason": "candidate_not_grounded",
        },
    )

    result = extract_with_llm(
        html="<html><body>no deterministic price</body></html>",
        site="test",
        task="price",
        origin="HND",
        dest="ITM",
        depart="2026-03-01",
        return_date="2026-03-08",
        screenshot_path="/tmp/fake.png",
    )

    assert result["price"] is None
    assert result["source"] != "vlm_multimodal"


def test_extract_with_llm_multimodal_judge_primary_runs_before_text_llm(monkeypatch):
    """judge_primary should attempt multimodal+judge before text-LLM fallback."""
    monkeypatch.setenv("FLIGHT_WATCHER_AGENTIC_MULTIMODAL_MODE", "judge_primary")
    monkeypatch.setenv("FLIGHT_WATCHER_VLM_EXTRACT_ENABLED", "0")
    monkeypatch.setenv("FLIGHT_WATCHER_EXTRACT_VISION_PRICE_ASSIST_ENABLED", "0")
    monkeypatch.setattr("core.extractor._extract_with_heuristics", lambda **kwargs: None)
    monkeypatch.setattr("core.extractor._semantic_html_chunks", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        "core.extractor.parse_html_with_llm",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("text llm should not run")),
    )
    monkeypatch.setattr(
        "core.extractor.parse_page_multimodal_with_vlm",
        lambda **kwargs: {
            "price": 21000.0,
            "currency": "JPY",
            "confidence": "medium",
            "selector_hint": None,
            "site": kwargs.get("site"),
            "task": kwargs.get("task"),
            "source": "vlm_multimodal",
            "reason": "price_found",
        },
    )
    monkeypatch.setattr(
        "core.extractor.assess_vlm_price_candidate_with_llm",
        lambda *args, **kwargs: {
            "accept": True,
            "support": "strong",
            "reason": "candidate_grounded",
        },
    )

    result = extract_with_llm(
        html="<html><body>no deterministic price</body></html>",
        site="test",
        task="price",
        origin="HND",
        dest="ITM",
        depart="2026-03-01",
        screenshot_path="/tmp/fake.png",
    )

    assert result["price"] == 21000.0
    assert result["source"] == "vlm_multimodal"
