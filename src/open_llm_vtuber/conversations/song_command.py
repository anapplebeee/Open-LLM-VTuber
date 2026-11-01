"""Point-song support for the normal /client-ws conversation path."""

from __future__ import annotations

import time
import uuid
from typing import Any

from ..live.events import RoomEvent, RoomUser
from ..live.song_player import NeteaseSongProvider, ResolvedSong
from ..live.songbook import SongRequestQueue, SongRequestResult

_QUEUES: dict[str, SongRequestQueue] = {}
_PROVIDER = NeteaseSongProvider()


def queue_snapshot(client_uid: str) -> dict[str, Any]:
    queue = _QUEUES.get(client_uid)
    return queue.snapshot() if queue is not None else {"items": [], "max_size": 8}


def finish_song(client_uid: str, title: str, entry_id: str = "") -> dict[str, Any]:
    queue = _QUEUES.get(client_uid)
    if queue is not None:
        queue.remove_finished(title, entry_id)
    return queue_snapshot(client_uid)


def handle_song_command(
    client_uid: str, text: str, human_name: str
) -> SongRequestResult | None:
    queue = _QUEUES.setdefault(client_uid, SongRequestQueue())
    now_ms = int(time.time() * 1000)
    event = RoomEvent(
        id=f"local-{uuid.uuid4().hex[:10]}",
        type="chat",
        platform="local",
        received_at_ms=now_ms,
        user=RoomUser(id=client_uid, name=human_name or "观众"),
        content=text,
    )
    return queue.handle(event, now_ms=now_ms)


async def resolve_song(result: SongRequestResult) -> ResolvedSong | None:
    if result.action != "queued" or result.entry is None:
        return None
    return await _PROVIDER.resolve(result.entry.title)


def song_payload(song: ResolvedSong, result: SongRequestResult) -> dict[str, Any]:
    entry = result.entry
    return {
        "type": "song",
        "action": "play",
        "entry_id": entry.id if entry is not None else "",
        "track_id": song.track_id,
        "title": song.title,
        "artist": song.artist,
        "url": song.url,
        "duration_ms": song.duration_ms,
        "requested_by": entry.user_name if entry else "",
    }
