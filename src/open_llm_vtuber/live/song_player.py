"""Resolve live song requests through NetEase.

Search uses the cookie-authenticated web API. Stream URLs are fetched through
the local ``neteasecli`` helper because its weapi token is required by the CDN;
the URL is only passed to the local audio proxy, never accepted from chat.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from loguru import logger

__all__ = ["NeteaseSongProvider", "ResolvedSong"]

_DEFAULT_SESSION = Path.home() / ".config" / "neteasecli" / "profiles" / "default" / "session.json"
_DEFAULT_NODE = r"C:\code\ailive\tools\netease\node_modules\node-win-x64\bin\node.exe"
_DEFAULT_CLI = r"C:\code\ailive\tools\netease\node_modules\neteasecli\dist\index.js"
_SEARCH_URL = "https://music.163.com/api/search/get"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
    ),
    "Referer": "https://music.163.com/",
}


@dataclass(frozen=True)
class ResolvedSong:
    track_id: str
    title: str
    artist: str
    url: str
    duration_ms: int


class NeteaseSongProvider:
    """Small HTTP adapter; never accepts a URL from chat."""

    def __init__(
        self,
        *,
        session_path: str | Path | None = None,
        node_path: str | None = None,
        cli_path: str | None = None,
        timeout: float = 8.0,
    ) -> None:
        self.session_path = Path(session_path or _DEFAULT_SESSION)
        self.node_path = node_path or os.environ.get("NETEASE_NODE", _DEFAULT_NODE)
        self.cli_path = cli_path or os.environ.get("NETEASE_CLI", _DEFAULT_CLI)
        self.timeout = max(3.0, float(timeout))
        self._liked_cache: list[dict[str, Any]] = []
        self._liked_cache_at = 0.0

    @property
    def enabled(self) -> bool:
        return (
            self.session_path.is_file()
            and Path(self.node_path).is_file()
            and Path(self.cli_path).is_file()
        )

    async def random_liked(self) -> ResolvedSong | None:
        """Pick a random NetEase 'liked songs' track and resolve its URL."""
        if not self.enabled:
            return None
        tracks = await asyncio.to_thread(self._load_liked_tracks)
        if not tracks:
            logger.warning("NetEase liked playlist is empty or unavailable")
            return None
        candidates = list(tracks)
        random.shuffle(candidates)
        for track in candidates[:12]:
            track_id = str(track.get("id") or "")
            if not track_id:
                continue
            url = await self._track_url(track_id)
            if not url:
                continue
            artist = track.get("artist") or track.get("artists") or ""
            if isinstance(artist, list):
                artist = "、".join(
                    str(item.get("name")) if isinstance(item, dict) else str(item)
                    for item in artist
                )
            return ResolvedSong(
                track_id=track_id,
                title=str(track.get("name") or "网易云喜欢的歌"),
                artist=str(artist),
                url=url,
                duration_ms=int(track.get("duration") or 0),
            )
        return None

    def _load_liked_tracks(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        if self._liked_cache and now - self._liked_cache_at < 300:
            return self._liked_cache
        try:
            proc = subprocess.run(
                [
                    self.node_path,
                    self.cli_path,
                    "library",
                    "liked",
                    "--limit",
                    "500",
                    "--json",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=max(15.0, self.timeout * 2),
            )
            if proc.returncode != 0:
                logger.warning(
                    "NetEase liked CLI returned {}: {}",
                    proc.returncode,
                    (proc.stderr or "")[:160],
                )
                return self._liked_cache
            payload = json.loads(proc.stdout or "{}")
            tracks = (payload.get("data") or {}).get("tracks") or []
            if isinstance(tracks, list):
                self._liked_cache = tracks
                self._liked_cache_at = now
            return self._liked_cache
        except Exception as exc:  # noqa: BLE001 - provider failure must not stop live
            logger.warning("NetEase liked failed: {}", type(exc).__name__)
            return self._liked_cache

    async def resolve(self, query: str) -> ResolvedSong | None:
        if not self.enabled:
            logger.info("NetEase provider disabled: session file not found")
            return None
        track = await asyncio.to_thread(self._search_track, query)
        if track is None:
            return None
        track_id = str(track.get("id") or "")
        if not track_id:
            return None
        url = await self._track_url(track_id)
        if not url:
            logger.warning("NetEase track unavailable: {}", track_id)
            return None
        artists = track.get("artists") or track.get("artists")
        artist = "、".join(
            str(item.get("name"))
            for item in artists
            if isinstance(item, dict) and item.get("name")
        )
        return ResolvedSong(
            track_id=track_id,
            title=str(track.get("name") or query),
            artist=artist,
            url=url,
            duration_ms=int(track.get("duration") or 0),
        )

    def _session_cookie(self) -> str:
        payload = json.loads(self.session_path.read_text(encoding="utf-8"))
        values = {
            key: payload[key]
            for key in ("MUSIC_U", "__csrf")
            if key in payload and payload[key]
        }
        return "; ".join(f"{key}={value}" for key, value in values.items())

    def _search_track(self, query: str) -> dict[str, Any] | None:
        try:
            response = requests.get(
                _SEARCH_URL,
                params={
                    "s": query,
                    "type": 1,
                    "offset": 0,
                    "total": "true",
                    "limit": 10,
                },
                headers={**_HEADERS, "Cookie": self._session_cookie()},
                timeout=(3, self.timeout),
                verify=False,
            )
            payload = response.json()
            songs = (payload.get("result") or {}).get("songs") or []
            return songs[0] if songs else None
        except Exception as exc:  # noqa: BLE001 - provider failure must not stop live
            logger.warning("NetEase search failed: {}", type(exc).__name__)
            return None

    async def _track_url(self, track_id: str) -> str | None:
        try:
            proc = await asyncio.create_subprocess_exec(
                self.node_path,
                self.cli_path,
                "track",
                "url",
                track_id,
                "--json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout
            )
        except (OSError, asyncio.TimeoutError) as exc:
            logger.warning("NetEase CLI URL failed: {}", type(exc).__name__)
            return None
        if proc.returncode != 0:
            logger.info(
                "NetEase CLI URL returned {}: {}",
                proc.returncode,
                (stderr or b"").decode("utf-8", "replace")[:160],
            )
            return None
        try:
            payload = json.loads(stdout.decode("utf-8", "replace"))
            data = payload.get("data") or {}
            return str(data["url"]) if data.get("url") else None
        except (json.JSONDecodeError, TypeError, KeyError):
            return None
