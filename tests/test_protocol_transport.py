from __future__ import annotations

import asyncio
import json
import unittest

from the_grid.protocol import (
    MAX_OUTER_FRAME_BYTES,
    FrameError,
    FrameErrorCode,
    decode_outer_frame,
    encode_outer_frame,
    make_frame,
    read_outer_frame,
    require_request_id,
)


class OuterFrameCodecTests(unittest.IsolatedAsyncioTestCase):
    def test_canonical_compact_utf8_json_round_trip(self) -> None:
        frame = make_frame("board_post", request_id="abc_123", text="snowman ☃")
        encoded = encode_outer_frame(frame)
        self.assertTrue(encoded.endswith(b"\n"))
        self.assertNotIn(b": ", encoded)
        self.assertNotIn(b", ", encoded)
        self.assertEqual(decode_outer_frame(encoded), frame)
        self.assertEqual(require_request_id(frame), "abc_123")

    def test_duplicate_keys_nested_or_top_level_are_rejected(self) -> None:
        with self.assertRaises(FrameError):
            decode_outer_frame(b'{"type":"hello","type":"hello","v":1}\n')
        with self.assertRaises(FrameError):
            decode_outer_frame(b'{"meta":{"x":1,"x":2},"type":"hello","v":1}\n')

    def test_invalid_newlines_nul_and_non_object_are_rejected(self) -> None:
        invalid = [
            b'{"type":"hello","v":1}',
            b'{"type":"hello","v":1}\r\n',
            b'[1,2,3]\n',
            b'{"type":"hello","v":1}\x00\n',
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(FrameError):
                decode_outer_frame(value)

    def test_frame_size_includes_final_newline(self) -> None:
        frame = make_frame("hello", payload="x" * MAX_OUTER_FRAME_BYTES)
        with self.assertRaises(FrameError) as caught:
            encode_outer_frame(frame)
        self.assertEqual(caught.exception.code, FrameErrorCode.TOO_LARGE)

    def test_unknown_protocol_version_is_decoded_for_negotiation(self) -> None:
        frame = decode_outer_frame(b'{"type":"hello","v":2}\n')
        self.assertEqual(frame["v"], 2)

    async def test_stream_reader_rejects_line_over_configured_limit(self) -> None:
        reader = asyncio.StreamReader(limit=128)
        reader.feed_data(b"{" + b"x" * 200 + b"}\n")
        reader.feed_eof()
        with self.assertRaises(FrameError) as caught:
            await read_outer_frame(reader, max_bytes=128)
        self.assertEqual(caught.exception.code, FrameErrorCode.TOO_LARGE)

    def test_nan_and_bad_request_ids_are_rejected(self) -> None:
        with self.assertRaises(FrameError):
            decode_outer_frame(b'{"n":NaN,"type":"hello","v":1}\n')
        with self.assertRaises(FrameError):
            require_request_id(make_frame("hello", request_id="bad id"))


if __name__ == "__main__":
    unittest.main()
