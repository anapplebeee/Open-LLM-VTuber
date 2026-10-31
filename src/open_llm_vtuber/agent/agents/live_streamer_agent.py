"""OLV-native live agent.

The live director selects a batch; this agent applies a planner decision and
then delegates expression, memory, sentence splitting and TTS to the existing
OLV BasicMemoryAgent pipeline. The text input is data, never a system prompt.
"""

from __future__ import annotations

import inspect
import re
from collections import deque
from collections.abc import AsyncIterator
from typing import Any

from loguru import logger

from ...live.character_profile import XIAOJING_PROFILE
from ...live.events import LiveBatch
from ...live.personality import (
    RESPONSE_POLICY,
    LiveInteractionPolicy,
    emotion_hint_for_text,
    fallback_for_input,
    fallback_for_text,
    sanitize_live_reply,
)
from ...live.planner import LiveDecision, LiveReActPlanner
from ...live.replyer import LiveReplyer
from ...live.speech_gate import SpeechGate
from ..input_types import BatchInput, TextData, TextSource
from ..output_types import SentenceOutput
from ..transformers import (
    actions_extractor,
    display_processor,
    sentence_divider,
    tts_filter,
)
from .basic_memory_agent import BasicMemoryAgent


class LiveStreamerAgent(BasicMemoryAgent):
    """BasicMemoryAgent with an explicit live decision boundary.

    The class intentionally does not start its own event loop or TTS queue.
    ``LiveDirector`` owns event timing and the OLV conversation layer owns
    sentence playback, so this remains one decision owner and one audio owner.
    """

    def __init__(
        self,
        *,
        live_planner: Any | None = None,
        live_replyer: LiveReplyer | None = None,
        rundown_state: Any = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._character_profile = XIAOJING_PROFILE
        self._system = (
            f"{self._system}\n{RESPONSE_POLICY}\n{self._character_profile.compact_prompt()}"
        )
        self.set_system(self._system)
        self._live_planner = live_planner or LiveReActPlanner(llm=self._llm)
        self._live_replyer = live_replyer or LiveReplyer(llm=self._llm)
        self._rundown_state = rundown_state
        self._session_recap = ""
        self._notebook_text = ""
        self._context_packet: Any = None
        self._interaction_policy = LiveInteractionPolicy()
        self._speech_gate = SpeechGate()
        self._recent_spoken: deque[str] = deque(maxlen=10)
        # BasicMemoryAgent.__init__ assigns self.chat as an instance attribute.
        # Capture it before binding the live-aware wrapper, otherwise the
        # method-level override below is shadowed and never runs.
        self._base_chat = self.chat
        self.chat = self._live_chat

    @property
    def llm(self) -> Any:
        """Expose the shared stateless LLM to the recap writer."""
        return self._llm

    def set_live_context(
        self,
        *,
        recent_recap: str = "",
        notebook: str = "",
        context_packet: Any = None,
    ) -> None:
        """Inject bounded context without touching the conversation history."""
        self._session_recap = recent_recap
        self._notebook_text = notebook
        self._context_packet = context_packet

    async def _live_chat(self, input_data: BatchInput) -> AsyncIterator[Any]:
        batch = (input_data.metadata or {}).get("live_batch")
        if batch is None:
            remaining_sentences = 2
            identity_replaced = False
            request_text = " ".join(
                getattr(item, "content", "") or ""
                for item in (getattr(input_data, "texts", None) or [])
            )
            requested_fallback = fallback_for_input(request_text)
            async for output in self._base_chat(input_data):
                if identity_replaced:
                    continue
                if isinstance(output, SentenceOutput):
                    raw_text = output.display_text.text
                    fallback = requested_fallback or fallback_for_text(raw_text)
                    if fallback is not None:
                        if identity_replaced:
                            continue
                        output.display_text.text = fallback
                        output.tts_text = fallback
                        identity_replaced = True
                        remaining_sentences -= 1
                        yield output
                        continue
                    cleaned = sanitize_live_reply(
                        raw_text,
                        max_sentences=max(1, remaining_sentences),
                    )
                    if not cleaned:
                        logger.warning("Discarded unsafe normal-chat reply")
                        continue
                    verdict = self._speech_gate.verdict(
                        cleaned, list(self._recent_spoken)
                    )
                    if not verdict.passed:
                        logger.info(
                            "Suppressed repeated speech (ratio={:.2f})",
                            verdict.ratio,
                        )
                        continue
                    remaining_sentences -= len(
                        re.findall(r"[。！？!?；;]", cleaned)
                    ) or 1
                    output.display_text.text = cleaned
                    output.tts_text = cleaned
                    # actions_extractor runs before sanitize_live_reply.  When
                    # the sanitizer creates a tag from a kaomoji, mirror that
                    # tag into the already-built Actions object.
                    if (
                        self._live2d_model is not None
                        and getattr(output, "actions", None) is not None
                    ):
                        expressions = self._live2d_model.extract_emotion(cleaned)
                        if not expressions or expressions == ["relax"]:
                            hint = emotion_hint_for_text(request_text)
                            if hint != "neutral":
                                expressions = self._live2d_model.extract_emotion(
                                    f"[{hint}]"
                                )
                        if expressions:
                            output.actions.expressions = expressions
                    self._recent_spoken.append(cleaned)
                    if remaining_sentences < 0:
                        continue
                yield output
            return

        if isinstance(batch, dict):
            batch = _batch_from_mapping(batch)
        if not isinstance(batch, LiveBatch):
            raise TypeError("live_batch metadata must be a LiveBatch")
        policy = self._interaction_policy.check(batch)
        if not policy.allow:
            logger.debug("Live batch suppressed by interaction policy: {}", policy.reason)
            return

        context_packet = getattr(self, "_context_packet", None)
        if context_packet is not None:
            context_text = context_packet.prompt_block()
            rundown_text = context_text
            notebook_text = context_packet.notebook
            recent_recap = context_packet.recent_recap
        else:
            rundown_text = (
                self._rundown_state.context_text()
                if self._rundown_state is not None
                else ""
            )
            notebook_text = getattr(self, "_notebook_text", "")
            if notebook_text:
                rundown_text = f"{rundown_text}\n本场笔记：{notebook_text}"
            context_text = rundown_text
            recent_recap = self._session_recap
        history = list(self._memory[-20:])
        decision = self._live_planner.plan(
            batch,
            rundown_text=rundown_text,
            recent_recap=recent_recap,
            history=history,
        )
        if inspect.isawaitable(decision):
            decision = await decision
        if not isinstance(decision, LiveDecision) or not decision.speak:
            return

        if self._live_replyer is not None:
            text = await self._live_replyer.reply(
                decision,
                batch,
                persona=self._system,
                recent_recap=recent_recap,
                notebook=notebook_text,
                context_text=context_text,
                emotion_hint=(
                    context_packet.emotion.expression_tag()
                    if context_packet is not None
                    else (decision.emotion or "")
                ),
                history=history,
            )
            self._add_message(
                "\n".join(event.to_agent_text() for event in batch.events), "user"
            )
            self._add_message(text, "assistant")
            async for output in self._stream_live_text(text):
                yield output
            return

        prepared = BatchInput(
            texts=[
                TextData(
                    source=TextSource.INPUT,
                    content=decision.prompt,
                    from_name="直播导播",
                )
            ],
            images=input_data.images,
            files=input_data.files,
            metadata={
                **(input_data.metadata or {}),
                "live_decision": decision.reason,
                "skip_memory": False,
            },
        )
        async for output in self._base_chat(prepared):
            yield output

    async def _stream_live_text(self, text: str) -> AsyncIterator[Any]:
        """Run replyer text through OLV's sentence/action/TTS pipeline."""

        @tts_filter(self._tts_preprocessor_config)
        @display_processor()
        @actions_extractor(self._live2d_model)
        @sentence_divider(
            faster_first_response=self._faster_first_response,
            segment_method=self._segment_method,
            valid_tags=["think"],
        )
        async def stream_text() -> AsyncIterator[str]:
            yield text

        async for output in stream_text():
            yield output


def _batch_from_mapping(value: dict[str, Any]) -> LiveBatch:
    from ...live.events import RoomEvent

    events = tuple(RoomEvent.model_validate(item) for item in value.get("events", []))
    return LiveBatch(
        id=str(value.get("id", "live-batch")),
        kind=value.get("kind", "chat"),
        priority=int(value.get("priority", 40)),
        events=events,
        topic=value.get("topic"),
    )


__all__ = ["LiveStreamerAgent"]
