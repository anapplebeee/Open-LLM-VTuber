"""Tiny process-local registry for the current live session.

The dashboard needs read-only access to the active live session without
creating a second runtime or opening another browser. The registry holds the
same object used by ``/live-ws``; it never owns audio or starts a second agent.
"""

from __future__ import annotations

from typing import Any

_current: Any = None


def set_current_live_session(session: Any) -> None:
    global _current
    _current = session


def get_current_live_session() -> Any:
    return _current
