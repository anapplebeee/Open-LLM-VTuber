"""Mapping from authorized Bilibili collector payloads to RoomEvent.

The collector itself is an injection point. Keeping network I/O out of this
module makes the mapping testable and lets a deployment provide its own pinned,
authorized collector without changing the live director.
"""

from __future__ import annotations

import time
from typing import Any

from .events import GiftInfo, RoomEvent, RoomUser

BILIBILI_OFFICIAL_COMMANDS = {
    "LIVE_OPEN_PLATFORM_DM",
    "LIVE_OPEN_PLATFORM_SEND_GIFT",
    "LIVE_OPEN_PLATFORM_SUPER_CHAT",
    "LIVE_OPEN_PLATFORM_GUARD",
    "LIVE_OPEN_PLATFORM_LIVE_ROOM_ENTER",
    "LIVE_OPEN_PLATFORM_WATCHED_CHANGE",
}


def map_bilibili_official_message(
    payload: dict[str, Any], *, now_ms: int | None = None
) -> RoomEvent | None:
    """Map a Bilibili Open Platform WebSocket message to a RoomEvent.

    Open Platform uses ``open_id`` (not account uid), so it is preserved as the
    viewer identity. Enter and watched-count events are state notifications, not
    speech stimuli, and are deliberately skipped.
    """
    if not isinstance(payload, dict):
        return None
    cmd = str(payload.get("cmd") or "")
    data = payload.get("data")
    if not isinstance(data, dict) or cmd not in BILIBILI_OFFICIAL_COMMANDS:
        return None

    user_id = str(data.get("open_id") or "")
    user_name = str(
        data.get("uname") or (data.get("user_info") or {}).get("uname") or ""
    )
    if not user_id:
        return None

    timestamp = int(data.get("timestamp") or 0)
    received_at = (
        timestamp * 1000 if timestamp else (now_ms or int(time.time() * 1000))
    )
    event_id = str(
        data.get("msg_id")
        or data.get("message_id")
        or f"bilibili-{cmd}-{received_at}-{user_id}"
    )
    base = {
        "id": event_id,
        "platform": "bilibili",
        "room_id": str(data.get("room_id") or ""),
        "received_at_ms": max(0, received_at),
        "user": RoomUser(id=user_id, name=user_name or "unknown"),
    }

    if cmd == "LIVE_OPEN_PLATFORM_DM":
        content = str(data.get("msg") or "")
        if not content:
            return None
        return RoomEvent(type="chat", content=content[:500], **base)

    if cmd == "LIVE_OPEN_PLATFORM_SEND_GIFT":
        combo = data.get("combo_info") or {}
        count = max(
            int(data.get("gift_num") or 1), int(combo.get("combo_count") or 0)
        )
        return RoomEvent(
            type="gift",
            gift=GiftInfo(
                name=str(data.get("gift_name") or "礼物"), count=max(1, count)
            ),
            **base,
        )

    if cmd == "LIVE_OPEN_PLATFORM_SUPER_CHAT":
        return RoomEvent(
            type="gift",
            content=str(data.get("message") or "")[:500],
            gift=GiftInfo(name="SC", count=1),
            **base,
        )

    if cmd == "LIVE_OPEN_PLATFORM_GUARD":
        guard_level = int(data.get("guard_level") or 0)
        guard_name = str(data.get("guard_name") or "大航海")
        return RoomEvent(
            type="gift",
            content=f"开通了{guard_name}"[:500],
            gift=GiftInfo(name=f"大航海L{guard_level}", count=1),
            **base,
        )

    return None


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
