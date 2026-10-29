"""Mapping from authorized Bilibili collector payloads to RoomEvent.

The collector itself is an injection point. Keeping network I/O out of this
module makes the mapping testable and lets a deployment provide its own pinned,
authorized collector without changing the live director.
"""

from __future__ import annotations

import time
from typing import Any

from .events import GiftInfo, RoomEvent, RoomUser


def map_bilibili_payload(payload: dict[str, Any]) -> RoomEvent | None:
    """Map Amaidesu-style normalized payloads or a narrow raw payload."""
    if not isinstance(payload, dict):
        return None
    message_type = (
        payload.get("message_type")
        or payload.get("messageType")
        or ("gift" if payload.get("gift") else "chat")
    )
    event_type = {
        "danmaku": "chat",
        "chat": "chat",
        "super_chat": "chat",
        "superchat": "chat",
        "gift": "gift",
    }.get(message_type)
    if not event_type:
        return None

    user_value = payload.get("user")
    if isinstance(user_value, dict):
        user = RoomUser(
            id=str(user_value.get("platform_user_id") or user_value.get("uid") or ""),
            name=str(user_value.get("name") or user_value.get("display_name") or ""),
        )
    else:
        user = RoomUser(
            id=str(payload.get("uid") or payload.get("open_id") or ""),
            name=str(payload.get("uname") or user_value or ""),
        )

    gift_value = payload.get("gift")
    gift = None
    if event_type == "gift":
        if isinstance(gift_value, dict):
            gift = GiftInfo(
                name=str(gift_value.get("name") or gift_value.get("gift_name") or ""),
                count=int(gift_value.get("count") or payload.get("gift_count") or 1),
            )
        else:
            gift = GiftInfo(
                name=str(payload.get("gift_name") or gift_value or ""),
                count=int(payload.get("gift_count") or 1),
            )

    received_at = (
        payload.get("timestamp_ms")
        or payload.get("received_at_ms")
        or int(time.time() * 1000)
    )
    user_id = user.id or user.name
    event_id = (
        payload.get("message_id")
        or payload.get("msg_id")
        or payload.get("id")
        or f"bilibili-{received_at}-{user_id}"
    )
    return RoomEvent(
        id=str(event_id),
        type=event_type,
        platform="bilibili",
        room_id=str(payload.get("room_id") or payload.get("roomId") or ""),
        received_at_ms=max(0, int(received_at)),
        user=user,
        content=str(
            payload.get("content") or payload.get("msg") or payload.get("text") or ""
        ),
        gift=gift,
    )


class BilibiliCollectorPort:
    """Minimal runtime contract a concrete authorized collector must satisfy."""

    async def start(self, on_event: Any) -> None:
        raise NotImplementedError

    async def stop(self) -> None:
        raise NotImplementedError
