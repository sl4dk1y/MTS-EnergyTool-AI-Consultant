"""Хранилище веб-прототипа на SQLite.

Хранит сессии, сообщения, заявки и передачи вопросов специалисту. Каждая
операция открывает своё соединение, поэтому хранилище можно вызывать из
нескольких потоков FastAPI.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Optional, Union

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    sources TEXT NOT NULL DEFAULT '[]',
    elapsed_ms INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_session ON messages(session_id, id);
CREATE TABLE IF NOT EXISTS leads (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(id) ON DELETE SET NULL,
    full_name TEXT NOT NULL,
    phone TEXT NOT NULL,
    company TEXT NOT NULL,
    summary TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS handoffs (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(id) ON DELETE SET NULL,
    question TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Storage:
    def __init__(self, path: Union[str, Path]) -> None:
        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self._path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            yield db
            db.commit()
        finally:
            db.close()

    # Сессии

    def create_session(self) -> dict:
        session_id = str(uuid.uuid4())
        now = _now()
        with self._connect() as db:
            db.execute("INSERT INTO sessions (id, created_at, updated_at) VALUES (?, ?, ?)", (session_id, now, now))
        return {"id": session_id, "created_at": now, "updated_at": now}

    def get_session(self, session_id: str) -> Optional[dict]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return dict(row) if row else None

    _SESSION_SUMMARY = """
        SELECT s.id, s.created_at, s.updated_at,
               (SELECT content FROM messages m WHERE m.session_id = s.id AND m.role = 'user'
                ORDER BY m.id LIMIT 1) AS title,
               (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS message_count,
               EXISTS (SELECT 1 FROM leads l WHERE l.session_id = s.id) AS has_lead
        FROM sessions s {where} ORDER BY s.updated_at DESC, s.created_at DESC LIMIT ?
    """

    def list_sessions(self, limit: int = 100) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(self._SESSION_SUMMARY.format(where=""), (limit,)).fetchall()
        return self._summaries(rows)

    def list_sessions_by_ids(self, ids: list[str]) -> list[dict]:
        unique = list(dict.fromkeys(i for i in ids if isinstance(i, str) and i))
        if not unique:
            return []
        where = "WHERE s.id IN (" + ",".join("?" for _ in unique) + ")"
        with self._connect() as db:
            rows = db.execute(self._SESSION_SUMMARY.format(where=where), (*unique, len(unique))).fetchall()
        return self._summaries(rows)

    @staticmethod
    def _summaries(rows) -> list[dict]:
        result = []
        for row in rows:
            item = dict(row)
            item["has_lead"] = bool(item["has_lead"])
            item["title"] = (item["title"] or "")[:80]
            result.append(item)
        return result

    def delete_session(self, session_id: str) -> bool:
        with self._connect() as db:
            cursor = db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        return cursor.rowcount > 0

    # Сообщения

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        sources: Optional[list[str]] = None,
        elapsed_ms: Optional[int] = None,
    ) -> dict:
        now = _now()
        with self._connect() as db:
            cursor = db.execute(
                "INSERT INTO messages (session_id, role, content, sources, elapsed_ms, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, role, content, json.dumps(sources or [], ensure_ascii=False), elapsed_ms, now),
            )
            db.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
            message_id = cursor.lastrowid
        return {
            "id": message_id,
            "role": role,
            "content": content,
            "sources": sources or [],
            "elapsed_ms": elapsed_ms,
            "created_at": now,
        }

    def get_messages(self, session_id: str) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT id, role, content, sources, elapsed_ms, created_at FROM messages "
                "WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()
        messages = []
        for row in rows:
            item = dict(row)
            item["sources"] = json.loads(item["sources"])
            messages.append(item)
        return messages

    def get_history(self, session_id: str) -> list[dict[str, str]]:
        return [{"role": m["role"], "content": m["content"]} for m in self.get_messages(session_id)]

    # Заявки и передачи специалисту

    def add_lead(self, session_id: str, full_name: str, phone: str, company: str, summary: str) -> dict:
        lead = {
            "id": str(uuid.uuid4()),
            "session_id": session_id,
            "full_name": full_name,
            "phone": phone,
            "company": company,
            "summary": summary,
            "created_at": _now(),
        }
        with self._connect() as db:
            db.execute(
                "INSERT INTO leads (id, session_id, full_name, phone, company, summary, created_at) "
                "VALUES (:id, :session_id, :full_name, :phone, :company, :summary, :created_at)",
                lead,
            )
        return lead

    def list_leads(self, limit: int = 100) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM leads ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def add_handoff(self, session_id: str, question: str) -> dict:
        handoff = {"id": str(uuid.uuid4()), "session_id": session_id, "question": question, "created_at": _now()}
        with self._connect() as db:
            db.execute(
                "INSERT INTO handoffs (id, session_id, question, created_at) "
                "VALUES (:id, :session_id, :question, :created_at)",
                handoff,
            )
        return handoff

    def list_handoffs(self, limit: int = 100) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM handoffs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    # Срок хранения

    def purge_older_than(self, session_days: int, lead_days: int) -> dict[str, int]:
        """Удаляет неактивные сессии и старые заявки. 0 отключает удаление."""
        now = datetime.now(timezone.utc)
        removed = {"sessions": 0, "leads": 0, "handoffs": 0}
        with self._connect() as db:
            if session_days > 0:
                cutoff = (now - timedelta(days=session_days)).isoformat(timespec="seconds")
                removed["sessions"] = db.execute("DELETE FROM sessions WHERE updated_at < ?", (cutoff,)).rowcount
            if lead_days > 0:
                cutoff = (now - timedelta(days=lead_days)).isoformat(timespec="seconds")
                removed["leads"] = db.execute("DELETE FROM leads WHERE created_at < ?", (cutoff,)).rowcount
                removed["handoffs"] = db.execute("DELETE FROM handoffs WHERE created_at < ?", (cutoff,)).rowcount
        return removed
