"""Append-only live review log.

This log is intentionally separate from OLV chat history and the recap store:
one row per selected live batch (or one normal-chat turn), with the triggering
viewer messages/gifts and the model reply. It is JSONL so it can be exported or
replayed without a database migration.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from .events import RoomEvent

__all__ = ["LiveReviewLogger", "default_review_logger"]


class LiveReviewLogger:
    def __init__(self, path: str | Path = "data/live-review.jsonl") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def record(
        self,
        events: Iterable[RoomEvent],
        reply: str,
        *,
        session_id: str = "",
        source: str = "live",
        now_ms: int | None = None,
    ) -> dict[str, Any] | None:
        items = [
            {
                "type": event.type,
                "user": event.user.name,
                "user_id": event.user.id,
                "content": event.content,
                "gift": event.gift.model_dump() if event.gift else None,
                "received_at_ms": event.received_at_ms,
            }
            for event in events
        ]
        if not items and not reply:
            return None
        timestamp = int(now_ms if now_ms is not None else time.time() * 1000)
        # Test harnesses historically passed synthetic millisecond counters
        # (e.g. 1000), which produced 1970-01-01 rows in the production log.
        if timestamp < 946_684_800_000:
            return None
        record = {
            "ts": datetime.fromtimestamp(timestamp / 1000).isoformat(timespec="seconds"),
            "ts_ms": timestamp,
            "session_id": session_id,
            "source": source,
            "events": items,
            "reply": reply,
        }
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        return record


default_review_logger = LiveReviewLogger()
