import sqlite3
from pathlib import Path
from typing import Any

from app.config import SETTINGS


VALID_JOB_STATUSES = {
    "queued",
    "running",
    "done",
    "error",
    "needs_approval",
    "cancelled",
}


def _ensure_db_parent_dir() -> None:
    db_path = Path(SETTINGS.db_path)
    if db_path.parent != Path("."):
        db_path.parent.mkdir(parents=True, exist_ok=True)


def get_connection() -> sqlite3.Connection:
    _ensure_db_parent_dir()
    conn = sqlite3.connect(SETTINGS.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


def init_db() -> None:
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chats (
                chat_id INTEGER PRIMARY KEY,
                default_agent TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                agent TEXT NOT NULL,
                prompt TEXT NOT NULL,
                status TEXT NOT NULL,
                result TEXT,
                error TEXT,
                is_approved INTEGER NOT NULL DEFAULT 0,
                approved_by INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                started_at TEXT,
                finished_at TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status_id ON jobs(status, id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_chat_id ON jobs(chat_id, id DESC)")


def get_default_agent(chat_id: int) -> str:
    with get_connection() as conn:
        row = conn.execute("SELECT default_agent FROM chats WHERE chat_id = ?", (chat_id,)).fetchone()
        return str(row["default_agent"]) if row else "researcher"


def set_default_agent(chat_id: int, agent: str) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO chats(chat_id, default_agent, updated_at)
            VALUES(?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(chat_id)
            DO UPDATE SET default_agent = excluded.default_agent, updated_at = CURRENT_TIMESTAMP
            """,
            (chat_id, agent),
        )


def create_job(chat_id: int, user_id: int, agent: str, prompt: str) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO jobs(chat_id, user_id, agent, prompt, status)
            VALUES (?, ?, ?, ?, 'queued')
            """,
            (chat_id, user_id, agent, prompt),
        )
        return int(cur.lastrowid)


def list_recent_jobs(chat_id: int, limit: int = 10) -> list[sqlite3.Row]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, agent, status, is_approved, created_at, updated_at, result, error
            FROM jobs
            WHERE chat_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (chat_id, limit),
        ).fetchall()
        return list(rows)


def approve_job(job_id: int, approved_by: int) -> bool:
    with get_connection() as conn:
        cur = conn.execute(
            """
            UPDATE jobs
            SET status = 'queued',
                is_approved = 1,
                approved_by = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'needs_approval'
            """,
            (approved_by, job_id),
        )
        return cur.rowcount == 1


def cancel_job(job_id: int) -> bool:
    with get_connection() as conn:
        cur = conn.execute(
            """
            UPDATE jobs
            SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status IN ('queued', 'needs_approval')
            """,
            (job_id,),
        )
        return cur.rowcount == 1


def claim_oldest_queued_job() -> dict[str, Any] | None:
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT id, chat_id, user_id, agent, prompt, status, is_approved
            FROM jobs
            WHERE status = 'queued'
            ORDER BY id ASC
            LIMIT 1
            """
        ).fetchone()
        if not row:
            conn.commit()
            return None

        cur = conn.execute(
            """
            UPDATE jobs
            SET status = 'running',
                started_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'queued'
            """,
            (row["id"],),
        )
        conn.commit()

        if cur.rowcount != 1:
            return None

        return {
            "id": int(row["id"]),
            "chat_id": int(row["chat_id"]),
            "user_id": int(row["user_id"]),
            "agent": str(row["agent"]),
            "prompt": str(row["prompt"]),
            "is_approved": bool(row["is_approved"]),
        }


def mark_job_needs_approval(job_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE jobs
            SET status = 'needs_approval',
                started_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (job_id,),
        )


def mark_job_done(job_id: int, result: str) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE jobs
            SET status = 'done',
                result = ?,
                error = NULL,
                updated_at = CURRENT_TIMESTAMP,
                finished_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (result, job_id),
        )


def mark_job_error(job_id: int, error: str) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE jobs
            SET status = 'error',
                error = ?,
                updated_at = CURRENT_TIMESTAMP,
                finished_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (error, job_id),
        )


def get_db_journal_mode() -> str:
    with get_connection() as conn:
        row = conn.execute("PRAGMA journal_mode;").fetchone()
        if row is None:
            return "unknown"
        return str(row[0])
