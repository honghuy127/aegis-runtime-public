"""Tests for DB/log retention maintenance."""

import os
import time

import pytest

from storage import maintenance
from storage import knowledge_store as ks
from storage import runs


@pytest.mark.integration
def test_enforce_db_limits_prunes_oldest_rows_by_count(tmp_path, monkeypatch):
    """runs.db should keep only newest rows when max row cap is exceeded."""
    monkeypatch.setattr(runs, "DB_PATH", tmp_path / "runs.db")
    cfg = {
        "runs_db_max_age_days": 0,
        "runs_db_max_rows": 5,
        "runs_db_max_bytes": 10_000_000,
        "runs_db_min_rows_to_keep": 1,
    }
    monkeypatch.setattr(runs, "get_threshold", lambda key, default=None: cfg.get(key, default))

    runs.init_db()
    for i in range(10):
        runs.save_run(
            site="s",
            task="price",
            price=float(i),
            currency="JPY",
            confidence="high",
            selector_used="body",
        )

    items = runs.list_runs(site="s", task="price")
    assert len(items) == 5
    prices = [item["price"] for item in items]
    assert prices == [9.0, 8.0, 7.0, 6.0, 5.0]


@pytest.mark.integration
def test_trim_file_to_tail_limits_log_size(tmp_path):
    """Large log files should be truncated to recent tail bytes."""
    path = tmp_path / "cron.log"
    path.write_bytes(b"a" * 200 + b"Z" * 30)

    changed = maintenance.trim_file_to_tail(path, max_bytes=100, keep_bytes=40)
    assert changed is True
    assert path.stat().st_size == 40
    assert path.read_bytes().endswith(b"Z" * 30)


@pytest.mark.integration
def test_purge_debug_html_files_keeps_recent_7_days(tmp_path):
    """debug_html cleanup should remove files older than max_age_days."""
    debug_dir = tmp_path / "storage" / "debug_html"
    debug_dir.mkdir(parents=True, exist_ok=True)
    old_file = debug_dir / "old.html"
    new_file = debug_dir / "new.html"
    old_file.write_text("old", encoding="utf-8")
    new_file.write_text("new", encoding="utf-8")

    now = time.time()
    ten_days_ago = now - (10 * 24 * 60 * 60)
    os.utime(old_file, (ten_days_ago, ten_days_ago))

    stats = maintenance.purge_debug_html_files(
        storage_dir=tmp_path / "storage",
        max_age_days=7,
    )

    assert stats["deleted"] == 1
    assert stats["kept"] == 1
    assert not old_file.exists()
    assert new_file.exists()
