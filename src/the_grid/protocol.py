"""Versioned neutral encodings for cryptography and outer transport.

Phase 2 froze the binary encodings used by access, board, and live-session
cryptography. Phase 3 adds a bounded newline-delimited UTF-8 JSON envelope for
network transport while preserving neutral machine-facing names.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
from collections.abc import Mapping
from enum import StrEnum
from typing import Final

PROTOCOL_VERSION: Final = 1
PROTOCOL_VERSION_BYTES: Final = PROTOCOL_VERSION.to_bytes(2, "big")

KEY_BYTES: Final = 32
HMAC_BYTES: Final = 32
SERVER_ID_BYTES: Final = 32
ACCESS_GENERATION_BYTES: Final = 16
ACCESS_CHALLENGE_BYTES: Final = 32
ACCESS_CLIENT_NONCE_BYTES: Final = 16
DISPLAY_TOKEN_BYTES: Final = 16
BOARD_MESSAGE_ID_BYTES: Final = 16
BOARD_NONCE_BYTES: Final = 12
ROOM_ID_BYTES: Final = 16
PAIR_ID_BYTES: Final = 16
HANDSHAKE_NONCE_BYTES: Final = 16
X25519_PUBLIC_BYTES: Final = 32
SESSION_ID_BYTES: Final = 16
SESSION_NONCE_BYTES: Final = 12

MAX_CRYPTO_FIELD_BYTES: Final = 1 << 20
MAX_CRYPTO_FIELDS: Final = 64

# Access labels.
ACCESS_KDF_LABEL: Final = b"access-kdf-v1"
ACCESS_AUTH_INFO: Final = b"access-auth-v1"
BOARD_MASTER_INFO: Final = b"board-master-v1"
DISPLAY_TOKEN_INFO: Final = b"display-token-v1"
ACCESS_PROOF_DOMAIN: Final = b"access-proof-v1"

# Board labels.
BOARD_MESSAGE_INFO: Final = b"board-message-v1"
BOARD_AAD_DOMAIN: Final = b"board-aad-v1"

# Live-session labels.
SESSION_KDF_LABEL: Final = b"comm-kdf-v1"
SESSION_AUTH_INFO: Final = b"session-auth-v1"
ROOM_ID_LABEL: Final = b"room-id-v1"
SESSION_TRANSCRIPT_DOMAIN: Final = b"session-handshake-v1"
SESSION_PROOF_DOMAIN: Final = b"session-proof-v1"
SESSION_CREATOR_TO_JOINER_INFO: Final = b"session-c2j-v1"
SESSION_JOINER_TO_CREATOR_INFO: Final = b"session-j2c-v1"
SESSION_ID_INFO: Final = b"session-id-v1"
SESSION_VERIFY_INFO: Final = b"session-verify-v1"
SESSION_DATA_AAD_DOMAIN: Final = b"session-data-aad-v1"
SESSION_PAYLOAD_DOMAIN: Final = b"session-payload-v1"


class EncodingError(ValueError):
    """Raised when a canonical protocol encoding is malformed."""


def encode_fields(domain: bytes, *fields: bytes) -> bytes:
    """Encode a domain and fields using the frozen Phase 2 binary format.

    The format is a two-byte unsigned field count followed by each field as a
    four-byte unsigned big-endian length and the exact field bytes. The domain
    is encoded as field zero and must be non-empty printable ASCII.
    """

    domain_bytes = require_bytes("domain", domain)
    if not domain_bytes or not domain_bytes.isascii():
        raise EncodingError("domain must be non-empty ascii bytes")
    if any(byte < 0x21 or byte > 0x7E for byte in domain_bytes):
        raise EncodingError("domain must contain printable ascii bytes")

    all_fields = (domain_bytes, *(require_bytes("field", item) for item in fields))
    if len(all_fields) > MAX_CRYPTO_FIELDS:
        raise EncodingError("too many encoded fields")

    encoded = bytearray(len(all_fields).to_bytes(2, "big"))
    for item in all_fields:
        if len(item) > MAX_CRYPTO_FIELD_BYTES:
            raise EncodingError("encoded field is too large")
        encoded.extend(len(item).to_bytes(4, "big"))
        encoded.extend(item)
    return bytes(encoded)


def decode_fields(
    encoded: bytes,
    *,
    expected_domain: bytes,
    expected_fields: int,
) -> tuple[bytes, ...]:
    """Strictly decode fields produced by :func:`encode_fields`."""

    data = require_bytes("encoded", encoded)
    domain = require_bytes("expected_domain", expected_domain)
    if isinstance(expected_fields, bool) or not isinstance(expected_fields, int):
        raise TypeError("expected_fields must be an integer")
    if expected_fields < 0 or expected_fields >= MAX_CRYPTO_FIELDS:
        raise EncodingError("invalid expected field count")
    if len(data) < 2:
        raise EncodingError("truncated field encoding")

    count = int.from_bytes(data[:2], "big")
    if count != expected_fields + 1:
        raise EncodingError("unexpected field count")

    offset = 2
    values: list[bytes] = []
    for _ in range(count):
        if len(data) - offset < 4:
            raise EncodingError("truncated field length")
        length = int.from_bytes(data[offset : offset + 4], "big")
        offset += 4
        if length > MAX_CRYPTO_FIELD_BYTES or len(data) - offset < length:
            raise EncodingError("invalid field length")
        values.append(data[offset : offset + length])
        offset += length

    if offset != len(data):
        raise EncodingError("trailing bytes in field encoding")
    if not values or values[0] != domain:
        raise EncodingError("unexpected encoding domain")
    return tuple(values[1:])


def require_bytes(name: str, value: object, length: int | None = None) -> bytes:
    """Return an immutable bytes value after strict optional length checking."""

    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise TypeError(f"{name} must be bytes-like")
    result = bytes(value)
    if length is not None:
        if isinstance(length, bool) or not isinstance(length, int) or length < 0:
            raise TypeError("length must be a non-negative integer or none")
        if len(result) != length:
            raise ValueError(f"{name} must be exactly {length} bytes")
    return result


def require_uint(name: str, value: object, bits: int) -> int:
    """Validate an unsigned integer that fits the requested width."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if isinstance(bits, bool) or not isinstance(bits, int):
        raise TypeError("bits must be an integer")
    if bits <= 0 or value < 0 or value >= 1 << bits:
        raise ValueError(f"{name} must fit in {bits} unsigned bits")
    return value


def uint64_bytes(value: int) -> bytes:
    """Return one unsigned 64-bit big-endian value."""

    return require_uint("value", value, 64).to_bytes(8, "big")


def b64url_encode(value: bytes) -> str:
    """Encode bytes as unpadded URL-safe Base64."""

    return base64.urlsafe_b64encode(require_bytes("value", value)).rstrip(b"=").decode(
        "ascii"
    )


def b64url_decode(value: str, *, expected_length: int | None = None) -> bytes:
    """Decode strict unpadded URL-safe Base64."""

    if not isinstance(value, str):
        raise TypeError("base64 value must be a string")
    if not value or "=" in value or not value.isascii():
        raise EncodingError("invalid base64url value")
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    if any(char not in alphabet for char in value):
        raise EncodingError("invalid base64url value")
    padding = "=" * (-len(value) % 4)
    try:
        decoded = base64.b64decode(value + padding, altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise EncodingError("invalid base64url value") from exc
    if b64url_encode(decoded) != value:
        raise EncodingError("non-canonical base64url value")
    if expected_length is not None:
        if (
            isinstance(expected_length, bool)
            or not isinstance(expected_length, int)
            or expected_length < 0
        ):
            raise TypeError("expected_length must be a non-negative integer or none")
        if len(decoded) != expected_length:
            raise EncodingError("decoded value has the wrong length")
    return decoded


# Outer transport constants frozen by Phase 3.
MAX_OUTER_FRAME_BYTES: Final = 16 * 1024
MAX_FRAME_TYPE_CHARS: Final = 64
MAX_REQUEST_ID_CHARS: Final = 64
MAX_VERSION_TEXT_CHARS: Final = 64
MAX_CAPABILITIES: Final = 32
MAX_CAPABILITY_CHARS: Final = 64


class FrameErrorCode(StrEnum):
    INVALID = "invalid_frame"
    TOO_LARGE = "frame_too_large"
    EOF = "end_of_stream"


class FrameError(ValueError):
    """Raised when one outer transport frame is malformed or oversized."""

    def __init__(self, code: FrameErrorCode, message: str | None = None) -> None:
        self.code = code
        super().__init__(code.value if message is None else message)


def encode_outer_frame(
    frame: Mapping[str, object],
    *,
    max_bytes: int = MAX_OUTER_FRAME_BYTES,
) -> bytes:
    """Encode one canonical compact JSON object terminated by exactly one LF."""

    checked_limit = _validate_frame_limit(max_bytes)
    if not isinstance(frame, Mapping):
        raise TypeError("frame must be a mapping")
    value = dict(frame)
    _validate_outer_basics(value)
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8") + b"\n"
    except (TypeError, ValueError, UnicodeError) as exc:
        raise FrameError(FrameErrorCode.INVALID) from exc
    if len(encoded) > checked_limit:
        raise FrameError(FrameErrorCode.TOO_LARGE)
    return encoded


def decode_outer_frame(
    encoded: bytes,
    *,
    max_bytes: int = MAX_OUTER_FRAME_BYTES,
) -> dict[str, object]:
    """Strictly decode one complete LF-terminated outer JSON frame."""

    checked_limit = _validate_frame_limit(max_bytes)
    data = require_bytes("encoded", encoded)
    if not data or len(data) > checked_limit:
        raise FrameError(
            FrameErrorCode.TOO_LARGE if len(data) > checked_limit else FrameErrorCode.INVALID
        )
    if not data.endswith(b"\n") or data.endswith(b"\n\n"):
        raise FrameError(FrameErrorCode.INVALID)
    payload = data[:-1]
    if not payload or b"\r" in payload or b"\n" in payload or b"\x00" in payload:
        raise FrameError(FrameErrorCode.INVALID)
    try:
        text = payload.decode("utf-8", errors="strict")
        parsed = json.loads(
            text,
            object_pairs_hook=_strict_frame_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError, FrameError, RecursionError) as exc:
        if isinstance(exc, FrameError):
            raise
        raise FrameError(FrameErrorCode.INVALID) from exc
    if not isinstance(parsed, dict):
        raise FrameError(FrameErrorCode.INVALID)
    _validate_outer_basics(parsed)
    return parsed


async def read_outer_frame(
    reader: asyncio.StreamReader,
    *,
    max_bytes: int = MAX_OUTER_FRAME_BYTES,
) -> dict[str, object]:
    """Read one bounded line from a stream without accepting an oversized frame."""

    if not isinstance(reader, asyncio.StreamReader):
        raise TypeError("reader must be an asyncio StreamReader")
    checked_limit = _validate_frame_limit(max_bytes)
    try:
        data = await reader.readuntil(b"\n")
    except asyncio.IncompleteReadError as exc:
        if exc.partial:
            raise FrameError(FrameErrorCode.INVALID) from exc
        raise FrameError(FrameErrorCode.EOF) from exc
    except asyncio.LimitOverrunError as exc:
        raise FrameError(FrameErrorCode.TOO_LARGE) from exc
    return decode_outer_frame(data, max_bytes=checked_limit)


async def write_outer_frame(
    writer: asyncio.StreamWriter,
    frame: Mapping[str, object],
    *,
    max_bytes: int = MAX_OUTER_FRAME_BYTES,
) -> None:
    """Encode, write, and drain one bounded outer frame."""

    if not isinstance(writer, asyncio.StreamWriter):
        raise TypeError("writer must be an asyncio StreamWriter")
    writer.write(encode_outer_frame(frame, max_bytes=max_bytes))
    await writer.drain()


def require_frame_string(
    frame: Mapping[str, object],
    name: str,
    *,
    max_chars: int = 256,
    ascii_only: bool = True,
) -> str:
    """Return one bounded string field from an outer frame."""

    value = frame.get(name)
    if not isinstance(value, str) or not value or len(value) > max_chars:
        raise FrameError(FrameErrorCode.INVALID)
    if "\x00" in value or any(char in "\r\n" for char in value):
        raise FrameError(FrameErrorCode.INVALID)
    if ascii_only and not value.isascii():
        raise FrameError(FrameErrorCode.INVALID)
    return value


def require_request_id(frame: Mapping[str, object]) -> str:
    value = require_frame_string(
        frame,
        "request_id",
        max_chars=MAX_REQUEST_ID_CHARS,
        ascii_only=True,
    )
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    if any(char not in alphabet for char in value):
        raise FrameError(FrameErrorCode.INVALID)
    return value


def require_frame_int(
    frame: Mapping[str, object],
    name: str,
    *,
    minimum: int = 0,
    maximum: int = (1 << 63) - 1,
) -> int:
    value = frame.get(name)
    if type(value) is not int or value < minimum or value > maximum:
        raise FrameError(FrameErrorCode.INVALID)
    return value


def require_frame_bool(frame: Mapping[str, object], name: str) -> bool:
    value = frame.get(name)
    if not isinstance(value, bool):
        raise FrameError(FrameErrorCode.INVALID)
    return value


def require_frame_bytes(
    frame: Mapping[str, object],
    name: str,
    *,
    expected_length: int | None = None,
    max_length: int | None = None,
) -> bytes:
    value = frame.get(name)
    if not isinstance(value, str):
        raise FrameError(FrameErrorCode.INVALID)
    try:
        decoded = b64url_decode(value, expected_length=expected_length)
    except (TypeError, ValueError) as exc:
        raise FrameError(FrameErrorCode.INVALID) from exc
    if max_length is not None:
        if type(max_length) is not int or max_length < 0:
            raise TypeError("max_length must be a non-negative integer or none")
        if len(decoded) > max_length:
            raise FrameError(FrameErrorCode.INVALID)
    return decoded


def make_frame(frame_type: str, /, **fields: object) -> dict[str, object]:
    """Build one v1 outer frame using a neutral machine-facing type."""

    if not isinstance(frame_type, str):
        raise TypeError("frame_type must be a string")
    value: dict[str, object] = {"v": PROTOCOL_VERSION, "type": frame_type}
    value.update(fields)
    _validate_outer_basics(value)
    return value


def _validate_outer_basics(frame: Mapping[str, object]) -> None:
    version = frame.get("v")
    if type(version) is not int or not 0 <= version <= 65535:
        raise FrameError(FrameErrorCode.INVALID)
    frame_type = frame.get("type")
    if (
        not isinstance(frame_type, str)
        or not frame_type
        or len(frame_type) > MAX_FRAME_TYPE_CHARS
        or not frame_type.isascii()
        or any(not (char.islower() or char.isdigit() or char == "_") for char in frame_type)
    ):
        raise FrameError(FrameErrorCode.INVALID)


def _strict_frame_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise FrameError(FrameErrorCode.INVALID)
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise FrameError(FrameErrorCode.INVALID, value)


def _validate_frame_limit(value: int) -> int:
    if type(value) is not int or value < 128 or value > MAX_OUTER_FRAME_BYTES:
        raise ValueError("max_bytes must be between 128 and 16384")
    return value
