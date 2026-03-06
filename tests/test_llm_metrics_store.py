"""Tests for persisted LLM metrics storage in SQLite."""

import pytest

from storage import runs

pytestmark = [pytest.mark.llm, pytest.mark.integration]


def test_save_and_list_llm_metrics(tmp_path, monkeypatch):
    """LLM metrics should be stored and returned in newest-first order."""
    monkeypatch.setattr(runs, "DB_PATH", tmp_path / "runs.db")
    runs.init_db()

    runs.save_llm_metric(
        status="ok",
        mode="light",
        think=False,
        model="qwen3:8b",
        endpoint="chat",
        attempts=1,
        elapsed_s=0.75,
        done_reason="stop",
        eval_count=12,
        prompt_eval_count=33,
        tokens_per_sec=9.5,
    )
    runs.save_llm_metric(
        status="error",
        category="timeout",
        mode="full",
        think=True,
        model="qwen3:8b",
        endpoint="none",
        attempts=2,
        elapsed_s=12.4,
        error_count=2,
        error_text="timeout_budget_exhausted",
    )

    items = runs.list_llm_metrics(limit=10)
    assert len(items) == 2
    assert items[0]["status"] == "error"
    assert items[1]["status"] == "ok"
    assert items[1]["done_reason"] == "stop"
    assert items[1]["tokens_per_sec"] == 9.5


def test_enforce_db_limits_prunes_llm_metrics_by_count(tmp_path, monkeypatch):
    """Retention should cap llm_metrics rows by configured max."""
    monkeypatch.setattr(runs, "DB_PATH", tmp_path / "runs.db")
    cfg = {
        "runs_db_max_age_days": 0,
        "runs_db_max_rows": 0,
        "runs_db_max_bytes": 10_000_000,
        "runs_db_min_rows_to_keep": 0,
        "llm_metrics_db_max_age_days": 0,
        "llm_metrics_db_max_rows": 3,
        "llm_metrics_db_min_rows_to_keep": 1,
    }
    monkeypatch.setattr(runs, "get_threshold", lambda key, default=None: cfg.get(key, default))

    runs.init_db()
    for i in range(8):
        runs.save_llm_metric(
            status="ok",
            mode="light",
            think=False,
            model="qwen3:8b",
            endpoint="chat",
            attempts=1,
            elapsed_s=float(i),
            done_reason="stop",
        )

    runs.enforce_db_limits()
    items = runs.list_llm_metrics(limit=10)
    assert len(items) == 3
    elapsed = [item["elapsed_s"] for item in items]
    assert elapsed == [7.0, 6.0, 5.0]
