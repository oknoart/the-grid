from __future__ import annotations

import unittest

from the_grid.protocol import (
    EncodingError,
    b64url_decode,
    b64url_encode,
    decode_fields,
    encode_fields,
    require_bytes,
    require_uint,
    uint64_bytes,
)


class CanonicalEncodingTests(unittest.TestCase):
    def test_binary_field_format_is_exact_and_round_trips(self) -> None:
        encoded = encode_fields(b"test-v1", b"a", b"bc")
        self.assertEqual(
            encoded.hex(),
            "000300000007746573742d76310000000161000000026263",
        )
        self.assertEqual(
            decode_fields(
                encoded,
                expected_domain=b"test-v1",
                expected_fields=2,
            ),
            (b"a", b"bc"),
        )

    def test_decoder_rejects_wrong_domain_count_truncation_and_trailing_bytes(self) -> None:
        encoded = encode_fields(b"test-v1", b"a")
        invalid_calls = [
            lambda: decode_fields(
                encoded,
                expected_domain=b"other-v1",
                expected_fields=1,
            ),
            lambda: decode_fields(
                encoded,
                expected_domain=b"test-v1",
                expected_fields=2,
            ),
            lambda: decode_fields(
                encoded[:-1],
                expected_domain=b"test-v1",
                expected_fields=1,
            ),
            lambda: decode_fields(
                encoded + b"x",
                expected_domain=b"test-v1",
                expected_fields=1,
            ),
        ]
        for call in invalid_calls:
            with self.subTest(call=call), self.assertRaises(EncodingError):
                call()

    def test_base64url_is_unpadded_and_canonical(self) -> None:
        value = bytes(range(16))
        encoded = b64url_encode(value)
        self.assertNotIn("=", encoded)
        self.assertEqual(b64url_decode(encoded, expected_length=16), value)
        for invalid in (encoded + "=", "not+url", "", "A"):
            with self.subTest(invalid=invalid), self.assertRaises(EncodingError):
                b64url_decode(invalid)

    def test_helper_width_arguments_reject_booleans(self) -> None:
        with self.assertRaises(TypeError):
            decode_fields(
                encode_fields(b"domain", b"value"),
                expected_domain=b"domain",
                expected_fields=True,
            )
        with self.assertRaises(TypeError):
            require_bytes("value", b"x", True)
        with self.assertRaises(TypeError):
            require_uint("value", 0, True)
        with self.assertRaises(TypeError):
            b64url_decode("eA", expected_length=True)

    def test_uint64_is_big_endian_and_bounded(self) -> None:
        self.assertEqual(uint64_bytes(0), b"\x00" * 8)
        self.assertEqual(uint64_bytes(0x0102030405060708).hex(), "0102030405060708")
        for invalid in (-1, 1 << 64, True):
            with self.subTest(invalid=invalid), self.assertRaises((TypeError, ValueError)):
                uint64_bytes(invalid)


if __name__ == "__main__":
    unittest.main()
