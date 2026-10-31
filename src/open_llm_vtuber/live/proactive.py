"""Timed proactive speech budget for a live rundown.

This is a scheduler, not an agent loop: it only decides when the existing
LiveSession may hand one synthetic proactive batch to the existing agent.
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class ProactiveScheduler:
    enabled: bool = False
    interval_ms: int = 30_000
    max_consecutive: int = 2
    _next_due_ms: int = 0
    _consecutive: int = 0

    def __post_init__(self) -> None:
        if self.interval_ms < 1_000:
            raise ValueError("proactive interval_ms must be >= 1000")
        if self.max_consecutive < 1:
            raise ValueError("proactive max_consecutive must be >= 1")

    def configure(
        self, *, enabled: bool | None = None, interval_ms: int | None = None
    ) -> None:
        if interval_ms is not None:
            if interval_ms < 1_000:
                raise ValueError("proactive interval_ms must be >= 1000")
            self.interval_ms = interval_ms
        if enabled is not None:
            self.enabled = enabled
            self._next_due_ms = 0
            if not enabled:
                self._consecutive = 0

    def note_user_activity(self, now_ms: int | None = None) -> None:
        """A real viewer/operator event resets the idle budget."""
        now = _now(now_ms)
        self._next_due_ms = now + self.interval_ms
        self._consecutive = 0

    def due(self, now_ms: int | None = None) -> bool:
        if not self.enabled:
            return False
        if self._consecutive >= self.max_consecutive:
            return False
        now = _now(now_ms)
        if self._next_due_ms == 0:
            self._next_due_ms = now + self.interval_ms
            return False
        return now >= self._next_due_ms

    def mark_spoken(self, now_ms: int | None = None) -> None:
        now = _now(now_ms)
        self._consecutive += 1
        self._next_due_ms = now + self.interval_ms

    def snapshot(self) -> dict[str, int | bool]:
        return {
            "enabled": self.enabled,
            "interval_ms": self.interval_ms,
            "consecutive": self._consecutive,
            "max_consecutive": self.max_consecutive,
        }


def _now(value: int | None) -> int:
    return int(time.time() * 1000) if value is None else value


__all__ = ["ProactiveScheduler"]
