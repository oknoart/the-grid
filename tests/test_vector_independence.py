from __future__ import annotations

import hashlib
import hmac
import unittest

from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

from phase2_support import unhex, vectors


def _fields(domain: bytes, *fields: bytes) -> bytes:
    values = (domain, *fields)
    encoded = bytearray(len(values).to_bytes(2, "big"))
    for value in values:
        encoded.extend(len(value).to_bytes(4, "big"))
        encoded.extend(value)
    return bytes(encoded)


def _hkdf(
    key_material: bytes,
    *,
    info: bytes,
    salt: bytes | None = None,
    length: int = 32,
) -> bytes:
    hash_length = hashlib.sha256().digest_size
    actual_salt = b"\x00" * hash_length if salt is None else salt
    pseudorandom_key = hmac.new(actual_salt, key_material, hashlib.sha256).digest()
    output = bytearray()
    previous = b""
    counter = 1
    while len(output) < length:
        previous = hmac.new(
            pseudorandom_key,
            previous + info + bytes([counter]),
            hashlib.sha256,
        ).digest()
        output.extend(previous)
        counter += 1
    return bytes(output[:length])


def _scrypt(secret: bytes, salt: bytes) -> bytes:
    return hashlib.scrypt(
        secret,
        salt=salt,
        n=32768,
        r=8,
        p=1,
        dklen=32,
        maxmem=128 * 1024 * 1024,
    )


class IndependentVectorTests(unittest.TestCase):
    """Cross-check pinned vectors without importing production glue code."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.all_vectors = vectors()
        cls.access = cls.all_vectors["access"]
        cls.board = cls.all_vectors["board"]
        cls.session = cls.all_vectors["session"]
        cls.server_id = unhex(cls.access["server_id"])
        cls.access_generation = unhex(cls.access["access_generation"])

        access_salt = hashlib.sha256(
            b"access-kdf-v1" + cls.server_id + cls.access_generation
        ).digest()
        cls.access_root = _scrypt(cls.access["phrase"].encode("ascii"), access_salt)
        cls.access_auth_key = _hkdf(cls.access_root, info=b"access-auth-v1")
        cls.board_master_key = _hkdf(cls.access_root, info=b"board-master-v1")
        cls.display_token_key = _hkdf(cls.access_root, info=b"display-token-v1")

    def test_access_vectors_with_standard_library_primitives(self) -> None:
        self.assertEqual(self.access_root.hex(), self.access["access_root"])
        self.assertEqual(
            self.access_auth_key.hex(),
            self.access["access_auth_key"],
        )
        self.assertEqual(
            self.board_master_key.hex(),
            self.access["board_master_key"],
        )
        self.assertEqual(
            self.display_token_key.hex(),
            self.access["display_token_key"],
        )

        proof_message = _fields(
            b"access-proof-v1",
            b"\x00\x01",
            self.server_id,
            self.access_generation,
            unhex(self.access["challenge"]),
            unhex(self.access["client_nonce"]),
        )
        proof = hmac.new(
            self.access_auth_key,
            proof_message,
            hashlib.sha256,
        ).digest()
        self.assertEqual(proof.hex(), self.access["proof"])

        display_token = hmac.new(
            self.display_token_key,
            self.access["display_id"].encode("ascii"),
            hashlib.sha256,
        ).digest()[:16]
        self.assertEqual(display_token.hex(), self.access["display_token"])

    def test_board_vector_with_independent_encoding_and_key_schedule(self) -> None:
        message_id = unhex(self.board["message_id"])
        display_token = unhex(self.access["display_token"])
        message_key = _hkdf(
            self.board_master_key,
            salt=message_id,
            info=b"board-message-v1",
        )
        self.assertEqual(message_key.hex(), self.board["message_key"])

        associated_data = _fields(
            b"board-aad-v1",
            b"\x00\x01",
            self.server_id,
            self.access_generation,
            message_id,
            display_token,
        )
        self.assertEqual(associated_data.hex(), self.board["aad"])
        ciphertext = ChaCha20Poly1305(message_key).encrypt(
            b"\x00" * 12,
            self.board["plaintext_utf8"].encode("utf-8"),
            associated_data,
        )
        self.assertEqual(ciphertext.hex(), self.board["ciphertext"])

    def test_session_vectors_with_independent_encoding_and_key_schedule(self) -> None:
        phrase_salt = hashlib.sha256(b"comm-kdf-v1" + self.server_id).digest()
        phrase_root = _scrypt(
            self.session["phrase"].encode("ascii"),
            phrase_salt,
        )
        authentication_key = _hkdf(phrase_root, info=b"session-auth-v1")
        room_id = hmac.new(
            authentication_key,
            b"room-id-v1",
            hashlib.sha256,
        ).digest()[:16]
        self.assertEqual(phrase_root.hex(), self.session["phrase_root"])
        self.assertEqual(authentication_key.hex(), self.session["auth_key"])
        self.assertEqual(room_id.hex(), self.session["room_id"])

        transcript = _fields(
            b"session-handshake-v1",
            b"\x00\x01",
            self.server_id,
            room_id,
            unhex(self.session["pair_id"]),
            b"\x01",
            unhex(self.session["creator_nonce"]),
            unhex(self.session["creator_public_key"]),
            b"\x02",
            unhex(self.session["joiner_nonce"]),
            unhex(self.session["joiner_public_key"]),
        )
        transcript_hash = hashlib.sha256(transcript).digest()
        self.assertEqual(transcript.hex(), self.session["transcript"])
        self.assertEqual(transcript_hash.hex(), self.session["transcript_hash"])

        for role_code, vector_name in (
            (b"\x01", "creator_proof"),
            (b"\x02", "joiner_proof"),
        ):
            proof_message = _fields(
                b"session-proof-v1",
                b"\x00\x01",
                role_code,
                transcript_hash,
            )
            proof = hmac.new(
                authentication_key,
                proof_message,
                hashlib.sha256,
            ).digest()
            self.assertEqual(proof.hex(), self.session[vector_name])

        creator_private = x25519.X25519PrivateKey.from_private_bytes(
            unhex(self.session["creator_private_key"])
        )
        joiner_public = x25519.X25519PublicKey.from_public_bytes(
            unhex(self.session["joiner_public_key"])
        )
        shared_secret = creator_private.exchange(joiner_public)
        self.assertEqual(shared_secret.hex(), self.session["shared_secret"])

        creator_to_joiner = _hkdf(
            shared_secret,
            salt=transcript_hash,
            info=b"session-c2j-v1",
        )
        joiner_to_creator = _hkdf(
            shared_secret,
            salt=transcript_hash,
            info=b"session-j2c-v1",
        )
        session_id = _hkdf(
            shared_secret,
            salt=transcript_hash,
            info=b"session-id-v1",
            length=16,
        )
        verification_seed = _hkdf(
            shared_secret,
            salt=transcript_hash,
            info=b"session-verify-v1",
        )
        self.assertEqual(
            creator_to_joiner.hex(),
            self.session["creator_to_joiner_key"],
        )
        self.assertEqual(
            joiner_to_creator.hex(),
            self.session["joiner_to_creator_key"],
        )
        self.assertEqual(session_id.hex(), self.session["session_id"])
        self.assertEqual(
            verification_seed.hex(),
            self.session["verification_seed"],
        )

        alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
        value = int.from_bytes(verification_seed[:5], "big")
        compact_code = "".join(
            alphabet[(value >> shift) & 0x1F]
            for shift in range(35, -1, -5)
        )
        verification_code = compact_code[:4] + "-" + compact_code[4:]
        self.assertEqual(verification_code, self.session["verification_code"])

        identity_payload = _fields(
            b"session-payload-v1",
            b"\x00\x01",
            b"\x01",
            b"ABC",
        )
        identity_aad = _fields(
            b"session-data-aad-v1",
            b"\x00\x01",
            session_id,
            b"\x01",
            (0).to_bytes(8, "big"),
        )
        identity_body = ChaCha20Poly1305(creator_to_joiner).encrypt(
            b"\x00\x00\x00\x01" + (0).to_bytes(8, "big"),
            identity_payload,
            identity_aad,
        )
        self.assertEqual(
            identity_body.hex(),
            self.session["creator_identity_frame"]["body"],
        )


if __name__ == "__main__":
    unittest.main()
