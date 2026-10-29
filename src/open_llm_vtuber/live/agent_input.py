"""Build an OLV BatchInput from a selected live batch without importing agents."""

from __future__ import annotations

from ..agent.input_types import BatchInput, TextData, TextSource
from .events import LiveBatch


def make_live_batch_input(batch: LiveBatch) -> BatchInput:
    """Wrap a selected live batch for the OLV agent pipeline."""
    return BatchInput(
        texts=[
            TextData(
                source=TextSource.INPUT,
                content="直播导播已选择一轮事件，请按人设处理。",
                from_name="直播导播",
            )
        ],
        metadata={"live_batch": batch, "source": "live-director"},
    )
