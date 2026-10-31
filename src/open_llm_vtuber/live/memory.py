"""Layered live memory with provenance and scope isolation.

The first implementation uses lexical retrieval (no embedding service) so it
can run inside OLV without a second process.  Every derived fact keeps its
source event ids and validity window; summaries never overwrite raw events.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import uuid
from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from .events import LiveBatch, RoomEvent

__all__ = [
    "MemoryHit",
    "MemoryItem",
    "MemoryManager",
    "MemoryScope",
    "MemoryStore",
]

MemoryScope = str
_SCOPE_ROOM = "room"
_SCOPE_VIEWER = "viewer"
_SCOPE_SESSION = "session"
_SCOPE_CHARACTER = "character"
_PREFERENCE = re.compile(r"(?:我|咱)(?:喜欢|不喜欢|爱看|不看|常玩|想要)(.{1,40})")
_PROMISE = re.compile(r"(?:下次|明天|晚点|等会|之后).{0,30}(?:再|会|来|讲|玩|看)")


@dataclass(frozen=True)
class MemoryItem:
    id: str
    text: str
    kind: str = "fact"
    scope: MemoryScope = _SCOPE_ROOM
    owner_id: str = ""
    source_event_ids: tuple[str, ...] = ()
    confidence: float = 0.8
    importance: float = 0.5
    created_at_ms: int = 0
    valid_from_ms: int = 0
    valid_to_ms: int | None = None
    superseded_by: str | None = None
    last_accessed_ms: int = 0

    def is_valid(self, now_ms: int) -> bool:
        return (
            self.superseded_by is None
            and self.valid_from_ms <= now_ms
            and (self.valid_to_ms is None or now_ms <= self.valid_to_ms)
        )


@dataclass(frozen=True)
class MemoryHit:
    item: MemoryItem
    score: float


class MemoryStore:
    """Small SQLite-backed store; pass path=None for an in-memory test store."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else None
        self._items: dict[str, MemoryItem] = {}
        self._viewers: dict[str, dict[str, object]] = {}
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _init_schema(self) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_items (
                    id TEXT PRIMARY KEY,
                    text TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    source_event_ids TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    importance REAL NOT NULL,
                    created_at_ms INTEGER NOT NULL,
                    valid_from_ms INTEGER NOT NULL,
                    valid_to_ms INTEGER,
                    superseded_by TEXT,
                    last_accessed_ms INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS viewer_profiles (
                    user_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    familiarity REAL NOT NULL,
                    warmth REAL NOT NULL,
                    interactions INTEGER NOT NULL,
                    last_seen_ms INTEGER NOT NULL
                )
                """
            )
            connection.commit()

    def add(self, item: MemoryItem) -> MemoryItem:
        if not item.id:
            item = MemoryItem(**{**item.__dict__, "id": uuid.uuid4().hex})
        if self.path:
            with closing(self._connect()) as connection:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO memory_items(
                        id,text,kind,scope,owner_id,source_event_ids,confidence,
                        importance,created_at_ms,valid_from_ms,valid_to_ms,
                        superseded_by,last_accessed_ms
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        item.id,
                        item.text,
                        item.kind,
                        item.scope,
                        item.owner_id,
                        json.dumps(list(item.source_event_ids), ensure_ascii=False),
                        item.confidence,
                        item.importance,
                        item.created_at_ms,
                        item.valid_from_ms,
                        item.valid_to_ms,
                        item.superseded_by,
                        item.last_accessed_ms,
                    ),
                )
                connection.commit()
        else:
            self._items[item.id] = item
        return item

    def get(self, item_id: str) -> MemoryItem | None:
        if not self.path:
            return self._items.get(item_id)
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM memory_items WHERE id=?", (item_id,)
            ).fetchone()
        return _row_to_item(row) if row else None

    def all(self) -> list[MemoryItem]:
        if not self.path:
            return list(self._items.values())
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT * FROM memory_items").fetchall()
        return [_row_to_item(row) for row in rows]

    def supersede(self, old_id: str, replacement: MemoryItem) -> MemoryItem:
        old = self.get(old_id)
        if old is None:
            raise KeyError(f"unknown memory item: {old_id}")
        replacement = self.add(replacement)
        updated = MemoryItem(
            **{
                **old.__dict__,
                "valid_to_ms": replacement.valid_from_ms or replacement.created_at_ms,
                "superseded_by": replacement.id,
            }
        )
        self.add(updated)
        return replacement

    def forget(self, item_id: str) -> None:
        if self.path:
            with closing(self._connect()) as connection:
                connection.execute("DELETE FROM memory_items WHERE id=?", (item_id,))
                connection.commit()
        else:
            self._items.pop(item_id, None)

    def save_viewer_profile(self, profile: dict[str, object]) -> None:
        user_id = str(profile.get("user_id") or "")
        if not user_id:
            return
        if self.path:
            with closing(self._connect()) as connection:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO viewer_profiles(
                        user_id,name,familiarity,warmth,interactions,last_seen_ms
                    ) VALUES (?,?,?,?,?,?)
                    """,
                    (
                        user_id,
                        str(profile.get("name") or ""),
                        float(profile.get("familiarity", 0.0)),
                        float(profile.get("warmth", 0.0)),
                        int(profile.get("interactions", 0)),
                        int(profile.get("last_seen_ms", 0)),
                    ),
                )
                connection.commit()
        else:
            self._viewers[user_id] = dict(profile)

    def load_viewer_profiles(self) -> list[dict[str, object]]:
        if not self.path:
            return list(self._viewers.values())
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT * FROM viewer_profiles").fetchall()
        return [dict(row) for row in rows]

    def search(
        self,
        query: str,
        *,
        now_ms: int,
        scope: str | None = None,
        owner_id: str | None = None,
        limit: int = 5,
    ) -> list[MemoryHit]:
        terms = _terms(query)
        if not terms:
            return []
        candidates = [
            item
            for item in self.all()
            if item.is_valid(now_ms)
            and (scope is None or item.scope == scope)
            and (owner_id is None or item.owner_id in ("", owner_id))
        ]
        if not candidates:
            return []
        # A small BM25-like IDF pass prevents common words from dominating the
        # tiny memory store, while keeping retrieval local and dependency-free.
        document_terms = [_terms(item.text) for item in candidates]
        document_frequency = Counter(
            term for terms_in_doc in document_terms for term in terms_in_doc
        )
        total = len(candidates)
        idf = {
            term: math.log((total + 1) / (document_frequency[term] + 1)) + 1.0
            for term in terms
        }
        idf_total = sum(idf.values()) or 1.0
        hits: list[MemoryHit] = []
        for item, item_terms in zip(candidates, document_terms):
            matched = terms & item_terms
            if not matched:
                continue
            relevance = sum(idf[term] for term in matched) / idf_total
            phrase_bonus = 0.2 if query.strip() in item.text else 0.0
            relevance = min(1.0, relevance + phrase_bonus)
            age_ms = max(0, now_ms - item.created_at_ms)
            recency = 1.0 / (1.0 + age_ms / (24 * 60 * 60 * 1000))
            score = 0.65 * relevance + 0.2 * item.importance + 0.1 * recency
            hits.append(MemoryHit(item, round(score, 4)))
        return sorted(hits, key=lambda hit: hit.score, reverse=True)[: max(1, limit)]


class MemoryManager:
    """Cheap, deterministic write and retrieval policy for a live session."""

    def __init__(self, store: MemoryStore | None = None) -> None:
        self.store = store or MemoryStore()

    def ingest_batch(self, batch: LiveBatch, *, session_id: str, now_ms: int) -> list[MemoryItem]:
        written: list[MemoryItem] = []
        for event in batch.events:
            for item in self._ingest_event(
                event, session_id=session_id, now_ms=now_ms
            ):
                written.append(self.store.add(item))
        return written

    def _ingest_event(
        self, event: RoomEvent, *, session_id: str, now_ms: int
    ) -> list[MemoryItem]:
        items: list[MemoryItem] = []
        if event.type == "gift" and event.gift:
            items.append(
                self._new(
                    f"{event.user.name} 送出 {event.gift.name}×{event.gift.count}",
                    kind="event",
                    scope=_SCOPE_ROOM,
                    source=(event.id,),
                    now_ms=now_ms,
                    importance=0.55,
                )
            )
        text = event.content or ""
        match = _PREFERENCE.search(text)
        value = match.group(1).strip() if match else ""
        if match and event.user.id and value and not re.search(
            r"[你您吗什么谁他她]", value
        ):
            items.append(
                self._new(
                    f"{event.user.name} 的偏好：{value}",
                    kind="preference",
                    scope=_SCOPE_VIEWER,
                    owner_id=event.user.id,
                    source=(event.id,),
                    now_ms=now_ms,
                    confidence=0.7,
                    importance=0.6,
                )
            )
        if _PROMISE.search(text):
            items.append(
                self._new(
                    f"待兑现话题：{text[:100]}",
                    kind="promise",
                    scope=_SCOPE_SESSION,
                    source=(event.id,),
                    now_ms=now_ms,
                    confidence=0.65,
                    importance=0.8,
                )
            )
        return items

    def add_recap(self, summary: str, *, session_id: str, now_ms: int) -> MemoryItem:
        return self.store.add(
            self._new(
                summary[:2000],
                kind="recap",
                scope=_SCOPE_ROOM,
                source=(session_id,),
                now_ms=now_ms,
                importance=0.75,
            )
        )

    def save_viewer(self, viewer: object) -> None:
        self.store.save_viewer_profile(
            {
                "user_id": getattr(viewer, "user_id", ""),
                "name": getattr(viewer, "name", ""),
                "familiarity": getattr(viewer, "familiarity", 0.0),
                "warmth": getattr(viewer, "warmth", 0.0),
                "interactions": getattr(viewer, "interactions", 0),
                "last_seen_ms": getattr(viewer, "last_seen_ms", 0),
            }
        )

    def load_viewers(self) -> list[dict[str, object]]:
        return self.store.load_viewer_profiles()

    def for_viewer(
        self, viewer_id: str, *, now_ms: int, limit: int = 3
    ) -> list[MemoryHit]:
        if not viewer_id:
            return []
        hits = self.store.search(
            "偏好", now_ms=now_ms, scope=_SCOPE_VIEWER, owner_id=viewer_id, limit=limit
        )
        return hits

    def for_room(self, query: str, *, now_ms: int, limit: int = 4) -> list[MemoryHit]:
        hits = self.store.search(query, now_ms=now_ms, scope=_SCOPE_ROOM, limit=limit)
        if not hits:
            hits = self.store.search(query, now_ms=now_ms, scope=_SCOPE_SESSION, limit=limit)
        return hits

    @staticmethod
    def _new(
        text: str,
        *,
        kind: str,
        scope: str,
        source: tuple[str, ...],
        now_ms: int,
        confidence: float = 0.8,
        importance: float = 0.5,
        owner_id: str = "",
    ) -> MemoryItem:
        return MemoryItem(
            id="",
            text=text,
            kind=kind,
            scope=scope,
            owner_id=owner_id,
            source_event_ids=source,
            confidence=confidence,
            importance=importance,
            created_at_ms=now_ms,
            valid_from_ms=now_ms,
            last_accessed_ms=now_ms,
        )


def _row_to_item(row: sqlite3.Row) -> MemoryItem:
    data = dict(row)
    data["source_event_ids"] = tuple(json.loads(data["source_event_ids"]))
    return MemoryItem(**data)


def _terms(text: str) -> set[str]:
    compact = re.sub(r"\s+", "", text or "")
    terms = {compact[index : index + 2] for index in range(len(compact) - 1)}
    terms.update(re.findall(r"[A-Za-z0-9_]+", text or ""))
    return {term.lower() for term in terms if term}
