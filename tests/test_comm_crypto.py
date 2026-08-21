from __future__ import annotations

import inspect
import unittest
from dataclasses import replace

from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

from phase2_support import unhex, vectors
from the_grid.crypto import sha256
from the_grid.models import CloseReason
from the_grid.sessions import (
    EncryptedSessionFrame,
    HandshakeHello,
    HandshakeParticipant,
    LiveSessionChannel,
    SessionDirection,
    SessionError,
    SessionErrorCode,
    SessionEventType,
    SessionIntegrityError,
    SessionMaterial,
    SessionRole,
    _encode_session_payload,
    build_handshake_transcript,
    create_handshake_proof,
    derive_session_material,
    derive_session_phrase_material,
    derive_session_phrase_root,
    session_associated_data,
    session_nonce,
    verify_handshake_proof,
)


class SessionCryptoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.access_vector = vectors()["access"]
        cls.vector = vectors()["session"]
        cls.server_id = unhex(cls.access_vector["server_id"])
        cls.phrase_material = derive_session_phrase_material(
            cls.vector["phrase"],
            server_id=cls.server_id,
        )
        cls.material = SessionMaterial(
            creator_to_joiner_key=unhex(cls.vector["creator_to_joiner_key"]),
            joiner_to_creator_key=unhex(cls.vector["joiner_to_creator_key"]),
            session_id=unhex(cls.vector["session_id"]),
            verification_seed=unhex(cls.vector["verification_seed"]),
        )

    def _participants(self) -> tuple[HandshakeParticipant, HandshakeParticipant]:
        creator = HandshakeParticipant._from_test_values(
            SessionRole.CREATOR,
            private_key_bytes=unhex(self.vector["creator_private_key"]),
            nonce=unhex(self.vector["creator_nonce"]),
        )
        joiner = HandshakeParticipant._from_test_values(
            SessionRole.JOINER,
            private_key_bytes=unhex(self.vector["joiner_private_key"]),
            nonce=unhex(self.vector["joiner_nonce"]),
        )
        return creator, joiner

    def _transcript(
        self,
        creator: HandshakeParticipant,
        joiner: HandshakeParticipant,
        *,
        pair_id: bytes | None = None,
    ) -> bytes:
        return build_handshake_transcript(
            server_id=self.server_id,
            room_id=self.phrase_material.room_id,
            pair_id=unhex(self.vector["pair_id"]) if pair_id is None else pair_id,
            first=creator.hello,
            second=joiner.hello,
        )

    def _active_channels(self) -> tuple[LiveSessionChannel, LiveSessionChannel]:
        creator = LiveSessionChannel(self.material, SessionRole.CREATOR)
        joiner = LiveSessionChannel(self.material, SessionRole.JOINER)
        creator_identity = creator.encrypt_identity("ABC")
        joiner_identity = joiner.encrypt_identity("J7K")
        self.assertNotIn(creator_identity.body.hex(), repr(creator_identity))
        self.assertNotIn(creator_identity.session_id.hex(), repr(creator_identity))
        self.assertEqual(joiner.decrypt_frame(creator_identity).value, "ABC")
        self.assertEqual(creator.decrypt_frame(joiner_identity).value, "J7K")
        self.assertTrue(creator.active)
        self.assertTrue(joiner.active)
        return creator, joiner

    def test_session_fixed_vectors_and_authenticated_handshake(self) -> None:
        self.assertEqual(
            derive_session_phrase_root(
                self.vector["phrase"],
                server_id=self.server_id,
            ).hex(),
            self.vector["phrase_root"],
        )
        self.assertEqual(
            self.phrase_material.authentication_key.hex(),
            self.vector["auth_key"],
        )
        self.assertEqual(self.phrase_material.room_id.hex(), self.vector["room_id"])

        creator, joiner = self._participants()
        self.assertEqual(creator.hello.public_key.hex(), self.vector["creator_public_key"])
        self.assertEqual(joiner.hello.public_key.hex(), self.vector["joiner_public_key"])
        transcript = self._transcript(creator, joiner)
        self.assertEqual(transcript.hex(), self.vector["transcript"])
        self.assertEqual(sha256(transcript).hex(), self.vector["transcript_hash"])

        creator_proof = creator.create_proof(
            self.phrase_material,
            server_id=self.server_id,
            pair_id=unhex(self.vector["pair_id"]),
            peer_hello=joiner.hello,
        )
        joiner_proof = joiner.create_proof(
            self.phrase_material,
            server_id=self.server_id,
            pair_id=unhex(self.vector["pair_id"]),
            peer_hello=creator.hello,
        )
        self.assertEqual(creator_proof.hex(), self.vector["creator_proof"])
        self.assertEqual(joiner_proof.hex(), self.vector["joiner_proof"])

        creator_material = creator.finalise(
            self.phrase_material,
            server_id=self.server_id,
            pair_id=unhex(self.vector["pair_id"]),
            peer_hello=joiner.hello,
            peer_proof=joiner_proof,
        )
        joiner_material = joiner.finalise(
            self.phrase_material,
            server_id=self.server_id,
            pair_id=unhex(self.vector["pair_id"]),
            peer_hello=creator.hello,
            peer_proof=creator_proof,
        )
        self.assertTrue(creator.destroyed)
        self.assertTrue(joiner.destroyed)
        self.assertEqual(creator_material, joiner_material)
        self.assertEqual(creator_material, self.material)
        self.assertEqual(creator_material.verification_code, self.vector["verification_code"])

        shared = x25519.X25519PrivateKey.from_private_bytes(
            unhex(self.vector["creator_private_key"])
        ).exchange(
            x25519.X25519PublicKey.from_public_bytes(
                unhex(self.vector["joiner_public_key"])
            )
        )
        self.assertEqual(shared.hex(), self.vector["shared_secret"])
        self.assertEqual(derive_session_material(shared, transcript=transcript), self.material)

    def test_wrong_phrase_proof_fails_and_destroys_ephemeral_key(self) -> None:
        creator, joiner = self._participants()
        transcript = self._transcript(creator, joiner)
        wrong_material = derive_session_phrase_material(
            "velvet orbit green cabin",
            server_id=self.server_id,
        )
        wrong_joiner_proof = create_handshake_proof(
            wrong_material.authentication_key,
            role=SessionRole.JOINER,
            transcript=transcript,
        )
        with self.assertRaises(SessionError) as raised:
            creator.finalise(
                self.phrase_material,
                server_id=self.server_id,
                pair_id=unhex(self.vector["pair_id"]),
                peer_hello=joiner.hello,
                peer_proof=wrong_joiner_proof,
            )
        self.assertEqual(raised.exception.code, SessionErrorCode.PROOF_FAILED)
        self.assertTrue(creator.destroyed)

    def test_key_nonce_role_and_pair_changes_invalidate_proofs(self) -> None:
        creator, joiner = self._participants()
        transcript = self._transcript(creator, joiner)
        creator_proof = create_handshake_proof(
            self.phrase_material.authentication_key,
            role=SessionRole.CREATOR,
            transcript=transcript,
        )
        joiner_proof = create_handshake_proof(
            self.phrase_material.authentication_key,
            role=SessionRole.JOINER,
            transcript=transcript,
        )
        self.assertTrue(
            verify_handshake_proof(
                self.phrase_material.authentication_key,
                role=SessionRole.JOINER,
                transcript=transcript,
                proof=joiner_proof,
            )
        )

        changed_key = HandshakeHello(
            role=SessionRole.JOINER,
            nonce=joiner.hello.nonce,
            public_key=bytes([joiner.hello.public_key[0] ^ 1]) + joiner.hello.public_key[1:],
        )
        changed_nonce = HandshakeHello(
            role=SessionRole.JOINER,
            nonce=bytes([joiner.hello.nonce[0] ^ 1]) + joiner.hello.nonce[1:],
            public_key=joiner.hello.public_key,
        )
        tampered_transcripts = [
            build_handshake_transcript(
                server_id=self.server_id,
                room_id=self.phrase_material.room_id,
                pair_id=unhex(self.vector["pair_id"]),
                first=creator.hello,
                second=changed_key,
            ),
            build_handshake_transcript(
                server_id=self.server_id,
                room_id=self.phrase_material.room_id,
                pair_id=unhex(self.vector["pair_id"]),
                first=creator.hello,
                second=changed_nonce,
            ),
            self._transcript(
                creator,
                joiner,
                pair_id=bytes([unhex(self.vector["pair_id"])[0] ^ 1])
                + unhex(self.vector["pair_id"])[1:],
            ),
        ]
        for changed in tampered_transcripts:
            with self.subTest(hash=sha256(changed).hex()):
                self.assertFalse(
                    verify_handshake_proof(
                        self.phrase_material.authentication_key,
                        role=SessionRole.JOINER,
                        transcript=changed,
                        proof=joiner_proof,
                    )
                )

        self.assertFalse(
            verify_handshake_proof(
                self.phrase_material.authentication_key,
                role=SessionRole.JOINER,
                transcript=transcript,
                proof=creator_proof,
            )
        )
        with self.assertRaises(SessionError):
            build_handshake_transcript(
                server_id=self.server_id,
                room_id=self.phrase_material.room_id,
                pair_id=unhex(self.vector["pair_id"]),
                first=creator.hello,
                second=HandshakeHello(
                    SessionRole.CREATOR,
                    joiner.hello.nonce,
                    joiner.hello.public_key,
                ),
            )

    def test_generate_api_always_creates_fresh_private_keys(self) -> None:
        parameters = inspect.signature(HandshakeParticipant.generate).parameters
        self.assertNotIn("private_key_bytes", parameters)
        self.assertNotIn("nonce", parameters)
        first = HandshakeParticipant.generate(SessionRole.CREATOR)
        second = HandshakeParticipant.generate(SessionRole.CREATOR)
        self.assertNotEqual(first.hello.public_key, second.hello.public_key)
        self.assertNotEqual(first.hello.nonce, second.hello.nonce)
        first.destroy()
        second.destroy()

    def test_fresh_ephemeral_keys_produce_fresh_session_material(self) -> None:
        creator = HandshakeParticipant._from_test_values(
            SessionRole.CREATOR,
            private_key_bytes=bytes(range(65, 97)),
            nonce=bytes(range(144, 160)),
        )
        joiner = HandshakeParticipant._from_test_values(
            SessionRole.JOINER,
            private_key_bytes=bytes(range(97, 129)),
            nonce=bytes(range(160, 176)),
        )
        pair_id = bytes(range(176, 192))
        creator_proof = creator.create_proof(
            self.phrase_material,
            server_id=self.server_id,
            pair_id=pair_id,
            peer_hello=joiner.hello,
        )
        joiner_proof = joiner.create_proof(
            self.phrase_material,
            server_id=self.server_id,
            pair_id=pair_id,
            peer_hello=creator.hello,
        )
        new_creator_material = creator.finalise(
            self.phrase_material,
            server_id=self.server_id,
            pair_id=pair_id,
            peer_hello=joiner.hello,
            peer_proof=joiner_proof,
        )
        new_joiner_material = joiner.finalise(
            self.phrase_material,
            server_id=self.server_id,
            pair_id=pair_id,
            peer_hello=creator.hello,
            peer_proof=creator_proof,
        )
        self.assertEqual(new_creator_material, new_joiner_material)
        self.assertNotEqual(new_creator_material.session_id, self.material.session_id)
        self.assertNotEqual(
            new_creator_material.creator_to_joiner_key,
            self.material.creator_to_joiner_key,
        )

    def test_fixed_identity_text_and_close_frames(self) -> None:
        creator = LiveSessionChannel(self.material, SessionRole.CREATOR)
        joiner = LiveSessionChannel(self.material, SessionRole.JOINER)
        creator_identity = creator.encrypt_identity("ABC")
        joiner_identity = joiner.encrypt_identity("J7K")
        self.assertEqual(
            creator_identity.body.hex(),
            self.vector["creator_identity_frame"]["body"],
        )
        self.assertEqual(
            joiner_identity.body.hex(),
            self.vector["joiner_identity_frame"]["body"],
        )
        self.assertNotIn(b"ABC", creator_identity.body)
        self.assertNotIn(b"J7K", joiner_identity.body)
        self.assertEqual(joiner.decrypt_frame(creator_identity).value, "ABC")
        self.assertEqual(creator.decrypt_frame(joiner_identity).value, "J7K")
        self.assertEqual(creator.peer_display_id, "J7K")
        self.assertEqual(joiner.peer_display_id, "ABC")

        creator_text = creator.encrypt_text("are you receiving this?")
        self.assertNotIn(b"are you receiving this?", creator_text.body)
        self.assertEqual(
            creator_text.body.hex(),
            self.vector["creator_text_frame"]["body"],
        )
        received = joiner.decrypt_frame(creator_text)
        self.assertEqual(received.event_type, SessionEventType.TEXT)
        self.assertEqual(received.value, "are you receiving this?")

        joiner_text = joiner.encrypt_text("yes.")
        self.assertEqual(
            joiner_text.body.hex(),
            self.vector["joiner_text_frame"]["body"],
        )
        self.assertEqual(creator.decrypt_frame(joiner_text).value, "yes.")

        close = creator.encrypt_close(CloseReason.USER_END)
        self.assertEqual(close.body.hex(), self.vector["creator_close_frame"]["body"])
        self.assertNotIn(b"user_end", close.body)
        event = joiner.decrypt_frame(close)
        self.assertEqual(event.event_type, SessionEventType.CLOSE)
        self.assertEqual(event.value, CloseReason.USER_END.value)
        self.assertTrue(creator.discarded)
        self.assertTrue(joiner.discarded)
        self.assertEqual(creator.verification_code, "")
        self.assertEqual(joiner.verification_code, "")
        self.assertIsNone(creator.peer_display_id)
        self.assertIsNone(joiner.peer_display_id)

    def test_nonce_prefixes_and_counter_width_are_frozen(self) -> None:
        self.assertEqual(
            session_nonce(SessionDirection.CREATOR_TO_JOINER, 0).hex(),
            "000000010000000000000000",
        )
        self.assertEqual(
            session_nonce(SessionDirection.JOINER_TO_CREATOR, 0).hex(),
            "000000020000000000000000",
        )
        self.assertEqual(
            session_nonce(SessionDirection.CREATOR_TO_JOINER, 1).hex(),
            "000000010000000000000001",
        )
        with self.assertRaises(ValueError):
            session_nonce(SessionDirection.CREATOR_TO_JOINER, 1 << 64)
        with self.assertRaises(SessionError):
            EncryptedSessionFrame(
                session_id=self.material.session_id,
                direction=SessionDirection.CREATOR_TO_JOINER,
                counter=1 << 64,
                body=b"x" * 16,
            )

    def test_modified_ciphertext_is_terminal_and_keys_are_discarded(self) -> None:
        creator, joiner = self._active_channels()
        frame = creator.encrypt_text("integrity")
        tampered = replace(
            frame,
            body=bytes([frame.body[0] ^ 1]) + frame.body[1:],
        )
        with self.assertRaises(SessionIntegrityError) as raised:
            joiner.decrypt_frame(tampered)
        self.assertEqual(raised.exception.code, SessionErrorCode.INVALID_TAG)
        self.assertTrue(joiner.failed)
        self.assertTrue(joiner.discarded)
        with self.assertRaises(SessionError) as unusable:
            joiner.encrypt_text("after failure")
        self.assertEqual(unusable.exception.code, SessionErrorCode.DISCARDED)

    def test_duplicate_lower_gapped_wrong_direction_and_wrong_session_fail(self) -> None:
        creator, joiner = self._active_channels()
        valid = creator.encrypt_text("once")
        self.assertEqual(joiner.decrypt_frame(valid).value, "once")
        with self.assertRaises(SessionIntegrityError) as duplicate:
            joiner.decrypt_frame(valid)
        self.assertEqual(duplicate.exception.code, SessionErrorCode.COUNTER)

        cases: list[tuple[str, callable]] = []
        for name in ("gapped", "direction", "session"):
            creator_case, joiner_case = self._active_channels()
            frame = creator_case.encrypt_text(name)
            if name == "gapped":
                changed = replace(frame, counter=frame.counter + 1)
                expected = SessionErrorCode.COUNTER
            elif name == "direction":
                changed = replace(
                    frame,
                    direction=SessionDirection.JOINER_TO_CREATOR,
                )
                expected = SessionErrorCode.WRONG_DIRECTION
            else:
                changed = replace(
                    frame,
                    session_id=bytes([frame.session_id[0] ^ 1]) + frame.session_id[1:],
                )
                expected = SessionErrorCode.WRONG_SESSION
            with self.subTest(name=name), self.assertRaises(SessionIntegrityError) as raised:
                joiner_case.decrypt_frame(changed)
            self.assertEqual(raised.exception.code, expected)
            self.assertTrue(joiner_case.discarded)

    def test_close_api_cannot_continue_after_close(self) -> None:
        self.assertNotIn(
            "discard",
            inspect.signature(LiveSessionChannel.encrypt_close).parameters,
        )
        creator, _joiner = self._active_channels()
        creator.encrypt_close()
        with self.assertRaises(SessionError) as raised:
            creator.encrypt_text("after close")
        self.assertEqual(raised.exception.code, SessionErrorCode.DISCARDED)

    def test_authenticated_close_can_abort_before_identity_exchange(self) -> None:
        creator = LiveSessionChannel(self.material, SessionRole.CREATOR)
        joiner = LiveSessionChannel(self.material, SessionRole.JOINER)
        frame = creator.encrypt_close(CloseReason.APPLICATION_EXIT)
        event = joiner.decrypt_frame(frame)
        self.assertEqual(event.event_type, SessionEventType.CLOSE)
        self.assertEqual(event.value, CloseReason.APPLICATION_EXIT.value)
        self.assertTrue(creator.discarded)
        self.assertTrue(joiner.discarded)

    def test_first_decrypted_payload_must_be_identity(self) -> None:
        joiner = LiveSessionChannel(self.material, SessionRole.JOINER)
        payload = _encode_session_payload(SessionEventType.TEXT, "premature")
        direction = SessionDirection.CREATOR_TO_JOINER
        counter = 0
        body = ChaCha20Poly1305(self.material.creator_to_joiner_key).encrypt(
            session_nonce(direction, counter),
            payload,
            session_associated_data(
                session_id=self.material.session_id,
                direction=direction,
                counter=counter,
            ),
        )
        frame = EncryptedSessionFrame(
            session_id=self.material.session_id,
            direction=direction,
            counter=counter,
            body=body,
        )
        with self.assertRaises(SessionIntegrityError) as raised:
            joiner.decrypt_frame(frame)
        self.assertEqual(raised.exception.code, SessionErrorCode.INVALID_PAYLOAD)
        self.assertTrue(joiner.discarded)

    def test_text_limit_uses_utf8_bytes_and_identity_exchange_gates_sending(self) -> None:
        creator = LiveSessionChannel(self.material, SessionRole.CREATOR)
        with self.assertRaises(SessionError) as early:
            creator.encrypt_text("too early")
        self.assertEqual(early.exception.code, SessionErrorCode.INVALID_STATE)

        creator, joiner = self._active_channels()
        accepted = creator.encrypt_text("é" * 2048)
        self.assertEqual(joiner.decrypt_frame(accepted).value, "é" * 2048)
        with self.assertRaises(SessionError) as too_long:
            creator.encrypt_text("é" * 2049)
        self.assertEqual(too_long.exception.code, SessionErrorCode.TOO_LONG)

    def test_verification_code_uses_eight_unambiguous_characters(self) -> None:
        self.assertEqual(self.material.verification_code, self.vector["verification_code"])
        compact = self.material.verification_code.replace("-", "")
        self.assertEqual(len(compact), 8)
        self.assertFalse(set(compact) & set("01IO"))


if __name__ == "__main__":
    unittest.main()
