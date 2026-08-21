"""Phrase-authenticated X25519 handshakes and live-session encryption."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

from .access import normalise_display_id
from .crypto import (
    RandomBytes,
    constant_time_equal,
    hkdf_sha256,
    hmac_sha256,
    random_bytes,
    scrypt_sha256_profile,
    sha256,
    zeroise,
)
from .models import CloseReason
from .phrases import normalise_phrase
from .protocol import (
    HANDSHAKE_NONCE_BYTES,
    HMAC_BYTES,
    KEY_BYTES,
    PAIR_ID_BYTES,
    PROTOCOL_VERSION_BYTES,
    ROOM_ID_BYTES,
    ROOM_ID_LABEL,
    SERVER_ID_BYTES,
    SESSION_AUTH_INFO,
    SESSION_CREATOR_TO_JOINER_INFO,
    SESSION_DATA_AAD_DOMAIN,
    SESSION_ID_BYTES,
    SESSION_ID_INFO,
    SESSION_JOINER_TO_CREATOR_INFO,
    SESSION_KDF_LABEL,
    SESSION_NONCE_BYTES,
    SESSION_PAYLOAD_DOMAIN,
    SESSION_PROOF_DOMAIN,
    SESSION_TRANSCRIPT_DOMAIN,
    SESSION_VERIFY_INFO,
    X25519_PUBLIC_BYTES,
    decode_fields,
    encode_fields,
    require_bytes,
    require_uint,
    uint64_bytes,
)

SESSION_TEXT_MAX_BYTES: Final = 4096
SESSION_COUNTER_MAX: Final = (1 << 64) - 1
VERIFICATION_ALPHABET: Final = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


class SessionRole(StrEnum):
    CREATOR = "creator"
    JOINER = "joiner"


class SessionDirection(StrEnum):
    CREATOR_TO_JOINER = "creator_to_joiner"
    JOINER_TO_CREATOR = "joiner_to_creator"


class SessionEventType(StrEnum):
    IDENTITY = "identity"
    TEXT = "text"
    CLOSE = "close"


class SessionErrorCode(StrEnum):
    INVALID_HANDSHAKE = "invalid_handshake"
    PROOF_FAILED = "proof_failed"
    KEY_EXCHANGE_FAILED = "key_exchange_failed"
    INVALID_FRAME = "invalid_frame"
    WRONG_SESSION = "wrong_session"
    WRONG_DIRECTION = "wrong_direction"
    COUNTER = "counter"
    INVALID_TAG = "invalid_tag"
    INVALID_PAYLOAD = "invalid_payload"
    INVALID_STATE = "invalid_state"
    TOO_LONG = "too_long"
    DISCARDED = "discarded"


class SessionError(ValueError):
    """Base exception for live-session cryptographic failures."""

    def __init__(self, code: SessionErrorCode, message: str | None = None) -> None:
        self.code = code
        super().__init__(code.value if message is None else message)


class SessionIntegrityError(SessionError):
    """A terminal integrity failure; the channel has discarded its keys."""


_ROLE_CODE: Final = {
    SessionRole.CREATOR: b"\x01",
    SessionRole.JOINER: b"\x02",
}
_DIRECTION_CODE: Final = {
    SessionDirection.CREATOR_TO_JOINER: b"\x01",
    SessionDirection.JOINER_TO_CREATOR: b"\x02",
}
_DIRECTION_PREFIX: Final = {
    SessionDirection.CREATOR_TO_JOINER: b"\x00\x00\x00\x01",
    SessionDirection.JOINER_TO_CREATOR: b"\x00\x00\x00\x02",
}
_EVENT_CODE: Final = {
    SessionEventType.IDENTITY: b"\x01",
    SessionEventType.TEXT: b"\x02",
    SessionEventType.CLOSE: b"\x03",
}
_EVENT_FROM_CODE: Final = {value: key for key, value in _EVENT_CODE.items()}


@dataclass(frozen=True, slots=True)
class SessionPhraseMaterial:
    """Room material derived from a received or generated comm phrase."""

    authentication_key: bytes = field(repr=False)
    room_id: bytes = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "authentication_key",
            require_bytes("authentication_key", self.authentication_key, KEY_BYTES),
        )
        object.__setattr__(
            self,
            "room_id",
            require_bytes("room_id", self.room_id, ROOM_ID_BYTES),
        )


@dataclass(frozen=True, slots=True)
class HandshakeHello:
    """One role-bound ephemeral public key and handshake nonce."""

    role: SessionRole
    nonce: bytes
    public_key: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.role, SessionRole):
            raise SessionError(SessionErrorCode.INVALID_HANDSHAKE)
        object.__setattr__(
            self,
            "nonce",
            require_bytes("nonce", self.nonce, HANDSHAKE_NONCE_BYTES),
        )
        object.__setattr__(
            self,
            "public_key",
            require_bytes("public_key", self.public_key, X25519_PUBLIC_BYTES),
        )


@dataclass(frozen=True, slots=True)
class SessionMaterial:
    """Separated material shared by both authenticated handshake participants."""

    creator_to_joiner_key: bytes = field(repr=False)
    joiner_to_creator_key: bytes = field(repr=False)
    session_id: bytes = field(repr=False)
    verification_seed: bytes = field(repr=False)

    def __post_init__(self) -> None:
        for name, length in (
            ("creator_to_joiner_key", KEY_BYTES),
            ("joiner_to_creator_key", KEY_BYTES),
            ("session_id", SESSION_ID_BYTES),
            ("verification_seed", KEY_BYTES),
        ):
            object.__setattr__(
                self,
                name,
                require_bytes(name, getattr(self, name), length),
            )

    @property
    def verification_code(self) -> str:
        return verification_code_from_seed(self.verification_seed)


@dataclass(frozen=True, slots=True)
class EncryptedSessionFrame:
    """One routed AEAD frame plus the metadata authenticated by that frame."""

    session_id: bytes = field(repr=False)
    direction: SessionDirection
    counter: int
    body: bytes = field(repr=False)

    def __post_init__(self) -> None:
        try:
            object.__setattr__(
                self,
                "session_id",
                require_bytes("session_id", self.session_id, SESSION_ID_BYTES),
            )
            if not isinstance(self.direction, SessionDirection):
                raise TypeError("direction must be SessionDirection")
            object.__setattr__(
                self,
                "counter",
                require_uint("counter", self.counter, 64),
            )
            checked_body = require_bytes("body", self.body)
        except (TypeError, ValueError) as exc:
            raise SessionError(SessionErrorCode.INVALID_FRAME) from exc
        if len(checked_body) < 16:
            raise SessionError(SessionErrorCode.INVALID_FRAME)
        object.__setattr__(self, "body", checked_body)


@dataclass(frozen=True, slots=True)
class SessionEvent:
    """A decrypted identity, text, or close event."""

    event_type: SessionEventType
    value: str = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.event_type, SessionEventType):
            raise SessionError(SessionErrorCode.INVALID_PAYLOAD)
        if not isinstance(self.value, str):
            raise TypeError("event value must be a string")


class HandshakeParticipant:
    """One ephemeral X25519 participant; finalisation destroys the private key."""

    __slots__ = ("hello", "_private_key")

    def __init__(
        self,
        role: SessionRole,
        private_key: x25519.X25519PrivateKey,
        nonce: bytes,
    ) -> None:
        if not isinstance(role, SessionRole):
            raise SessionError(SessionErrorCode.INVALID_HANDSHAKE)
        if not isinstance(private_key, x25519.X25519PrivateKey):
            raise TypeError("private_key must be X25519PrivateKey")
        public_key = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.hello = HandshakeHello(
            role=role,
            nonce=nonce,
            public_key=public_key,
        )
        self._private_key: x25519.X25519PrivateKey | None = private_key

    @classmethod
    def generate(
        cls,
        role: SessionRole,
        *,
        random_source: RandomBytes = secrets.token_bytes,
    ) -> HandshakeParticipant:
        """Generate a fresh X25519 key pair and handshake nonce."""

        return cls(
            role,
            x25519.X25519PrivateKey.generate(),
            random_bytes(HANDSHAKE_NONCE_BYTES, random_source),
        )

    @classmethod
    def _from_test_values(
        cls,
        role: SessionRole,
        *,
        private_key_bytes: bytes,
        nonce: bytes,
    ) -> HandshakeParticipant:
        """Construct deterministic vector material; never use in production flow."""

        return cls(
            role,
            x25519.X25519PrivateKey.from_private_bytes(
                require_bytes("private_key_bytes", private_key_bytes, KEY_BYTES)
            ),
            require_bytes("nonce", nonce, HANDSHAKE_NONCE_BYTES),
        )

    @property
    def destroyed(self) -> bool:
        return self._private_key is None

    def create_proof(
        self,
        phrase_material: SessionPhraseMaterial,
        *,
        server_id: bytes,
        pair_id: bytes,
        peer_hello: HandshakeHello,
    ) -> bytes:
        self._require_private_key()
        if not isinstance(phrase_material, SessionPhraseMaterial):
            raise TypeError("phrase_material must be SessionPhraseMaterial")
        transcript = build_handshake_transcript(
            server_id=server_id,
            room_id=phrase_material.room_id,
            pair_id=pair_id,
            first=self.hello,
            second=peer_hello,
        )
        return create_handshake_proof(
            phrase_material.authentication_key,
            role=self.hello.role,
            transcript=transcript,
        )

    def finalise(
        self,
        phrase_material: SessionPhraseMaterial,
        *,
        server_id: bytes,
        pair_id: bytes,
        peer_hello: HandshakeHello,
        peer_proof: bytes,
    ) -> SessionMaterial:
        private_key = self._require_private_key()
        if not isinstance(phrase_material, SessionPhraseMaterial):
            self.destroy()
            raise TypeError("phrase_material must be SessionPhraseMaterial")
        try:
            transcript = build_handshake_transcript(
                server_id=server_id,
                room_id=phrase_material.room_id,
                pair_id=pair_id,
                first=self.hello,
                second=peer_hello,
            )
            if not verify_handshake_proof(
                phrase_material.authentication_key,
                role=peer_hello.role,
                transcript=transcript,
                proof=peer_proof,
            ):
                raise SessionError(SessionErrorCode.PROOF_FAILED)

            actual_public = private_key.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
            if not constant_time_equal(actual_public, self.hello.public_key):
                raise SessionError(SessionErrorCode.INVALID_HANDSHAKE)
            try:
                peer_key = x25519.X25519PublicKey.from_public_bytes(
                    peer_hello.public_key
                )
                shared_secret = private_key.exchange(peer_key)
            except (TypeError, ValueError) as exc:
                raise SessionError(SessionErrorCode.KEY_EXCHANGE_FAILED) from exc
            return derive_session_material(shared_secret, transcript=transcript)
        finally:
            self.destroy()

    def destroy(self) -> None:
        self._private_key = None

    def _require_private_key(self) -> x25519.X25519PrivateKey:
        if self._private_key is None:
            raise SessionError(SessionErrorCode.DISCARDED)
        return self._private_key


class LiveSessionChannel:
    """Stateful directional AEAD with strict counters and identity-first gating."""

    __slots__ = (
        "role",
        "session_id",
        "verification_code",
        "_send_direction",
        "_receive_direction",
        "_send_key",
        "_receive_key",
        "_send_counter",
        "_receive_counter",
        "_local_identity_sent",
        "_peer_identity_received",
        "_local_display_id",
        "_peer_display_id",
        "_discarded",
        "_failed",
    )

    def __init__(self, material: SessionMaterial, role: SessionRole) -> None:
        if not isinstance(material, SessionMaterial):
            raise TypeError("material must be SessionMaterial")
        if not isinstance(role, SessionRole):
            raise TypeError("role must be SessionRole")
        self.role = role
        self.session_id = material.session_id
        self.verification_code = material.verification_code
        if role is SessionRole.CREATOR:
            self._send_direction = SessionDirection.CREATOR_TO_JOINER
            self._receive_direction = SessionDirection.JOINER_TO_CREATOR
            self._send_key = bytearray(material.creator_to_joiner_key)
            self._receive_key = bytearray(material.joiner_to_creator_key)
        else:
            self._send_direction = SessionDirection.JOINER_TO_CREATOR
            self._receive_direction = SessionDirection.CREATOR_TO_JOINER
            self._send_key = bytearray(material.joiner_to_creator_key)
            self._receive_key = bytearray(material.creator_to_joiner_key)
        self._send_counter = 0
        self._receive_counter = 0
        self._local_identity_sent = False
        self._peer_identity_received = False
        self._local_display_id: str | None = None
        self._peer_display_id: str | None = None
        self._discarded = False
        self._failed = False

    @property
    def send_counter(self) -> int:
        return self._send_counter

    @property
    def expected_receive_counter(self) -> int:
        return self._receive_counter

    @property
    def peer_display_id(self) -> str | None:
        return self._peer_display_id

    @property
    def active(self) -> bool:
        return (
            not self._discarded
            and not self._failed
            and self._local_identity_sent
            and self._peer_identity_received
        )

    @property
    def discarded(self) -> bool:
        return self._discarded

    @property
    def failed(self) -> bool:
        return self._failed

    def encrypt_identity(self, display_id: str) -> EncryptedSessionFrame:
        self._require_usable()
        if self._local_identity_sent or self._send_counter != 0:
            raise SessionError(SessionErrorCode.INVALID_STATE)
        checked = normalise_display_id(display_id)
        frame = self._encrypt_payload(
            _encode_session_payload(SessionEventType.IDENTITY, checked)
        )
        self._local_identity_sent = True
        self._local_display_id = checked
        return frame

    def encrypt_text(self, text: str) -> EncryptedSessionFrame:
        self._require_usable()
        if not self.active:
            raise SessionError(SessionErrorCode.INVALID_STATE)
        return self._encrypt_payload(
            _encode_session_payload(SessionEventType.TEXT, text)
        )

    def encrypt_close(
        self,
        reason: CloseReason = CloseReason.USER_END,
    ) -> EncryptedSessionFrame:
        self._require_usable()
        if not isinstance(reason, CloseReason):
            raise TypeError("reason must be CloseReason")
        frame = self._encrypt_payload(
            _encode_session_payload(SessionEventType.CLOSE, reason.value)
        )
        self.discard()
        return frame

    def decrypt_frame(self, frame: EncryptedSessionFrame) -> SessionEvent:
        self._require_usable()
        if not isinstance(frame, EncryptedSessionFrame):
            return self._fail(SessionErrorCode.INVALID_FRAME)
        if not constant_time_equal(frame.session_id, self.session_id):
            return self._fail(SessionErrorCode.WRONG_SESSION)
        if frame.direction is not self._receive_direction:
            return self._fail(SessionErrorCode.WRONG_DIRECTION)
        if frame.counter != self._receive_counter:
            return self._fail(SessionErrorCode.COUNTER)

        nonce = session_nonce(frame.direction, frame.counter)
        aad = session_associated_data(
            session_id=self.session_id,
            direction=frame.direction,
            counter=frame.counter,
        )
        try:
            plaintext = ChaCha20Poly1305(bytes(self._receive_key)).decrypt(
                nonce,
                frame.body,
                aad,
            )
        except InvalidTag:
            return self._fail(SessionErrorCode.INVALID_TAG)

        try:
            event = _decode_session_payload(plaintext)
            self._validate_received_event(event)
        except (TypeError, ValueError, SessionError):
            return self._fail(SessionErrorCode.INVALID_PAYLOAD)

        self._receive_counter += 1
        if event.event_type is SessionEventType.IDENTITY:
            self._peer_identity_received = True
            self._peer_display_id = event.value
        elif event.event_type is SessionEventType.CLOSE:
            self.discard()
        return event

    def discard(self) -> None:
        if not self._discarded:
            zeroise(self._send_key)
            zeroise(self._receive_key)
            self._send_counter = 0
            self._receive_counter = 0
            self._local_identity_sent = False
            self._peer_identity_received = False
            self._local_display_id = None
            self._peer_display_id = None
            self.verification_code = ""
            self._discarded = True

    def _encrypt_payload(self, payload: bytes) -> EncryptedSessionFrame:
        if self._send_counter > SESSION_COUNTER_MAX:
            raise SessionError(SessionErrorCode.INVALID_STATE)
        counter = self._send_counter
        nonce = session_nonce(self._send_direction, counter)
        aad = session_associated_data(
            session_id=self.session_id,
            direction=self._send_direction,
            counter=counter,
        )
        body = ChaCha20Poly1305(bytes(self._send_key)).encrypt(
            nonce,
            payload,
            aad,
        )
        self._send_counter += 1
        return EncryptedSessionFrame(
            session_id=self.session_id,
            direction=self._send_direction,
            counter=counter,
            body=body,
        )

    def _validate_received_event(self, event: SessionEvent) -> None:
        if not self._peer_identity_received:
            if event.event_type in {
                SessionEventType.IDENTITY,
                SessionEventType.CLOSE,
            }:
                return
            raise SessionError(SessionErrorCode.INVALID_PAYLOAD)
        if event.event_type is SessionEventType.IDENTITY:
            raise SessionError(SessionErrorCode.INVALID_PAYLOAD)
        if event.event_type is SessionEventType.TEXT and not self._local_identity_sent:
            raise SessionError(SessionErrorCode.INVALID_PAYLOAD)

    def _require_usable(self) -> None:
        if self._discarded or self._failed:
            raise SessionError(SessionErrorCode.DISCARDED)

    def _fail(self, code: SessionErrorCode) -> SessionEvent:
        self._failed = True
        self.discard()
        raise SessionIntegrityError(code)


def derive_session_phrase_root(phrase: str, *, server_id: bytes) -> bytes:
    """Harden a normalised comm phrase in the persistent server context."""

    checked_server_id = require_bytes("server_id", server_id, SERVER_ID_BYTES)
    normalised = normalise_phrase(phrase)
    salt = sha256(SESSION_KDF_LABEL + checked_server_id)
    return scrypt_sha256_profile(normalised.encode("ascii"), salt=salt)


def derive_session_phrase_material(
    phrase: str,
    *,
    server_id: bytes,
) -> SessionPhraseMaterial:
    """Derive the phrase proof key and opaque 16-byte waiting-room ID."""

    root = derive_session_phrase_root(phrase, server_id=server_id)
    auth_key = hkdf_sha256(root, info=SESSION_AUTH_INFO)
    room_id = hmac_sha256(auth_key, ROOM_ID_LABEL)[:ROOM_ID_BYTES]
    return SessionPhraseMaterial(authentication_key=auth_key, room_id=room_id)


def build_handshake_transcript(
    *,
    server_id: bytes,
    room_id: bytes,
    pair_id: bytes,
    first: HandshakeHello,
    second: HandshakeHello,
) -> bytes:
    """Build the canonical creator-then-joiner authenticated transcript."""

    if not isinstance(first, HandshakeHello) or not isinstance(second, HandshakeHello):
        raise TypeError("handshake values must be HandshakeHello")
    by_role = {first.role: first, second.role: second}
    if set(by_role) != {SessionRole.CREATOR, SessionRole.JOINER}:
        raise SessionError(SessionErrorCode.INVALID_HANDSHAKE)
    creator = by_role[SessionRole.CREATOR]
    joiner = by_role[SessionRole.JOINER]
    return encode_fields(
        SESSION_TRANSCRIPT_DOMAIN,
        PROTOCOL_VERSION_BYTES,
        require_bytes("server_id", server_id, SERVER_ID_BYTES),
        require_bytes("room_id", room_id, ROOM_ID_BYTES),
        require_bytes("pair_id", pair_id, PAIR_ID_BYTES),
        _ROLE_CODE[SessionRole.CREATOR],
        creator.nonce,
        creator.public_key,
        _ROLE_CODE[SessionRole.JOINER],
        joiner.nonce,
        joiner.public_key,
    )


def create_handshake_proof(
    authentication_key: bytes,
    *,
    role: SessionRole,
    transcript: bytes,
) -> bytes:
    """Authenticate one role and the complete canonical transcript hash."""

    if not isinstance(role, SessionRole):
        raise SessionError(SessionErrorCode.INVALID_HANDSHAKE)
    message = encode_fields(
        SESSION_PROOF_DOMAIN,
        PROTOCOL_VERSION_BYTES,
        _ROLE_CODE[role],
        sha256(require_bytes("transcript", transcript)),
    )
    return hmac_sha256(
        require_bytes("authentication_key", authentication_key, KEY_BYTES),
        message,
    )


def verify_handshake_proof(
    authentication_key: bytes,
    *,
    role: SessionRole,
    transcript: bytes,
    proof: bytes,
) -> bool:
    """Verify one role-bound phrase proof in constant time."""

    try:
        expected = create_handshake_proof(
            authentication_key,
            role=role,
            transcript=transcript,
        )
        supplied = require_bytes("proof", proof, HMAC_BYTES)
    except (TypeError, ValueError, SessionError):
        return False
    return constant_time_equal(expected, supplied)


def derive_session_material(
    shared_secret: bytes,
    *,
    transcript: bytes,
) -> SessionMaterial:
    """Derive four separately labelled values from X25519 and transcript hash."""

    shared = require_bytes("shared_secret", shared_secret, KEY_BYTES)
    transcript_hash = sha256(require_bytes("transcript", transcript))
    return SessionMaterial(
        creator_to_joiner_key=hkdf_sha256(
            shared,
            salt=transcript_hash,
            info=SESSION_CREATOR_TO_JOINER_INFO,
        ),
        joiner_to_creator_key=hkdf_sha256(
            shared,
            salt=transcript_hash,
            info=SESSION_JOINER_TO_CREATOR_INFO,
        ),
        session_id=hkdf_sha256(
            shared,
            salt=transcript_hash,
            info=SESSION_ID_INFO,
            length=SESSION_ID_BYTES,
        ),
        verification_seed=hkdf_sha256(
            shared,
            salt=transcript_hash,
            info=SESSION_VERIFY_INFO,
        ),
    )


def verification_code_from_seed(seed: bytes) -> str:
    """Map the first 40 seed bits to eight unbiased base32 characters."""

    if len(VERIFICATION_ALPHABET) != 32:
        raise RuntimeError("verification alphabet must contain 32 characters")
    raw = require_bytes("seed", seed, KEY_BYTES)
    value = int.from_bytes(raw[:5], "big")
    characters = [
        VERIFICATION_ALPHABET[(value >> shift) & 0x1F]
        for shift in range(35, -1, -5)
    ]
    return "".join(characters[:4]) + "-" + "".join(characters[4:])


def session_associated_data(
    *,
    session_id: bytes,
    direction: SessionDirection,
    counter: int,
) -> bytes:
    """Return the metadata authenticated by one live-session frame."""

    if not isinstance(direction, SessionDirection):
        raise TypeError("direction must be SessionDirection")
    return encode_fields(
        SESSION_DATA_AAD_DOMAIN,
        PROTOCOL_VERSION_BYTES,
        require_bytes("session_id", session_id, SESSION_ID_BYTES),
        _DIRECTION_CODE[direction],
        uint64_bytes(counter),
    )


def session_nonce(direction: SessionDirection, counter: int) -> bytes:
    """Return the frozen four-byte direction prefix plus uint64 counter."""

    if not isinstance(direction, SessionDirection):
        raise TypeError("direction must be SessionDirection")
    nonce = _DIRECTION_PREFIX[direction] + uint64_bytes(counter)
    return require_bytes("nonce", nonce, SESSION_NONCE_BYTES)


def _encode_session_payload(event_type: SessionEventType, value: str) -> bytes:
    if not isinstance(event_type, SessionEventType):
        raise SessionError(SessionErrorCode.INVALID_PAYLOAD)
    if not isinstance(value, str):
        raise TypeError("payload value must be a string")

    if event_type is SessionEventType.IDENTITY:
        body = normalise_display_id(value).encode("ascii")
    elif event_type is SessionEventType.TEXT:
        if "\x00" in value:
            raise SessionError(SessionErrorCode.INVALID_PAYLOAD)
        try:
            body = value.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise SessionError(SessionErrorCode.INVALID_PAYLOAD) from exc
        if len(body) > SESSION_TEXT_MAX_BYTES:
            raise SessionError(SessionErrorCode.TOO_LONG)
    else:
        try:
            reason = CloseReason(value)
        except ValueError as exc:
            raise SessionError(SessionErrorCode.INVALID_PAYLOAD) from exc
        body = reason.value.encode("ascii")

    return encode_fields(
        SESSION_PAYLOAD_DOMAIN,
        PROTOCOL_VERSION_BYTES,
        _EVENT_CODE[event_type],
        body,
    )


def _decode_session_payload(encoded: bytes) -> SessionEvent:
    try:
        version, type_code, body = decode_fields(
            require_bytes("encoded", encoded),
            expected_domain=SESSION_PAYLOAD_DOMAIN,
            expected_fields=3,
        )
    except (TypeError, ValueError) as exc:
        raise SessionError(SessionErrorCode.INVALID_PAYLOAD) from exc
    if version != PROTOCOL_VERSION_BYTES or type_code not in _EVENT_FROM_CODE:
        raise SessionError(SessionErrorCode.INVALID_PAYLOAD)
    event_type = _EVENT_FROM_CODE[type_code]

    if event_type is SessionEventType.IDENTITY:
        try:
            value = normalise_display_id(body.decode("ascii", errors="strict"))
        except (UnicodeError, TypeError, ValueError) as exc:
            raise SessionError(SessionErrorCode.INVALID_PAYLOAD) from exc
    elif event_type is SessionEventType.TEXT:
        if len(body) > SESSION_TEXT_MAX_BYTES:
            raise SessionError(SessionErrorCode.TOO_LONG)
        try:
            value = body.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise SessionError(SessionErrorCode.INVALID_PAYLOAD) from exc
        if "\x00" in value:
            raise SessionError(SessionErrorCode.INVALID_PAYLOAD)
    else:
        try:
            value = CloseReason(body.decode("ascii", errors="strict")).value
        except (UnicodeError, ValueError) as exc:
            raise SessionError(SessionErrorCode.INVALID_PAYLOAD) from exc
    return SessionEvent(event_type=event_type, value=value)
