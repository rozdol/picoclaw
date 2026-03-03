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
    conn.execute("PRAGMA foreign_keys=ON;")
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
                skill_context TEXT,
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
        job_columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        if "skill_context" not in job_columns:
            conn.execute("ALTER TABLE jobs ADD COLUMN skill_context TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status_id ON jobs(status, id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_chat_id ON jobs(chat_id, id DESC)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS skills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_skills (
                chat_id INTEGER NOT NULL,
                skill_id INTEGER NOT NULL,
                is_enabled INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (chat_id, skill_id),
                FOREIGN KEY (skill_id) REFERENCES skills(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_skills_chat ON chat_skills(chat_id, is_enabled)")


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


def create_job(chat_id: int, user_id: int, agent: str, prompt: str, skill_context: str = "") -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO jobs(chat_id, user_id, agent, prompt, skill_context, status)
            VALUES (?, ?, ?, ?, ?, 'queued')
            """,
            (chat_id, user_id, agent, prompt, skill_context.strip()),
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
            SELECT id, chat_id, user_id, agent, prompt, skill_context, status, is_approved
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
            "skill_context": str(row["skill_context"] or ""),
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


def _normalize_skill_name(name: str) -> str:
    return name.strip().lower()


def upsert_skill(name: str, content: str) -> tuple[int, bool]:
    normalized_name = _normalize_skill_name(name)
    cleaned_content = content.strip()
    if not normalized_name:
        raise ValueError("Skill name cannot be empty")
    if not cleaned_content:
        raise ValueError("Skill content cannot be empty")

    with get_connection() as conn:
        existing = conn.execute("SELECT id FROM skills WHERE name = ?", (normalized_name,)).fetchone()
        if existing:
            skill_id = int(existing["id"])
            conn.execute(
                """
                UPDATE skills
                SET content = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (cleaned_content, skill_id),
            )
            return skill_id, False

        cur = conn.execute(
            """
            INSERT INTO skills(name, content)
            VALUES (?, ?)
            """,
            (normalized_name, cleaned_content),
        )
        return int(cur.lastrowid), True


def set_chat_skill_enabled(chat_id: int, skill_name: str, enabled: bool) -> bool:
    normalized_name = _normalize_skill_name(skill_name)
    if not normalized_name:
        return False

    with get_connection() as conn:
        row = conn.execute("SELECT id FROM skills WHERE name = ?", (normalized_name,)).fetchone()
        if row is None:
            return False

        skill_id = int(row["id"])
        conn.execute(
            """
            INSERT INTO chat_skills(chat_id, skill_id, is_enabled, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(chat_id, skill_id)
            DO UPDATE SET is_enabled = excluded.is_enabled, updated_at = CURRENT_TIMESTAMP
            """,
            (chat_id, skill_id, 1 if enabled else 0),
        )
        return True


def list_skills_with_chat_state(chat_id: int) -> list[sqlite3.Row]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT s.name, s.updated_at, COALESCE(cs.is_enabled, 0) AS is_enabled
            FROM skills s
            LEFT JOIN chat_skills cs
              ON cs.skill_id = s.id
             AND cs.chat_id = ?
            ORDER BY s.name ASC
            """,
            (chat_id,),
        ).fetchall()
        return list(rows)


def get_enabled_skills_for_chat(chat_id: int) -> list[dict[str, str]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT s.name, s.content
            FROM skills s
            INNER JOIN chat_skills cs
              ON cs.skill_id = s.id
            WHERE cs.chat_id = ?
              AND cs.is_enabled = 1
            ORDER BY s.name ASC
            """,
            (chat_id,),
        ).fetchall()

    return [{"name": str(row["name"]), "content": str(row["content"])} for row in rows]
