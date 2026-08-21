"""Small wrappers around the approved cryptographic primitives.

Algorithms are provided by ``cryptography``. This module centralises fixed
parameter validation so higher layers cannot silently select other profiles.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable
from typing import Final

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .protocol import KEY_BYTES, require_bytes

SCRYPT_N: Final = 32768
SCRYPT_R: Final = 8
SCRYPT_P: Final = 1
SCRYPT_LENGTH: Final = KEY_BYTES
HKDF_LENGTH: Final = KEY_BYTES

RandomBytes = Callable[[int], bytes]


def sha256(value: bytes) -> bytes:
    """Return SHA-256 over exact bytes."""

    return hashlib.sha256(require_bytes("value", value)).digest()


def hmac_sha256(key: bytes, value: bytes) -> bytes:
    """Return HMAC-SHA256 over exact bytes."""

    return hmac.new(
        require_bytes("key", key, KEY_BYTES),
        require_bytes("value", value),
        hashlib.sha256,
    ).digest()


def scrypt_sha256_profile(secret: bytes, *, salt: bytes) -> bytes:
    """Derive 32 bytes using the immutable v1 Scrypt profile."""

    secret_bytes = require_bytes("secret", secret)
    salt_bytes = require_bytes("salt", salt, hashlib.sha256().digest_size)
    return Scrypt(
        salt=salt_bytes,
        length=SCRYPT_LENGTH,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
    ).derive(secret_bytes)


def hkdf_sha256(
    key_material: bytes,
    *,
    info: bytes,
    salt: bytes | None = None,
    length: int = HKDF_LENGTH,
) -> bytes:
    """Derive labelled material with HKDF-SHA256."""

    material = require_bytes("key_material", key_material)
    info_bytes = require_bytes("info", info)
    salt_bytes = None if salt is None else require_bytes("salt", salt)
    if isinstance(length, bool) or not isinstance(length, int):
        raise TypeError("length must be an integer")
    if not 1 <= length <= 255 * hashlib.sha256().digest_size:
        raise ValueError("invalid HKDF output length")
    return HKDF(
        algorithm=hashes.SHA256(),
        length=length,
        salt=salt_bytes,
        info=info_bytes,
    ).derive(material)


def constant_time_equal(left: bytes, right: bytes) -> bool:
    """Compare byte strings without content-dependent early exit."""

    return hmac.compare_digest(
        require_bytes("left", left),
        require_bytes("right", right),
    )


def random_bytes(length: int, source: RandomBytes) -> bytes:
    """Call an injectable secure source and enforce its exact output length."""

    if isinstance(length, bool) or not isinstance(length, int) or length <= 0:
        raise ValueError("length must be a positive integer")
    result = source(length)
    return require_bytes("random value", result, length)


def zeroise(value: bytearray) -> None:
    """Best-effort overwrite of a mutable secret buffer."""

    if not isinstance(value, bytearray):
        raise TypeError("value must be a bytearray")
    value[:] = b"\x00" * len(value)
