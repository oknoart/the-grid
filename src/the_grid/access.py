"""Access derivation, one-shot challenge proofs, and server verifier state."""

from __future__ import annotations

import json
import os
import secrets
import tempfile
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Final

from .crypto import (
    RandomBytes,
    constant_time_equal,
    hkdf_sha256,
    hmac_sha256,
    random_bytes,
    scrypt_sha256_profile,
    sha256,
)
from .phrases import Sampler, generate_phrase, normalise_phrase
from .protocol import (
    ACCESS_AUTH_INFO,
    ACCESS_CHALLENGE_BYTES,
    ACCESS_CLIENT_NONCE_BYTES,
    ACCESS_GENERATION_BYTES,
    ACCESS_KDF_LABEL,
    ACCESS_PROOF_DOMAIN,
    BOARD_MASTER_INFO,
    DISPLAY_TOKEN_BYTES,
    DISPLAY_TOKEN_INFO,
    KEY_BYTES,
    PROTOCOL_VERSION_BYTES,
    SERVER_ID_BYTES,
    b64url_decode,
    b64url_encode,
    encode_fields,
    require_bytes,
)

DISPLAY_ID_ALPHABET: Final = "ABCDEFGHIJKLMNOPQRSTUVWXYZ23456789"
DISPLAY_ID_LENGTH: Final = 3
ACCESS_STATE_VERSION: Final = 1


class AccessErrorCode(StrEnum):
    INVALID_CONTEXT = "invalid_context"
    INVALID_STATE = "invalid_state"
    INVALID_DISPLAY = "invalid_display"
    ALREADY_INITIALISED = "already_initialised"
    FILE_ERROR = "file_error"


class AccessError(ValueError):
    """Raised when access material or state violates the frozen v1 format."""

    def __init__(self, code: AccessErrorCode, message: str | None = None) -> None:
        self.code = code
        super().__init__(code.value if message is None else message)


@dataclass(frozen=True, slots=True)
class AccessContext:
    """Public server values that scope one access generation."""

    server_id: bytes
    access_generation: bytes

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "server_id",
            require_bytes("server_id", self.server_id, SERVER_ID_BYTES),
        )
        object.__setattr__(
            self,
            "access_generation",
            require_bytes(
                "access_generation",
                self.access_generation,
                ACCESS_GENERATION_BYTES,
            ),
        )


@dataclass(frozen=True, slots=True)
class AccessKeys:
    """Client-only sibling keys derived from the hardened access root."""

    authentication_key: bytes = field(repr=False)
    board_master_key: bytes = field(repr=False)
    display_token_key: bytes = field(repr=False)

    def __post_init__(self) -> None:
        for name in (
            "authentication_key",
            "board_master_key",
            "display_token_key",
        ):
            object.__setattr__(
                self,
                name,
                require_bytes(name, getattr(self, name), KEY_BYTES),
            )


@dataclass(frozen=True, slots=True)
class AccessVerifierState:
    """The only access-derived secret retained by the server."""

    access_generation: bytes
    verifier_key: bytes = field(repr=False)
    version: int = ACCESS_STATE_VERSION

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != ACCESS_STATE_VERSION:
            raise AccessError(AccessErrorCode.INVALID_STATE)
        object.__setattr__(
            self,
            "access_generation",
            require_bytes(
                "access_generation",
                self.access_generation,
                ACCESS_GENERATION_BYTES,
            ),
        )
        object.__setattr__(
            self,
            "verifier_key",
            require_bytes("verifier_key", self.verifier_key, KEY_BYTES),
        )

    def to_bytes(self) -> bytes:
        """Return deterministic compact UTF-8 JSON with a final newline."""

        value = {
            "access_generation": b64url_encode(self.access_generation),
            "v": self.version,
            "verifier_key": b64url_encode(self.verifier_key),
        }
        return (
            json.dumps(
                value,
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")

    @classmethod
    def from_bytes(cls, encoded: bytes) -> AccessVerifierState:
        """Parse only the exact v1 access-state object."""

        data = require_bytes("encoded", encoded)
        try:
            text = data.decode("utf-8", errors="strict")
            parsed = json.loads(text, object_pairs_hook=_strict_object)
        except (UnicodeError, json.JSONDecodeError, AccessError) as exc:
            raise AccessError(AccessErrorCode.INVALID_STATE) from exc

        if not isinstance(parsed, dict) or set(parsed) != {
            "access_generation",
            "v",
            "verifier_key",
        }:
            raise AccessError(AccessErrorCode.INVALID_STATE)
        if type(parsed["v"]) is not int or parsed["v"] != ACCESS_STATE_VERSION:
            raise AccessError(AccessErrorCode.INVALID_STATE)
        try:
            generation = b64url_decode(
                parsed["access_generation"],
                expected_length=ACCESS_GENERATION_BYTES,
            )
            verifier = b64url_decode(
                parsed["verifier_key"],
                expected_length=KEY_BYTES,
            )
        except (TypeError, ValueError) as exc:
            raise AccessError(AccessErrorCode.INVALID_STATE) from exc
        state = cls(access_generation=generation, verifier_key=verifier)
        if state.to_bytes() != data:
            raise AccessError(AccessErrorCode.INVALID_STATE)
        return state


@dataclass(frozen=True, slots=True)
class AccessSetup:
    """One generated phrase plus the server values created from it."""

    phrase: str = field(repr=False)
    context: AccessContext
    verifier_state: AccessVerifierState

    def __post_init__(self) -> None:
        if not isinstance(self.context, AccessContext):
            raise TypeError("context must be AccessContext")
        if not isinstance(self.verifier_state, AccessVerifierState):
            raise TypeError("verifier_state must be AccessVerifierState")
        normalised = normalise_phrase(self.phrase)
        if normalised != self.phrase:
            raise AccessError(AccessErrorCode.INVALID_STATE)
        if self.context.access_generation != self.verifier_state.access_generation:
            raise AccessError(AccessErrorCode.INVALID_STATE)


class AccessChallengeVerifier:
    """A one-use server challenge verifier that rejects proof replay."""

    __slots__ = ("_consumed", "challenge", "context", "_verifier_key")

    def __init__(
        self,
        context: AccessContext,
        verifier_state: AccessVerifierState,
        *,
        challenge: bytes,
    ) -> None:
        if not isinstance(context, AccessContext):
            raise TypeError("context must be AccessContext")
        if not isinstance(verifier_state, AccessVerifierState):
            raise TypeError("verifier_state must be AccessVerifierState")
        if context.access_generation != verifier_state.access_generation:
            raise AccessError(AccessErrorCode.INVALID_CONTEXT)
        self.context = context
        self.challenge = require_bytes(
            "challenge", challenge, ACCESS_CHALLENGE_BYTES
        )
        self._verifier_key = verifier_state.verifier_key
        self._consumed = False

    @classmethod
    def generate(
        cls,
        context: AccessContext,
        verifier_state: AccessVerifierState,
        *,
        random_source: RandomBytes = secrets.token_bytes,
    ) -> AccessChallengeVerifier:
        return cls(
            context,
            verifier_state,
            challenge=random_bytes(ACCESS_CHALLENGE_BYTES, random_source),
        )

    @property
    def consumed(self) -> bool:
        return self._consumed

    def verify(self, *, client_nonce: bytes, proof: bytes) -> bool:
        """Consume this challenge and verify one proof attempt."""

        if self._consumed:
            return False
        self._consumed = True
        try:
            nonce = require_bytes(
                "client_nonce", client_nonce, ACCESS_CLIENT_NONCE_BYTES
            )
            supplied = require_bytes("proof", proof, KEY_BYTES)
            expected = create_access_proof(
                self._verifier_key,
                self.context,
                challenge=self.challenge,
                client_nonce=nonce,
            )
        except (TypeError, ValueError):
            return False
        return constant_time_equal(expected, supplied)


def derive_access_root(phrase: str, context: AccessContext) -> bytes:
    """Harden one normalised access phrase with the immutable v1 profile."""

    if not isinstance(context, AccessContext):
        raise TypeError("context must be AccessContext")
    normalised = normalise_phrase(phrase)
    salt = sha256(
        ACCESS_KDF_LABEL + context.server_id + context.access_generation
    )
    return scrypt_sha256_profile(normalised.encode("ascii"), salt=salt)


def derive_access_keys(phrase: str, context: AccessContext) -> AccessKeys:
    """Derive independent authentication, board, and display-token keys."""

    root = derive_access_root(phrase, context)
    return AccessKeys(
        authentication_key=hkdf_sha256(root, info=ACCESS_AUTH_INFO),
        board_master_key=hkdf_sha256(root, info=BOARD_MASTER_INFO),
        display_token_key=hkdf_sha256(root, info=DISPLAY_TOKEN_INFO),
    )


def create_initial_access(
    *,
    phrase_sampler: Sampler | None = None,
    random_source: RandomBytes = secrets.token_bytes,
) -> AccessSetup:
    """Create a new server ID, access generation, phrase, and verifier."""

    server_id = random_bytes(SERVER_ID_BYTES, random_source)
    return _create_access_setup(
        server_id,
        phrase_sampler=phrase_sampler,
        random_source=random_source,
    )


def rotate_access(
    server_id: bytes,
    *,
    phrase_sampler: Sampler | None = None,
    random_source: RandomBytes = secrets.token_bytes,
) -> AccessSetup:
    """Create a fresh generation and verifier while preserving server identity."""

    checked_server_id = require_bytes("server_id", server_id, SERVER_ID_BYTES)
    return _create_access_setup(
        checked_server_id,
        phrase_sampler=phrase_sampler,
        random_source=random_source,
    )


def _create_access_setup(
    server_id: bytes,
    *,
    phrase_sampler: Sampler | None,
    random_source: RandomBytes,
) -> AccessSetup:
    phrase = generate_phrase(sampler=phrase_sampler)
    generation = random_bytes(ACCESS_GENERATION_BYTES, random_source)
    context = AccessContext(server_id=server_id, access_generation=generation)
    keys = derive_access_keys(phrase, context)
    state = AccessVerifierState(
        access_generation=generation,
        verifier_key=keys.authentication_key,
    )
    return AccessSetup(phrase=phrase, context=context, verifier_state=state)


def create_access_proof(
    authentication_key: bytes,
    context: AccessContext,
    *,
    challenge: bytes,
    client_nonce: bytes,
) -> bytes:
    """Create the frozen v1 access challenge response."""

    key = require_bytes("authentication_key", authentication_key, KEY_BYTES)
    message = access_proof_message(
        context,
        challenge=challenge,
        client_nonce=client_nonce,
    )
    return hmac_sha256(key, message)


def access_proof_message(
    context: AccessContext,
    *,
    challenge: bytes,
    client_nonce: bytes,
) -> bytes:
    """Return the canonical bytes authenticated by an access proof."""

    if not isinstance(context, AccessContext):
        raise TypeError("context must be AccessContext")
    return encode_fields(
        ACCESS_PROOF_DOMAIN,
        PROTOCOL_VERSION_BYTES,
        context.server_id,
        context.access_generation,
        require_bytes("challenge", challenge, ACCESS_CHALLENGE_BYTES),
        require_bytes(
            "client_nonce", client_nonce, ACCESS_CLIENT_NONCE_BYTES
        ),
    )


def normalise_display_id(value: str) -> str:
    """Normalise lowercase input and validate the approved three-character ID."""

    if not isinstance(value, str):
        raise TypeError("display id must be a string")
    if not value.isascii():
        raise AccessError(AccessErrorCode.INVALID_DISPLAY)
    normalised = value.upper()
    if len(normalised) != DISPLAY_ID_LENGTH or any(
        char not in DISPLAY_ID_ALPHABET for char in normalised
    ):
        raise AccessError(AccessErrorCode.INVALID_DISPLAY)
    return normalised


def derive_display_token(display_token_key: bytes, display_id: str) -> bytes:
    """Derive the stable 16-byte opaque token for one access generation."""

    key = require_bytes("display_token_key", display_token_key, KEY_BYTES)
    normalised = normalise_display_id(display_id)
    return hmac_sha256(key, normalised.encode("ascii"))[:DISPLAY_TOKEN_BYTES]


def save_initial_access(
    setup: AccessSetup,
    *,
    server_id_path: Path,
    access_state_path: Path,
) -> tuple[Path, Path]:
    """Create both initial server files once, rolling back ordinary failures."""

    if not isinstance(setup, AccessSetup):
        raise TypeError("setup must be AccessSetup")
    identity_target = Path(server_id_path)
    state_target = Path(access_state_path)
    if identity_target == state_target:
        raise AccessError(AccessErrorCode.FILE_ERROR)
    if identity_target.exists() or state_target.exists():
        raise AccessError(AccessErrorCode.ALREADY_INITIALISED)

    identity_written = False
    try:
        save_server_id(identity_target, setup.context.server_id)
        identity_written = True
        save_access_state(state_target, setup.verifier_state)
    except Exception:
        if identity_written:
            try:
                identity_target.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass
        raise
    return identity_target, state_target


def save_server_id(path: Path, server_id: bytes, *, overwrite: bool = False) -> Path:
    """Persist the exact 32-byte server identity with private permissions."""

    return _write_private_file(
        Path(path),
        require_bytes("server_id", server_id, SERVER_ID_BYTES),
        overwrite=overwrite,
    )


def load_server_id(path: Path) -> bytes:
    """Load an exact raw server identity file."""

    try:
        return require_bytes(
            "server_id",
            Path(path).read_bytes(),
            SERVER_ID_BYTES,
        )
    except (OSError, TypeError, ValueError) as exc:
        raise AccessError(AccessErrorCode.FILE_ERROR) from exc


def save_access_state(
    path: Path,
    state: AccessVerifierState,
    *,
    overwrite: bool = False,
) -> Path:
    """Persist deterministic verifier state with private permissions."""

    if not isinstance(state, AccessVerifierState):
        raise TypeError("state must be AccessVerifierState")
    return _write_private_file(Path(path), state.to_bytes(), overwrite=overwrite)


def load_access_state(path: Path) -> AccessVerifierState:
    """Load and strictly validate persisted verifier state."""

    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise AccessError(AccessErrorCode.FILE_ERROR) from exc
    return AccessVerifierState.from_bytes(data)


def _write_private_file(path: Path, data: bytes, *, overwrite: bool) -> Path:
    target = Path(path)
    payload = require_bytes("data", data)
    try:
        target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    except OSError as exc:
        raise AccessError(AccessErrorCode.FILE_ERROR) from exc

    if not overwrite:
        created = False
        try:
            descriptor = os.open(
                target,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            created = True
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(target, 0o600)
            return target
        except FileExistsError as exc:
            raise AccessError(AccessErrorCode.ALREADY_INITIALISED) from exc
        except OSError as exc:
            if created:
                try:
                    target.unlink()
                except OSError:
                    pass
            raise AccessError(AccessErrorCode.FILE_ERROR) from exc

    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.",
            dir=target.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
            os.chmod(target, 0o600)
        finally:
            if temporary.exists():
                temporary.unlink()
        return target
    except OSError as exc:
        raise AccessError(AccessErrorCode.FILE_ERROR) from exc


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise AccessError(AccessErrorCode.INVALID_STATE)
        result[key] = value
    return result
