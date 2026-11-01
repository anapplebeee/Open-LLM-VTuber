"""Bounded song-request queue for live chat.

This module only owns the request/queue state. Playback stays an operator concern
until an authorized local song library is added; the live agent receives a short
prompt note so it can acknowledge the request naturally.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Literal

from .events import RoomEvent

__all__ = [
    "SongQueueSnapshot",
    "SongRequest",
    "SongRequestQueue",
    "SongRequestResult",
]

MAX_TITLE_CHARS = 60
MAX_QUEUE = 8
MAX_PER_USER = 3
COOLDOWN_MS = 45_000
USAGE = "点歌 歌名；点歌列表；取消点歌"

_BRACKETS = re.compile(r"[\[\]【】《》〈〉\"'“”‘’`]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_URL = re.compile(r"(?:https?://|www\.|/|\\)", re.IGNORECASE)
_TITLE = re.compile(
    r"^(?:点歌|點歌|song|播放|放一首|放一首歌|想听|想聽|来一首|來一首)\s*[:：]?\s*(.+)$",
    re.IGNORECASE,
)
_LIST = re.compile(r"^(?:点歌列表|點歌列表|歌单|歌單|队列|隊列|songlist)$")
_BARE = re.compile(r"^(?:点歌|點歌|song|播放|想听|想聽|来一首|來一首)$")
_ARTIST_TITLE = re.compile(r"^(.{2,20}?)(?:演唱|唱的|唱)的*(.{1,48})$")
_CANCEL = re.compile(r"^(?:取消点歌|取消點歌|撤掉点歌|删掉点歌|刪掉點歌)\s*(.*)$")
_DUPLICATE_PUNCT = re.compile(r"[\s，,。.!！？?、~～·]+")
_PLAYBACK_CONTROL = re.compile(
    r"^(?:切歌|跳过|下一首|上一首|暂停音乐|继续音乐|关闭音乐|停止音乐|关掉音乐)$"
)
_SONG_CORRECTION = re.compile(r"^(?:不是这首(?:呀|啊)?|不对(?:呀|啊)?|换一首)$")


def detect_song_control(text: str) -> str | None:
    """Map a short playback command to a frontend control action."""
    command = _CONTROL.sub("", (text or "")).strip()
    if not _PLAYBACK_CONTROL.match(command):
        return None
    mapping = {
        "切歌": "next",
        "跳过": "next",
        "下一首": "next",
        "上一首": "prev",
        "暂停音乐": "pause",
        "继续音乐": "resume",
        "关闭音乐": "stop",
        "停止音乐": "stop",
        "关掉音乐": "stop",
    }
    return mapping.get(command)


def detect_song_correction(text: str) -> str | None:
    """Map a correction phrase, caller must gate it by active song context."""
    command = _CONTROL.sub("", (text or "")).strip()
    if _SONG_CORRECTION.match(command):
        return "next"
    return None


def normalize_title(value: str) -> str:
    title = _CONTROL.sub("", value or "").strip()
    title = _BRACKETS.sub("", title)
    title = re.sub(r"\s+", " ", title).strip()
    # "播放一首幼稚完" currently leaves "一首幼稚完"; strip the quantifier.
    title = re.sub(r"^(?:一首歌|一首|一首曲子|首|歌曲|歌|曲)\s*", "", title).strip()
    return title[:MAX_TITLE_CHARS]


def parse_request_title(value: str) -> tuple[str, str]:
    """Return (display title, NetEase search query)."""
    title = normalize_title(value)
    match = _ARTIST_TITLE.match(title)
    if not match:
        return title, title
    artist = normalize_title(match.group(1))
    song = normalize_title(match.group(2))
    if not artist or not song:
        return title, title
    return f"{song} - {artist}", f"{song} {artist}"


@dataclass(frozen=True)
class SongRequest:
    id: str
    user_id: str
    user_name: str
    title: str
    requested_at_ms: int
    query: str = ""


@dataclass(frozen=True)
class SongRequestResult:
    action: Literal["queued", "listed", "cancelled", "rejected"]
    reply_hint: str
    entry: SongRequest | None = None
    position: int | None = None
    reason: str = ""


@dataclass
class SongQueueSnapshot:
    items: list[dict[str, object]] = field(default_factory=list)
    max_size: int = MAX_QUEUE

    def to_dict(self) -> dict[str, object]:
        return {"items": list(self.items), "max_size": self.max_size}


@dataclass
class SongPlaybackState:
    """Backend-visible playback status for operations and tests.

    The browser remains the audio renderer; this state records what the
    backend has resolved/dispatched and gives failures a stable reason.
    """

    state: str = "idle"
    current_entry_id: str = ""
    current_title: str = ""
    current_index: int = -1
    last_error: str = ""
    updated_at_ms: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "current_entry_id": self.current_entry_id,
            "current_title": self.current_title,
            "current_index": self.current_index,
            "last_error": self.last_error,
            "updated_at_ms": self.updated_at_ms,
        }


class SongRequestQueue:
    """In-memory queue; one instance belongs to one live session."""

    def __init__(
        self,
        *,
        max_queue: int = MAX_QUEUE,
        max_per_user: int = MAX_PER_USER,
        cooldown_ms: int = COOLDOWN_MS,
    ) -> None:
        self.max_queue = max(1, int(max_queue))
        self.max_per_user = max(1, int(max_per_user))
        self.cooldown_ms = max(0, int(cooldown_ms))
        self._items: list[SongRequest] = []
        self._last_request_ms: dict[str, int] = {}

    def handle(self, event: RoomEvent, *, now_ms: int | None = None) -> SongRequestResult | None:
        if event.type != "chat":
            return None
        text = _CONTROL.sub("", event.content or "").strip()
        if not text:
            return None
        now = int(now_ms if now_ms is not None else event.received_at_ms)
        if _LIST.match(text):
            return self._listed()
        if _BARE.match(text):
            return SongRequestResult("rejected", f"要听什么？格式：{USAGE}")
        cancel_match = _CANCEL.match(text)
        if cancel_match:
            return self._cancelled(event, cancel_match.group(1), now)
        title_match = _TITLE.match(text)
        if title_match:
            return self._enqueue(event, title_match.group(1), now)
        # Follow-up corrections often omit "播放": "林峰演唱的幼稚完".
        if _ARTIST_TITLE.match(text):
            return self._enqueue(event, text, now)
        return None

    def remove_finished(self, title: str, entry_id: str = "") -> None:
        """Remove a track after its playback has finished.

        Prefer the stable queue entry id: a resolved song title can differ from
        the original search query, which would otherwise leave the old queue row
        behind after playback.
        """
        if entry_id:
            for index, item in enumerate(self._items):
                if item.id == entry_id:
                    self._items.pop(index)
                    return
        normalized = _DUPLICATE_PUNCT.sub("", normalize_title(title))
        for index, item in enumerate(self._items):
            if _DUPLICATE_PUNCT.sub("", item.title) == normalized:
                self._items.pop(index)
                return

    def snapshot(self) -> dict[str, object]:
        return SongQueueSnapshot(
            items=[
                {
                    "id": item.id,
                    "title": item.title,
                    "query": item.query,
                    "user": item.user_name,
                    "position": index + 1,
                }
                for index, item in enumerate(self._items)
            ],
            max_size=self.max_queue,
        ).to_dict()

    def _enqueue(self, event: RoomEvent, raw_title: str, now: int) -> SongRequestResult:
        title, query = parse_request_title(raw_title)
        if not title:
            return SongRequestResult("rejected", f"没听清歌名，格式：{USAGE}")
        if _URL.search(title):
            return SongRequestResult("rejected", "只收歌名，不收链接或文件名。")
        key = self._user_key(event)
        last = self._last_request_ms.get(key, 0)
        if now - last < self.cooldown_ms:
            wait = max(1, (self.cooldown_ms - (now - last)) // 1000)
            return SongRequestResult(
                "rejected", f"点歌太频繁了，{wait} 秒后再试。"
            )
        if sum(1 for item in self._items if item.user_id == event.user.id) >= self.max_per_user:
            return SongRequestResult(
                "rejected", f"你最多同时点 {self.max_per_user} 首，等前面的唱完再点吧。"
            )
        normalized = _DUPLICATE_PUNCT.sub("", title)
        if any(
            _DUPLICATE_PUNCT.sub("", item.title) == normalized for item in self._items
        ):
            return SongRequestResult("rejected", "这首已经在歌单里了，换一首吧。")
        if len(self._items) >= self.max_queue:
            return SongRequestResult(
                "rejected", f"歌单已经满了（{self.max_queue} 首），稍后再来。"
            )
        entry = SongRequest(
            id=uuid.uuid4().hex[:12],
            user_id=event.user.id,
            user_name=event.user.name,
            title=title,
            requested_at_ms=now,
            query=query,
        )
        self._items.append(entry)
        self._last_request_ms[key] = now
        position = len(self._items)
        return SongRequestResult(
            "queued",
            f"点歌成功：《{title}》，当前第 {position} 位，前面还有 {position - 1} 首。",
            entry=entry,
            position=position,
        )

    def _listed(self) -> SongRequestResult:
        if not self._items:
            return SongRequestResult("listed", "歌单现在还是空的。")
        lines = [f"{i + 1}.《{item.title}》- {item.user_name}" for i, item in enumerate(self._items)]
        return SongRequestResult("listed", "当前歌单：" + "；".join(lines))

    def _cancelled(self, event: RoomEvent, raw_title: str, now: int) -> SongRequestResult:
        title = normalize_title(raw_title)
        if not title:
            return SongRequestResult("rejected", "要取消哪一首？发“取消点歌 歌名”。")
        for index, item in enumerate(self._items):
            if _DUPLICATE_PUNCT.sub("", item.title) == _DUPLICATE_PUNCT.sub("", title):
                if item.user_id != event.user.id:
                    return SongRequestResult("rejected", "只能取消自己点的歌。")
                self._items.pop(index)
                self._last_request_ms.pop(self._user_key(event), None)
                return SongRequestResult(
                    "cancelled", f"已取消《{title}》，想唱别的可以再点。"
                )
        return SongRequestResult("rejected", f"歌单里没找到《{title}》。")

    @staticmethod
    def _user_key(event: RoomEvent) -> str:
        return event.user.id or event.user.name

    @staticmethod
    def prompt_note(result: SongRequestResult) -> str:
        return (
            "【点歌系统】" + result.reply_hint +
            " 请用一句自然短话回应观众，不要念出方括号或系统标签。"
        )
