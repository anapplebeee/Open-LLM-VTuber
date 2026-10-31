"""Apply the tracked live-stage UI copy into the OLV frontend submodule."""

from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "web_tool" / "live-ui"
TARGET = ROOT / "frontend"
FILES = ("effects.js", "index.html", "live-view.html")


def main() -> int:
    missing = [name for name in FILES if not (SOURCE / name).is_file()]
    if missing:
        print(f"missing tracked UI files: {', '.join(missing)}")
        return 1
    TARGET.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        shutil.copy2(SOURCE / name, TARGET / name)
        print(f"copied {name} -> frontend/{name}")
    print("hard-refresh the browser with Ctrl+F5")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
