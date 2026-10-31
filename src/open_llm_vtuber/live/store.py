"""Small SQLite persistence boundary for live sessions.

This is deliberately a narrow first slice: session lifecycle, canonical room
events, live chat rows and one recap per session. It is not a replacement for
OLV's companion chat history.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .events import RoomEvent


class LiveStore:
    """SQLite-backed live facts, using one short-lived connection per call."""

    def __init__(self, db_path: str | Path = "data/live.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _session(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _init_schema(self) -> None:
        with self._session() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS live_sessions (
                    id TEXT PRIMARY KEY,
                    started_at_ms INTEGER NOT NULL,
                    ended_at_ms INTEGER,
                    status TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS live_events (
                    session_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    room_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    user_name TEXT NOT NULL,
                    content TEXT NOT NULL,
                    gift_json TEXT,
                    received_at_ms INTEGER NOT NULL,
                    PRIMARY KEY (session_id, platform, room_id, event_id),
                    FOREIGN KEY (session_id) REFERENCES live_sessions(id)
                );
                CREATE TABLE IF NOT EXISTS live_chat (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES live_sessions(id)
                );
                CREATE TABLE IF NOT EXISTS session_recaps (
                    session_id TEXT PRIMARY KEY,
                    summary TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES live_sessions(id)
                );
                """
            )

    def start_session(self, session_id: str, started_at_ms: int) -> None:
        with self._session() as connection:
            connection.execute(
                "INSERT INTO live_sessions(id, started_at_ms, status) VALUES (?, ?, 'running')",
                (session_id, started_at_ms),
            )

    def end_session(self, session_id: str, ended_at_ms: int) -> None:
        with self._session() as connection:
            connection.execute(
                "UPDATE live_sessions SET ended_at_ms=?, status='ended' WHERE id=?",
                (ended_at_ms, session_id),
            )

    @staticmethod
    def _insert_event(
        connection: sqlite3.Connection, session_id: str, event: RoomEvent
    ) -> None:
        gift_json = event.gift.model_dump_json() if event.gift else None
        connection.execute(
            """
            INSERT OR IGNORE INTO live_events(
                session_id, event_id, type, platform, room_id, user_id,
                user_name, content, gift_json, received_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                event.id,
                event.type,
                event.platform,
                event.room_id,
                event.user.id,
                event.user.name,
                event.content,
                gift_json,
                event.received_at_ms,
            ),
        )

    def append_event(self, session_id: str, event: RoomEvent) -> None:
        with self._session() as connection:
            self._insert_event(connection, session_id, event)

    def append_events(self, session_id: str, events: Iterable[RoomEvent]) -> None:
        # One transaction per batch instead of one SQLite connection/commit
        # per event; a 20-event batch otherwise pays 20 fsyncs.
        with self._session() as connection:
            for event in events:
                self._insert_event(connection, session_id, event)

    def append_chat(
        self, session_id: str, role: str, content: str, created_at_ms: int
    ) -> None:
        with self._session() as connection:
            connection.execute(
                "INSERT INTO live_chat(session_id, role, content, created_at_ms) VALUES (?, ?, ?, ?)",
                (session_id, role, content, created_at_ms),
            )

    def latest_recap(self) -> dict[str, Any] | None:
        with self._session() as connection:
            row = connection.execute(
                "SELECT session_id, summary, created_at_ms FROM session_recaps ORDER BY created_at_ms DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def set_recap(self, session_id: str, summary: str, created_at_ms: int) -> None:
        with self._session() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO session_recaps(session_id, summary, created_at_ms) VALUES (?, ?, ?)",
                (session_id, summary, created_at_ms),
            )

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._session() as connection:
            row = connection.execute(
                "SELECT * FROM live_sessions WHERE id=?", (session_id,)
            ).fetchone()
        return dict(row) if row else None

    def recent_events(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._session() as connection:
            rows = connection.execute(
                "SELECT * FROM live_events WHERE session_id=? ORDER BY received_at_ms DESC LIMIT ?",
                (session_id, max(1, min(limit, 200))),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["gift"] = json.loads(item.pop("gift_json")) if item["gift_json"] else None
            result.append(item)
        return result

    def recent_chat(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._session() as connection:
            rows = connection.execute(
                "SELECT role, content, created_at_ms FROM live_chat WHERE session_id=? ORDER BY id DESC LIMIT ?",
                (session_id, max(1, min(limit, 200))),
            ).fetchall()
        return [dict(row) for row in rows]


__all__ = ["LiveStore"]
