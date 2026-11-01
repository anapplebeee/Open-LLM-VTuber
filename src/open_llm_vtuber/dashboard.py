"""Local control-plane dashboard API.

This is intentionally a thin adapter over OLV's active LiveSession and the
dyhub collector. It does not start a second agent, TTS engine or browser.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request

from .live.registry import get_current_live_session

__all__ = ["init_dashboard_routes"]

ROOT = Path(__file__).resolve().parents[2]
REVIEW_PATH = ROOT / "data" / "live-review.jsonl"
DYHUB = os.getenv("DYHUB_API", "http://127.0.0.1:8757").rstrip("/")
TTS = os.getenv("LIVE_TTS_URL", "http://127.0.0.1:9880").rstrip("/")
CLIENT_TIMEOUT = 2.0


async def _request(method: str, url: str, body: dict[str, Any] | None = None):
    async with httpx.AsyncClient(timeout=CLIENT_TIMEOUT) as client:
        response = await client.request(method, url, json=body)
        response.raise_for_status()
        try:
            return response.json()
        except ValueError:
            return {"ok": response.is_success}


async def _dyhub(path: str, method: str = "GET", body: dict[str, Any] | None = None):
    return await _request(method, f"{DYHUB}{path}", body)


async def _read_events(limit: int = 40) -> list[dict[str, Any]]:
    if not REVIEW_PATH.exists():
        return []
    try:
        # Dashboard polling must not load an ever-growing review log into RAM.
        with REVIEW_PATH.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - 256 * 1024))
            text = handle.read().decode("utf-8", "replace")
        lines = text.splitlines()
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines[-max(1, limit) :]:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        for event in record.get("events") or []:
            rows.append(
                {
                    "ts": record.get("ts", ""),
                    "type": event.get("type", ""),
                    "user": event.get("user", ""),
                    "content": event.get("content", ""),
                    "reply": record.get("reply", ""),
                    "gift": event.get("gift"),
                }
            )
    return rows[-limit:]


def _resources() -> dict[str, Any]:
    try:
        import psutil  # type: ignore
    except ImportError:
        return {"available": False, "reason": "psutil not installed"}
    processes = []
    for proc in psutil.process_iter(
        ["pid", "name", "memory_info", "cpu_percent"]
    ):
        try:
            name = (proc.info.get("name") or "").lower()
            if not any(
                token in name
                for token in ("python", "node", "obs64", "chrome", "msedge")
            ):
                continue
            memory = proc.info.get("memory_info")
            processes.append(
                {
                    "pid": proc.info["pid"],
                    "name": proc.info["name"],
                    "rss_mb": round((memory.rss if memory else 0) / 1024 / 1024, 1),
                    "cpu_percent": proc.info.get("cpu_percent") or 0,
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return {"available": True, "processes": sorted(processes, key=lambda x: -x["rss_mb"])[:30]}


def init_dashboard_routes(default_context_cache: Any = None) -> APIRouter:
    router = APIRouter(prefix="/api/dashboard")

    @router.get("/status")
    async def status():
        session = get_current_live_session()
        live: dict[str, Any] = {"active": session is not None}
        if session is not None:
            try:
                live["status"] = session.status()
            except Exception as exc:  # noqa: BLE001 - dashboard must stay up
                live["error"] = f"{type(exc).__name__}: {exc}"
        checks: dict[str, Any] = {}
        for name, url in (("tts", f"{TTS}/docs"),):
            try:
                response = await _request("GET", url)
                checks[name] = {"ok": True, "data": response}
            except Exception as exc:  # noqa: BLE001
                checks[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        rooms: list[dict[str, Any]] = []
        stats: dict[str, Any] = {}
        try:
            rooms_payload = await _dyhub("/api/rooms")
            rooms = rooms_payload.get("rooms") or []
            stats = await _dyhub("/api/stats")
            # Avoid duplicating the full room list inside the stats payload.
            if isinstance(stats, dict):
                stats = {key: value for key, value in stats.items() if key != "rooms"}
            checks["dyhub"] = {"ok": True}
        except Exception as exc:  # noqa: BLE001
            checks["dyhub"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        gift_rooms = [room for room in rooms if room.get("hasOwnCookie")]
        return {
            "ts": int(time.time() * 1000),
            "checks": checks,
            "olv": {"ok": True, "live": live},
            "dyhub": {"ok": checks.get("dyhub", {}).get("ok", False), "stats": stats},
            "rooms": rooms,
            "gift_ready": bool(gift_rooms),
            "gift_rooms": [room.get("roomId") for room in gift_rooms],
            "bridge_ws_clients": stats.get("wsClients", 0) if isinstance(stats, dict) else 0,
        }

    @router.get("/events")
    async def events(limit: int = 40):
        return {"events": await _read_events(max(1, min(200, limit)))}

    @router.get("/rooms")
    async def rooms():
        try:
            return await _dyhub("/api/rooms")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.post("/rooms/connect")
    async def connect_room(request: Request):
        body = await request.json()
        room_id = str(body.get("roomId") or "").strip()
        if not room_id:
            raise HTTPException(status_code=400, detail="roomId required")
        try:
            return await _dyhub("/api/rooms/connect", "POST", {"roomId": room_id})
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.post("/rooms/{room_id}/disconnect")
    async def disconnect_room(room_id: str):
        try:
            return await _dyhub(f"/api/rooms/{room_id}/disconnect", "POST", {})
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.delete("/rooms/{room_id}")
    async def delete_room(room_id: str):
        try:
            return await _dyhub(f"/api/rooms/{room_id}", "DELETE")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.put("/rooms/{room_id}/cookie")
    async def set_cookie(room_id: str, request: Request):
        body = await request.json()
        cookie = str(body.get("cookie") or "").strip()
        if not cookie:
            raise HTTPException(status_code=400, detail="cookie required")
        try:
            # Never echo or log the cookie value.
            return await _dyhub(f"/api/rooms/{room_id}/cookie", "PUT", {"cookie": cookie})
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.post("/rooms/cleanup")
    async def cleanup_rooms():
        try:
            payload = await _dyhub("/api/rooms")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        removed: list[str] = []
        for room in payload.get("rooms") or []:
            if room.get("status") in {"live", "connecting"}:
                continue
            try:
                await _dyhub(f"/api/rooms/{room.get('roomId')}", "DELETE")
                removed.append(str(room.get("roomId")))
            except Exception:
                continue
        return {"ok": True, "removed": removed}

    @router.post("/song/control")
    async def song_control(request: Request):
        body = await request.json()
        action = str(body.get("action") or "").strip()
        session = get_current_live_session()
        if session is None:
            raise HTTPException(status_code=409, detail="no active live session")
        try:
            await session.control_song(action)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "action": action}

    @router.get("/resources")
    async def resources():
        return _resources()

    return router
