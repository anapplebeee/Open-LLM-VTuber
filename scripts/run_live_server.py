"""Start OLV for the local live stage without requiring the frontend submodule.

The normal entry point also initializes the bundled frontend. The stage page is
served separately and only needs the OLV WebSocket/API server.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import uvicorn

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.open_llm_vtuber.config_manager import read_yaml, validate_config
from src.open_llm_vtuber.server import WebSocketServer


async def main() -> None:
    config = validate_config(read_yaml(str(PROJECT_ROOT / "conf.yaml")))
    server = WebSocketServer(config=config)
    await server.initialize()
    system = config.system_config
    uvicorn_config = uvicorn.Config(
        app=server.app,
        host=system.host,
        port=system.port,
        log_level="info",
    )
    await uvicorn.Server(uvicorn_config).serve()


if __name__ == "__main__":
    asyncio.run(main())
