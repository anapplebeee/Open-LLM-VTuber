import json
import os
from datetime import datetime
from uuid import uuid4

import numpy as np
import requests
from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile, WebSocket
from loguru import logger
from starlette.responses import JSONResponse, StreamingResponse
from starlette.websockets import WebSocketDisconnect
from urllib.parse import urlparse

from .agent.agents.live_streamer_agent import LiveStreamerAgent
from .live.events import RoomEvent
from .live.memory import MemoryManager, MemoryStore
from .live.output import LiveBrowserOutput
from .live.output_hub import LiveOutputHub
from .live.recap import SessionRecapWriter
from .live.runtime import LiveSession
from .live.store import LiveStore
from .proxy_handler import ProxyHandler
from .service_context import ServiceContext
from .websocket_handler import WebSocketHandler


def init_client_ws_route(default_context_cache: ServiceContext) -> APIRouter:
    """
    Create and return API routes for handling the `/client-ws` WebSocket connections.

    Args:
        default_context_cache: Default service context cache for new sessions.

    Returns:
        APIRouter: Configured router with WebSocket endpoint.
    """

    router = APIRouter()
    ws_handler = WebSocketHandler(default_context_cache)

    @router.websocket("/client-ws")
    async def websocket_endpoint(websocket: WebSocket):
        """WebSocket endpoint for client connections"""
        await websocket.accept()
        client_uid = str(uuid4())

        try:
            await ws_handler.handle_new_connection(websocket, client_uid)
            await ws_handler.handle_websocket_communication(websocket, client_uid)
        except WebSocketDisconnect:
            await ws_handler.handle_disconnect(client_uid)
        except Exception as e:
            logger.error(f"Error in WebSocket connection: {e}")
            await ws_handler.handle_disconnect(client_uid)
            raise

    @router.get("/song-audio")
    async def song_audio(url: str, request: Request):
        """Proxy NetEase CDN audio with a stable Referer for browser playback."""
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        allowed = host.endswith(".music.126.net") or host.endswith(".music.163.com")
        if parsed.scheme not in {"http", "https"} or not allowed:
            raise HTTPException(status_code=400, detail="unsupported song URL")

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
            ),
            "Referer": "https://music.163.com/",
        }
        range_header = request.headers.get("range")
        if range_header:
            headers["Range"] = range_header
        try:
            upstream = requests.get(
                url, headers=headers, stream=True, timeout=(5, 20), verify=False
            )
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail="song upstream unavailable") from exc
        if upstream.status_code not in (200, 206):
            upstream.close()
            raise HTTPException(status_code=502, detail="song upstream rejected")

        response_headers = {"Accept-Ranges": upstream.headers.get("accept-ranges", "bytes")}
        for key in ("content-range", "content-length"):
            if upstream.headers.get(key):
                response_headers[key.title()] = upstream.headers[key]

        def chunks():
            try:
                for chunk in upstream.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        yield chunk
            finally:
                upstream.close()

        return StreamingResponse(
            chunks(),
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "audio/mpeg"),
            headers=response_headers,
        )

    return router


def init_live_ws_route(default_context_cache: ServiceContext) -> APIRouter:
    """Create the event-driven live-room WebSocket endpoint.

    The endpoint accepts normalized RoomEvent or Bilibili payloads and uses the
    same OLV agent/TTS/Live2D pipeline as the regular client. It is intentionally
    separate from /client-ws because a live connection has no microphone or
    per-viewer history.
    """

    router = APIRouter()
    live_active = False
    live_session = None
    live_store = LiveStore()
    live_memory = MemoryManager(MemoryStore("data/xiaojing-memory.db"))
    live_output_hub = LiveOutputHub()

    async def send_live_view_config(websocket: WebSocket) -> None:
        """Bootstrap a read-only Live2D viewer with the model configuration."""
        context = default_context_cache
        await websocket.send_text(
            json.dumps(
                {
                    "type": "set-model-and-conf",
                    "model_info": context.live2d_model.model_info,
                    "conf_name": context.character_config.character_name,
                    "conf_uid": context.character_config.conf_uid,
                    "client_uid": str(uuid4()),
                }
            )
        )

    @router.websocket("/live-ws")
    async def live_websocket_endpoint(websocket: WebSocket):
        nonlocal live_active, live_session
        await websocket.accept()

        if live_active:
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "error",
                        "message": "another live session is already active",
                    }
                )
            )
            await websocket.close()
            return

        context = default_context_cache

        if not isinstance(context.agent_engine, LiveStreamerAgent):
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "error",
                        "message": (
                            "live-ws requires conversation_agent_choice: "
                            "live_streamer_agent"
                        ),
                    }
                )
            )
            await websocket.close()
            return

        live_active = True
        session_id = uuid4().hex
        output = LiveBrowserOutput(
            context,
            websocket.send_text,
            store=live_store,
            session_id=session_id,
            broadcast_send=live_output_hub.broadcast,
        )
        recap_writer = SessionRecapWriter(
            live_store,
            getattr(context.agent_engine, "llm", None),
        )
        session = LiveSession(
            agent=context.agent_engine,
            on_output=output.send,
            on_song=output.send_event,
            on_reply=output.send_event,
            on_batch_end=output.end_batch,
            on_close=output.close,
            session_id=session_id,
            store=live_store,
            recap_writer=recap_writer,
            memory=live_memory,
        )
        live_session = session
        try:
            try:
                await session.start()
            except Exception as exc:
                await websocket.send_text(
                    json.dumps({"type": "error", "message": f"live start failed: {exc}"})
                )
                raise
            while True:
                raw = await websocket.receive_text()
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    await websocket.send_text(
                        json.dumps({"type": "error", "message": "invalid JSON"})
                    )
                    continue
                if not isinstance(message, dict):
                    await websocket.send_text(
                        json.dumps(
                            {"type": "error", "message": "live message must be an object"}
                        )
                    )
                    continue

                message_type = message.get("type")
                if message_type == "bilibili-start":
                    try:
                        from .live.collectors.bilibili_official import (
                            BilibiliOfficialConfig,
                        )

                        raw_config = message.get("config", {})
                        config = (
                            BilibiliOfficialConfig.from_env()
                            if raw_config.get("from_env")
                            else BilibiliOfficialConfig(**raw_config)
                        )
                        await session.start_bilibili(config)
                    except Exception as exc:  # noqa: BLE001 - report config/runtime failure
                        await websocket.send_text(
                            json.dumps({"type": "error", "message": str(exc)})
                        )
                        continue
                    accepted = True
                elif message_type == "room-event":
                    try:
                        event = RoomEvent.model_validate(message.get("event", {}))
                    except Exception as exc:  # noqa: BLE001 - report validation error
                        await websocket.send_text(
                            json.dumps({"type": "error", "message": str(exc)})
                        )
                        continue
                    accepted = session.ingest(event)
                elif message_type == "bilibili-event":
                    try:
                        accepted = session.ingest_bilibili(message.get("payload", {}))
                    except Exception as exc:  # noqa: BLE001 - malformed payload
                        await websocket.send_text(
                            json.dumps({"type": "error", "message": str(exc)})
                        )
                        continue
                elif message_type == "douyin-event":
                    try:
                        accepted = session.ingest_douyin(message.get("payload", {}))
                    except Exception as exc:  # noqa: BLE001 - malformed payload
                        await websocket.send_text(
                            json.dumps({"type": "error", "message": str(exc)})
                        )
                        continue
                elif message_type == "rundown-load":
                    try:
                        snapshot = session.load_rundown(
                            message.get("segments", []),
                            auto_begin=bool(message.get("auto_begin", False)),
                        )
                    except Exception as exc:  # noqa: BLE001 - report validation error
                        await websocket.send_text(
                            json.dumps({"type": "error", "message": str(exc)})
                        )
                        continue
                    await websocket.send_text(
                        json.dumps({"type": "rundown-status", **snapshot})
                    )
                    continue
                elif message_type == "proactive":
                    try:
                        snapshot = session.set_proactive(
                            enabled=message.get("enabled"),
                            interval_ms=message.get("interval_ms"),
                        )
                    except Exception as exc:  # noqa: BLE001 - report config error
                        await websocket.send_text(
                            json.dumps({"type": "error", "message": str(exc)})
                        )
                        continue
                    await websocket.send_text(
                        json.dumps({"type": "proactive-status", **snapshot})
                    )
                    continue
                elif message_type == "rundown-control":
                    try:
                        snapshot = session.control_rundown(
                            str(message.get("action", "")),
                            by=str(message.get("by", "operator")),
                        )
                    except Exception as exc:  # noqa: BLE001 - report control error
                        await websocket.send_text(
                            json.dumps({"type": "error", "message": str(exc)})
                        )
                        continue
                    await websocket.send_text(
                        json.dumps({"type": "rundown-status", **snapshot})
                    )
                    continue
                elif message_type == "notebook":
                    try:
                        if message.get("action", "read") == "set":
                            content = session.set_notebook(str(message.get("content", "")))
                        elif message.get("action", "read") == "clear":
                            content = session.set_notebook("")
                        else:
                            content = session.read_notebook()
                    except Exception as exc:  # noqa: BLE001 - report notebook error
                        await websocket.send_text(
                            json.dumps({"type": "error", "message": str(exc)})
                        )
                        continue
                    await websocket.send_text(
                        json.dumps({"type": "notebook-status", "content": content})
                    )
                    continue
                elif message_type == "status":
                    await websocket.send_text(
                        json.dumps({"type": "live-status", **session.status()})
                    )
                    continue
                elif message_type == "stop":
                    break
                else:
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "error",
                                "message": f"unsupported live message: {message_type}",
                            }
                        )
                    )
                    continue

                await websocket.send_text(
                    json.dumps({"type": "live-event-ack", "accepted": accepted})
                )
        except WebSocketDisconnect:
            logger.info("Live WebSocket disconnected")
        except Exception:  # noqa: BLE001 - always release the single-session flag
            logger.exception("Live WebSocket session failed")
        finally:
            try:
                await session.stop()
            finally:
                live_session = None
                live_active = False

    @router.websocket("/live-view-ws")
    async def live_view_websocket_endpoint(websocket: WebSocket):
        """Read-only browser viewer for live audio/Live2D output.

        The dyhub bridge or another event producer connects to ``/live-ws``;
        this socket only receives output and never submits live events.
        """
        await websocket.accept()
        await send_live_view_config(websocket)
        await live_output_hub.register(websocket)
        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(message, dict):
                    continue
                if message.get("type") == "heartbeat":
                    await websocket.send_text(
                        json.dumps({"type": "heartbeat-ack"})
                    )
                elif message.get("type") == "song-play-target":
                    if live_session is not None:
                        await live_session.play_song_target(
                            str(message.get("title") or ""),
                            str(message.get("entry_id") or ""),
                        )
                elif message.get("type") == "song-finished":
                    if live_session is not None:
                        await live_session.finish_song(
                            str(message.get("title") or ""),
                            str(message.get("entry_id") or ""),
                        )
                elif message.get("type") == "request-init-config":
                    await send_live_view_config(websocket)
        except WebSocketDisconnect:
            logger.info("Live view WebSocket disconnected")
        finally:
            await live_output_hub.unregister(websocket)

    return router


def init_proxy_route(server_url: str) -> APIRouter:
    """
    Create and return API routes for handling proxy connections.

    Args:
        server_url: The WebSocket URL of the actual server

    Returns:
        APIRouter: Configured router with proxy WebSocket endpoint
    """
    router = APIRouter()
    proxy_handler = ProxyHandler(server_url)

    @router.websocket("/proxy-ws")
    async def proxy_endpoint(websocket: WebSocket):
        """WebSocket endpoint for proxy connections"""
        try:
            await proxy_handler.handle_client_connection(websocket)
        except Exception as e:
            logger.error(f"Error in proxy connection: {e}")
            raise

    return router


def init_webtool_routes(default_context_cache: ServiceContext) -> APIRouter:
    """
    Create and return API routes for handling web tool interactions.

    Args:
        default_context_cache: Default service context cache for new sessions.

    Returns:
        APIRouter: Configured router with WebSocket endpoint.
    """

    router = APIRouter()

    @router.get("/web-tool")
    async def web_tool_redirect():
        """Redirect /web-tool to /web_tool/index.html"""
        return Response(status_code=302, headers={"Location": "/web-tool/index.html"})

    @router.get("/web_tool")
    async def web_tool_redirect_alt():
        """Redirect /web_tool to /web_tool/index.html"""
        return Response(status_code=302, headers={"Location": "/web-tool/index.html"})

    @router.get("/live2d-models/info")
    async def get_live2d_folder_info():
        """Get information about available Live2D models"""
        live2d_dir = "live2d-models"
        if not os.path.exists(live2d_dir):
            return JSONResponse(
                {"error": "Live2D models directory not found"}, status_code=404
            )

        valid_characters = []
        supported_extensions = [".png", ".jpg", ".jpeg"]

        for entry in os.scandir(live2d_dir):
            if entry.is_dir():
                folder_name = entry.name.replace("\\", "/")
                model3_file = os.path.join(
                    live2d_dir, folder_name, f"{folder_name}.model3.json"
                ).replace("\\", "/")

                if os.path.isfile(model3_file):
                    # Find avatar file if it exists
                    avatar_file = None
                    for ext in supported_extensions:
                        avatar_path = os.path.join(
                            live2d_dir, folder_name, f"{folder_name}{ext}"
                        )
                        if os.path.isfile(avatar_path):
                            avatar_file = avatar_path.replace("\\", "/")
                            break

                    valid_characters.append(
                        {
                            "name": folder_name,
                            "avatar": avatar_file,
                            "model_path": model3_file,
                        }
                    )
        return JSONResponse(
            {
                "type": "live2d-models/info",
                "count": len(valid_characters),
                "characters": valid_characters,
            }
        )

    @router.post("/asr")
    async def transcribe_audio(file: UploadFile = File(...)):
        """
        Endpoint for transcribing audio using the ASR engine
        """
        logger.info(f"Received audio file for transcription: {file.filename}")

        try:
            contents = await file.read()

            # Validate minimum file size
            if len(contents) < 44:  # Minimum WAV header size
                raise ValueError("Invalid WAV file: File too small")

            # Decode the WAV header and get actual audio data
            wav_header_size = 44  # Standard WAV header size
            audio_data = contents[wav_header_size:]

            # Validate audio data size
            if len(audio_data) % 2 != 0:
                raise ValueError("Invalid audio data: Buffer size must be even")

            # Convert to 16-bit PCM samples to float32
            try:
                audio_array = (
                    np.frombuffer(audio_data, dtype=np.int16).astype(np.float32)
                    / 32768.0
                )
            except ValueError as e:
                raise ValueError(
                    f"Audio format error: {e!s}. Please ensure the file is 16-bit PCM WAV format."
                )

            # Validate audio data
            if len(audio_array) == 0:
                raise ValueError("Empty audio data")

            text = await default_context_cache.asr_engine.async_transcribe_np(
                audio_array
            )
            logger.info(f"Transcription result: {text}")
            return {"text": text}

        except ValueError as e:
            logger.error(f"Audio format error: {e}")
            return Response(
                content=json.dumps({"error": str(e)}),
                status_code=400,
                media_type="application/json",
            )
        except Exception as e:
            logger.error(f"Error during transcription: {e}")
            return Response(
                content=json.dumps(
                    {"error": "Internal server error during transcription"}
                ),
                status_code=500,
                media_type="application/json",
            )

    @router.websocket("/tts-ws")
    async def tts_endpoint(websocket: WebSocket):
        """WebSocket endpoint for TTS generation"""
        await websocket.accept()
        logger.info("TTS WebSocket connection established")

        try:
            while True:
                data = await websocket.receive_json()
                text = data.get("text")
                if not text:
                    continue

                logger.info(f"Received text for TTS: {text}")

                # Split text into sentences
                sentences = [s.strip() for s in text.split(".") if s.strip()]

                try:
                    # Generate and send audio for each sentence
                    for sentence in sentences:
                        sentence = sentence + "."  # Add back the period
                        file_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{str(uuid4())[:8]}"
                        audio_path = (
                            await default_context_cache.tts_engine.async_generate_audio(
                                text=sentence, file_name_no_ext=file_name
                            )
                        )
                        logger.info(
                            f"Generated audio for sentence: {sentence} at: {audio_path}"
                        )

                        await websocket.send_json(
                            {
                                "status": "partial",
                                "audioPath": audio_path,
                                "text": sentence,
                            }
                        )

                    # Send completion signal
                    await websocket.send_json({"status": "complete"})

                except Exception as e:
                    logger.error(f"Error generating TTS: {e}")
                    await websocket.send_json({"status": "error", "message": str(e)})

        except WebSocketDisconnect:
            logger.info("TTS WebSocket client disconnected")
        except Exception as e:
            logger.error(f"Error in TTS WebSocket connection: {e}")
            await websocket.close()

    return router
