"""Set a dyhub room cookie without printing the secret.

Examples:
    python scripts/set_dyhub_cookie.py --room-id 615688226618 --cookie-file cookie.txt
    Get-Content cookie.txt -Raw | python scripts/set_dyhub_cookie.py --room-id 615688226618
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_API = "http://127.0.0.1:8757"


def main() -> int:
    parser = argparse.ArgumentParser(description="Set a per-room dyhub cookie")
    parser.add_argument("--room-id", required=True)
    parser.add_argument("--cookie", default="", help="Cookie string; prefer --cookie-file")
    parser.add_argument("--cookie-file", default="")
    parser.add_argument("--api-base", default=DEFAULT_API)
    args = parser.parse_args()
    cookie = args.cookie.strip()
    if not cookie and args.cookie_file:
        cookie = Path(args.cookie_file).read_text(encoding="utf-8").strip()
    if not cookie:
        cookie = sys.stdin.read().strip()
    if not cookie:
        parser.error("没有 Cookie；请用 --cookie-file 或 stdin 提供")
    body = json.dumps({"cookie": cookie}).encode("utf-8")
    request = Request(
        f"{args.api_base.rstrip('/')}/api/rooms/{args.room_id}/cookie",
        data=body,
        method="PUT",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        print(f"dyhub HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:300]}")
        return 1
    except URLError as exc:
        print(f"dyhub unavailable: {exc.reason}")
        return 1
    # Never print the cookie itself.
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
