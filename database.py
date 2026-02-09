"""
TechTap — Tag Database & Logging Module
Stores tag write history, UIDs, and operation logs in SQLite.
"""

import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from techtap.utils import DATA_DIR, logger, format_uid


DB_PATH = DATA_DIR / "techtap.db"


class TagDatabase:
    """SQLite database for tag write history and logging."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def _init_db(self) -> None:
        """Create tables if they don't exist."""
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uid TEXT NOT NULL,
                tag_type TEXT DEFAULT 'UNKNOWN',
                first_seen TEXT NOT NULL,
                last_written TEXT,
                write_count INTEGER DEFAULT 0,
                is_locked INTEGER DEFAULT 0,
                notes TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS write_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uid TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                operation TEXT NOT NULL,
                record_type TEXT NOT NULL,
                content_summary TEXT,
                data_size INTEGER DEFAULT 0,
                success INTEGER DEFAULT 1,
                error_message TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS bulk_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                record_type TEXT NOT NULL,
                content_template TEXT,
                total_written INTEGER DEFAULT 0,
                total_failed INTEGER DEFAULT 0
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_tags_uid ON tags(uid);
            CREATE INDEX IF NOT EXISTS idx_write_log_uid ON write_log(uid);
            CREATE INDEX IF NOT EXISTS idx_write_log_ts ON write_log(timestamp);
        """)
        conn.commit()
        logger.info("Database initialized.")

    # ── Tag Management ─────────────────────────────────────────────────

    def register_tag(self, uid: str, tag_type: str = "UNKNOWN") -> dict:
        """Register a new tag or return existing record."""
        conn = self._get_conn()
        uid = uid.upper().strip()
        now = datetime.now().isoformat()

        existing = conn.execute(
            "SELECT * FROM tags WHERE uid = ?", (uid,)
        ).fetchone()

        if existing:
            return dict(existing)

        conn.execute(
            "INSERT INTO tags (uid, tag_type, first_seen) VALUES (?, ?, ?)",
            (uid, tag_type, now)
        )
        conn.commit()
        logger.info(f"New tag registered: {uid}")
        return {
            "uid": uid, "tag_type": tag_type, "first_seen": now,
            "write_count": 0, "is_locked": False
        }

    def update_tag_write(self, uid: str) -> None:
        """Increment write count and update last_written timestamp."""
        conn = self._get_conn()
        uid = uid.upper().strip()
        now = datetime.now().isoformat()
        conn.execute(
            "UPDATE tags SET write_count = write_count + 1, last_written = ? WHERE uid = ?",
            (now, uid)
        )
        conn.commit()

    def set_tag_locked(self, uid: str, locked: bool = True) -> None:
        """Mark tag as locked/unlocked."""
        conn = self._get_conn()
        uid = uid.upper().strip()
        conn.execute(
            "UPDATE tags SET is_locked = ? WHERE uid = ?",
            (1 if locked else 0, uid)
        )
        conn.commit()

    def get_tag(self, uid: str) -> Optional[dict]:
        """Get tag info by UID."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM tags WHERE uid = ?", (uid.upper().strip(),)
        ).fetchone()
        return dict(row) if row else None

    def get_all_tags(self, limit: int = 50) -> list[dict]:
        """Get all registered tags."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM tags ORDER BY last_written DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def tag_has_data(self, uid: str) -> bool:
        """Check if a tag has been written to before (duplicate detection)."""
        tag = self.get_tag(uid)
        return tag is not None and tag.get("write_count", 0) > 0

    # ── Write Log ──────────────────────────────────────────────────────

    def log_write(self, uid: str, operation: str, record_type: str,
                  content_summary: str = "", data_size: int = 0,
                  success: bool = True, error_message: str = "") -> None:
        """Log a write/erase/lock operation."""
        conn = self._get_conn()
        uid = uid.upper().strip()
        now = datetime.now().isoformat()
        conn.execute(
            """INSERT INTO write_log
               (uid, timestamp, operation, record_type, content_summary,
                data_size, success, error_message)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (uid, now, operation, record_type, content_summary[:200],
             data_size, 1 if success else 0, error_message)
        )
        conn.commit()

        if success:
            self.register_tag(uid)
            self.update_tag_write(uid)

        logger.info(
            f"Write log: uid={uid} op={operation} type={record_type} "
            f"success={success}"
        )

    def get_write_history(self, uid: Optional[str] = None,
                          limit: int = 50) -> list[dict]:
        """Get write history, optionally filtered by UID."""
        conn = self._get_conn()
        if uid:
            rows = conn.execute(
                "SELECT * FROM write_log WHERE uid = ? ORDER BY timestamp DESC LIMIT ?",
                (uid.upper().strip(), limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM write_log ORDER BY timestamp DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Bulk Sessions ──────────────────────────────────────────────────

    def start_bulk_session(self, record_type: str,
                           content_template: str) -> int:
        """Start a new bulk write session. Returns session ID."""
        conn = self._get_conn()
        now = datetime.now().isoformat()
        cursor = conn.execute(
            """INSERT INTO bulk_sessions
               (started_at, record_type, content_template)
               VALUES (?, ?, ?)""",
            (now, record_type, content_template)
        )
        conn.commit()
        session_id = cursor.lastrowid
        logger.info(f"Bulk session started: #{session_id}")
        return session_id

    def update_bulk_session(self, session_id: int,
                            written: int = 0, failed: int = 0) -> None:
        """Update bulk session counters."""
        conn = self._get_conn()
        conn.execute(
            """UPDATE bulk_sessions
               SET total_written = total_written + ?,
                   total_failed = total_failed + ?
               WHERE id = ?""",
            (written, failed, session_id)
        )
        conn.commit()

    def end_bulk_session(self, session_id: int) -> dict:
        """End a bulk session and return summary."""
        conn = self._get_conn()
        now = datetime.now().isoformat()
        conn.execute(
            "UPDATE bulk_sessions SET ended_at = ? WHERE id = ?",
            (now, session_id)
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM bulk_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        logger.info(f"Bulk session ended: #{session_id}")
        return dict(row) if row else {}

    # ── Stats ──────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """Get overall database statistics."""
        conn = self._get_conn()
        total_tags = conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
        total_writes = conn.execute("SELECT COUNT(*) FROM write_log").fetchone()[0]
        successful = conn.execute(
            "SELECT COUNT(*) FROM write_log WHERE success = 1"
        ).fetchone()[0]
        failed = conn.execute(
            "SELECT COUNT(*) FROM write_log WHERE success = 0"
        ).fetchone()[0]
        return {
            "total_tags": total_tags,
            "total_writes": total_writes,
            "successful_writes": successful,
            "failed_writes": failed,
            "success_rate": f"{(successful / total_writes * 100):.1f}%" if total_writes else "N/A"
        }

    # ── Cleanup ────────────────────────────────────────────────────────

    def close(self) -> None:
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
