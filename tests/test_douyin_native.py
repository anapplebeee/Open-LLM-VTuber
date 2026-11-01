"""Offline tests for the native Douyin collector (no network, no Node)."""

from __future__ import annotations

import gzip
import unittest

from src.open_llm_vtuber.live.collectors.douyin_native import (
    HEARTBEAT_FRAME,
    NativeDouyinCollector,
    ProtoDecodeError,
    build_ac_signature,
    decode_frame,
    encode_push_frame,
    map_raw_message,
    parse_room_id,
    parse_room_meta,
    parse_room_status,
)

WEB_RID = "7123456789"
CREATE_TIME = 1_700_000_000


# ---------------------------------------------------------------------------
# Tiny protobuf wire encoder used only by these tests.
# ---------------------------------------------------------------------------


def _varint(value: int) -> bytes:
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


def _tag(field: int, wire_type: int) -> bytes:
    return _varint((field << 3) | wire_type)


def _varint_field(field: int, value: int) -> bytes:
    return _tag(field, 0) + _varint(value)


def _bytes_field(field: int, payload: bytes) -> bytes:
    return _tag(field, 2) + _varint(len(payload)) + payload


def _string_field(field: int, text: str) -> bytes:
    return _bytes_field(field, text.encode("utf-8"))


def _user_message(uid: int = 123456, nickname: str = "小鱼") -> bytes:
    return (
        _varint_field(1, uid)
        + _string_field(3, nickname)
        + _string_field(1029, f"u-{uid}")
    )


def _common_message(method: str, msg_id: int) -> bytes:
    return (
        _string_field(1, method)
        + _varint_field(2, msg_id)
        + _varint_field(3, 42)
        + _varint_field(4, CREATE_TIME)
    )


def _chat_body(content: str = "主播你好", msg_id: int = 9001) -> bytes:
    return (
        _bytes_field(1, _common_message("WebcastChatMessage", msg_id))
        + _bytes_field(2, _user_message())
        + _string_field(3, content)
    )


def _gift_body(msg_id: int = 9002) -> bytes:
    gift_struct = _string_field(16, "小心心") + _varint_field(12, 1)
    return (
        _bytes_field(1, _common_message("WebcastGiftMessage", msg_id))
        + _varint_field(2, 777)
        + _varint_field(5, 3)
        + _bytes_field(7, _user_message(uid=222, nickname="小蓝"))
        + _bytes_field(15, gift_struct)
    )


def _member_body(msg_id: int = 9003) -> bytes:
    return (
        _bytes_field(1, _common_message("WebcastMemberMessage", msg_id))
        + _bytes_field(2, _user_message(uid=333, nickname="新观众"))
        + _varint_field(3, 10)
    )


def _push_frame(
    method: str,
    body: bytes,
    *,
    msg_id: int,
    log_id: int = 777,
    need_ack: bool = True,
    internal_ext: str = "ext-1",
) -> bytes:
    """Build PushFrame(payloadType=msg, gzip Response(Message(...)))."""

    message = (
        _string_field(1, method)
        + _bytes_field(2, body)
        + _varint_field(3, msg_id)
        + _varint_field(4, 1)
    )
    response = _bytes_field(1, message)
    if internal_ext:
        response += _string_field(5, internal_ext)
    if need_ack:
        response += _varint_field(9, 1)
    compressed = gzip.compress(response)
    headers = _string_field(1, "compress_type") + _string_field(2, "gzip")
    return (
        _varint_field(1, 1)
        + _varint_field(2, log_id)
        + _bytes_field(5, headers)
        + _string_field(6, "gzip")
        + _string_field(7, "msg")
        + _bytes_field(8, compressed)
    )


class ProtobufDecodeTest(unittest.TestCase):
    def test_push_frame_response_chat_message_decodes(self):
        frame = decode_frame(
            _push_frame("WebcastChatMessage", _chat_body(), msg_id=9001)
        )

        self.assertEqual(frame.payload_type, "msg")
        self.assertEqual(frame.log_id, 777)
        self.assertEqual(frame.headers["compress_type"], "gzip")
        self.assertTrue(frame.need_ack)
        self.assertEqual(frame.internal_ext, "ext-1")
        self.assertEqual(len(frame.messages), 1)

        message = frame.messages[0]
        self.assertEqual(message.method, "WebcastChatMessage")
        self.assertEqual(message.msg_id, 9001)

        event = map_raw_message(message, web_rid=WEB_RID, now_ms=CREATE_TIME * 1000)
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.type, "chat")
        self.assertEqual(event.platform, "douyin")
        self.assertEqual(event.room_id, WEB_RID)
        self.assertEqual(event.user.id, "u-123456")
        self.assertEqual(event.user.name, "小鱼")
        self.assertEqual(event.content, "主播你好")
        self.assertEqual(event.received_at_ms, CREATE_TIME * 1000)
        self.assertEqual(event.id, f"douyin-{WEB_RID}-9001")

    def test_gift_and_member_map_to_canonical_events(self):
        gift_frame = decode_frame(
            _push_frame("WebcastGiftMessage", _gift_body(), msg_id=9002)
        )
        gift = map_raw_message(
            gift_frame.messages[0],
            web_rid=WEB_RID,
            now_ms=CREATE_TIME * 1000,
        )
        self.assertIsNotNone(gift)
        assert gift is not None
        self.assertEqual(gift.type, "gift")
        self.assertEqual(gift.user.name, "小蓝")
        self.assertEqual(gift.gift.name, "小心心")  # type: ignore[union-attr]
        self.assertEqual(gift.gift.count, 3)  # type: ignore[union-attr]
        self.assertEqual(gift.received_at_ms, CREATE_TIME * 1000)

        member_frame = decode_frame(
            _push_frame("WebcastMemberMessage", _member_body(), msg_id=9003)
        )
        member = map_raw_message(
            member_frame.messages[0],
            web_rid=WEB_RID,
            now_ms=CREATE_TIME * 1000,
        )
        self.assertIsNotNone(member)
        assert member is not None
        self.assertEqual(member.type, "join")
        self.assertEqual(member.user.name, "新观众")

    def test_unknown_method_is_ignored_and_event_id_is_stable(self):
        like_body = _bytes_field(
            1, _common_message("WebcastLikeMessage", 9004)
        ) + _bytes_field(2, _user_message())
        frame = decode_frame(_push_frame("WebcastLikeMessage", like_body, msg_id=9004))
        self.assertIsNone(map_raw_message(frame.messages[0], web_rid=WEB_RID))

        raw = decode_frame(
            _push_frame("WebcastChatMessage", _chat_body(), msg_id=9001)
        ).messages[0]
        first = map_raw_message(raw, web_rid=WEB_RID, now_ms=1)
        second = map_raw_message(raw, web_rid=WEB_RID, now_ms=2)
        self.assertEqual(first.id, second.id)  # type: ignore[union-attr]

    def test_truncated_frame_raises_decode_error(self):
        with self.assertRaises(ProtoDecodeError):
            decode_frame(b"\x0a\xff")

    def test_heartbeat_and_ack_frames_encode(self):
        self.assertEqual(HEARTBEAT_FRAME.hex(), "3a026862")
        ack = encode_push_frame(log_id=777, payload_type="ack", payload=b"ext-1")
        frame = decode_frame(ack)
        self.assertEqual(frame.payload_type, "ack")
        self.assertEqual(frame.log_id, 777)
        self.assertFalse(frame.need_ack)


class RoomPageParsingTest(unittest.TestCase):
    def test_room_status_plain_escaped_and_html_entity(self):
        plain = '{"roomStore":{"roomInfo":{"room":{"id_str":"7123","status":2}}}}'
        self.assertEqual(parse_room_status(plain), 2)
        self.assertEqual(parse_room_status(plain.replace('"', '\\"')), 2)
        self.assertEqual(parse_room_status(plain.replace('"', "&quot;")), 2)
        self.assertEqual(
            parse_room_status(plain.replace('"status":2', '"status":4')), 4
        )
        self.assertIsNone(parse_room_status("<html>not a room</html>"))

    def test_room_meta_extracts_anchor_fields(self):
        html = (
            '<script>self.__pace_f = "{\\"nickname\\":\\"小鲸\\",'
            '\\"avatar\\":\\"https://p3.douyinpic.com/avatar.jpeg\\",'
            '\\"title\\":\\"今晚聊聊天\\"}";</script>'
        )
        meta = parse_room_meta(html)
        self.assertIsNotNone(meta)
        assert meta is not None
        self.assertEqual(meta.nickname, "小鲸")
        self.assertEqual(meta.avatar, "https://p3.douyinpic.com/avatar.jpeg")
        self.assertEqual(meta.title, "今晚聊聊天")
        self.assertIsNone(parse_room_meta("<html></html>"))

    def test_room_meta_skips_template_placeholders(self):
        html = (
            '{"nickname":"$undefined","title":"$undefined",'
            '"nickname":"真正主播","title":"真正的标题"}'
        )
        meta = parse_room_meta(html)
        self.assertIsNotNone(meta)
        assert meta is not None
        self.assertEqual(meta.nickname, "真正主播")
        self.assertEqual(meta.title, "真正的标题")

    def test_room_id_parser_handles_escaped_json(self):
        html = '{"roomId":"7123456789","status":2}'
        self.assertEqual(parse_room_id(html), "7123456789")
        self.assertEqual(parse_room_id(html.replace('"', '\\"')), "7123456789")
        self.assertEqual(parse_room_id(html.replace('"', "&quot;")), "7123456789")
        self.assertIsNone(parse_room_id("<html></html>"))


class MappingAndSafetyTest(unittest.TestCase):
    def test_ac_signature_matches_dyhub_javascript_vector(self):
        # Vector produced by the same acSignature function used in
        # dyhub's lightweightSession.ts.
        self.assertEqual(
            build_ac_signature("www.douyin.com", "abc", "Mozilla/5.0", 1_700_000_000),
            "lU.EASX-GFVgfLklU.EA",
        )

    def test_collector_repr_does_not_leak_cookie(self):
        collector = NativeDouyinCollector(
            WEB_RID,
            cookie="ttwid=TOP-SECRET; sessionid=ALSO-SECRET",
        )
        rendered = repr(collector)
        self.assertIn(WEB_RID, rendered)
        self.assertNotIn("TOP-SECRET", rendered)
        self.assertNotIn("ALSO-SECRET", rendered)
        self.assertEqual(collector.stats()["status"], "idle")


if __name__ == "__main__":
    unittest.main()
