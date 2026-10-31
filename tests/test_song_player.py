from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.open_llm_vtuber.live.runtime import LiveSession
from src.open_llm_vtuber.live.song_player import NeteaseSongProvider


class FakeSongProvider(NeteaseSongProvider):
    def __init__(self, session_path: Path) -> None:
        super().__init__(session_path=session_path)

    def _search_track(self, query):
        return {
            "id": "123",
            "name": "海底",
            "duration": 1000,
            "artists": [{"name": "一支榴莲"}],
        }

    async def _track_url(self, track_id):
        return "http://example.test/song.mp3"


class SongPlayerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        )
        json.dump({"MUSIC_U": "test"}, self.tmp)
        self.tmp.close()
        self.session_path = Path(self.tmp.name)

    async def asyncTearDown(self):
        self.session_path.unlink(missing_ok=True)

    async def test_resolve_returns_playable_song(self):
        provider = FakeSongProvider(self.session_path)
        song = await provider.resolve("海底")
        self.assertIsNotNone(song)
        self.assertEqual(song.track_id, "123")
        self.assertEqual(song.title, "海底")
        self.assertEqual(song.artist, "一支榴莲")
        self.assertEqual(song.url, "http://example.test/song.mp3")

    async def test_live_session_dispatches_song_payload(self):
        payloads = []

        async def on_song(payload):
            payloads.append(payload)

        session = LiveSession(
            agent=None,
            on_output=lambda _: None,
            on_song=on_song,
            song_provider=FakeSongProvider(self.session_path),
        )
        await session._play_song("海底")
        self.assertEqual(payloads[0]["type"], "song")
        self.assertEqual(payloads[0]["action"], "play")
        self.assertEqual(payloads[0]["title"], "海底")
        self.assertEqual(payloads[0]["url"], "http://example.test/song.mp3")


if __name__ == "__main__":
    unittest.main()
