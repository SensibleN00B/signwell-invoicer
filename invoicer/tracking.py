"""Local SQLite tracking of sent invoices.

Why SQLite over a JSON log:
  - atomic writes (UPDATE ... WHERE status='draft' for the draft->sent transition)
  - SQL for reporting ("show me all pending invoices older than 7 days")
  - no external deps (stdlib sqlite3)

Schema is intentionally minimal; extend as needs grow.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Literal

SCHEMA = """
CREATE TABLE IF NOT EXISTS sent_invoices (
    document_id   TEXT PRIMARY KEY,
    client_key    TEXT NOT NULL,
    client_email  TEXT NOT NULL,
    file_path     TEXT NOT NULL,
    file_sha256   TEXT NOT NULL,
    test_mode     INTEGER NOT NULL,  -- 0/1
    status        TEXT NOT NULL,     -- draft | sent | completed | declined | cancelled
    created_at    TEXT NOT NULL,     -- ISO-8601 UTC
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_status ON sent_invoices(status);
CREATE INDEX IF NOT EXISTS idx_client ON sent_invoices(client_key);
CREATE INDEX IF NOT EXISTS idx_hash   ON sent_invoices(file_sha256);
"""

Status = Literal["draft", "sent", "completed", "declined", "cancelled"]


class Tracker:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # --- Queries ---------------------------------------------------------------

    def find_by_file_hash(
        self, file_sha256: str, test_mode: bool, client_key: str | None = None
    ) -> sqlite3.Row | None:
        """Return existing record for this file+client pair (to catch accidental re-sends)."""
        with self._conn() as c:
            if client_key is not None:
                row = c.execute(
                    "SELECT * FROM sent_invoices"
                    " WHERE file_sha256 = ? AND test_mode = ? AND client_key = ?"
                    " ORDER BY created_at DESC LIMIT 1",
                    (file_sha256, int(test_mode), client_key),
                ).fetchone()
            else:
                row = c.execute(
                    "SELECT * FROM sent_invoices WHERE file_sha256 = ? AND test_mode = ?"
                    " ORDER BY created_at DESC LIMIT 1",
                    (file_sha256, int(test_mode)),
                ).fetchone()
            return row

    def get(self, document_id: str) -> sqlite3.Row | None:
        with self._conn() as c:
            return c.execute(
                "SELECT * FROM sent_invoices WHERE document_id = ?", (document_id,)
            ).fetchone()

    def list_pending(self) -> list[sqlite3.Row]:
        with self._conn() as c:
            return c.execute(
                "SELECT * FROM sent_invoices WHERE status IN ('draft', 'sent')"
                " ORDER BY created_at DESC"
            ).fetchall()

    def list_all(self, limit: int = 50) -> list[sqlite3.Row]:
        with self._conn() as c:
            return c.execute(
                "SELECT * FROM sent_invoices ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()

    # --- Mutations -------------------------------------------------------------

    def insert_draft(
        self,
        *,
        document_id: str,
        client_key: str,
        client_email: str,
        file_path: str,
        file_sha256: str,
        test_mode: bool,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as c:
            c.execute(
                "INSERT INTO sent_invoices (document_id, client_key, client_email,"
                " file_path, file_sha256, test_mode, status, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 'draft', ?, ?)",
                (document_id, client_key, client_email, file_path, file_sha256,
                 int(test_mode), now, now),
            )

    def update_status(self, document_id: str, status: Status) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as c:
            c.execute(
                "UPDATE sent_invoices SET status = ?, updated_at = ? WHERE document_id = ?",
                (status, now, document_id),
            )
