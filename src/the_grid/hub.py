"""Deterministic board plaintext and authenticated encryption."""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

from .access import (
    AccessContext,
    AccessKeys,
    derive_display_token,
    normalise_display_id,
)
from .crypto import RandomBytes, constant_time_equal, hkdf_sha256, random_bytes
from .protocol import (
    BOARD_AAD_DOMAIN,
    BOARD_MESSAGE_ID_BYTES,
    BOARD_MESSAGE_INFO,
    BOARD_NONCE_BYTES,
    DISPLAY_TOKEN_BYTES,
    PROTOCOL_VERSION_BYTES,
    encode_fields,
    require_bytes,
)

BOARD_PLAINTEXT_VERSION: Final = 1
BOARD_TEXT_MAX_BYTES: Final = 1024
_BOARD_NONCE: Final = b"\x00" * BOARD_NONCE_BYTES


class BoardCryptoErrorCode(StrEnum):
    INVALID_MESSAGE = "invalid_message"
    TOO_LONG = "too_long"
    INVALID_RECORD = "invalid_record"
    INTEGRITY = "integrity"
    TOKEN_MISMATCH = "token_mismatch"


class BoardCryptoError(ValueError):
    """Raised when a board message or encrypted record cannot be accepted."""

    def __init__(
        self,
        code: BoardCryptoErrorCode,
        message: str | None = None,
    ) -> None:
        self.code = code
        super().__init__(code.value if message is None else message)


@dataclass(frozen=True, slots=True)
class BoardMessage:
    """The complete decrypted v1 board object."""

    display_id: str = field(repr=False)
    text: str = field(repr=False)

    def __post_init__(self) -> None:
        try:
            checked_id = normalise_display_id(self.display_id)
        except (TypeError, ValueError) as exc:
            raise BoardCryptoError(BoardCryptoErrorCode.INVALID_MESSAGE) from exc
        if not isinstance(self.text, str):
            raise TypeError("text must be a string")
        try:
            encoded = self.text.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise BoardCryptoError(BoardCryptoErrorCode.INVALID_MESSAGE) from exc
        if not encoded or "\x00" in self.text:
            raise BoardCryptoError(BoardCryptoErrorCode.INVALID_MESSAGE)
        if len(encoded) > BOARD_TEXT_MAX_BYTES:
            raise BoardCryptoError(BoardCryptoErrorCode.TOO_LONG)
        object.__setattr__(self, "display_id", checked_id)


@dataclass(frozen=True, slots=True)
class EncryptedBoardRecord:
    """The encrypted fields stored and routed by the server."""

    message_id: bytes
    display_token: bytes = field(repr=False)
    ciphertext: bytes = field(repr=False)

    def __post_init__(self) -> None:
        try:
            object.__setattr__(
                self,
                "message_id",
                require_bytes(
                    "message_id",
                    self.message_id,
                    BOARD_MESSAGE_ID_BYTES,
                ),
            )
            object.__setattr__(
                self,
                "display_token",
                require_bytes(
                    "display_token",
                    self.display_token,
                    DISPLAY_TOKEN_BYTES,
                ),
            )
            checked_ciphertext = require_bytes("ciphertext", self.ciphertext)
        except (TypeError, ValueError) as exc:
            raise BoardCryptoError(BoardCryptoErrorCode.INVALID_RECORD) from exc
        if len(checked_ciphertext) < 16:
            raise BoardCryptoError(BoardCryptoErrorCode.INVALID_RECORD)
        object.__setattr__(self, "ciphertext", checked_ciphertext)


def serialise_board_message(message: BoardMessage) -> bytes:
    """Return the frozen compact UTF-8 JSON representation."""

    if not isinstance(message, BoardMessage):
        raise TypeError("message must be BoardMessage")
    value = {
        "v": BOARD_PLAINTEXT_VERSION,
        "id": message.display_id,
        "text": message.text,
    }
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def deserialise_board_message(encoded: bytes) -> BoardMessage:
    """Strictly parse one exact v1 board object."""

    data = require_bytes("encoded", encoded)
    try:
        text = data.decode("utf-8", errors="strict")
        parsed = json.loads(
            text,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError, BoardCryptoError) as exc:
        raise BoardCryptoError(BoardCryptoErrorCode.INVALID_MESSAGE) from exc

    if not isinstance(parsed, dict) or set(parsed) != {"v", "id", "text"}:
        raise BoardCryptoError(BoardCryptoErrorCode.INVALID_MESSAGE)
    if type(parsed["v"]) is not int or parsed["v"] != BOARD_PLAINTEXT_VERSION:
        raise BoardCryptoError(BoardCryptoErrorCode.INVALID_MESSAGE)
    try:
        return BoardMessage(display_id=parsed["id"], text=parsed["text"])
    except (TypeError, ValueError) as exc:
        if isinstance(exc, BoardCryptoError):
            raise
        raise BoardCryptoError(BoardCryptoErrorCode.INVALID_MESSAGE) from exc


def board_associated_data(
    context: AccessContext,
    *,
    message_id: bytes,
    display_token: bytes,
) -> bytes:
    """Return canonical authenticated metadata for one board record."""

    if not isinstance(context, AccessContext):
        raise TypeError("context must be AccessContext")
    return encode_fields(
        BOARD_AAD_DOMAIN,
        PROTOCOL_VERSION_BYTES,
        context.server_id,
        context.access_generation,
        require_bytes("message_id", message_id, BOARD_MESSAGE_ID_BYTES),
        require_bytes("display_token", display_token, DISPLAY_TOKEN_BYTES),
    )


def derive_board_message_key(
    board_master_key: bytes,
    *,
    message_id: bytes,
) -> bytes:
    """Derive a unique 32-byte AEAD key for one unique message ID."""

    checked_message_id = require_bytes(
        "message_id", message_id, BOARD_MESSAGE_ID_BYTES
    )
    return hkdf_sha256(
        require_bytes("board_master_key", board_master_key, 32),
        salt=checked_message_id,
        info=BOARD_MESSAGE_INFO,
    )


def encrypt_board_message(
    message: BoardMessage,
    context: AccessContext,
    access_keys: AccessKeys,
    *,
    random_source: RandomBytes = secrets.token_bytes,
) -> EncryptedBoardRecord:
    """Encrypt and token-bind a board message before server submission."""

    if not isinstance(message, BoardMessage):
        raise TypeError("message must be BoardMessage")
    if not isinstance(context, AccessContext):
        raise TypeError("context must be AccessContext")
    if not isinstance(access_keys, AccessKeys):
        raise TypeError("access_keys must be AccessKeys")
    identifier = random_bytes(BOARD_MESSAGE_ID_BYTES, random_source)
    token = derive_display_token(
        access_keys.display_token_key,
        message.display_id,
    )
    key = derive_board_message_key(
        access_keys.board_master_key,
        message_id=identifier,
    )
    plaintext = serialise_board_message(message)
    aad = board_associated_data(
        context,
        message_id=identifier,
        display_token=token,
    )
    ciphertext = ChaCha20Poly1305(key).encrypt(_BOARD_NONCE, plaintext, aad)
    return EncryptedBoardRecord(
        message_id=identifier,
        display_token=token,
        ciphertext=ciphertext,
    )


def decrypt_board_record(
    record: EncryptedBoardRecord,
    context: AccessContext,
    access_keys: AccessKeys,
) -> BoardMessage:
    """Authenticate, decrypt, parse, and verify the outer token binding."""

    if not isinstance(record, EncryptedBoardRecord):
        raise TypeError("record must be EncryptedBoardRecord")
    if not isinstance(context, AccessContext):
        raise TypeError("context must be AccessContext")
    if not isinstance(access_keys, AccessKeys):
        raise TypeError("access_keys must be AccessKeys")
    key = derive_board_message_key(
        access_keys.board_master_key,
        message_id=record.message_id,
    )
    aad = board_associated_data(
        context,
        message_id=record.message_id,
        display_token=record.display_token,
    )
    try:
        plaintext = ChaCha20Poly1305(key).decrypt(
            _BOARD_NONCE,
            record.ciphertext,
            aad,
        )
    except InvalidTag as exc:
        raise BoardCryptoError(BoardCryptoErrorCode.INTEGRITY) from exc

    message = deserialise_board_message(plaintext)
    expected_token = derive_display_token(
        access_keys.display_token_key,
        message.display_id,
    )
    if not constant_time_equal(expected_token, record.display_token):
        raise BoardCryptoError(BoardCryptoErrorCode.TOKEN_MISMATCH)
    return message


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise BoardCryptoError(BoardCryptoErrorCode.INVALID_MESSAGE)
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise BoardCryptoError(BoardCryptoErrorCode.INVALID_MESSAGE, value)
