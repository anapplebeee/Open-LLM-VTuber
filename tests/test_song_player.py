from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.open_llm_vtuber.live.runtime import LiveSession
from src.open_llm_vtuber.live.song_player import NeteaseSongProvider


class MissingSongProvider:
    async def resolve(self, _query):
        return None


class BrokenSongProvider:
    async def resolve(self, _query):
        raise RuntimeError("provider down")


class FakeSongProvider(NeteaseSongProvider):
    def __init__(self, session_path: Path) -> None:
        super().__init__(session_path=session_path)

    def _load_liked_tracks(self):
        return [
            {
                "id": "456",
                "name": "喜欢的歌",
                "artist": "测试歌手",
                "duration": 1200,
            }
        ]

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
        self.assertEqual(session.song_playback.state, "playing")
        self.assertEqual(session.song_playback.current_title, "海底")

    async def test_random_liked_uses_liked_playlist(self):
        provider = FakeSongProvider(self.session_path)
        song = await provider.random_liked()
        self.assertIsNotNone(song)
        self.assertEqual(song.title, "喜欢的歌")
        self.assertEqual(song.artist, "测试歌手")

    async def test_missing_song_emits_unavailable_and_removes_queue(self):
        payloads = []

        async def on_song(payload):
            payloads.append(payload)

        session = LiveSession(
            agent=None,
            on_output=lambda _: None,
            on_song=on_song,
            song_provider=MissingSongProvider(),
        )
        await session._play_song("不存在", "entry-1")
        self.assertEqual(payloads[0]["type"], "song-unavailable")
        self.assertEqual(payloads[0]["entry_id"], "entry-1")
        self.assertEqual(payloads[0]["reason"], "not_found")
        self.assertEqual(session.song_requests.snapshot()["items"], [])
        self.assertEqual(session.song_playback.state, "failed")
        self.assertEqual(session.song_playback.last_error, "not_found")

    async def test_provider_error_emits_unavailable(self):
        payloads = []

        async def on_song(payload):
            payloads.append(payload)

        session = LiveSession(
            agent=None,
            on_output=lambda _: None,
            on_song=on_song,
            song_provider=BrokenSongProvider(),
        )
        await session._play_song("出错", "entry-2")
        self.assertEqual(payloads[0]["type"], "song-unavailable")
        self.assertEqual(payloads[0]["reason"], "resolve_error")
        self.assertEqual(session.song_playback.state, "failed")


if __name__ == "__main__":
    unittest.main()
