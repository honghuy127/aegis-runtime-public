"""Tests for Ollama client timeout/fallback behavior."""

import pytest
import requests

import llm.llm_client as client
from llm.llm_client import _llm_stall_detected, call_llm

pytestmark = [pytest.mark.llm, pytest.mark.heavy]


@pytest.fixture(autouse=True)
def _stub_llm_metric_persistence(monkeypatch):
    """Avoid touching real storage/runs.db and reset client globals per test."""
    monkeypatch.setattr("llm.llm_client.save_llm_metric", lambda **kwargs: None)
    monkeypatch.setattr("llm.llm_client._CIRCUIT_OPEN_UNTIL", 0.0)
    monkeypatch.setattr("llm.llm_client._LAST_MONOTONIC_TS", 0.0)
    monkeypatch.setattr("llm.llm_client._CIRCUIT_OPEN_UNTIL_BY_MODEL", {})
    monkeypatch.setattr("llm.llm_client._TOUCHED_OLLAMA_MODELS", set())


def test_call_llm_skips_duplicate_endpoint_after_timeout(monkeypatch):
    """If one endpoint times out, don't retry same endpoint variant again."""
    urls = []

    def _fake_post(url, json, timeout):
        urls.append(url)
        raise requests.exceptions.ReadTimeout("timeout")

    monkeypatch.setenv("OLLAMA_CONNECT_TIMEOUT_SEC", "1")
    monkeypatch.setenv("OLLAMA_READ_TIMEOUT_SEC", "30")
    monkeypatch.setenv("OLLAMA_TOTAL_TIMEOUT_SEC", "30")
    monkeypatch.setenv("OLLAMA_CIRCUIT_OPEN_SEC", "0")
    monkeypatch.setattr("llm.llm_client._CIRCUIT_OPEN_UNTIL", 0.0)
    monkeypatch.setattr("llm.llm_client.requests.post", _fake_post)
    monkeypatch.setattr("llm.llm_client.time.monotonic", lambda: 0.0)

    with pytest.raises(RuntimeError):
        call_llm("ping", model="qwen3:8b")

    assert urls == [
        "http://localhost:11434/api/chat",
        "http://localhost:11434/api/generate",
    ]


def test_call_llm_respects_total_timeout_budget(monkeypatch):
    """Total timeout budget should stop retries before all fallbacks consume read_timeout."""
    urls = []
    now = {"t": 0.0}

    def _fake_monotonic():
        value = now["t"]
        now["t"] += 1.1
        return value

    def _fake_post(url, json, timeout):
        urls.append((url, timeout))
        raise requests.exceptions.ReadTimeout("timeout")

    monkeypatch.setenv("OLLAMA_CONNECT_TIMEOUT_SEC", "1")
    monkeypatch.setenv("OLLAMA_READ_TIMEOUT_SEC", "600")
    monkeypatch.setenv("OLLAMA_TOTAL_TIMEOUT_SEC", "2")
    monkeypatch.setenv("OLLAMA_CIRCUIT_OPEN_SEC", "0")
    monkeypatch.setattr("llm.llm_client._CIRCUIT_OPEN_UNTIL", 0.0)
    monkeypatch.setattr("llm.llm_client.time.monotonic", _fake_monotonic)
    monkeypatch.setattr("llm.llm_client.requests.post", _fake_post)

    with pytest.raises(RuntimeError, match="timeout_budget_exhausted"):
        call_llm("ping", model="qwen3:8b")

    assert len(urls) == 1
    assert urls[0][1][1] <= 2


def test_call_llm_circuit_breaker_fails_fast_after_timeout(monkeypatch):
    """After timeout, subsequent call should fail fast while circuit is open."""
    ticks = {"n": 0}

    def _fake_monotonic():
        ticks["n"] += 1
        return 10.0 if ticks["n"] <= 2 else 11.0

    def _fake_post(url, json, timeout):
        raise requests.exceptions.ReadTimeout("timeout")

    monkeypatch.setenv("OLLAMA_CONNECT_TIMEOUT_SEC", "1")
    monkeypatch.setenv("OLLAMA_READ_TIMEOUT_SEC", "2")
    monkeypatch.setenv("OLLAMA_TOTAL_TIMEOUT_SEC", "2")
    monkeypatch.setattr("llm.llm_client._CIRCUIT_OPEN_UNTIL", 0.0)
    monkeypatch.setattr("llm.llm_client.time.monotonic", _fake_monotonic)
    monkeypatch.setattr("llm.llm_client.requests.post", _fake_post)

    with pytest.raises(RuntimeError):
        call_llm("ping", model="qwen3:8b")

    with pytest.raises(RuntimeError, match="circuit_open"):
        call_llm("ping", model="qwen3:8b")


def test_call_llm_classifies_token_cap_when_empty_with_length_done_reason(monkeypatch):
    """Empty output with done_reason=length should be classified as token_cap."""

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"response": "", "done_reason": "length"}

    monkeypatch.setenv("OLLAMA_CONNECT_TIMEOUT_SEC", "1")
    monkeypatch.setenv("OLLAMA_READ_TIMEOUT_SEC", "2")
    monkeypatch.setenv("OLLAMA_TOTAL_TIMEOUT_SEC", "2")
    monkeypatch.setenv("OLLAMA_CIRCUIT_OPEN_SEC", "0")
    monkeypatch.setattr("llm.llm_client._CIRCUIT_OPEN_UNTIL", 0.0)
    monkeypatch.setattr("llm.llm_client.requests.post", lambda *args, **kwargs: _Resp())
    monkeypatch.setattr("llm.llm_client.time.monotonic", lambda: 0.0)

    with pytest.raises(RuntimeError, match="\\[token_cap\\]"):
        call_llm("ping", model="qwen3:8b")


def test_call_llm_persists_success_metrics(monkeypatch):
    """Successful LLM calls should persist one status=ok metrics row."""

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "response": "{\"ok\":true}",
                "done_reason": "stop",
                "eval_count": 8,
                "prompt_eval_count": 12,
                "eval_duration": 1_000_000_000,
            }

    saved = []
    monkeypatch.setattr("llm.llm_client.save_llm_metric", lambda **kwargs: saved.append(kwargs))
    monkeypatch.setattr("llm.llm_client.requests.post", lambda *args, **kwargs: _Resp())
    monkeypatch.setattr("llm.llm_client.time.monotonic", lambda: 0.0)

    out = call_llm("ping", model="qwen3:8b", think=False)

    assert out == "{\"ok\":true}"
    assert len(saved) == 1
    assert saved[0]["status"] == "ok"
    assert saved[0]["endpoint"] in ("chat", "generate")
    assert saved[0]["done_reason"] == "stop"


def test_call_llm_ignores_stale_circuit_after_monotonic_rollback(monkeypatch):
    """Stale in-memory circuit state should not block unrelated later calls."""

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"response": "ok", "done_reason": "stop"}

    monkeypatch.setattr("llm.llm_client.requests.post", lambda *args, **kwargs: _Resp())
    monkeypatch.setattr("llm.llm_client.time.monotonic", lambda: 0.0)
    monkeypatch.setattr("llm.llm_client._CIRCUIT_OPEN_UNTIL", 9999.0)
    monkeypatch.setattr("llm.llm_client._LAST_MONOTONIC_TS", 100.0)
    monkeypatch.setenv("OLLAMA_CIRCUIT_OPEN_SEC", "120")

    out = call_llm("ping", model="qwen3:8b", think=False)
    assert out == "ok"


def test_call_llm_persists_error_metrics(monkeypatch):
    """Failed LLM calls should persist status=error with category."""
    saved = []

    def _fake_post(url, json, timeout):
        raise requests.exceptions.ReadTimeout("timeout")

    monkeypatch.setenv("OLLAMA_CONNECT_TIMEOUT_SEC", "1")
    monkeypatch.setenv("OLLAMA_READ_TIMEOUT_SEC", "2")
    monkeypatch.setenv("OLLAMA_TOTAL_TIMEOUT_SEC", "2")
    monkeypatch.setenv("OLLAMA_CIRCUIT_OPEN_SEC", "0")
    monkeypatch.setattr("llm.llm_client._CIRCUIT_OPEN_UNTIL", 0.0)
    monkeypatch.setattr("llm.llm_client.time.monotonic", lambda: 0.0)
    monkeypatch.setattr("llm.llm_client.requests.post", _fake_post)
    monkeypatch.setattr("llm.llm_client.save_llm_metric", lambda **kwargs: saved.append(kwargs))

    with pytest.raises(RuntimeError, match="\\[timeout\\]"):
        call_llm("ping", model="qwen3:8b")

    assert saved
    assert saved[-1]["status"] == "error"
    assert saved[-1]["category"] == "timeout"


def test_call_llm_splits_timeout_budget_across_unique_endpoints(monkeypatch):
    """When total budget is small, keep non-trivial time for second endpoint fallback."""
    timeouts = []

    def _fake_post(url, json, timeout):
        # timeout is tuple(connect_timeout, read_timeout)
        timeouts.append((url, timeout))
        raise requests.exceptions.ReadTimeout("timeout")

    monkeypatch.setenv("OLLAMA_CONNECT_TIMEOUT_SEC", "10")
    monkeypatch.setenv("OLLAMA_READ_TIMEOUT_SEC", "600")
    monkeypatch.setenv("OLLAMA_TOTAL_TIMEOUT_SEC", "60")
    monkeypatch.setenv("OLLAMA_CIRCUIT_OPEN_SEC", "0")
    monkeypatch.setattr("llm.llm_client._CIRCUIT_OPEN_UNTIL", 0.0)
    monkeypatch.setattr("llm.llm_client.time.monotonic", lambda: 0.0)
    monkeypatch.setattr("llm.llm_client.requests.post", _fake_post)

    with pytest.raises(RuntimeError):
        call_llm("ping", model="qwen3:8b")

    assert len(timeouts) == 2
    first = timeouts[0]
    second = timeouts[1]
    assert first[0].endswith("/api/chat")
    assert second[0].endswith("/api/generate")
    # 60s total split over 2 unique endpoints -> around 30s each.
    assert first[1][1] >= 25
    assert second[1][1] >= 25


def test_call_llm_includes_images_in_multimodal_payload(monkeypatch):
    """When images are provided, call_llm should include them in request payload."""

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"response": "ok", "done_reason": "stop"}

    captured = {}

    def _fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        return _Resp()

    monkeypatch.setattr("llm.llm_client.requests.post", _fake_post)
    monkeypatch.setattr("llm.llm_client.time.monotonic", lambda: 0.0)

    out = call_llm("ping", model="qwen3-vl:8b", images=["abc123"], think=False)
    assert out == "ok"
    assert captured["url"].endswith("/api/chat")
    assert captured["json"]["messages"][0]["images"] == ["abc123"]


def test_call_llm_tracks_touched_models(monkeypatch):
    """Touched models should be tracked for best-effort release at process exit."""

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"response": "ok", "done_reason": "stop"}

        def close(self):
            return None

    monkeypatch.setattr("llm.llm_client.requests.post", lambda *args, **kwargs: _Resp())
    monkeypatch.setattr("llm.llm_client.time.monotonic", lambda: 0.0)

    out = call_llm("ping", model="qwen3:8b", think=False)

    assert out == "ok"
    assert client.get_touched_ollama_models() == ["qwen3:8b"]


def test_release_touched_ollama_models_unloads_models(monkeypatch):
    """Release helper should issue keep_alive=0 unload requests for touched models."""
    posts = []
    monkeypatch.setattr("llm.llm_client._TOUCHED_OLLAMA_MODELS", {"qwen3:8b", "minicpm-v:8b"})

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            return None

        def close(self):
            return None

    def _fake_post(url, json, timeout):
        posts.append((url, json, timeout))
        return _Resp()

    monkeypatch.setattr("llm.llm_client.requests.post", _fake_post)

    out = client.release_touched_ollama_models(timeout_sec=2)

    assert out["ok"] is True
    assert out["attempted"] == 2
    assert sorted(out["released"]) == ["minicpm-v:8b", "qwen3:8b"]
    assert client.get_touched_ollama_models() == []
    assert len(posts) == 2
    for url, payload, timeout in posts:
        assert url.endswith("/api/generate")
        assert payload["keep_alive"] == 0
        assert payload["prompt"] == ""
        assert timeout == (2, 2)


def test_llm_stall_detected_helper():
    """Stall helper should only trigger when enabled + slow throughput + elapsed threshold."""
    assert _llm_stall_detected(
        tokens_per_sec=0.05,
        elapsed_s=200.0,
        enabled=True,
        min_tokens_per_sec=0.15,
        min_elapsed_sec=180.0,
    )
    assert not _llm_stall_detected(
        tokens_per_sec=0.2,
        elapsed_s=200.0,
        enabled=True,
        min_tokens_per_sec=0.15,
        min_elapsed_sec=180.0,
    )
    assert not _llm_stall_detected(
        tokens_per_sec=0.05,
        elapsed_s=120.0,
        enabled=True,
        min_tokens_per_sec=0.15,
        min_elapsed_sec=180.0,
    )


def test_call_llm_aborts_on_stall_metrics(monkeypatch):
    """When stall abort is enabled, very low throughput long calls should fail closed."""

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "response": "{\"ok\":true}",
                "done_reason": "stop",
                "eval_count": 1,
                "prompt_eval_count": 32,
                "eval_duration": 20_000_000_000,  # 20s => 0.05 tok/s
            }

    saved = []
    original_get_threshold = client.get_threshold

    def _fake_get_threshold(key, default=None):
        if key == "llm_stall_abort_enabled":
            return True
        if key == "llm_stall_tokens_per_sec":
            return 0.15
        if key == "llm_stall_min_elapsed_sec":
            return 60
        return original_get_threshold(key, default)

    perf_ticks = iter([0.0, 200.0, 200.0, 200.0])
    monkeypatch.setattr("llm.llm_client.get_threshold", _fake_get_threshold)
    monkeypatch.setattr("llm.llm_client.requests.post", lambda *args, **kwargs: _Resp())
    monkeypatch.setattr("llm.llm_client.save_llm_metric", lambda **kwargs: saved.append(kwargs))
    monkeypatch.setattr("llm.llm_client.time.monotonic", lambda: 0.0)
    monkeypatch.setattr(
        "llm.llm_client.time.perf_counter",
        lambda: next(perf_ticks, 200.0),
    )

    with pytest.raises(RuntimeError, match="llm_stalled"):
        call_llm("ping", model="qwen3:8b", think=False)

    assert saved
    assert saved[-1]["status"] == "error"
    assert saved[-1]["category"] == "timeout_or_stall"
