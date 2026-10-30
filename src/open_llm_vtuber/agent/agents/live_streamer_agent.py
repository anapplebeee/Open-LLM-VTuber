"""OLV-native live agent.

The live director selects a batch; this agent applies a planner decision and
then delegates expression, memory, sentence splitting and TTS to the existing
OLV BasicMemoryAgent pipeline. The text input is data, never a system prompt.
"""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator
from typing import Any

from ...live.events import LiveBatch
from ...live.planner import LiveDecision, LiveReActPlanner
from ...live.replyer import LiveReplyer
from ..input_types import BatchInput, TextData, TextSource
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
        self._live_planner = live_planner or LiveReActPlanner(llm=self._llm)
        self._live_replyer = live_replyer or LiveReplyer(llm=self._llm)
        self._rundown_state = rundown_state
        self._session_recap = ""

    @property
    def llm(self) -> Any:
        """Expose the shared stateless LLM to the recap writer."""
        return self._llm

    def set_live_context(self, *, recent_recap: str = "") -> None:
        """Inject the latest session recap without changing live history."""
        self._session_recap = recent_recap

    async def chat(self, input_data: BatchInput) -> AsyncIterator[Any]:
        batch = (input_data.metadata or {}).get("live_batch")
        if batch is None:
            async for output in super().chat(input_data):
                yield output
            return

        if isinstance(batch, dict):
            batch = _batch_from_mapping(batch)
        if not isinstance(batch, LiveBatch):
            raise TypeError("live_batch metadata must be a LiveBatch")

        rundown_text = (
            self._rundown_state.context_text()
            if self._rundown_state is not None
            else ""
        )
        history = list(self._memory[-20:])
        decision = self._live_planner.plan(
            batch,
            rundown_text=rundown_text,
            recent_recap=self._session_recap,
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
                recent_recap=self._session_recap,
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
        async for output in super().chat(prepared):
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
