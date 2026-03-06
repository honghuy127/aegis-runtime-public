"""SQLite persistence for extraction runs and LLM runtime metrics."""

import sqlite3
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta, UTC

from utils.thresholds import get_threshold

DB_PATH = Path("storage/runs.db")
_RUNS_TABLE = "runs"
_LLM_METRICS_TABLE = "llm_metrics"


# -------------------------
# Init
# -------------------------

def init_db():
    """Create DB tables used by runtime storage if they do not exist yet."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(DB_PATH) as conn:
        _ensure_tables(conn)
        conn.commit()


def _ensure_tables(conn: sqlite3.Connection) -> None:
    """Create required tables/indexes in an existing DB connection."""
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_RUNS_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site TEXT NOT NULL,
            task TEXT NOT NULL,
            price REAL,
            currency TEXT,
            confidence TEXT,
            selector_used TEXT,
            drift_detected INTEGER,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_LLM_METRICS_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            status TEXT NOT NULL,
            category TEXT,
            mode TEXT,
            think TEXT,
            model TEXT,
            endpoint TEXT,
            attempts INTEGER,
            elapsed_s REAL,
            done_reason TEXT,
            eval_count INTEGER,
            prompt_eval_count INTEGER,
            tokens_per_sec REAL,
            error_count INTEGER,
            retry_after_s INTEGER,
            error_text TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{_RUNS_TABLE}_created_at ON {_RUNS_TABLE}(created_at)"
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{_RUNS_TABLE}_site_task_created_at ON {_RUNS_TABLE}(site, task, created_at)"
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{_LLM_METRICS_TABLE}_created_at ON {_LLM_METRICS_TABLE}(created_at)"
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{_LLM_METRICS_TABLE}_status_mode_created_at ON {_LLM_METRICS_TABLE}(status, mode, created_at)"
    )

def save_run(
    site: str,
    task: str,
    price: Optional[float],
    currency: Optional[str],
    confidence: Optional[str],
    selector_used: Optional[str],
    drift_detected: bool = False,
):
    """Insert one extraction run record into the runs table."""
    with sqlite3.connect(DB_PATH) as conn:
        _ensure_tables(conn)
        conn.execute(
            """
            INSERT INTO runs (
                site,
                task,
                price,
                currency,
                confidence,
                selector_used,
                drift_detected,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                site,
                task,
                price,
                currency,
                confidence,
                selector_used,
                int(drift_detected),
                datetime.now(UTC).isoformat(),
            ),
        )
        conn.commit()
    enforce_db_limits()


def save_llm_metric(
    *,
    status: str,
    category: Optional[str] = None,
    mode: Optional[str] = None,
    think: Optional[bool] = None,
    model: Optional[str] = None,
    endpoint: Optional[str] = None,
    attempts: Optional[int] = None,
    elapsed_s: Optional[float] = None,
    done_reason: Optional[str] = None,
    eval_count: Optional[int] = None,
    prompt_eval_count: Optional[int] = None,
    tokens_per_sec: Optional[float] = None,
    error_count: Optional[int] = None,
    retry_after_s: Optional[int] = None,
    error_text: Optional[str] = None,
) -> None:
    """Insert one low-level LLM call metric row for long-term diagnostics."""
    with sqlite3.connect(DB_PATH) as conn:
        _ensure_tables(conn)
        conn.execute(
            f"""
            INSERT INTO {_LLM_METRICS_TABLE} (
                status,
                category,
                mode,
                think,
                model,
                endpoint,
                attempts,
                elapsed_s,
                done_reason,
                eval_count,
                prompt_eval_count,
                tokens_per_sec,
                error_count,
                retry_after_s,
                error_text,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(status),
                category if category else None,
                mode if mode else None,
                None if think is None else str(bool(think)).lower(),
                model if model else None,
                endpoint if endpoint else None,
                int(attempts) if attempts is not None else None,
                float(elapsed_s) if elapsed_s is not None else None,
                done_reason if done_reason else None,
                int(eval_count) if eval_count is not None else None,
                int(prompt_eval_count) if prompt_eval_count is not None else None,
                float(tokens_per_sec) if tokens_per_sec is not None else None,
                int(error_count) if error_count is not None else None,
                int(retry_after_s) if retry_after_s is not None else None,
                (str(error_text)[:4000] if error_text else None),
                datetime.now(UTC).isoformat(),
            ),
        )
        conn.commit()


def _limit_int(key: str, default: int) -> int:
    """Read one integer runtime limit from thresholds config."""
    try:
        value = int(get_threshold(key, default))
    except Exception:
        return default
    return max(0, value)


def enforce_db_limits() -> None:
    """Prune old/excess rows and bound SQLite file size growth."""
    if not DB_PATH.exists():
        return

    max_age_days = _limit_int("runs_db_max_age_days", 365)
    max_rows = _limit_int("runs_db_max_rows", 100_000)
    llm_metrics_max_age_days = _limit_int("llm_metrics_db_max_age_days", 90)
    llm_metrics_max_rows = _limit_int("llm_metrics_db_max_rows", 300_000)
    max_bytes = _limit_int("runs_db_max_bytes", 200 * 1024 * 1024)
    min_rows_to_keep = _limit_int("runs_db_min_rows_to_keep", 5_000)
    llm_metrics_min_rows_to_keep = _limit_int("llm_metrics_db_min_rows_to_keep", 20_000)

    with sqlite3.connect(DB_PATH) as conn:
        _ensure_tables(conn)
        _prune_table_by_age_and_rows(
            conn,
            _RUNS_TABLE,
            max_age_days=max_age_days,
            max_rows=max_rows,
        )
        _prune_table_by_age_and_rows(
            conn,
            _LLM_METRICS_TABLE,
            max_age_days=llm_metrics_max_age_days,
            max_rows=llm_metrics_max_rows,
        )
        conn.commit()

        # Size-based retention by deleting oldest chunks until below target.
        if max_bytes > 0 and DB_PATH.stat().st_size > max_bytes:
            while DB_PATH.exists() and DB_PATH.stat().st_size > max_bytes:
                runs_count = _table_row_count(conn, _RUNS_TABLE)
                metrics_count = _table_row_count(conn, _LLM_METRICS_TABLE)
                can_prune_runs = runs_count > max(min_rows_to_keep, 1)
                can_prune_metrics = metrics_count > max(llm_metrics_min_rows_to_keep, 1)
                if not can_prune_runs and not can_prune_metrics:
                    break
                if can_prune_metrics and (not can_prune_runs or metrics_count >= runs_count):
                    table = _LLM_METRICS_TABLE
                    row_count = metrics_count
                    min_keep = llm_metrics_min_rows_to_keep
                else:
                    table = _RUNS_TABLE
                    row_count = runs_count
                    min_keep = min_rows_to_keep
                chunk = max(500, row_count // 20)  # delete oldest 5% per pass
                removable = row_count - max(min_keep, 1)
                chunk = max(0, min(chunk, removable))
                if chunk <= 0:
                    break
                _delete_oldest_rows(conn, table, chunk)
                conn.commit()

        # Reclaim pages when pruning happened or DB remains above target.
        if max_bytes > 0 and DB_PATH.exists() and DB_PATH.stat().st_size > max_bytes:
            conn.execute("VACUUM")
            conn.commit()


def _table_row_count(conn: sqlite3.Connection, table: str) -> int:
    """Return row count for a known local table."""
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _delete_oldest_rows(conn: sqlite3.Connection, table: str, n: int) -> None:
    """Delete oldest rows by created_at/id for one known table."""
    conn.execute(
        f"""
        DELETE FROM {table}
        WHERE id IN (
            SELECT id
            FROM {table}
            ORDER BY created_at ASC, id ASC
            LIMIT ?
        )
        """,
        (int(n),),
    )


def _prune_table_by_age_and_rows(
    conn: sqlite3.Connection,
    table: str,
    *,
    max_age_days: int,
    max_rows: int,
) -> None:
    """Apply age + row-count retention on one known table."""
    if max_age_days > 0:
        cutoff = (datetime.now(UTC) - timedelta(days=max_age_days)).isoformat()
        conn.execute(f"DELETE FROM {table} WHERE created_at < ?", (cutoff,))

    if max_rows > 0:
        row_count = _table_row_count(conn, table)
        over = row_count - max_rows
        if over > 0:
            _delete_oldest_rows(conn, table, over)

def get_recent_prices(
    site: str,
    task: str,
    limit: int = 20,
) -> List[float]:
    """Return recent non-low-confidence prices for a site/task pair."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            """
            SELECT price
            FROM runs
            WHERE site = ?
              AND task = ?
              AND price IS NOT NULL
              AND confidence != 'low'
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (site, task, limit),
        )

        rows = cursor.fetchall()

    return [row[0] for row in rows if row[0] is not None]

def get_last_success(
    site: str,
    task: str,
):
    """Return the most recent successful run payload for a site/task pair."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            """
            SELECT price, currency, confidence, created_at
            FROM runs
            WHERE site = ?
              AND task = ?
              AND confidence IN ('high', 'medium')
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (site, task),
        )

        row = cursor.fetchone()

    if not row:
        return None

    return {
        "price": row[0],
        "currency": row[1],
        "confidence": row[2],
        "created_at": row[3],
    }


def get_last_price_record(site: str, task: str) -> Optional[Dict[str, Any]]:
    """Return latest run with a non-null price for one site/task."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            """
            SELECT price, currency, confidence, created_at
            FROM runs
            WHERE site = ?
              AND task = ?
              AND price IS NOT NULL
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (site, task),
        )
        row = cursor.fetchone()

    if not row:
        return None

    return {
        "price": row[0],
        "currency": row[1],
        "confidence": row[2],
        "created_at": row[3],
    }


def list_runs(site: Optional[str] = None, task: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return run rows ordered by newest first, optionally filtered."""
    where_parts = []
    params: List[Any] = []
    if site:
        where_parts.append("site = ?")
        params.append(site)
    if task:
        where_parts.append("task = ?")
        params.append(task)

    where_sql = ""
    if where_parts:
        where_sql = "WHERE " + " AND ".join(where_parts)

    query = f"""
        SELECT site, task, price, currency, confidence, created_at
        FROM runs
        {where_sql}
        ORDER BY created_at DESC
    """

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(query, params)
        rows = cursor.fetchall()

    out: List[Dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "site": row[0],
                "task": row[1],
                "price": row[2],
                "currency": row[3],
                "confidence": row[4],
                "created_at": row[5],
            }
        )
    return out


def list_llm_metrics(
    *,
    limit: int = 500,
    status: Optional[str] = None,
    mode: Optional[str] = None,
    model: Optional[str] = None,
    endpoint: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return recent persisted LLM call metrics for diagnostics dashboards."""
    where_parts = []
    params: List[Any] = []
    if status:
        where_parts.append("status = ?")
        params.append(status)
    if mode:
        where_parts.append("mode = ?")
        params.append(mode)
    if model:
        where_parts.append("model = ?")
        params.append(model)
    if endpoint:
        where_parts.append("endpoint = ?")
        params.append(endpoint)

    where_sql = ""
    if where_parts:
        where_sql = "WHERE " + " AND ".join(where_parts)

    safe_limit = max(1, int(limit))
    params.append(safe_limit)
    query = f"""
        SELECT
            status,
            category,
            mode,
            think,
            model,
            endpoint,
            attempts,
            elapsed_s,
            done_reason,
            eval_count,
            prompt_eval_count,
            tokens_per_sec,
            error_count,
            retry_after_s,
            error_text,
            created_at
        FROM {_LLM_METRICS_TABLE}
        {where_sql}
        ORDER BY created_at DESC, id DESC
        LIMIT ?
    """

    with sqlite3.connect(DB_PATH) as conn:
        _ensure_tables(conn)
        cursor = conn.execute(query, params)
        rows = cursor.fetchall()

    out: List[Dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "status": row[0],
                "category": row[1],
                "mode": row[2],
                "think": row[3],
                "model": row[4],
                "endpoint": row[5],
                "attempts": row[6],
                "elapsed_s": row[7],
                "done_reason": row[8],
                "eval_count": row[9],
                "prompt_eval_count": row[10],
                "tokens_per_sec": row[11],
                "error_count": row[12],
                "retry_after_s": row[13],
                "error_text": row[14],
                "created_at": row[15],
            }
        )
    return out
