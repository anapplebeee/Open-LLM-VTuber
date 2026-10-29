"""Linear rundown state for a live session.

This is a programme memo, not a second agent loop. It validates a director's
curated segment list and exposes the current segment to the planner context.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

RundownStatus = Literal["idle", "armed", "running", "paused", "done"]


@dataclass(frozen=True)
class RundownSegment:
    id: str
    title: str
    goal: str
    expected_ms: int
    min_duration_ms: int = 0
    notes: str = ""

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> RundownSegment:
        segment = cls(
            id=str(value.get("id", "")).strip(),
            title=str(value.get("title", "")).strip(),
            goal=str(value.get("goal", value.get("task_description", ""))).strip(),
            expected_ms=int(value.get("expected_ms", value.get("expected", 0))),
            min_duration_ms=int(value.get("min_duration_ms", 0)),
            notes=str(value.get("notes", "")),
        )
        segment.validate()
        return segment

    def validate(self) -> None:
        if not self.id or not self.title or not self.goal:
            raise ValueError("rundown segment requires id, title and goal")
        if self.expected_ms < 1000:
            raise ValueError("rundown expected_ms must be >= 1000")
        if self.min_duration_ms < 0 or self.min_duration_ms > self.expected_ms:
            raise ValueError("rundown min_duration_ms is invalid")


class RundownState:
    """The only mutation boundary for a live programme's current segment."""

    def __init__(
        self,
        segments: list[RundownSegment | dict[str, Any]] | None = None,
        *,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._segments: list[RundownSegment] = []
        self._index = -1
        self._status: RundownStatus = "idle"
        self._started_at_ms: int | None = None
        self._paused_at_ms: int | None = None
        self._elapsed_ms = 0
        self._history: list[dict[str, Any]] = []
        if segments:
            self.load(segments)

    @property
    def status(self) -> RundownStatus:
        return self._status

    def load(self, segments: list[RundownSegment | dict[str, Any]]) -> dict[str, Any]:
        if not segments:
            raise ValueError("rundown requires at least one segment")
        converted = [
            segment
            if isinstance(segment, RundownSegment)
            else RundownSegment.from_mapping(segment)
            for segment in segments
        ]
        ids = [segment.id for segment in converted]
        if len(ids) != len(set(ids)):
            raise ValueError("rundown segment ids must be unique")
        self._segments = converted
        self._index = -1
        self._status = "armed"
        self._started_at_ms = None
        self._paused_at_ms = None
        self._elapsed_ms = 0
        self._history = []
        return self.snapshot()

    def begin(self, by: str = "system") -> dict[str, Any]:
        if not self._segments:
            raise ValueError("rundown is not loaded")
        if self._status not in ("armed", "paused"):
            raise ValueError(f"cannot begin rundown from {self._status}")
        self._index = 0
        self._status = "running"
        self._started_at_ms = self._now_ms()
        self._paused_at_ms = None
        self._elapsed_ms = 0
        return self._changed("begin", by)

    def next(self, by: str = "system", *, force: bool = False) -> dict[str, Any]:
        if self._status != "running":
            raise ValueError(f"cannot advance rundown from {self._status}")
        current = self.current()
        elapsed = self._current_elapsed_ms()
        if current and not force and elapsed < current.min_duration_ms:
            raise ValueError(
                f"rundown segment {current.id} has not reached min_duration_ms"
            )
        if self._index >= len(self._segments) - 1:
            self._status = "done"
            self._started_at_ms = None
            self._paused_at_ms = None
            self._elapsed_ms = elapsed
            return self._changed("finish", by)
        self._index += 1
        self._started_at_ms = self._now_ms()
        self._paused_at_ms = None
        self._elapsed_ms = 0
        return self._changed("next", by)

    def pause(self, by: str = "human") -> dict[str, Any]:
        if self._status != "running":
            raise ValueError(f"cannot pause rundown from {self._status}")
        self._elapsed_ms = self._current_elapsed_ms()
        self._status = "paused"
        self._paused_at_ms = self._now_ms()
        self._started_at_ms = None
        return self._changed("pause", by)

    def resume(self, by: str = "human") -> dict[str, Any]:
        if self._status != "paused":
            raise ValueError(f"cannot resume rundown from {self._status}")
        self._status = "running"
        self._started_at_ms = self._now_ms()
        self._paused_at_ms = None
        return self._changed("resume", by)

    def finish(self, by: str = "operator") -> dict[str, Any]:
        if self._status in ("idle", "done"):
            return self.snapshot()
        self._elapsed_ms = self._current_elapsed_ms()
        self._status = "done"
        self._started_at_ms = None
        self._paused_at_ms = None
        return self._changed("finish", by)

    def current(self) -> RundownSegment | None:
        if self._index < 0:
            return None
        return self._segments[self._index]

    def context_text(self) -> str:
        current = self.current()
        if not current:
            return ""
        snapshot = self.snapshot()
        return (
            f"当前节目环节：{current.title}（{snapshot['index'] + 1}/{snapshot['total']}）\n"
            f"目标：{current.goal}\n"
            f"预期时长：{current.expected_ms}ms，剩余：{snapshot['remaining_ms']}ms"
        )

    def _current_elapsed_ms(self) -> int:
        if self._started_at_ms is None:
            return self._elapsed_ms
        end = self._paused_at_ms or self._now_ms()
        return self._elapsed_ms + max(0, end - self._started_at_ms)

    def snapshot(self) -> dict[str, Any]:
        current = self.current()
        elapsed = self._current_elapsed_ms() if current else 0
        return {
            "status": self._status,
            "index": self._index,
            "total": len(self._segments),
            "current": {
                "id": current.id,
                "title": current.title,
                "goal": current.goal,
                "expected_ms": current.expected_ms,
                "notes": current.notes,
            }
            if current
            else None,
            "elapsed_ms": elapsed,
            "remaining_ms": max(0, current.expected_ms - elapsed) if current else 0,
            "history": list(self._history[-30:]),
        }

    def _changed(self, action: str, by: str) -> dict[str, Any]:
        self._history.append(
            {"action": action, "by": by, "at_ms": self._now_ms(), "index": self._index}
        )
        if len(self._history) > 50:
            del self._history[:-50]
        return self.snapshot()
