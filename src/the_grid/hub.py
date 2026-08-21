"""Deterministic board plaintext and authenticated encryption."""

from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Callable, Final

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
    ACCESS_GENERATION_BYTES,
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


BOARD_CAPACITY: Final = 24
BOARD_LIFETIME_SECONDS: Final = 86_400
BOARD_COOLDOWN_SECONDS: Final = 86_400
MAX_BOARD_CIPHERTEXT_BYTES: Final = 8 * 1024


class BoardStoreErrorCode(StrEnum):
    INVALID_RECORD = "invalid_record"
    DUPLICATE_MESSAGE = "duplicate_message"
    DATABASE = "database_error"


class BoardStoreError(RuntimeError):
    """Raised when persistent board state cannot be safely updated."""

    def __init__(self, code: BoardStoreErrorCode, message: str | None = None) -> None:
        self.code = code
        super().__init__(code.value if message is None else message)


@dataclass(frozen=True, slots=True)
class StoredBoardRecord:
    """One encrypted board record plus server-owned timestamps."""

    record: EncryptedBoardRecord
    created_at: int
    expires_at: int

    def __post_init__(self) -> None:
        if not isinstance(self.record, EncryptedBoardRecord):
            raise TypeError("record must be EncryptedBoardRecord")
        for name in ("created_at", "expires_at"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.expires_at != self.created_at + BOARD_LIFETIME_SECONDS:
            raise ValueError("board timestamps do not match the v1 lifetime")


@dataclass(frozen=True, slots=True)
class BoardPostResult:
    """Result of the atomic cooldown/capacity posting transaction."""

    accepted: bool
    stored: StoredBoardRecord | None
    removed_message_ids: tuple[bytes, ...]
    next_post_at: int | None = None

class BoardStore:
    """SQLite-backed encrypted board and independent posting cooldowns."""

    def __init__(
        self,
        database: Path | str,
        *,
        access_generation: bytes | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not callable(clock):
            raise TypeError("clock must be callable")
        self.path = Path(database)
        self._clock = clock
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._db = sqlite3.connect(self.path, isolation_level=None)
            self._db.execute("PRAGMA foreign_keys = ON")
            self._db.execute("PRAGMA journal_mode = WAL")
            self._db.execute("PRAGMA synchronous = FULL")
            self._create_schema()
            if access_generation is not None:
                self.bind_access_generation(access_generation)
        except sqlite3.Error as exc:
            raise BoardStoreError(BoardStoreErrorCode.DATABASE) from exc

    def close(self) -> None:
        with self._lock:
            try:
                self._db.close()
            except sqlite3.ProgrammingError:
                pass

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def _create_schema(self) -> None:
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS board_messages (
                message_id   BLOB PRIMARY KEY,
                id_token     BLOB NOT NULL,
                created_at   INTEGER NOT NULL,
                expires_at   INTEGER NOT NULL,
                ciphertext   BLOB NOT NULL
            );

            CREATE INDEX IF NOT EXISTS board_created
            ON board_messages(created_at, message_id);

            CREATE INDEX IF NOT EXISTS board_expiry
            ON board_messages(expires_at);

            CREATE TABLE IF NOT EXISTS board_seen_ids (
                message_id     BLOB PRIMARY KEY,
                first_seen_at  INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS board_cooldowns (
                id_token      BLOB PRIMARY KEY,
                last_post_at  INTEGER NOT NULL,
                next_post_at  INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS cooldown_expiry
            ON board_cooldowns(next_post_at);

            CREATE TABLE IF NOT EXISTS board_meta (
                key    TEXT PRIMARY KEY,
                value  BLOB NOT NULL
            );
            """
        )

    def bind_access_generation(self, access_generation: bytes) -> bool:
        """Bind storage to one access generation, clearing old-generation state."""

        generation = require_bytes(
            "access_generation",
            access_generation,
            ACCESS_GENERATION_BYTES,
        )
        with self._lock:
            try:
                self._db.execute("BEGIN IMMEDIATE")
                row = self._db.execute(
                    "SELECT value FROM board_meta WHERE key = 'access_generation'"
                ).fetchone()
                if row is None:
                    # A pre-Phase-5/unbound database has no trustworthy generation
                    # association. Preserve it only when it is empty; otherwise clear
                    # opaque state rather than guess which access generation owns it.
                    has_unbound_state = any(
                        self._db.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
                        is not None
                        for table in (
                            "board_messages",
                            "board_cooldowns",
                            "board_seen_ids",
                        )
                    )
                    changed = has_unbound_state
                else:
                    changed = bytes(row[0]) != generation
                if changed:
                    self._db.execute("DELETE FROM board_messages")
                    self._db.execute("DELETE FROM board_cooldowns")
                    self._db.execute("DELETE FROM board_seen_ids")
                self._db.execute(
                    """
                    INSERT INTO board_meta (key, value) VALUES ('access_generation', ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    (generation,),
                )
                self._db.execute("COMMIT")
            except sqlite3.Error as exc:
                self._rollback_quietly()
                raise BoardStoreError(BoardStoreErrorCode.DATABASE) from exc
        return changed

    def list_current(self, *, now: int | None = None) -> tuple[tuple[StoredBoardRecord, ...], tuple[bytes, ...]]:
        """Cleanup expired state and return the canonical oldest-first list."""

        timestamp = self._now(now)
        with self._lock:
            try:
                self._db.execute("BEGIN IMMEDIATE")
                removed = self._cleanup_locked(timestamp)
                rows = self._db.execute(
                    """
                    SELECT message_id, id_token, created_at, expires_at, ciphertext
                    FROM board_messages
                    ORDER BY created_at ASC, message_id ASC
                    """
                ).fetchall()
                self._db.execute("COMMIT")
            except sqlite3.Error as exc:
                self._rollback_quietly()
                raise BoardStoreError(BoardStoreErrorCode.DATABASE) from exc
        return tuple(self._stored_from_row(row) for row in rows), tuple(removed)

    def cleanup(self, *, now: int | None = None) -> tuple[bytes, ...]:
        timestamp = self._now(now)
        with self._lock:
            try:
                self._db.execute("BEGIN IMMEDIATE")
                removed = self._cleanup_locked(timestamp)
                self._db.execute("COMMIT")
            except sqlite3.Error as exc:
                self._rollback_quietly()
                raise BoardStoreError(BoardStoreErrorCode.DATABASE) from exc
        return tuple(removed)

    def post(
        self,
        record: EncryptedBoardRecord,
        *,
        now: int | None = None,
    ) -> BoardPostResult:
        """Apply expiry, duplicate, cooldown, insert, and capacity in one transaction."""

        if not isinstance(record, EncryptedBoardRecord):
            raise TypeError("record must be EncryptedBoardRecord")
        if len(record.ciphertext) > MAX_BOARD_CIPHERTEXT_BYTES:
            raise BoardStoreError(BoardStoreErrorCode.INVALID_RECORD)
        timestamp = self._now(now)
        expires_at = timestamp + BOARD_LIFETIME_SECONDS
        next_post_at = timestamp + BOARD_COOLDOWN_SECONDS

        with self._lock:
            try:
                self._db.execute("BEGIN IMMEDIATE")
                removed = self._cleanup_locked(timestamp)
                duplicate = self._db.execute(
                    "SELECT 1 FROM board_seen_ids WHERE message_id = ?",
                    (record.message_id,),
                ).fetchone()
                if duplicate is not None:
                    self._db.execute("COMMIT")
                    raise BoardStoreError(BoardStoreErrorCode.DUPLICATE_MESSAGE)

                cooldown = self._db.execute(
                    "SELECT next_post_at FROM board_cooldowns WHERE id_token = ?",
                    (record.display_token,),
                ).fetchone()
                if cooldown is not None and int(cooldown[0]) > timestamp:
                    self._db.execute("COMMIT")
                    return BoardPostResult(
                        accepted=False,
                        stored=None,
                        removed_message_ids=tuple(removed),
                        next_post_at=int(cooldown[0]),
                    )

                self._db.execute(
                    "INSERT INTO board_seen_ids (message_id, first_seen_at) VALUES (?, ?)",
                    (record.message_id, timestamp),
                )
                self._db.execute(
                    """
                    INSERT INTO board_messages
                    (message_id, id_token, created_at, expires_at, ciphertext)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        record.message_id,
                        record.display_token,
                        timestamp,
                        expires_at,
                        record.ciphertext,
                    ),
                )
                self._db.execute(
                    """
                    INSERT INTO board_cooldowns (id_token, last_post_at, next_post_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(id_token) DO UPDATE SET
                        last_post_at = excluded.last_post_at,
                        next_post_at = excluded.next_post_at
                    """,
                    (record.display_token, timestamp, next_post_at),
                )

                count = int(
                    self._db.execute("SELECT COUNT(*) FROM board_messages").fetchone()[0]
                )
                if count > BOARD_CAPACITY:
                    oldest = self._db.execute(
                        """
                        SELECT message_id FROM board_messages
                        ORDER BY created_at ASC, message_id ASC
                        LIMIT 1
                        """
                    ).fetchone()
                    if oldest is None:
                        raise sqlite3.DatabaseError("capacity query returned no row")
                    evicted = bytes(oldest[0])
                    self._db.execute(
                        "DELETE FROM board_messages WHERE message_id = ?",
                        (evicted,),
                    )
                    removed.append(evicted)

                self._db.execute("COMMIT")
            except BoardStoreError:
                self._rollback_quietly()
                raise
            except sqlite3.IntegrityError as exc:
                self._rollback_quietly()
                raise BoardStoreError(BoardStoreErrorCode.DUPLICATE_MESSAGE) from exc
            except sqlite3.Error as exc:
                self._rollback_quietly()
                raise BoardStoreError(BoardStoreErrorCode.DATABASE) from exc

        return BoardPostResult(
            accepted=True,
            stored=StoredBoardRecord(record, timestamp, expires_at),
            removed_message_ids=tuple(removed),
            next_post_at=next_post_at,
        )

    def cooldown_remaining(self, display_token: bytes, *, now: int | None = None) -> int:
        checked = require_bytes("display_token", display_token, DISPLAY_TOKEN_BYTES)
        timestamp = self._now(now)
        with self._lock:
            try:
                row = self._db.execute(
                    "SELECT next_post_at FROM board_cooldowns WHERE id_token = ?",
                    (checked,),
                ).fetchone()
            except sqlite3.Error as exc:
                raise BoardStoreError(BoardStoreErrorCode.DATABASE) from exc
        if row is None:
            return 0
        return max(0, int(row[0]) - timestamp)

    def counts(self) -> tuple[int, int]:
        with self._lock:
            messages = int(self._db.execute("SELECT COUNT(*) FROM board_messages").fetchone()[0])
            cooldowns = int(self._db.execute("SELECT COUNT(*) FROM board_cooldowns").fetchone()[0])
        return messages, cooldowns

    def _cleanup_locked(self, timestamp: int) -> list[bytes]:
        rows = self._db.execute(
            "SELECT message_id FROM board_messages WHERE expires_at <= ?",
            (timestamp,),
        ).fetchall()
        removed = [bytes(row[0]) for row in rows]
        self._db.execute(
            "DELETE FROM board_messages WHERE expires_at <= ?",
            (timestamp,),
        )
        self._db.execute(
            "DELETE FROM board_cooldowns WHERE next_post_at <= ?",
            (timestamp,),
        )
        return removed

    def _stored_from_row(self, row: tuple[object, ...]) -> StoredBoardRecord:
        try:
            record = EncryptedBoardRecord(
                message_id=bytes(row[0]),
                display_token=bytes(row[1]),
                ciphertext=bytes(row[4]),
            )
            return StoredBoardRecord(record, int(row[2]), int(row[3]))
        except (TypeError, ValueError, IndexError) as exc:
            raise BoardStoreError(BoardStoreErrorCode.DATABASE) from exc

    def _now(self, explicit: int | None) -> int:
        value = int(self._clock()) if explicit is None else explicit
        if type(value) is not int or value < 0:
            raise ValueError("time must be a non-negative integer")
        return value

    def _rollback_quietly(self) -> None:
        try:
            self._db.execute("ROLLBACK")
        except sqlite3.Error:
            pass
