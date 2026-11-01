"""Native Douyin live collector (Python data plane, Node signature only).

Replaces dyhub's data plane: this module does **not** start a dyhub HTTP/WS
server and does **not** use ``scripts/dyhub_bridge.py``.  HTTP, WebSocket,
protobuf decoding and reconnects all run in Python.  The only Node.js usage is
the signature helper ``scripts/douyin_sign.mjs``, which loads the untouched
third-party webmssdk bundle from
``tools/dyhub-main/third_party/douyin-sign/sign.js`` to compute the WebSocket
``signature``.  Node is a signature oracle, not a data plane.

Protocol chain (ported from dyhub ``lightweightSession.ts``):

    cookie chain (ttwid / __ac_nonce / __ac_signature)
      -> web_rid -> webcast room id (live.douyin.com page, status 2 live / 4 closed)
      -> X-Bogus signature (Node helper)
      -> wss push v2 handshake with heartbeat every 5s and Response acks
      -> PushFrame -> gzip payload -> Response -> Message[] -> Chat/Gift/Member
      -> canonical :class:`open_llm_vtuber.live.events.RoomEvent`

Nothing here writes cookies to logs; pass cookies via the constructor or the
CLI ``--cookie`` flag (or ``DOUYIN_COOKIE``) and they stay in memory.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import inspect
import os
import re
import shutil
import time
import zlib
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

import aiohttp
import websockets
from loguru import logger
from pydantic import ValidationError

from ..events import GiftInfo, RoomEvent, RoomUser

__all__ = [
    "HEARTBEAT_FRAME",
    "DecodedFrame",
    "DouyinCollectorError",
    "NativeDouyinCollector",
    "NodeUnavailableError",
    "ProtoDecodeError",
    "RawProtoMessage",
    "RoomClosedError",
    "RoomMeta",
    "RoomResolveError",
    "SignatureError",
    "build_ac_signature",
    "build_signature_md5",
    "build_wss_url",
    "decode_frame",
    "decode_message_body",
    "encode_push_frame",
    "get_douyin_signature",
    "map_raw_message",
    "normalize_web_rid",
    "parse_room_id",
    "parse_room_meta",
    "parse_room_status",
    "resolve_node_executable",
]

_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0"
)

# Browser UA used only for the signed query string's browser_version component.
_SIGN_BROWSER_VERSION = (
    "5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

_WSS_ENDPOINT = "wss://webcast100-ws-web-lq.douyin.com/webcast/im/push/v2/"

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_SIGN_SCRIPT = _PROJECT_ROOT / "scripts" / "douyin_sign.mjs"

# Fallback used on this workstation; NODE (or DOUYIN_NODE) / PATH win first.
_DEFAULT_NODE_PATH = Path(
    r"C:\code\ailive\tools\netease\node_modules\node-win-x64\bin\node.exe"
)

ROOM_STATUS_LIVE = 2
ROOM_STATUS_CLOSED = 4

_COOKIE_TTL_SECONDS = 15 * 60
_COOKIE_CHAIN_ATTEMPTS = 3
_FRAME_MAX_SIZE = 16 * 1024 * 1024
_DEDUPE_WINDOW = 1000

_METHOD_CHAT = "WebcastChatMessage"
_METHOD_GIFT = "WebcastGiftMessage"
_METHOD_MEMBER = "WebcastMemberMessage"


def _default_now_ms() -> int:
    return int(time.time() * 1000)


class DouyinCollectorError(RuntimeError):
    """Base error for the native Douyin collector."""


class NodeUnavailableError(DouyinCollectorError):
    """The Node.js executable required for signing is unavailable."""


class SignatureError(DouyinCollectorError):
    """The Node signature helper failed or returned unusable output."""


class CookieError(DouyinCollectorError):
    """The cookie chain could not obtain ``ttwid``/``__ac_nonce``."""


class RoomResolveError(DouyinCollectorError):
    """The live room page could not be mapped to a webcast room id."""


class RoomClosedError(DouyinCollectorError):
    """The room is not currently collectable (status 4 = finished)."""

    def __init__(self, web_rid: str, status: int | None) -> None:
        self.web_rid = web_rid
        self.status = status
        if status == ROOM_STATUS_CLOSED:
            detail = "直播间已结束"
        elif status is None:
            detail = "直播间状态未知"
        else:
            detail = f"直播间当前不可采集(status={status})"
        super().__init__(f"{web_rid}: {detail}")


class ProtoDecodeError(ValueError):
    """A Webcast protobuf frame is truncated or uses an unsupported wire type."""


# ---------------------------------------------------------------------------
# Minimal protobuf wire decoder
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RawProtoMessage:
    """One ``Message`` inside a decoded ``Response``."""

    method: str
    msg_id: int
    payload: bytes


@dataclass(frozen=True)
class DecodedFrame:
    """A decoded ``PushFrame`` plus any webcast messages it carried."""

    payload_type: str
    log_id: int
    headers: Mapping[str, str]
    messages: tuple[RawProtoMessage, ...]
    need_ack: bool = False
    internal_ext: str = ""


def _read_varint(data: bytes, offset: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        if offset >= len(data):
            raise ProtoDecodeError("truncated varint")
        byte = data[offset]
        offset += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, offset
        shift += 7
        if shift > 70:
            raise ProtoDecodeError("varint is too long")


def _parse_fields(data: bytes) -> dict[int, list[int | bytes | None]]:
    """Parse protobuf fields into ``{field_number: [values...]}``.

    Varints become ``int``, length-delimited values become ``bytes`` and
    fixed32/fixed64 values become ``None`` (no webcast field we consume uses
    them).  Unknown fields are skipped, which is required for forward
    compatibility with Douyin's evolving schema.
    """

    fields: dict[int, list[int | bytes | None]] = {}
    offset = 0
    length = len(data)
    while offset < length:
        key, offset = _read_varint(data, offset)
        field_number = key >> 3
        wire_type = key & 0x07
        if field_number <= 0:
            raise ProtoDecodeError("invalid field number")
        value: int | bytes | None
        if wire_type == 0:
            value, offset = _read_varint(data, offset)
        elif wire_type == 1:
            if offset + 8 > length:
                raise ProtoDecodeError("truncated fixed64 field")
            value = None
            offset += 8
        elif wire_type == 2:
            size, offset = _read_varint(data, offset)
            end = offset + size
            if end > length:
                raise ProtoDecodeError("truncated length-delimited field")
            value = data[offset:end]
            offset = end
        elif wire_type == 5:
            if offset + 4 > length:
                raise ProtoDecodeError("truncated fixed32 field")
            value = None
            offset += 4
        else:
            raise ProtoDecodeError(f"unsupported wire type {wire_type}")
        fields.setdefault(field_number, []).append(value)
    return fields


def _first_field(fields: Mapping[int, list[Any]], number: int) -> Any:
    values = fields.get(number)
    return values[0] if values else None


def _field_uint(fields: Mapping[int, list[Any]], number: int, default: int = 0) -> int:
    value = _first_field(fields, number)
    return value if isinstance(value, int) else default


def _field_int64(fields: Mapping[int, list[Any]], number: int, default: int = 0) -> int:
    value = _first_field(fields, number)
    if not isinstance(value, int):
        return default
    return value - (1 << 64) if value >= (1 << 63) else value


def _field_bytes(fields: Mapping[int, list[Any]], number: int) -> bytes | None:
    value = _first_field(fields, number)
    return value if isinstance(value, bytes) else None


def _field_str(fields: Mapping[int, list[Any]], number: int, default: str = "") -> str:
    value = _field_bytes(fields, number)
    if value is None:
        return default
    return value.decode("utf-8", errors="replace")


def _gunzip(payload: bytes) -> bytes:
    try:
        return gzip.decompress(payload)
    except (OSError, EOFError, zlib.error) as exc:
        raise ProtoDecodeError("invalid gzip payload") from exc


def decode_frame(buffer: bytes | bytearray | memoryview) -> DecodedFrame:
    """Decode one ``PushFrame`` WebSocket binary frame.

    The gzip layer is applied when ``headers['compress_type'] == 'gzip'`` or
    when the payload itself starts with the gzip magic bytes.  A frame that
    carries ``payloadType`` ``msg``/``push`` is decoded as ``Response`` and its
    ``Message`` entries are returned undecoded (see :func:`decode_message_body`).
    """

    data = bytes(buffer)
    if not data:
        raise ProtoDecodeError("empty frame")
    fields = _parse_fields(data)
    payload_type = _field_str(fields, 7)
    log_id = _field_uint(fields, 2)

    headers: dict[str, str] = {}
    for item in fields.get(5, []):
        if not isinstance(item, bytes):
            continue
        header_fields = _parse_fields(item)
        key = _field_str(header_fields, 1)
        if key:
            headers[key] = _field_str(header_fields, 2)

    payload = _field_bytes(fields, 8) or b""
    if payload and (
        headers.get("compress_type") == "gzip" or payload.startswith(b"\x1f\x8b")
    ):
        payload = _gunzip(payload)

    messages: list[RawProtoMessage] = []
    need_ack = False
    internal_ext = ""
    if payload_type in {"msg", "push"} and payload:
        response_fields = _parse_fields(payload)
        internal_ext = _field_str(response_fields, 5)
        need_ack = bool(_field_uint(response_fields, 9))
        for item in response_fields.get(1, []):
            if not isinstance(item, bytes):
                continue
            message_fields = _parse_fields(item)
            method = _field_str(message_fields, 1)
            if not method:
                continue
            messages.append(
                RawProtoMessage(
                    method=method,
                    msg_id=_field_int64(message_fields, 3),
                    payload=_field_bytes(message_fields, 2) or b"",
                )
            )

    return DecodedFrame(
        payload_type=payload_type,
        log_id=log_id,
        headers=headers,
        messages=tuple(messages),
        need_ack=need_ack,
        internal_ext=internal_ext,
    )


def _decode_common(fields: Mapping[int, list[Any]]) -> dict[str, Any]:
    raw = _field_bytes(fields, 1)
    if not raw:
        return {}
    common_fields = _parse_fields(raw)
    return {
        "method": _field_str(common_fields, 1),
        "msg_id": _field_uint(common_fields, 2),
        "room_id": _field_uint(common_fields, 3),
        "create_time": _field_uint(common_fields, 4),
    }


def _decode_user(
    fields: Mapping[int, list[Any]], field_number: int = 2
) -> dict[str, Any]:
    raw = _field_bytes(fields, field_number)
    if not raw:
        return {}
    user_fields = _parse_fields(raw)
    return {
        "id": _field_uint(user_fields, 1),
        "nickname": _field_str(user_fields, 3),
        "sec_uid": _field_str(user_fields, 46),
        "id_str": _field_str(user_fields, 1029),
    }


def _decode_gift(fields: Mapping[int, list[Any]]) -> dict[str, Any]:
    raw = _field_bytes(fields, 15)
    if not raw:
        return {}
    gift_fields = _parse_fields(raw)
    return {
        "name": _field_str(gift_fields, 16),
        "id": _field_int64(gift_fields, 5),
        "diamond_count": _field_int64(gift_fields, 12),
    }


def decode_message_body(message: RawProtoMessage) -> dict[str, Any] | None:
    """Decode a supported webcast message body; ``None`` for ignored methods."""

    if message.method not in {_METHOD_CHAT, _METHOD_GIFT, _METHOD_MEMBER}:
        return None
    try:
        fields = _parse_fields(message.payload)
        if message.method == _METHOD_CHAT:
            return {
                "common": _decode_common(fields),
                "user": _decode_user(fields),
                "content": _field_str(fields, 3),
            }
        if message.method == _METHOD_GIFT:
            return {
                "common": _decode_common(fields),
                # GiftMessage.user is field 7, unlike Chat/Member (field 2).
                "user": _decode_user(fields, 7),
                "gift": _decode_gift(fields),
                "gift_id": _field_int64(fields, 2),
                "group_count": _field_int64(fields, 4),
                "repeat_count": _field_int64(fields, 5),
                "combo_count": _field_int64(fields, 6),
                "repeat_end": bool(_field_uint(fields, 9)),
                "group_id": _field_int64(fields, 11),
                "log_id": _field_str(fields, 16),
                "send_type": _field_int64(fields, 17),
                "send_time": _field_int64(fields, 33),
                "trace_id": _field_str(fields, 35),
            }
        return {
            "common": _decode_common(fields),
            "user": _decode_user(fields),
            "member_count": _field_int64(fields, 3),
        }
    except ProtoDecodeError:
        return None


# ---------------------------------------------------------------------------
# Outbound protobuf encoding (heartbeat / ack only)
# ---------------------------------------------------------------------------


def _encode_varint(value: int) -> bytes:
    if value < 0:
        value += 1 << 64
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _encode_varint_field(number: int, value: int) -> bytes:
    return _encode_varint(number << 3) + _encode_varint(value)


def _encode_bytes_field(number: int, value: bytes) -> bytes:
    return _encode_varint((number << 3) | 2) + _encode_varint(len(value)) + value


def encode_push_frame(
    *, log_id: int = 0, payload_type: str = "", payload: bytes = b""
) -> bytes:
    """Encode the small ``PushFrame`` shape needed for hb/ack frames."""

    out = bytearray()
    if log_id:
        out += _encode_varint_field(2, log_id)
    if payload_type:
        out += _encode_bytes_field(7, payload_type.encode("utf-8"))
    if payload:
        out += _encode_bytes_field(8, payload)
    return bytes(out)


HEARTBEAT_FRAME = encode_push_frame(payload_type="hb")


# ---------------------------------------------------------------------------
# Douyin page metadata / room status parsing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoomMeta:
    """Best-effort anchor information parsed from the live room HTML."""

    nickname: str = ""
    avatar: str = ""
    title: str = ""


def _html_unescape(text: str) -> str:
    return (
        text.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&amp;", "&")
    )


def _clean_embedded_html(html: str) -> str:
    return _html_unescape(html.replace('\\"', '"'))


def parse_room_status(html: str) -> int | None:
    """Parse ``roomStore.roomInfo.room.status`` (2 = live, 4 = closed).

    Returns ``None`` when the field is absent.  Callers follow dyhub's
    whitelist policy: anything other than 2 is treated as not collectable.
    """

    clean = html.replace('\\"', '"').replace("&quot;", '"')
    patterns = (
        (
            r'"roomStore"\s*:\s*\{\s*"roomInfo"\s*:\s*\{\s*"room"\s*:\s*\{'
            r'\s*"id_str"\s*:\s*"[^"]*"\s*,\s*"status"\s*:\s*(\d+)'
        ),
        r'"room"\s*:\s*\{\s*"id_str"\s*:\s*"[^"]*"\s*,\s*"status"\s*:\s*(\d+)',
    )
    for pattern in patterns:
        match = re.search(pattern, clean)
        if match:
            return int(match.group(1))
    return None


def parse_room_meta(html: str) -> RoomMeta | None:
    """Extract anchor nickname/avatar/title; returns ``None`` when absent."""

    clean = _clean_embedded_html(html)

    def first_value(key: str, max_length: int) -> str:
        pattern = rf'"{key}"\s*:\s*"([^"]{{1,{max_length}}})"'
        for match in re.finditer(pattern, clean):
            value = match.group(1)
            # The page template embeds placeholders before the real room data.
            if value not in {"$undefined", "undefined", "null"}:
                return value
        return ""

    nickname = first_value("nickname", 80)
    title = first_value("title", 200)
    if not nickname and not title:
        return None
    avatar_match = re.search(r'"avatar"\s*:\s*"(https:[^"]+?)"', clean)
    return RoomMeta(
        nickname=nickname,
        avatar=avatar_match.group(1) if avatar_match else "",
        title=title,
    )


def parse_room_id(html: str) -> str | None:
    """Extract the numeric webcast room id from a live room HTML page."""

    clean = html.replace("&quot;", '"').replace("&amp;", "&")
    match = re.search(r'roomId\\?"\s*[:=]\s*\\?"?(\d+)', clean)
    return match.group(1) if match else None


def normalize_web_rid(value: str | int) -> str:
    """Accept a bare web_rid or a ``https://live.douyin.com/<rid>`` URL."""

    text = str(value).strip()
    if not text:
        raise ValueError("Douyin web_rid is required")
    candidate = text.split("?", 1)[0].rstrip("/")
    if candidate.isdigit():
        return candidate
    match = re.search(r"live\.douyin\.com/(\d+)", text)
    if match:
        return match.group(1)
    match = re.search(r"(\d{5,})", text)
    if match:
        return match.group(1)
    raise ValueError("invalid Douyin web_rid")


# ---------------------------------------------------------------------------
# Signature / cookie helpers
# ---------------------------------------------------------------------------


def resolve_node_executable(
    explicit: str | os.PathLike[str] | None = None,
) -> str:
    """Resolve the Node binary used for signing.

    Order: explicit argument, ``NODE``, ``DOUYIN_NODE``, ``PATH``, then the
    workstation fallback path.  Node is only a signature oracle.
    """

    candidates = (
        explicit,
        os.environ.get("NODE"),
        os.environ.get("DOUYIN_NODE"),
        shutil.which("node"),
        _DEFAULT_NODE_PATH,
    )
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if path.is_file():
            return str(path)
    raise NodeUnavailableError(
        "Node.js is required only for the Douyin WebSocket signature "
        "(scripts/douyin_sign.mjs); set NODE to a node executable"
    )


async def get_douyin_signature(
    md5_hex: str,
    *,
    node_path: str | os.PathLike[str] | None = None,
    sign_script: str | os.PathLike[str] | None = None,
    timeout: float = 20.0,
) -> str:
    """Run ``scripts/douyin_sign.mjs <md5>`` and return the X-Bogus signature."""

    if not re.fullmatch(r"[0-9a-fA-F]{32}", md5_hex):
        raise SignatureError("signature input must be a 32-character MD5 hex digest")
    script = Path(
        sign_script or os.environ.get("DOUYIN_SIGN_SCRIPT") or _DEFAULT_SIGN_SCRIPT
    )
    if not script.is_file():
        raise SignatureError(f"Douyin signature helper not found: {script}")
    executable = resolve_node_executable(node_path)
    try:
        process = await asyncio.create_subprocess_exec(
            executable,
            str(script),
            md5_hex.lower(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(_PROJECT_ROOT),
        )
    except OSError as exc:
        raise NodeUnavailableError(
            f"cannot start Node.js for Douyin signing: {exc}"
        ) from exc
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise SignatureError(
            f"Douyin signature helper timed out after {timeout:.0f}s"
        ) from exc
    except asyncio.CancelledError:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise
    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()
        detail = " ".join(detail.split())[:300]
        raise SignatureError(
            f"Douyin signature helper failed (exit {process.returncode}): {detail}"
        )
    signature = stdout.decode("utf-8", errors="replace").strip()
    if not signature or any(char.isspace() for char in signature):
        raise SignatureError("Douyin signature helper returned empty/invalid output")
    return signature


def build_signature_md5(room_id: str) -> str:
    """MD5 over the fixed webcast handshake parameter string (dyhub-compatible)."""

    params = (
        "live_id=1,aid=6383,version_code=180800,"
        "webcast_sdk_version=1.0.14-beta.0,"
        f"room_id={room_id},sub_room_id=,sub_channel_id=,did_rule=3,"
        f"user_unique_id={room_id},device_platform=web,device_type=,"
        "ac=,identity=audience"
    )
    return hashlib.md5(params.encode("utf-8")).hexdigest()


def _encode_uri_component(value: str) -> str:
    # JavaScript encodeURIComponent leaves these characters unescaped.
    return quote(value, safe="-_.!~*'()")


def build_wss_url(room_id: str, signature: str, *, now_ms: int | None = None) -> str:
    """Build the webcast push v2 URL, preserving dyhub's parameter order."""

    if not signature:
        raise ValueError("Douyin signature is required")
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    cursor = f"r-{room_id}_d-1_u-1_fh-{room_id}_t-{now}"
    internal_ext = (
        f"internal_src:dim|wss_push_room_id:{room_id}|"
        f"wss_push_did:{room_id}|first_req_ms:{now}|fetch_time:{now}|"
        f"seq:1|wss_info:0-{now}-0-0|wrds_v:{room_id}"
    )
    params = [
        "app_name=douyin_web",
        "version_code=180800",
        "webcast_sdk_version=1.0.14-beta.0",
        "update_version_code=1.0.14-beta.0",
        "compress=gzip",
        "device_platform=web",
        "cookie_enabled=true",
        "screen_width=1536",
        "screen_height=864",
        "browser_language=zh-CN",
        "browser_platform=Win32",
        "browser_name=Mozilla",
        f"browser_version={_encode_uri_component(_SIGN_BROWSER_VERSION)}",
        "browser_online=true",
        "tz_name=Asia/Shanghai",
        f"cursor={_encode_uri_component(cursor)}",
        f"internal_ext={_encode_uri_component(internal_ext)}",
        "host=https://live.douyin.com",
        "aid=6383",
        "live_id=1",
        "did_rule=3",
        "endpoint=live_pc",
        "support_wrds=1",
        f"user_unique_id={room_id}",
        "im_path=/webcast/im/fetch/",
        "identity=audience",
        "need_persist_msg_count=15",
        "insert_task_id=",
        "live_reason=",
        f"room_id={room_id}",
        "heartbeatDuration=0",
    ]
    return f"{_WSS_ENDPOINT}?{'&'.join(params)}&signature={signature}"


def build_ac_signature(site: str, nonce: str, user_agent: str, timestamp: int) -> str:
    """Compute ``__ac_signature`` (port of dyhub ``acSignature``).

    This is the small deterministic *cookie* helper from
    ``lightweightSession.ts``; the obfuscated WebSocket X-Bogus signer is still
    delegated to Node (``scripts/douyin_sign.mjs``).

    The dyhub/upstream JavaScript masks with ``& 0xffffffff``, which produces a
    *signed* 32-bit integer.  ``cal2`` uses that value as ``k % length`` and as
    ``charCodeAt`` index, so a negative value triggers JS ``NaN`` coercion.
    The local ``_to_int32`` / ``_js_remainder`` helpers reproduce that behavior
    exactly; without them the signature differs from the Node/sign.js path.
    """

    def _to_int32(value: int) -> int:
        value &= 0xFFFFFFFF
        return value - (1 << 32) if value >= (1 << 31) else value

    def _js_remainder(value: int, divisor: int) -> int:
        remainder = abs(value) % divisor
        return -remainder if value < 0 else remainder

    def cal1(text: str, initial: int) -> int:
        value = initial
        for char in text:
            value = ((value ^ ord(char)) * 65599) & 0xFFFFFFFF
        return _to_int32(value)

    def cal2(text: str, initial: int) -> int:
        value = _to_int32(initial)
        length = len(text)
        if length == 0:
            return value
        for _ in range(32):
            index = _js_remainder(value, length)
            if index < 0:
                # charCodeAt(<0) is NaN and NaN & 0xffffffff is 0 in JS.
                value = 0
                continue
            value = _to_int32(value * 65599 + ord(text[index]))
        return value

    def cal3(text: str, initial: int) -> int:
        value = initial
        for char in text:
            value = (value * 65599 + ord(char)) & 0xFFFFFFFF
        return _to_int32(value)

    def encode_char(value: int) -> str:
        if value < 26:
            return chr(value + 65)
        if value < 52:
            return chr(value + 71)
        if value < 62:
            return chr(value - 4)
        return chr(value - 17)

    def encode(value: int) -> str:
        return "".join(
            encode_char((value >> shift) & 63) for shift in range(24, -1, -6)
        )

    s1 = cal1(site, 0)
    s3 = cal1(site, 0)
    s5 = cal2(site, 0)
    s2 = cal2(user_agent, s1)
    s4 = cal3(user_agent, s3)
    s6 = cal3(user_agent, s5)
    s7 = (s1 + s2) & 0xFFFFFFFF
    s8 = (s3 + s4) & 0xFFFFFFFF
    s9 = (s5 + s6) & 0xFFFFFFFF
    s10 = (s7 + s8) & 0xFFFFFFFF
    s11 = (s8 + s9) & 0xFFFFFFFF
    s12 = (s10 + s11) & 0xFFFFFFFF
    s13 = (s1 + s4 + s5 + s7 + s9 + s11) & 0xFFFFFFFF
    s14 = (s2 + s3 + s6 + s8 + s10 + s12) & 0xFFFFFFFF
    s15 = (s13 + s14) & 0xFFFFFFFF
    masked_timestamp = timestamp & 0xFFFFFFFF
    return (
        encode(masked_timestamp) + encode(s12) + encode(s15) + encode(masked_timestamp)
    )


class _CookieJar:
    """Small in-memory cookie jar; never logs values."""

    def __init__(self, cookie: str = "") -> None:
        self._cookies: OrderedDict[str, str] = OrderedDict()
        if cookie and cookie.strip():
            self.absorb_cookie_header(cookie)

    def __repr__(self) -> str:  # never expose cookie values
        return f"<DouyinCookieJar cookies={len(self._cookies)}>"

    def __bool__(self) -> bool:
        return bool(self._cookies)

    def get(self, name: str) -> str:
        return self._cookies.get(name, "")

    def __contains__(self, name: str) -> bool:
        return name in self._cookies

    def set(self, name: str, value: str) -> None:
        if name:
            self._cookies[name] = value

    def absorb_cookie_header(self, header: str) -> None:
        for part in header.split(";"):
            name, separator, value = part.partition("=")
            if separator and name.strip():
                self._cookies[name.strip()] = value.strip()

    def absorb_set_cookie_headers(self, headers: list[str]) -> None:
        for header in headers:
            pair = header.split(";", 1)[0]
            name, separator, value = pair.partition("=")
            if separator and name.strip():
                self._cookies[name.strip()] = value.strip()

    def header(self) -> str:
        return "; ".join(f"{name}={value}" for name, value in self._cookies.items())


# ---------------------------------------------------------------------------
# Event mapping
# ---------------------------------------------------------------------------


def _create_time_ms(raw: int, fallback_ms: int) -> int:
    if raw <= 0:
        return fallback_ms
    # Douyin sometimes reports seconds and sometimes milliseconds.
    return raw * 1000 if raw < 10**12 else raw


def _room_user(raw: Mapping[str, Any]) -> RoomUser | None:
    if not raw:
        return None
    user_id = str(raw.get("id_str") or raw.get("id") or "").strip()
    name = str(raw.get("nickname") or "").strip()
    if not user_id and not name:
        return None
    if not name:
        name = "未知观众"
    try:
        return RoomUser(id=user_id[:100], name=name[:60])
    except ValidationError:
        try:
            return RoomUser(id=user_id[:100], name="未知观众")
        except ValidationError:
            return None


def _event_id(
    web_rid: str,
    message: RawProtoMessage,
    common: Mapping[str, Any],
    user_id: str,
    content: str,
    received_at_ms: int,
) -> str:
    common_msg_id = int(common.get("msg_id") or 0)
    platform_msg_id = message.msg_id if message.msg_id > 0 else common_msg_id
    if platform_msg_id > 0:
        return f"douyin-{web_rid}-{platform_msg_id}"[:100]
    digest = hashlib.sha1(
        (
            f"{message.method}|{web_rid}|{user_id}|{content}|"
            f"{common.get('create_time', 0)}|{received_at_ms}"
        ).encode()
    ).hexdigest()[:24]
    return f"douyin-{digest}"


def map_raw_message(
    message: RawProtoMessage,
    *,
    web_rid: str,
    now_ms: int | None = None,
) -> RoomEvent | None:
    """Map a decoded webcast ``Message`` to a canonical ``RoomEvent``.

    Supports chat, gift and member (join) messages.  Like/social/room-stats
    messages are intentionally ignored because they are not speech events.
    """

    body = decode_message_body(message)
    if body is None:
        return None
    common = body.get("common") or {}
    user = _room_user(body.get("user") or {})
    if user is None:
        return None
    timestamp_ms = int(now_ms if now_ms is not None else _default_now_ms())
    received_at_ms = _create_time_ms(int(common.get("create_time") or 0), timestamp_ms)
    event_id = _event_id(
        web_rid,
        message,
        common,
        user.id,
        str(body.get("content") or ""),
        received_at_ms,
    )

    if message.method == _METHOD_CHAT:
        content = str(body.get("content") or "").strip()
        if not content:
            return None
        return RoomEvent(
            id=event_id,
            type="chat",
            platform="douyin",
            room_id=web_rid,
            received_at_ms=received_at_ms,
            user=user,
            content=content[:500],
        )

    if message.method == _METHOD_GIFT:
        gift = body.get("gift") or {}
        name = str(gift.get("name") or "").strip() or "礼物"
        count = (
            int(body.get("repeat_count") or 0)
            or int(body.get("combo_count") or 0)
            or int(body.get("group_count") or 0)
            or 1
        )
        count = max(1, min(10000, count))
        return RoomEvent(
            id=event_id,
            type="gift",
            platform="douyin",
            room_id=web_rid,
            received_at_ms=received_at_ms,
            user=user,
            content="",
            gift=GiftInfo(name=name[:60], count=count),
        )

    if message.method == _METHOD_MEMBER:
        return RoomEvent(
            id=event_id,
            type="join",
            platform="douyin",
            room_id=web_rid,
            received_at_ms=received_at_ms,
            user=user,
            content="",
        )
    return None


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------

Status = Literal["idle", "connecting", "live", "closed", "error", "stopped"]
EventHandler = Callable[[RoomEvent], Awaitable[None] | None]


class NativeDouyinCollector:
    """Own the native Douyin webcast WebSocket and emit canonical events.

    Single-use lifecycle: ``start()`` (background) or ``await run()`` (owning),
    then ``await stop()``.  Events are delivered to ``on_event`` when provided,
    otherwise they are queued and can be taken with ``await next_event()``.

    Node is only started per handshake to compute the WebSocket signature; it
    never proxies chat, gifts or HTTP traffic.
    """

    def __init__(
        self,
        web_rid: str = "",
        *,
        cookie: str = "",
        on_event: EventHandler | None = None,
        node_path: str | os.PathLike[str] | None = None,
        sign_script: str | os.PathLike[str] | None = None,
        signature_provider: Callable[[str], Awaitable[str]] | None = None,
        http_session: aiohttp.ClientSession | None = None,
        heartbeat_seconds: float = 5.0,
        reconnect_base_seconds: float = 3.0,
        reconnect_max_seconds: float = 60.0,
        max_queue: int = 2000,
        user_agent: str = _DEFAULT_USER_AGENT,
        now_ms: Callable[[], int] | None = None,
        dedupe: bool = True,
    ) -> None:
        self.web_rid = normalize_web_rid(web_rid) if web_rid else ""
        self.status: Status = "idle"
        self.meta: RoomMeta | None = None
        self.room_status: int | None = None
        self.webcast_room_id = ""

        self._jar = _CookieJar(cookie or "")
        # A user-provided ttwid is trusted and reused (dyhub behavior); fetched
        # cookies are refreshed after the TTL instead of being kept forever.
        self._cookie_override_has_ttwid = bool(self._jar.get("ttwid"))
        self._jar_ready_at = 0.0
        self._on_event = on_event
        self._node_path = node_path
        self._sign_script = sign_script
        self._signature_provider = signature_provider
        self._owns_session = http_session is None
        self._session: aiohttp.ClientSession | None = http_session
        self._heartbeat_seconds = max(1.0, float(heartbeat_seconds))
        self._reconnect_base = max(0.5, float(reconnect_base_seconds))
        self._reconnect_max = max(self._reconnect_base, float(reconnect_max_seconds))
        self._user_agent = user_agent
        self._clock = now_ms or _default_now_ms
        self._dedupe_enabled = dedupe
        self._seen: OrderedDict[str, None] = OrderedDict()

        self._queue: asyncio.Queue[RoomEvent] = asyncio.Queue(maxsize=max(1, max_queue))
        self._stop_event = asyncio.Event()
        self._ws: Any = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._receiver_task: asyncio.Task[None] | None = None
        self._run_task: asyncio.Task[None] | None = None
        self._running = False

        self._frames = 0
        self._messages = 0
        self._events = 0
        self._acks = 0
        self._heartbeats = 0
        self._dropped = 0

    def __repr__(self) -> str:
        # Deliberately excludes the cookie and the signed WebSocket URL.
        return (
            f"NativeDouyinCollector(web_rid={self.web_rid!r}, status={self.status!r})"
        )

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> asyncio.Task[None]:
        """Start the reconnect loop in the background and return its task."""

        if not self.web_rid:
            raise ValueError("Douyin web_rid is required")
        if self._run_task is not None and not self._run_task.done():
            raise RuntimeError("Douyin collector is already running")
        self._run_task = asyncio.create_task(self.run(), name="douyin-native-collector")
        self._run_task.add_done_callback(self._on_run_done)
        return self._run_task

    async def run(self) -> None:
        """Connect, consume, and reconnect until stopped or the room closes.

        Raises :class:`RoomClosedError` when the room is finished/unavailable;
        ordinary network failures are retried with exponential backoff.
        """

        if not self.web_rid:
            raise ValueError("Douyin web_rid is required")
        if self._running:
            raise RuntimeError("Douyin collector is already running")
        self._running = True
        delay = self._reconnect_base
        try:
            while not self._stop_event.is_set():
                try:
                    await self.connect()
                    delay = self._reconnect_base
                    receiver = self._receiver_task
                    if receiver is not None:
                        await receiver
                except RoomClosedError:
                    self.status = "closed"
                    raise
                except NodeUnavailableError:
                    self.status = "error"
                    raise
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - reconnect is intended
                    self.status = "error"
                    logger.warning(
                        "Douyin collector disconnected: {}", _brief_error(exc)
                    )
                finally:
                    await self._teardown_connection()
                if self._stop_event.is_set():
                    break
                logger.info("Reconnecting Douyin collector in {:.1f}s", delay)
                await self._sleep_or_stop(delay)
                delay = min(delay * 2, self._reconnect_max)
        finally:
            self._running = False
            await self._teardown_connection()
            if self._stop_event.is_set():
                self.status = "stopped"

    async def stop(self) -> None:
        """Stop heartbeat/receive loops, close the socket and the HTTP session."""

        self._stop_event.set()
        current = asyncio.current_task()
        receiver = self._receiver_task
        await self._teardown_connection()
        task = self._run_task
        if task is not None and task is not current and receiver is not current:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, RoomClosedError):
                pass
            except Exception as exc:  # noqa: BLE001 - shutdown must not raise
                logger.debug(
                    "Douyin collector stopped with error: {}", _brief_error(exc)
                )
        self._run_task = None
        await self._close_http_session()
        if self.status != "closed":
            self.status = "stopped"

    async def connect(self, web_rid: str | None = None) -> None:
        """Perform one full handshake (cookie chain -> room id -> signature -> wss)."""

        if web_rid is not None:
            self.web_rid = normalize_web_rid(web_rid)
        if not self.web_rid:
            raise ValueError("Douyin web_rid is required")
        if self._stop_event.is_set():
            raise RuntimeError("Douyin collector is stopped")
        if self._ws is not None:
            raise RuntimeError("Douyin collector is already connected")

        self.status = "connecting"
        session = await self._ensure_http_session()
        try:
            await self._ensure_cookies(session)
            self.webcast_room_id = await self._resolve_webcast_room_id(session)
            md5_hex = build_signature_md5(self.webcast_room_id)
            signature = await self._sign(md5_hex)
            url = build_wss_url(self.webcast_room_id, signature, now_ms=self._now_ms())
            ws = await self._open_websocket(url)
        except RoomClosedError:
            self.status = "closed"
            raise
        except asyncio.CancelledError:
            self.status = "stopped"
            raise
        except Exception:
            self.status = "error"
            raise

        self._ws = ws
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(ws), name="douyin-native-heartbeat"
        )
        self._receiver_task = asyncio.create_task(
            self._receive_loop(ws), name="douyin-native-receive"
        )
        self.status = "live"
        logger.info(
            "Douyin collector live: web_rid={} anchor={}",
            self.web_rid,
            (self.meta.nickname if self.meta else "") or "unknown",
        )

    async def next_event(self, timeout: float | None = None) -> RoomEvent:
        """Take the next queued event; used by the OLV forwarding CLI."""

        if timeout is None:
            return await self._queue.get()
        return await asyncio.wait_for(self._queue.get(), timeout=timeout)

    def stats(self) -> dict[str, int | str]:
        return {
            "status": self.status,
            "web_rid": self.web_rid,
            "frames": self._frames,
            "messages": self._messages,
            "events": self._events,
            "acks": self._acks,
            "heartbeats": self._heartbeats,
            "dropped": self._dropped,
        }

    # -- handshake internals ----------------------------------------------

    async def _sign(self, md5_hex: str) -> str:
        if self._signature_provider is not None:
            return await self._signature_provider(md5_hex)
        return await get_douyin_signature(
            md5_hex,
            node_path=self._node_path,
            sign_script=self._sign_script,
        )

    async def _ensure_http_session(self) -> aiohttp.ClientSession:
        if self._session is not None and not self._session.closed:
            return self._session
        if not self._owns_session:
            raise DouyinCollectorError("injected aiohttp session is closed")
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15, connect=10),
            cookie_jar=aiohttp.DummyCookieJar(),
        )
        self._owns_session = True
        return self._session

    async def _close_http_session(self) -> None:
        session = self._session
        self._session = None
        if session is not None and self._owns_session and not session.closed:
            await session.close()

    async def _get_text(
        self,
        session: aiohttp.ClientSession,
        url: str,
        *,
        cookie: str,
        referer: str = "",
    ) -> tuple[int, str]:
        headers = {
            "User-Agent": self._user_agent,
            "Accept": "text/html,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        if cookie:
            headers["Cookie"] = cookie
        if referer:
            headers["Referer"] = referer
        async with session.get(url, headers=headers, allow_redirects=True) as response:
            self._jar.absorb_set_cookie_headers(
                response.headers.getall("Set-Cookie", [])
            )
            body = await response.text(errors="replace")
            return response.status, body

    async def _ensure_cookies(self, session: aiohttp.ClientSession) -> str:
        now = time.monotonic()
        if self._jar_ready_at and now - self._jar_ready_at < _COOKIE_TTL_SECONDS:
            return self._jar.header()
        if self._cookie_override_has_ttwid and self._jar.get("ttwid"):
            self._finish_cookie_chain()
            return self._jar.header()

        for attempt in range(_COOKIE_CHAIN_ATTEMPTS):
            try:
                await self._get_text(session, "https://www.douyin.com/", cookie="")
                await self._get_text(
                    session,
                    "https://live.douyin.com/",
                    cookie=self._jar.header(),
                )
                if self._jar.get("ttwid") and self._jar.get("__ac_nonce"):
                    self._finish_cookie_chain()
                    return self._jar.header()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - retries are intended
                logger.debug(
                    "Douyin cookie chain attempt {} failed: {}",
                    attempt + 1,
                    _brief_error(exc),
                )
            if attempt + 1 < _COOKIE_CHAIN_ATTEMPTS:
                await asyncio.sleep(4.0 * (attempt + 1))
        raise CookieError("failed to obtain Douyin cookies (ttwid/__ac_nonce)")

    def _finish_cookie_chain(self) -> None:
        nonce = self._jar.get("__ac_nonce")
        if nonce:
            self._jar.set(
                "__ac_signature",
                build_ac_signature(
                    "www.douyin.com",
                    nonce,
                    self._user_agent,
                    int(time.time()),
                ),
            )
        self._jar_ready_at = time.monotonic()

    async def _resolve_webcast_room_id(self, session: aiohttp.ClientSession) -> str:
        last_error: Exception | None = None
        room_url = f"https://live.douyin.com/{self.web_rid}"
        for attempt in range(3):
            try:
                status_code, html = await self._get_text(
                    session,
                    room_url,
                    cookie=self._jar.header(),
                    referer=room_url,
                )
                if status_code >= 400:
                    logger.debug("Douyin room page returned HTTP {}", status_code)
                room_id = parse_room_id(html)
                if room_id:
                    self.meta = parse_room_meta(html)
                    self.room_status = parse_room_status(html)
                    if (
                        self.room_status is not None
                        and self.room_status != ROOM_STATUS_LIVE
                    ):
                        raise RoomClosedError(self.web_rid, self.room_status)
                    return room_id
                last_error = RoomResolveError(
                    f"could not resolve webcast room id for {self.web_rid}"
                )
            except RoomClosedError:
                raise
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - retries are intended
                last_error = exc
                logger.debug(
                    "Douyin room resolve attempt {} failed: {}",
                    attempt + 1,
                    _brief_error(exc),
                )
            if attempt < 2:
                await asyncio.sleep(2.5 * (attempt + 1))
        raise RoomResolveError(
            f"could not resolve webcast room id for {self.web_rid}: "
            f"{_brief_error(last_error)}"
        )

    async def _open_websocket(self, url: str) -> Any:
        headers = {
            "User-Agent": self._user_agent,
            "Cookie": self._jar.header(),
        }
        try:
            return await websockets.connect(
                url,
                additional_headers=headers,
                user_agent_header=None,
                max_size=_FRAME_MAX_SIZE,
                ping_interval=None,
                ping_timeout=None,
                open_timeout=15,
                close_timeout=5,
                compression=None,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise DouyinCollectorError(
                f"Douyin websocket handshake failed: {_brief_error(exc)}"
            ) from exc

    # -- connection loops --------------------------------------------------

    async def _heartbeat_loop(self, ws: Any) -> None:
        try:
            while True:
                await asyncio.sleep(self._heartbeat_seconds)
                await ws.send(HEARTBEAT_FRAME)
                self._heartbeats += 1
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - socket close ends the loop
            logger.debug("Douyin heartbeat stopped: {}", _brief_error(exc))

    async def _receive_loop(self, ws: Any) -> None:
        try:
            async for raw in ws:
                if isinstance(raw, str):
                    logger.debug("Ignoring text frame from Douyin")
                    continue
                await self._handle_frame(bytes(raw), ws)
        except asyncio.CancelledError:
            raise
        except websockets.exceptions.ConnectionClosed as exc:
            logger.info(
                "Douyin websocket closed: code={}",
                getattr(exc, "code", "unknown"),
            )
        except Exception as exc:  # noqa: BLE001 - one bad frame must not kill us
            logger.warning("Douyin receive loop stopped: {}", _brief_error(exc))
        finally:
            if not self._stop_event.is_set() and self.status == "live":
                self.status = "error"

    async def _handle_frame(self, raw: bytes, ws: Any) -> None:
        self._frames += 1
        try:
            frame = decode_frame(raw)
        except ProtoDecodeError as exc:
            logger.debug("Ignoring malformed Douyin frame: {}", exc)
            return
        except Exception as exc:  # noqa: BLE001 - never log frame contents
            logger.warning("Ignoring undecodable Douyin frame: {}", _brief_error(exc))
            return

        if frame.need_ack and frame.payload_type != "hb":
            await ws.send(
                encode_push_frame(
                    log_id=frame.log_id,
                    payload_type="ack",
                    payload=frame.internal_ext.encode("utf-8"),
                )
            )
            self._acks += 1

        now_ms = self._now_ms()
        for message in frame.messages:
            self._messages += 1
            try:
                event = map_raw_message(message, web_rid=self.web_rid, now_ms=now_ms)
            except Exception as exc:  # noqa: BLE001 - skip only this message
                logger.debug(
                    "Failed to map Douyin {} message: {}",
                    message.method,
                    _brief_error(exc),
                )
                continue
            if event is None:
                continue
            if self._dedupe_enabled and not self._remember(event.id):
                continue
            try:
                await self._dispatch(event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - consumer errors are isolated
                logger.warning("Douyin event consumer failed: {}", _brief_error(exc))

    async def _dispatch(self, event: RoomEvent) -> None:
        self._events += 1
        if self._on_event is not None:
            result = self._on_event(event)
            if inspect.isawaitable(result):
                await result
            return
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self._dropped += 1
            logger.warning("Douyin event queue full; dropped {} event", event.type)

    async def _teardown_connection(self) -> None:
        current = asyncio.current_task()
        ws = self._ws
        self._ws = None
        heartbeat = self._heartbeat_task
        self._heartbeat_task = None
        receiver = self._receiver_task
        self._receiver_task = None

        if heartbeat is not None and heartbeat is not current:
            heartbeat.cancel()
            await _await_cancelled(heartbeat)
        if ws is not None:
            try:
                await asyncio.wait_for(ws.close(), timeout=3.0)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - best-effort close
                logger.debug("Douyin websocket close failed: {}", _brief_error(exc))
        if receiver is not None and receiver is not current:
            receiver.cancel()
            await _await_cancelled(receiver)

    async def _sleep_or_stop(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    def _remember(self, event_id: str) -> bool:
        if event_id in self._seen:
            return False
        self._seen[event_id] = None
        while len(self._seen) > _DEDUPE_WINDOW:
            self._seen.popitem(last=False)
        return True

    def _now_ms(self) -> int:
        return int(self._clock())

    def _on_run_done(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if isinstance(error, RoomClosedError):
            logger.info("Douyin collector stopped: {}", error)
        elif error is not None:
            logger.error("Douyin collector task failed: {}", _brief_error(error))


async def _await_cancelled(task: asyncio.Task[Any]) -> None:
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception as exc:  # noqa: BLE001 - task already failing; ignore here
        logger.debug("Douyin task ended with error: {}", _brief_error(exc))


def _brief_error(error: BaseException | None) -> str:
    if error is None:
        return "unknown error"
    text = " ".join(str(error).split())
    return f"{type(error).__name__}: {text[:300]}" if text else type(error).__name__
