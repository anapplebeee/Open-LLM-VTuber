"""Export data/live-review.jsonl to a human-readable Markdown review."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/live-review.jsonl")
    parser.add_argument("--output", default="data/live-review.md")
    args = parser.parse_args()

    source = Path(args.input)
    target = Path(args.output)
    if not source.is_file():
        print(f"review log not found: {source}")
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = ["# 直播间复盘记录", ""]
    current_session = None
    for raw in source.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            continue
        session = record.get("session_id") or "normal"
        if session != current_session:
            current_session = session
            lines.extend([f"## 场次 `{session}`", ""])
        lines.append(f"### {record.get('ts', '')}")
        for event in record.get("events", []):
            user = event.get("user") or "未知观众"
            text = event.get("content") or ""
            if event.get("type") == "gift" and event.get("gift"):
                gift = event["gift"]
                text = f"送出 {gift.get('name')} ×{gift.get('count')}"
            elif event.get("type") == "join":
                text = "进入了直播间"
            lines.append(f"- **{user}**：{text}")
        reply = record.get("reply") or ""
        if reply:
            lines.append(f"- **小鲸**：{reply}")
        else:
            lines.append("- **小鲸**：（未回复）")
        lines.append("")

    target.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
