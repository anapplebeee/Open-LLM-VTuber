"""OLV-native live agent.

The live director selects a batch; this agent applies a planner decision and
then delegates expression, memory, sentence splitting and TTS to the existing
OLV BasicMemoryAgent pipeline. The text input is data, never a system prompt.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from ...live.events import LiveBatch
from ...live.planner import RuleBasedLivePlanner
from ..input_types import BatchInput, TextData, TextSource
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
        live_planner: RuleBasedLivePlanner | None = None,
        rundown_state: Any = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._live_planner = live_planner or RuleBasedLivePlanner()
        self._rundown_state = rundown_state

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
        decision = self._live_planner.plan(batch, rundown_text=rundown_text)
        if not decision.speak:
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
