"""Read-only health check for the local live stack.

Checks:
  - OLV HTTP
  - GPT-SoVITS HTTP
  - dyhub API and active rooms
  - gift readiness (dyhub room hasOwnCookie)
  - bridge WebSocket client presence (dyhub wsClients)

This does not start/stop anything and does not send live events.
"""

from __future__ import annotations

import argparse
import json
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen


def get_json(url: str, timeout: float = 5.0) -> tuple[bool, Any, str]:
    try:
        with urlopen(url, timeout=timeout) as response:
            return True, json.loads(response.read().decode("utf-8")), ""
    except Exception as exc:  # noqa: BLE001 - health checks report all failures
        return False, None, f"{type(exc).__name__}: {exc}"


def get_status(url: str, timeout: float = 5.0) -> tuple[bool, int | None, str]:
    try:
        with urlopen(url, timeout=timeout) as response:
            return True, response.status, ""
    except Exception as exc:  # noqa: BLE001
        return False, None, f"{type(exc).__name__}: {exc}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Live stack health check")
    parser.add_argument("--olv", default="http://127.0.0.1:12393")
    parser.add_argument("--tts", default="http://127.0.0.1:9880")
    parser.add_argument("--dyhub", default="http://127.0.0.1:8757")
    parser.add_argument("--require-gift", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    checks: dict[str, Any] = {}
    hard_fail = False

    ok, status, error = get_status(args.olv.rstrip("/") + "/")
    checks["olv"] = {"ok": ok, "status": status, "error": error}
    hard_fail |= not ok

    ok, status, error = get_status(args.tts.rstrip("/") + "/docs")
    checks["tts"] = {"ok": ok, "status": status, "error": error}
    hard_fail |= not ok

    ok, stats, error = get_json(args.dyhub.rstrip("/") + "/api/stats")
    checks["dyhub"] = {"ok": ok, "error": error, "stats": stats}
    hard_fail |= not ok

    rooms: list[dict[str, Any]] = []
    rooms_ok, rooms_payload, rooms_error = get_json(
        args.dyhub.rstrip("/") + "/api/rooms"
    )
    checks["dyhub_rooms"] = {"ok": rooms_ok, "error": rooms_error}
    if rooms_ok and isinstance(rooms_payload, dict):
        rooms = rooms_payload.get("rooms") or []
    elif ok and isinstance(stats, dict):
        rooms = stats.get("rooms") or []
    live_rooms = [room for room in rooms if room.get("status") == "live"]
    gift_ready = [room for room in live_rooms if room.get("hasOwnCookie")]
    checks["live_rooms"] = live_rooms
    checks["gift_ready"] = bool(gift_ready)
    checks["gift_rooms"] = [room.get("roomId") for room in gift_ready]
    checks["bridge_ws_clients"] = (
        stats.get("wsClients") if isinstance(stats, dict) else None
    )
    if args.require_gift and not gift_ready:
        hard_fail = True

    if args.json:
        print(json.dumps(checks, ensure_ascii=False, indent=2))
    else:
        print(f"OLV: {'OK' if checks['olv']['ok'] else 'FAIL'} {checks['olv']['error']}")
        print(f"TTS: {'OK' if checks['tts']['ok'] else 'FAIL'} {checks['tts']['error']}")
        print(f"dyhub: {'OK' if checks['dyhub']['ok'] else 'FAIL'} {checks['dyhub']['error']}")
        print(f"live rooms: {len(live_rooms)}")
        for room in live_rooms:
            print(
                f"  - {room.get('roomId')} {room.get('meta', {}).get('nickname', '')} "
                f"cookie={room.get('hasOwnCookie')}"
            )
        print(f"gift ready: {checks['gift_ready']} rooms={checks['gift_rooms']}")
        print(f"bridge ws clients: {checks['bridge_ws_clients']}")
        if not gift_ready:
            print("WARN: 礼物采集未就绪，需要在 dyhub 给房间设置登录 Cookie")

    return 1 if hard_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
