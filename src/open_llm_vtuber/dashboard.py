"""Local control-plane dashboard API.

This is intentionally a thin adapter over OLV's active LiveSession and the
dyhub collector. It does not start a second agent, TTS engine or browser.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from .live.registry import get_current_live_session

__all__ = [
    "dashboard_auth_middleware",
    "init_dashboard_routes",
    "run_room_reaper",
]

ROOT = Path(__file__).resolve().parents[2]
REVIEW_PATH = ROOT / "data" / "live-review.jsonl"
TOKEN_PATH = ROOT / "data" / "dashboard-token.txt"
DYHUB = os.getenv("DYHUB_API", "http://127.0.0.1:8757").rstrip("/")
TTS = os.getenv("LIVE_TTS_URL", "http://127.0.0.1:9880").rstrip("/")
TTS_PID_PATH = ROOT / "data" / "tts.pid"
TTS_START_SCRIPT = ROOT / "scripts" / "start_tts.ps1"
NATIVE_COLLECTOR_SCRIPT = ROOT / "scripts" / "native_douyin_collector.py"
COLLECTOR_MODE = os.getenv("DASHBOARD_COLLECTOR", "native").strip().lower()
LIVE_WS_URL = os.getenv("LIVE_WS_URL", "ws://127.0.0.1:12393/live-ws")
CLIENT_TIMEOUT = 2.0
_native_process: subprocess.Popen | None = None
_native_room_id = ""
COOKIE_NAME = "dsh_dashboard"
AUTH_DISABLED = os.getenv("DASHBOARD_AUTH_DISABLED", "0") == "1"
_REAPER_INTERVAL_SEC = max(30, int(os.getenv("DASHBOARD_REAPER_SEC", "300")))
_REAPER_ENABLED = os.getenv("DASHBOARD_REAPER_ENABLED", "1") != "0"


def _load_token() -> str:
    configured = os.getenv("DASHBOARD_TOKEN", "").strip()
    if configured:
        return configured
    try:
        existing = TOKEN_PATH.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass
    token = secrets.token_urlsafe(24)
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(token + "\n", encoding="utf-8")
    return token


DASHBOARD_TOKEN = _load_token()


def _authorized(request: Request) -> bool:
    if AUTH_DISABLED:
        return True
    supplied = request.cookies.get(COOKIE_NAME, "")
    if not supplied:
        header = request.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            supplied = header[7:].strip()
    return bool(supplied) and hmac.compare_digest(supplied, DASHBOARD_TOKEN)


async def dashboard_auth_middleware(request: Request, call_next):
    path = request.url.path.rstrip("/") or "/"
    if path.startswith("/api/dashboard") and path != "/api/dashboard/login":
        if not _authorized(request):
            return JSONResponse(
                status_code=401,
                content={"detail": "dashboard authentication required"},
            )
    return await call_next(request)


async def run_room_reaper() -> None:
    """Reclaim inactive collector rooms without touching live rooms."""
    if not _REAPER_ENABLED:
        return
    while True:
        await asyncio.sleep(_REAPER_INTERVAL_SEC)
        try:
            await _reap_rooms()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - background maintenance must not die
            continue


async def _reap_rooms() -> None:
    if COLLECTOR_MODE == "native":
        return
    payload = await _dyhub("/api/rooms")
    now_ms = int(time.time() * 1000)
    for room in payload.get("rooms") or []:
        status = room.get("status")
        if status in {"live", "connecting"}:
            continue
        started = int(room.get("startedAt") or 0)
        age_ms = now_ms - started if started else 0
        # stopped rooms are kept for 30 minutes; failed/closed rooms for 5.
        limit_ms = 30 * 60_000 if status == "stopped" else 5 * 60_000
        if age_ms < limit_ms:
            continue
        try:
            await _dyhub(f"/api/rooms/{room.get('roomId')}", "DELETE")
        except Exception:  # noqa: BLE001 - individual cleanup is best effort
            continue


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


def _native_status() -> dict[str, Any]:
    global _native_process, _native_room_id
    running = _native_process is not None and _native_process.poll() is None
    if _native_process is not None and not running:
        _native_process = None
    return {
        "mode": COLLECTOR_MODE,
        "native_available": NATIVE_COLLECTOR_SCRIPT.is_file(),
        "running": running,
        "room_id": _native_room_id,
        "pid": _native_process.pid if running and _native_process else None,
    }


def _start_native(room_id: str) -> dict[str, Any]:
    global _native_process, _native_room_id
    status = _native_status()
    if status["running"]:
        return {"ok": True, "already_running": True, **status}
    if not NATIVE_COLLECTOR_SCRIPT.is_file():
        raise HTTPException(status_code=503, detail="native collector script missing")
    log_path = ROOT / "data" / "native-collector.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log_path.open("ab") as log:
            _native_process = subprocess.Popen(
                [
                    sys.executable,
                    str(NATIVE_COLLECTOR_SCRIPT),
                    "--room-id",
                    room_id,
                    "--olv-url",
                    LIVE_WS_URL,
                ],
                cwd=str(ROOT),
                stdout=log,
                stderr=log,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    _native_room_id = room_id
    return {"ok": True, "starting": True, **_native_status()}


def _stop_native() -> dict[str, Any]:
    global _native_process, _native_room_id
    if _native_process is not None and _native_process.poll() is None:
        _native_process.terminate()
        try:
            _native_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _native_process.kill()
    _native_process = None
    _native_room_id = ""
    return {"ok": True, "running": False}


def _resources() -> dict[str, Any]:
    """Read the OLV process RSS without scanning every Windows process.

    Full process enumeration is intentionally avoided: on Windows it can stall
    behind protected processes and it would make the dashboard itself a
    resource problem. External services are covered by health checks.
    """
    try:
        import psutil  # type: ignore

        process = psutil.Process(os.getpid())
        memory = process.memory_info()
        return {
            "available": True,
            "source": "current-process",
            "processes": [
                {
                    "pid": os.getpid(),
                    "name": "OLV " + process.name(),
                    "rss_mb": round(memory.rss / 1024 / 1024, 1),
                }
            ],
        }
    except Exception as exc:  # noqa: BLE001 - metrics are best effort
        return {"available": False, "reason": type(exc).__name__}


def init_dashboard_routes(default_context_cache: Any = None) -> APIRouter:
    router = APIRouter(prefix="/api/dashboard")

    @router.post("/login")
    async def login(request: Request, response: Response):
        body = await request.json()
        token = str(body.get("token") or "").strip()
        if not token or not hmac.compare_digest(token, DASHBOARD_TOKEN):
            raise HTTPException(status_code=401, detail="invalid dashboard token")
        response.set_cookie(
            COOKIE_NAME,
            DASHBOARD_TOKEN,
            max_age=7 * 24 * 3600,
            httponly=True,
            samesite="lax",
            secure=False,
        )
        return {"ok": True}

    @router.post("/logout")
    async def logout(response: Response):
        response.delete_cookie(COOKIE_NAME)
        return {"ok": True}

    @router.get("/session")
    async def session():
        return {"ok": True, "auth_required": not AUTH_DISABLED}

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
        if COLLECTOR_MODE == "native":
            checks["dyhub"] = {"ok": True, "disabled": "native collector mode"}
            native = _native_status()
            if native["running"]:
                rooms = [
                    {
                        "roomId": native["room_id"],
                        "status": "live",
                        "meta": {"nickname": "native collector", "title": "native"},
                        "stats": {},
                        "hasOwnCookie": False,
                    }
                ]
        else:
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
            "collector": _native_status(),
        }

    @router.get("/events")
    async def events(limit: int = 40):
        return {"events": await _read_events(max(1, min(200, limit)))}

    @router.get("/rooms")
    async def rooms():
        if COLLECTOR_MODE == "native":
            native = _native_status()
            return {
                "rooms": [
                    {
                        "roomId": native["room_id"],
                        "status": "live" if native["running"] else "stopped",
                        "meta": {"nickname": "native collector", "title": "native"},
                        "stats": {},
                        "hasOwnCookie": False,
                    }
               
                ]
                if native["room_id"]
                else [],
            }
        try:
            return await _dyhub("/api/rooms")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.get("/collector")
    async def collector_status():
        return _native_status()

    @router.post("/rooms/connect")
    async def connect_room(request: Request):
        body = await request.json()
        room_id = str(body.get("roomId") or "").strip()
        if not room_id:
            raise HTTPException(status_code=400, detail="roomId required")
        if COLLECTOR_MODE == "native":
            return _start_native(room_id)
        try:
            return await _dyhub("/api/rooms/connect", "POST", {"roomId": room_id})
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.post("/rooms/{room_id}/disconnect")
    async def disconnect_room(room_id: str):
        if COLLECTOR_MODE == "native":
            return _stop_native()
        try:
            return await _dyhub(f"/api/rooms/{room_id}/disconnect", "POST", {})
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.delete("/rooms/{room_id}")
    async def delete_room(room_id: str):
        if COLLECTOR_MODE == "native":
            return _stop_native()
        try:
            return await _dyhub(f"/api/rooms/{room_id}", "DELETE")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.put("/rooms/{room_id}/cookie")
    async def set_cookie(room_id: str, request: Request):
        if COLLECTOR_MODE == "native":
            raise HTTPException(
                status_code=409,
                detail="native collector cookie is set via DOUYIN_COOKIE before start",
            )
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
        if COLLECTOR_MODE == "native":
            return {"ok": True, "removed": []}
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

    @router.get("/tts/status")
    async def tts_status():
        try:
            await _request("GET", f"{TTS}/docs")
            healthy = True
        except Exception:
            healthy = False
        pid = None
        try:
            pid = int(TTS_PID_PATH.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            pass
        return {"healthy": healthy, "pid": pid}

    @router.post("/tts/start")
    async def tts_start():
        try:
            await _request("GET", f"{TTS}/docs")
            return {"ok": True, "already_running": True}
        except Exception:
            pass
        try:
            subprocess.Popen(
                [
                    "powershell",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(TTS_START_SCRIPT),
                ],
                cwd=str(ROOT),
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"ok": True, "starting": True}

    @router.post("/tts/stop")
    async def tts_stop():
        # Prefer the PID written by start_tts.ps1; fall back to the API control
        # endpoint for manually started TTS instances.
        pid = None
        try:
            pid = int(TTS_PID_PATH.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            pass
        if pid is not None:
            try:
                import psutil  # type: ignore

                process = psutil.Process(pid)
                # start_tts.ps1 launches a venv launcher plus the uv Python
                # child; kill the whole tree, not just the launcher.
                tree = process.children(recursive=True) + [process]
                for item in reversed(tree):
                    try:
                        item.kill()
                    except psutil.Error:
                        continue
                for item in reversed(tree):
                    try:
                        item.wait(timeout=5)
                    except psutil.Error:
                        continue
            except Exception:
                # Fall back to taskkill for environments without psutil.
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
            TTS_PID_PATH.unlink(missing_ok=True)
            return {"ok": True, "stopped": pid}
        try:
            await _request("GET", f"{TTS}/control?command=exit")
            return {"ok": True, "stopped": "control"}
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.get("/resources")
    async def resources():
        return _resources()

    return router
