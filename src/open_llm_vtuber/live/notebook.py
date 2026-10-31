"""In-session live notebook.

Adapted from Amaidesu's StreamerNotebook (MIT). It is a bounded, in-memory
working note for one live session, not a second memory database and not a
replacement for the persisted session recap.
"""

from __future__ import annotations

__all__ = ["DEFAULT_NOTEBOOK_MAX_CHARS", "LiveNotebook"]

DEFAULT_NOTEBOOK_MAX_CHARS = 2000


class LiveNotebook:
    def __init__(self, *, max_chars: int = DEFAULT_NOTEBOOK_MAX_CHARS) -> None:
        self.max_chars = max(100, int(max_chars))
        self._content = ""

    def read(self) -> str:
        return self._content

    def set(self, content: str) -> str:
        self._content = str(content or "")[: self.max_chars]
        return self._content

    def clear(self) -> None:
        self._content = ""
