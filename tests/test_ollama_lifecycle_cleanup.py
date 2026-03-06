"""Deterministic tests for Ollama lifecycle cleanup helpers."""

import llm.llm_client as client


def test_release_touched_ollama_models_unloads_tracked_models(monkeypatch):
    monkeypatch.setattr(client, "_TOUCHED_OLLAMA_MODELS", {"qwen3:8b", "minicpm-v:8b"})

    posts = []

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            return None

        def close(self):
            return None

    def _fake_post(url, json, timeout):
        posts.append((url, dict(json), timeout))
        return _Resp()

    monkeypatch.setattr(client.requests, "post", _fake_post)

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
        assert payload["stream"] is False
        assert timeout == (2, 2)


def test_release_touched_ollama_models_keeps_failed_models_tracked(monkeypatch):
    monkeypatch.setattr(client, "_TOUCHED_OLLAMA_MODELS", {"qwen3:8b", "bad:model"})

    class _Resp:
        def raise_for_status(self):
            return None

        def close(self):
            return None

    def _fake_post(url, json, timeout):  # noqa: ARG001
        if json["model"] == "bad:model":
            raise RuntimeError("boom")
        return _Resp()

    monkeypatch.setattr(client.requests, "post", _fake_post)

    out = client.release_touched_ollama_models(timeout_sec=1)

    assert out["ok"] is False
    assert out["failed"]["bad:model"].startswith("RuntimeError:")
    assert client.get_touched_ollama_models() == ["bad:model"]
