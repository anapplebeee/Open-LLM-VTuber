"""Remove known synthetic rows from data/live-review.jsonl.

The old test harness used timestamps around 1970 and a few fixed session ids.
Real live rows are preserved; a timestamped backup is written first.
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "data" / "live-review.jsonl"
TEST_SESSIONS = {
    "context-test",
    "chat-persist",
    "rundown-test",
    "proactive-test",
    "collector-test",
    "auto-begin",
}
TEST_CONTENTS = {"你好", "谢谢你，真棒", "今天聊什么"}


def is_test_row(record: dict) -> bool:
    if str(record.get("ts") or "").startswith("1970-01-01"):
        return True
    if str(record.get("session_id") or "") in TEST_SESSIONS:
        return True
    events = record.get("events") or []
    if events and all(
        str(event.get("user") or "") == "小蓝"
        and str(event.get("content") or "") in TEST_CONTENTS
        for event in events
    ):
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Prune synthetic live-review rows")
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--backup", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    path = Path(args.input)
    if not path.exists():
        print(f"not found: {path}")
        return 1
    backup = (
        Path(args.backup)
        if args.backup
        else path.with_name(
            path.stem + f".bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}" + path.suffix
        )
    )
    kept: list[str] = []
    removed = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            kept.append(line)
            continue
        if is_test_row(record):
            removed += 1
            continue
        kept.append(line)
    print(f"input={path} total_kept={len(kept)} removed={removed}")
    if args.dry_run:
        return 0
    shutil.copy2(path, backup)
    path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    print(f"backup={backup}")
    print(f"written={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
