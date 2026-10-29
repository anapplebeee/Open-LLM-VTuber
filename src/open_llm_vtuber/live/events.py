"""Canonical live-room event contract.

The contract is deliberately independent from the OLV WebSocket protocol. Live
collectors produce RoomEvent objects; the director selects batches, and only the
live agent converts a selected batch into an OLV BatchInput.
"""

from __future__ import annotations

from dataclasses import dataclass
from json import dumps
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RoomUser(BaseModel):
    """A viewer identity after platform normalization."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default="", max_length=100)
    name: str = Field(min_length=1, max_length=60)


class GiftInfo(BaseModel):
    """A gift fact. Amount is intentionally absent unless the platform supplies it."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=60)
    count: int = Field(default=1, ge=1, le=10000)


class RoomEvent(BaseModel):
    """A validated event received from a live platform adapter."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    id: str = Field(min_length=1, max_length=100)
    type: Literal["chat", "gift", "operator"]
    platform: str = Field(min_length=1, max_length=40)
    room_id: str = Field(default="", max_length=100)
    received_at_ms: int = Field(ge=0)
    user: RoomUser
    content: str = Field(default="", max_length=500)
    gift: GiftInfo | None = None

    @field_validator("id", "platform", "content", mode="before")
    @classmethod
    def _strip_text(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("gift")
    @classmethod
    def _gift_is_required_for_gift_events(cls, value: GiftInfo | None, info):
        event_type = info.data.get("type")
        if event_type == "gift" and value is None:
            raise ValueError("gift event requires gift information")
        return value

    @classmethod
    def from_legacy(
        cls,
        value: dict[str, Any],
        *,
        platform: str = "simulated",
        now_ms: int = 0,
    ) -> RoomEvent:
        """Accept the small shape used by the existing OLV live script."""
        event_type = value.get("type") or ("gift" if value.get("gift") else "chat")
        user = value.get("user", "")
        if isinstance(user, dict):
            user_model = RoomUser(
                id=str(user.get("id") or user.get("platform_user_id") or ""),
                name=str(user.get("name") or user.get("display_name") or ""),
            )
        else:
            user_model = RoomUser(name=str(user))
        gift = value.get("gift")
        gift_model = None
        if event_type == "gift":
            if isinstance(gift, dict):
                gift_model = GiftInfo(
                    name=str(gift.get("name") or ""),
                    count=int(gift.get("count") or value.get("count") or 1),
                )
            else:
                gift_model = GiftInfo(
                    name=str(gift or ""),
                    count=int(value.get("count") or 1),
                )
        received_at = value.get("received_at_ms", value.get("receivedAt", now_ms))
        return cls(
            id=str(
                value.get("id") or f"{platform}-{received_at}-{value.get('user', '')}"
            ),
            type=event_type,
            platform=str(value.get("platform") or platform),
            room_id=str(value.get("room_id") or value.get("roomId") or ""),
            received_at_ms=max(0, int(received_at)),
            user=user_model,
            content=str(value.get("content") or value.get("text") or ""),
            gift=gift_model,
        )

    def to_agent_text(self) -> str:
        """Render the event as escaped JSON inside a fixed untrusted-data block."""
        payload = {
            "type": self.type,
            "user": self.user.name,
            **({"text": self.content} if self.content else {}),
            **({"gift": self.gift.model_dump()} if self.gift else {}),
        }
        return dumps(payload, ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class LiveBatch:
    """A selected set of events handed to the live agent."""

    id: str
    kind: Literal["chat", "gift", "operator", "proactive"]
    priority: int
    events: tuple[RoomEvent, ...]
    topic: str | None = None

    @property
    def created_at_ms(self) -> int:
        return self.events[0].received_at_ms if self.events else 0


__all__ = ["GiftInfo", "LiveBatch", "RoomEvent", "RoomUser"]
