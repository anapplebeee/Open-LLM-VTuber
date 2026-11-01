"""In-process bridge between the live director and an OLV agent.

The bridge deliberately accepts an async output callback instead of importing a
WebSocket handler. That keeps the live core testable and lets the OLV
conversation layer remain the single owner of TTS, playback and Live2D.
"""

from __future__ import annotations

import asyncio
import inspect
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from .agent_input import make_live_batch_input
from .bilibili import map_bilibili_payload
from .character_profile import XIAOJING_PROFILE, CharacterProfile
from .context import ContextPacket, StreamContextBuilder
from .director import LiveDirector
from .douyin import map_douyin_payload
from .events import LiveBatch, RoomEvent
from .memory import MemoryManager
from .notebook import LiveNotebook
from .memes import MemeLibrary
from .proactive import ProactiveScheduler
from .review import LiveReviewLogger
from .rundown import RundownState
from .song_player import NeteaseSongProvider
from .songbook import SongRequestQueue, detect_song_control
from .state import EmotionEngine, EmotionState, StreamState, ViewerRegistry
from .store import LiveStore

OutputHandler = Callable[[Any], Awaitable[None]]


_INTERNAL_REPLY_MARKERS = (
    "【点歌系统】",
    "【进入直播间】",
    "请先用“欢迎",
    "请用一句自然短话回应观众",
    "不要念出方括号或系统标签",
    "不要只点评名字而漏掉欢迎语",
    "若用户名不适合点评",
)


def clean_reply_context(text: str) -> str:
    """Strip internal prompt suffixes before showing a reply quote on stream."""
    value = str(text or "")
    for marker in _INTERNAL_REPLY_MARKERS:
        index = value.find(marker)
        if index > 0:
            value = value[:index]
    lines = []
    for line in value.splitlines():
        if any(marker in line for marker in _INTERNAL_REPLY_MARKERS):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


class LiveSession:
    """One live session with a director, a rundown and one agent."""

    def __init__(
        self,
        *,
        agent: Any,
        on_output: OutputHandler,
        director: LiveDirector | None = None,
        rundown: RundownState | None = None,
        session_id: str | None = None,
        on_batch_end: Callable[[], Awaitable[None]] | None = None,
        on_song: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        on_reply: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        review_logger: LiveReviewLogger | None = None,
        meme_library: MemeLibrary | None = None,
        song_provider: NeteaseSongProvider | None = None,
        store: LiveStore | None = None,
        recap_writer: Any = None,
        proactive: ProactiveScheduler | None = None,
        notebook: LiveNotebook | None = None,
        profile: CharacterProfile | None = None,
        emotion_engine: EmotionEngine | None = None,
        memory: MemoryManager | None = None,
        context_builder: StreamContextBuilder | None = None,
        context_max_chars: int = 4000,
        on_close: Callable[[], Awaitable[None] | None] | None = None,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self.session_id = session_id or uuid.uuid4().hex
        self.agent = agent
        self.on_output = on_output
        self.on_batch_end = on_batch_end
        self.on_song = on_song
        self.on_reply = on_reply
        self.review_logger = review_logger or LiveReviewLogger()
        self.meme_library = meme_library or MemeLibrary()
        self.song_provider = song_provider or NeteaseSongProvider()
        self._pending_song_result: Any = None
        self._pending_song_control: str | None = None
        self._song_tasks: set[asyncio.Task[None]] = set()
        self.rundown = rundown
        if self.rundown is not None and hasattr(self.agent, "_rundown_state"):
            self.agent._rundown_state = self.rundown
        self.store = store
        self.recap_writer = recap_writer
        self.proactive = proactive or ProactiveScheduler()
        self.notebook = notebook or LiveNotebook()
        self.song_requests = SongRequestQueue()
        self.profile = profile or XIAOJING_PROFILE
        self.emotion_engine = emotion_engine or EmotionEngine()
        self.emotion_state = EmotionState()
        self.viewers = ViewerRegistry()
        self.memory = memory or MemoryManager()
        self.stream_state = StreamState()
        self.context_builder = context_builder or StreamContextBuilder()
        self.context_max_chars = max(500, int(context_max_chars))
        self._last_context: ContextPacket | None = None
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._batch_lock = asyncio.Lock()
        self._bilibili_collector: Any = None
        self._proactive_task: asyncio.Task[None] | None = None
        self._store_started = False
        self.on_close = on_close
        self.director = director or LiveDirector(
            topic_provider=lambda: (
                self.rundown.current().goal
                if self.rundown and self.rundown.current()
                else None
            )
        )
        self._running = False

    async def start(self) -> None:
        if self._running:
            raise RuntimeError("live session is already running")
        if self.rundown and self.rundown.status != "running":
            self.rundown.begin("system")
        if self.store:
            await asyncio.to_thread(
                self.store.start_session, self.session_id, int(time.time() * 1000)
            )
            self._store_started = True
        await self.director.start(self._handle_batch)
        self._running = True
        if (
            self.rundown is not None
            and self.rundown.status == "running"
            and hasattr(self.agent, "_rundown_state")
        ):
            self.agent._rundown_state = self.rundown
        self.stream_state.update_from_rundown(self.rundown)
        self.viewers.restore(self.memory.load_viewers())
        self._proactive_task = asyncio.create_task(
            self._proactive_loop(), name="live-proactive"
        )

    async def stop(self) -> None:
        if not self._running and not self._store_started and not self._proactive_task:
            return
        self._running = False
        if self._proactive_task:
            self._proactive_task.cancel()
            try:
                await self._proactive_task
            except asyncio.CancelledError:
                pass
            self._proactive_task = None
        if self._bilibili_collector:
            await self._bilibili_collector.stop()
            self._bilibili_collector = None
        for task in list(self._song_tasks):
            task.cancel()
        if self._song_tasks:
            await asyncio.gather(*self._song_tasks, return_exceptions=True)
            self._song_tasks.clear()
        await self.director.stop()
        if self.on_close:
            try:
                result = self.on_close()
                if inspect.isawaitable(result):
                    await result
            except Exception:  # noqa: BLE001 - output cleanup must not block shutdown
                logger.exception("Live output close failed for {}", self.session_id)
        if self.rundown:
            self.rundown.finish("stop")
        if self.store and self._store_started:
            self._store_started = False
            await asyncio.to_thread(
                self.store.end_session, self.session_id, int(time.time() * 1000)
            )
            if self.recap_writer:
                try:
                    summary = await self.recap_writer.write(self.session_id)
                    if summary:
                        self.memory.add_recap(
                            str(summary),
                            session_id=self.session_id,
                            now_ms=self._now_ms(),
                        )
                except Exception:  # noqa: BLE001 - recap must not break shutdown
                    logger.exception("Session recap failed for {}", self.session_id)

    def load_rundown(
        self, segments: list[dict[str, Any]], *, auto_begin: bool = False
    ) -> dict[str, Any]:
        """Load a programme rundown and expose it to the live planner."""
        if self.rundown is not None and self.rundown.status == "running":
            raise RuntimeError("cannot replace a running rundown")
        self.rundown = RundownState(segments)
        if hasattr(self.agent, "_rundown_state"):
            self.agent._rundown_state = self.rundown
        if auto_begin:
            self.rundown.begin("operator")
        self.stream_state.update_from_rundown(self.rundown)
        return self.rundown.snapshot()

    def set_proactive(
        self, *, enabled: bool | None = None, interval_ms: int | None = None
    ) -> dict[str, Any]:
        self.proactive.configure(enabled=enabled, interval_ms=interval_ms)
        return self.proactive.snapshot()

    def set_notebook(self, content: str) -> str:
        return self.notebook.set(content)

    def read_notebook(self) -> str:
        return self.notebook.read()

    async def proactive_tick(self, now_ms: int | None = None) -> LiveBatch | None:
        """Ask the current rundown for one proactive line, at most one at a time."""
        if not self._running or self.rundown is None:
            return None
        if self.rundown.status != "running" or not self.proactive.due(now_ms):
            return None
        if self.director.status()["queued"] > 0 or self._batch_lock.locked():
            return None
        current = self.rundown.current()
        if current is None:
            return None
        batch = LiveBatch(
            id=f"proactive-{uuid.uuid4().hex}",
            kind="proactive",
            priority=10,
            events=(),
            topic=current.goal,
        )
        self.proactive.mark_spoken(now_ms)
        await self._handle_batch(batch)
        return batch

    async def _proactive_loop(self) -> None:
        while self._running:
            await asyncio.sleep(1.0)
            if not self._running:
                return
            try:
                await self.proactive_tick(self._now_ms())
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a bad tick must not kill the session
                logger.exception("Proactive tick failed for {}", self.session_id)

    def control_rundown(self, action: str, *, by: str = "operator") -> dict[str, Any]:
        """Advance the programme clock without creating a second decision loop."""
        if self.rundown is None:
            raise RuntimeError("no rundown is loaded")
        if action == "begin":
            result = self.rundown.begin(by)
        elif action == "next":
            result = self.rundown.next(by)
        elif action == "pause":
            result = self.rundown.pause(by)
        elif action == "resume":
            result = self.rundown.resume(by)
        elif action == "finish":
            result = self.rundown.finish(by)
        else:
            raise ValueError(f"unsupported rundown action: {action}")
        if hasattr(self.agent, "_rundown_state"):
            self.agent._rundown_state = self.rundown
        return result

    def ingest(self, event: RoomEvent) -> bool:
        if not self._running:
            return False
        accepted = self.director.ingest(self._with_song_request(event))
        if accepted:
            self._dispatch_pending_song()
        else:
            self._pending_song_result = None
        return accepted

    def _with_song_request(self, event: RoomEvent) -> RoomEvent:
        """Handle point-song commands before the LLM planner sees the event."""
        self._pending_song_result = None
        self._pending_song_control = None
        if event.type == "chat":
            control = detect_song_control(event.content or "")
            if control is not None:
                self._pending_song_control = control
                return event
        if event.type == "join":
            name = event.user.name
            note = (
                f"【进入直播间】观众名：{name}。"
                f"请先用“欢迎{name}进入直播间”这句话开头，"
                "再用一句自然短话轻轻点评一下用户名。"
                "不要只点评名字而漏掉欢迎语；若用户名不适合点评，"
                "就只保留欢迎语。不要编造认识对方，不要念出方括号或系统标签。"
            )
            content = f"{event.content}\n{note}".strip()
            return event.model_copy(update={"content": content[:500]})
        result = self.song_requests.handle(event, now_ms=self._now_ms())
        if result is None:
            return event
        self._pending_song_result = result
        note = SongRequestQueue.prompt_note(result)
        content = f"{event.content}\n{note}".strip()
        return event.model_copy(update={"content": content[:500]})

    def _dispatch_pending_song(self) -> None:
        control = self._pending_song_control
        self._pending_song_control = None
        result = self._pending_song_result
        self._pending_song_result = None
        if self.on_song is None:
            return
        if control is not None:
            control_task = asyncio.create_task(
                self.on_song({"type": "song-control", "action": control}),
                name="live-song-control",
            )
            self._song_tasks.add(control_task)
            control_task.add_done_callback(self._song_tasks.discard)
        if result is None:
            return
        queue_task = asyncio.create_task(
            self.on_song({"type": "song-queue", **self.song_requests.snapshot()}),
            name="live-song-queue",
        )
        self._song_tasks.add(queue_task)
        queue_task.add_done_callback(self._song_tasks.discard)
        if result.action != "queued" or result.entry is None:
            return
        task = asyncio.create_task(
            self._play_song(result.entry.title, result.entry.id),
            name=f"live-song-{result.entry.id}",
        )
        self._song_tasks.add(task)
        task.add_done_callback(self._song_tasks.discard)

    async def finish_song(self, title: str, entry_id: str = "") -> None:
        """Remove a completed track from the live queue and re-broadcast it."""
        self.song_requests.remove_finished(title, entry_id)
        if self.on_song is not None:
            await self.on_song(
                {"type": "song-queue", **self.song_requests.snapshot()}
            )

    async def play_song_target(
        self, title: str, entry_id: str = ""
    ) -> None:
        """Resolve one queued track and ask the frontend to jump to it."""
        await self._play_song(title, entry_id, play_now=True)

    async def _play_song(
        self, title: str, entry_id: str = "", *, play_now: bool = False
    ) -> None:
        try:
            song = await self.song_provider.resolve(title)
            if song is None:
                logger.warning("No playable NetEase result for: {}", title)
                return
            payload = {
                "type": "song",
                "action": "play",
                "entry_id": entry_id,
                "track_id": song.track_id,
                "title": song.title,
                "artist": song.artist,
                "url": song.url,
                "duration_ms": song.duration_ms,
                "play_now": play_now,
            }
            await self.on_song(payload)
        except Exception:
            logger.exception("Song playback dispatch failed for: {}", title)

    def ingest_bilibili(self, payload: dict[str, Any]) -> bool:
        if not self._running:
            return False
        event = map_bilibili_payload(payload)
        if event is None:
            return False
        accepted = self.director.ingest(self._with_song_request(event))
        if accepted:
            self._dispatch_pending_song()
        else:
            self._pending_song_result = None
        return accepted

    def ingest_douyin(self, payload: dict[str, Any]) -> bool:
        if not self._running:
            return False
        event = map_douyin_payload(payload)
        if event is None:
            return False
        accepted = self.director.ingest(self._with_song_request(event))
        if accepted:
            self._dispatch_pending_song()
        else:
            self._pending_song_result = None
        return accepted

    async def start_bilibili(self, config: Any) -> None:
        """Start the official collector and feed its events into this session."""
        if not self._running:
            raise RuntimeError("start the live session before its collector")
        if self._bilibili_collector:
            raise RuntimeError("Bilibili collector is already running")
        # Optional dependency boundary: the collector socket is only imported
        # when a deployment actually enables Bilibili.
        from .collectors.bilibili_official import BilibiliOfficialCollector

        collector = BilibiliOfficialCollector(config)

        async def forward(event: RoomEvent) -> None:
            # Collector callbacks are awaited; ``ingest`` is a synchronous
            # queue operation and must be wrapped instead of passed directly.
            self.ingest(event)

        await collector.start(forward)
        self._bilibili_collector = collector

    def status(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "running": self._running,
            "bilibili_collector": bool(self._bilibili_collector),
            "director": self.director.status(),
            "rundown": self.rundown.snapshot() if self.rundown else None,
            "proactive": self.proactive.snapshot(),
            "notebook_chars": len(self.notebook.read()),
            "song_requests": self.song_requests.snapshot(),
            "song_playback": {
                "enabled": self.song_provider.enabled and self.on_song is not None,
                "active": len(self._song_tasks),
            },
            "emotion": {
                "label": self.emotion_state.label,
                "intensity": self.emotion_state.intensity,
                "valence": self.emotion_state.valence,
            },
            "viewers": len(self.viewers.all()),
            "memories": len(self.memory.store.all()),
            "context_trace": self._last_context.trace() if self._last_context else None,
        }

    async def _handle_batch(self, batch: Any) -> None:
        async with self._batch_lock:
            now = self._now_ms()
            if batch.kind != "proactive":
                self.proactive.note_user_activity(now)
                appraisal = self.emotion_engine.appraise(batch)
                self.emotion_state = self.emotion_state.decay(now).apply(
                    appraisal, now
                )
                viewer = self.viewers.observe(batch, appraisal)
                if viewer is not None:
                    self.memory.save_viewer(viewer)
                self.memory.ingest_batch(
                    batch, session_id=self.session_id, now_ms=now
                )
            else:
                viewer = None
                self.emotion_state = self.emotion_state.decay(now)

            if self.store:
                await asyncio.to_thread(
                    self.store.append_events, self.session_id, batch.events
                )
                recap = await asyncio.to_thread(self.store.latest_recap)
                recent_recap = str(recap["summary"]) if recap else ""
            else:
                recent_recap = ""

            self.stream_state.update_from_rundown(self.rundown)
            events_text = "\n".join(
                event.to_agent_text() for event in batch.events
            ) or "本轮没有新的观众事件，按节目单主动推进。"
            meme_hint = self.meme_library.hint_for_text(events_text)
            if self.store and batch.events:
                # Keep the user side of the dialogue in live_chat, otherwise a
                # recap only sees assistant lines and cannot reconstruct it.
                await asyncio.to_thread(
                    self.store.append_chat,
                    self.session_id,
                    "user",
                    events_text,
                    now,
                )
            query = events_text
            if batch.kind == "proactive" and self.stream_state.goal:
                query = self.stream_state.goal
            memories = self.memory.for_room(
                query, now_ms=now, limit=4
            )
            if viewer is not None:
                viewer_memories = self.memory.for_viewer(
                    viewer.user_id, now_ms=now, limit=2
                )
                seen = {hit.item.id for hit in memories}
                memories.extend(
                    hit for hit in viewer_memories if hit.item.id not in seen
                )
            self._last_context = self.context_builder.build(
                profile=self.profile,
                emotion=self.emotion_state,
                stream=self.stream_state,
                viewer=viewer,
                events_text=events_text,
                rundown_text=(
                    self.rundown.context_text() if self.rundown else ""
                ),
                notebook=self.notebook.read(),
                recent_recap=recent_recap,
                meme_hint=meme_hint,
                memories=memories,
                style_scene=_style_scene(batch),
                max_chars=self.context_max_chars,
            )
            if hasattr(self.agent, "set_live_context"):
                self.agent.set_live_context(
                    recent_recap=recent_recap,
                    notebook=self.notebook.read(),
                    context_packet=self._last_context,
                )
            if self.on_reply is not None:
                reply_event = next(
                    (
                        item
                        for item in reversed(batch.events)
                        if item.type in {"chat", "gift", "operator", "join"}
                    ),
                    None,
                )
                if reply_event is not None:
                    if reply_event.type == "join":
                        # Join events carry an internal prompt in content. Never
                        # render that prompt on stream; show only the welcome.
                        display_text = (
                            f"欢迎{reply_event.user.name or '观众'}进入直播间"
                        )
                    else:
                        display_text = clean_reply_context(reply_event.content)
                        if not display_text and reply_event.gift is not None:
                            display_text = (
                                f"送出 {reply_event.gift.name}"
                                f" ×{reply_event.gift.count}"
                            )
                        display_text = (
                            f"{reply_event.user.name}：{display_text[:80]}"
                        )
                    if display_text:
                        try:
                            await self.on_reply(
                                {
                                    "type": "reply-context",
                                    "text": display_text,
                                }
                            )
                        except Exception:  # noqa: BLE001 - UI event must not stop live
                            logger.debug("Live reply-context send failed")

            assistant_parts: list[str] = []
            async for output in self.agent.chat(make_live_batch_input(batch)):
                display = getattr(output, "display_text", None)
                text = getattr(display, "text", None) if display else None
                if text:
                    assistant_parts.append(str(text))
                await self.on_output(output)
            if batch.events or assistant_parts:
                await asyncio.to_thread(
                    self.review_logger.record,
                    batch.events,
                    "".join(assistant_parts),
                    session_id=self.session_id,
                    source="live",
                    now_ms=self._now_ms(),
                )
            if self.store and assistant_parts:
                await asyncio.to_thread(
                    self.store.append_chat,
                    self.session_id,
                    "assistant",
                    "".join(assistant_parts),
                    self._now_ms(),
                )
            if self.on_batch_end:
                await self.on_batch_end()
            logger.debug("Live batch {} handed to OLV output handler", batch.id)


def _style_scene(batch: Any) -> str:
    if batch.kind == "gift":
        return "praise"
    if batch.kind == "proactive":
        return "greeting"
    text = " ".join(event.content for event in batch.events)
    if any(word in text for word in ("难过", "伤心", "焦虑", "压力", "生病")):
        return "serious"
    if any(word in text for word in ("谢谢", "喜欢", "可爱", "厉害", "好棒")):
        return "praise"
    return "greeting"
