from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

from src.open_llm_vtuber.conversations import conversation_utils


class _FakeTTSManager:
    def __init__(self) -> None:
        self.task_list = [asyncio.create_task(asyncio.sleep(0))]
        self.waited_for_sender = False

    async def wait_idle(self, timeout: float | None = None) -> bool:
        self.waited_for_sender = True
        return True


class ConversationTurnTest(unittest.IsolatedAsyncioTestCase):
    async def test_finalize_waits_for_sender_and_sends_one_backend_complete(self):
        messages: list[dict] = []

        async def send(text: str) -> None:
            messages.append(json.loads(text))

        async def fake_wait(*_args, **_kwargs):
            return {"type": "frontend-playback-complete"}

        manager = _FakeTTSManager()
        with patch.object(
            conversation_utils.message_handler, "wait_for_response", fake_wait
        ):
            await conversation_utils.finalize_conversation_turn(
                tts_manager=manager,
                websocket_send=send,
                client_uid="client-1",
            )

        self.assertTrue(manager.waited_for_sender)
        self.assertEqual(
            [message["type"] for message in messages],
            ["backend-synth-complete", "force-new-message", "control"],
        )
        self.assertEqual(messages[-1]["text"], "conversation-chain-end")


if __name__ == "__main__":
    unittest.main()
