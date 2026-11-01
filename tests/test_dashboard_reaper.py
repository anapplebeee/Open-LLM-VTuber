from __future__ import annotations

import time
import unittest
from unittest.mock import AsyncMock, patch

from src.open_llm_vtuber import dashboard


class DashboardReaperTest(unittest.IsolatedAsyncioTestCase):
    async def test_reaper_removes_old_inactive_rooms_only(self):
        now = int(time.time() * 1000)
        rooms = {
            "rooms": [
                {"roomId": "live", "status": "live", "startedAt": now - 99_000_000},
                {
                    "roomId": "old-error",
                    "status": "error",
                    "startedAt": now - 10 * 60_000,
                },
                {
                    "roomId": "fresh-stopped",
                    "status": "stopped",
                    "startedAt": now - 60_000,
                },
            ]
        }
        calls: list[tuple[str, str]] = []

        async def fake_dyhub(path, method="GET", body=None):
            calls.append((path, method))
            if path == "/api/rooms":
                return rooms
            return {"ok": True}

        with (
            patch.object(dashboard, "COLLECTOR_MODE", "dyhub"),
            patch.object(dashboard, "_dyhub", side_effect=fake_dyhub),
        ):
            await dashboard._reap_rooms()

        deletes = [path for path, method in calls if method == "DELETE"]
        self.assertEqual(deletes, ["/api/rooms/old-error"])


if __name__ == "__main__":
    unittest.main()
