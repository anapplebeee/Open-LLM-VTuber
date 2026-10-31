"""Normalize Douyin live events into the platform-neutral RoomEvent contract.

This module intentionally does not implement Douyin's private WebSocket
signature/protobuf protocol. Official or reviewed collectors can feed either
their normalized JSON or the common Webcast message shape into this adapter.
"""

from __future__ import annotations

import time
from typing import Any

from .events import GiftInfo, RoomEvent, RoomUser

__all__ = ["map_douyin_payload", "map_dyhub_event"]

_CHAT_METHODS = {"WebcastChatMessage", "chat", "danmaku", "comment", "message"}
_GIFT_METHODS = {"WebcastGiftMessage", "gift", "super_chat", "superchat"}
_JOIN_METHODS = {"WebcastRoomEnterMessage", "WebcastMemberMessage", "enter", "join"}
_IGNORED_METHODS = {
    "WebcastLikeMessage",
    "like",
    "WebcastRoomUserSeqMessage",
    "room_user_seq",
}


def _field(data: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = data.get(name)
        if value not in (None, ""):
            return value
    return None


def _timestamp_ms(data: dict[str, Any], payload: dict[str, Any]) -> int:
    raw = _field(data, "timestamp_ms", "received_at_ms", "timestamp", "create_time")
    if raw is None:
        raw = _field(payload, "timestamp_ms", "received_at_ms", "timestamp")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return int(time.time() * 1000)
    # Douyin commonly reports Unix seconds in create_time.
    return value * 1000 if 0 < value < 10_000_000_000 else value


def _user(data: dict[str, Any], payload: dict[str, Any]) -> RoomUser | None:
    raw = data.get("user") or data.get("user_info") or payload.get("user") or {}
    if isinstance(raw, str):
        raw = {"nickname": raw}
    if not isinstance(raw, dict):
        return None
    user_id = _field(raw, "id", "user_id", "uid", "open_id", "sec_uid")
    name = _field(raw, "nickname", "name", "user_name", "display_name")
    if not name and not user_id:
        return None
    return RoomUser(id=str(user_id or ""), name=str(name or "unknown"))


def map_douyin_payload(
    payload: dict[str, Any], *, now_ms: int | None = None
) -> RoomEvent | None:
    """Map a reviewed Douyin collector event to RoomEvent.

    Accepted shapes:
    - ``{"method": "WebcastChatMessage", "data": {...}}``
    - ``{"type": "chat", "user": {...}, "content": "..."}``
    - a normalized RoomEvent-shaped payload with explicit fields
    """
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        data = payload
    method = str(
        payload.get("method")
        or payload.get("message_type")
        or payload.get("messageType")
        or payload.get("type")
        or ""
    )
    if method in _IGNORED_METHODS:
        return None
    if (
        method not in _CHAT_METHODS
        and method not in _GIFT_METHODS
        and method not in _JOIN_METHODS
        and not (_field(data, "content", "text", "message") or data.get("gift"))
    ):
        # Be liberal for already-normalized collectors.
        return None

    user = _user(data, payload)
    if user is None:
        return None
    received_at = _timestamp_ms(data, payload)
    event_id = _field(data, "message_id", "msg_id", "id") or _field(
        payload, "message_id", "msg_id", "id"
    )
    event_id = event_id or f"douyin-{method or 'event'}-{received_at}-{user.id or user.name}"
    common = {
        "id": str(event_id),
        "platform": "douyin",
        "room_id": str(_field(data, "room_id", "roomId") or _field(payload, "room_id", "roomId") or ""),
        "received_at_ms": max(0, received_at),
        "user": user,
    }

    if method in _JOIN_METHODS:
        return RoomEvent(type="join", **common)

    is_gift = method in _GIFT_METHODS or bool(data.get("gift"))
    if is_gift:
        raw_gift = data.get("gift")
        if isinstance(raw_gift, dict):
            name = str(raw_gift.get("name") or raw_gift.get("gift_name") or "礼物")
            try:
                count = int(raw_gift.get("count") or raw_gift.get("gift_count") or 1)
            except (TypeError, ValueError):
                count = 1
        else:
            name = str(_field(data, "gift_name") or raw_gift or "礼物")
            try:
                count = int(_field(data, "gift_count", "count") or 1)
            except (TypeError, ValueError):
                count = 1
        return RoomEvent(
            type="gift",
            content=str(_field(data, "content", "text", "message") or "")[:500],
            gift=GiftInfo(name=name, count=max(1, min(10000, count))),
            **common,
        )

    content = str(_field(data, "content", "text", "message", "msg") or "").strip()
    if not content:
        return None
    return RoomEvent(type="chat", content=content[:500], **common)


def map_dyhub_event(payload: dict[str, Any]) -> RoomEvent | None:
    """Map a dyhub standard event (chat/gift) into a RoomEvent.

    dyhub uses ``{"type":"chat","roomId":...,"user":...,"data":{"content":...}}``.
    It is converted through the same normalizer as other collectors so the
    bridge cannot bypass validation.
    """
    if not isinstance(payload, dict):
        return None
    event_type = str(payload.get("type") or "")
    if event_type not in {"chat", "gift", "enter", "join"}:
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        data = {}
    user = payload.get("user")
    if isinstance(user, dict):
        user = dict(user)
    normalized: dict[str, Any] = {
        "type": event_type,
        "message_id": payload.get("id") or payload.get("message_id"),
        "room_id": payload.get("roomId") or payload.get("room_id"),
        "user": user or {},
    }
    if event_type in {"enter", "join"}:
        normalized["type"] = "enter"
        normalized["content"] = ""
    elif event_type == "chat":
        normalized["content"] = data.get("content") or data.get("text") or ""
    else:
        normalized["gift"] = {
            "name": data.get("giftName") or data.get("gift_name") or "礼物",
            "count": data.get("repeatCount")
            or data.get("comboCount")
            or data.get("count")
            or 1,
        }
        normalized["content"] = data.get("content") or ""
    return map_douyin_payload(normalized)


def map_douyin_event(payload: dict[str, Any]) -> RoomEvent | None:
    """Backward-friendly alias used by the live session."""
    return map_douyin_payload(payload)
