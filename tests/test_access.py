from __future__ import annotations

import inspect
import json
import os
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path

from phase2_support import unhex, vectors
from the_grid.access import (
    AccessChallengeVerifier,
    AccessContext,
    AccessError,
    AccessErrorCode,
    AccessVerifierState,
    create_access_proof,
    create_initial_access,
    derive_access_keys,
    derive_access_root,
    derive_display_token,
    load_access_state,
    load_server_id,
    normalise_display_id,
    rotate_access,
    save_access_state,
    save_initial_access,
)


class _SequenceSource:
    def __init__(self, *chunks: bytes) -> None:
        self._chunks = list(chunks)

    def __call__(self, length: int) -> bytes:
        if not self._chunks:
            raise AssertionError("random source was called too many times")
        value = self._chunks.pop(0)
        if len(value) != length:
            raise AssertionError(f"expected request for {len(value)} bytes, got {length}")
        return value


def _sampler_for(phrase: str):
    selected = tuple(phrase.split(" "))

    def sample(words: tuple[str, ...], count: int) -> tuple[str, ...]:
        if count != 4 or any(word not in words for word in selected):
            raise AssertionError("invalid deterministic phrase sample")
        return selected

    return sample


class AccessCryptoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.vector = vectors()["access"]
        cls.context = AccessContext(
            server_id=unhex(cls.vector["server_id"]),
            access_generation=unhex(cls.vector["access_generation"]),
        )
        cls.root = derive_access_root(cls.vector["phrase"], cls.context)
        cls.keys = derive_access_keys(cls.vector["phrase"], cls.context)

    def test_access_fixed_vectors(self) -> None:
        self.assertEqual(self.root.hex(), self.vector["access_root"])
        self.assertEqual(
            self.keys.authentication_key.hex(),
            self.vector["access_auth_key"],
        )
        self.assertEqual(
            self.keys.board_master_key.hex(),
            self.vector["board_master_key"],
        )
        self.assertEqual(
            self.keys.display_token_key.hex(),
            self.vector["display_token_key"],
        )
        proof = create_access_proof(
            self.keys.authentication_key,
            self.context,
            challenge=unhex(self.vector["challenge"]),
            client_nonce=unhex(self.vector["client_nonce"]),
        )
        self.assertEqual(proof.hex(), self.vector["proof"])
        self.assertEqual(
            derive_display_token(
                self.keys.display_token_key,
                self.vector["display_id"],
            ).hex(),
            self.vector["display_token"],
        )

    def test_display_token_is_stable_within_generation_and_changes_after_rotation(self) -> None:
        first = derive_display_token(self.keys.display_token_key, "J7K")
        self.assertEqual(
            first,
            derive_display_token(self.keys.display_token_key, "j7k"),
        )
        changed_context = AccessContext(
            self.context.server_id,
            bytes([self.context.access_generation[0] ^ 1])
            + self.context.access_generation[1:],
        )
        changed_keys = derive_access_keys(self.vector["phrase"], changed_context)
        self.assertNotEqual(
            first,
            derive_display_token(changed_keys.display_token_key, "J7K"),
        )

    def test_one_shot_challenge_accepts_once_and_rejects_replay(self) -> None:
        state = AccessVerifierState(
            access_generation=self.context.access_generation,
            verifier_key=self.keys.authentication_key,
        )
        challenge = unhex(self.vector["challenge"])
        nonce = unhex(self.vector["client_nonce"])
        proof = unhex(self.vector["proof"])
        verifier = AccessChallengeVerifier(
            self.context,
            state,
            challenge=challenge,
        )
        self.assertTrue(verifier.verify(client_nonce=nonce, proof=proof))
        self.assertTrue(verifier.consumed)
        self.assertFalse(verifier.verify(client_nonce=nonce, proof=proof))

    def test_changed_challenge_nonce_or_proof_fails(self) -> None:
        state = AccessVerifierState(
            access_generation=self.context.access_generation,
            verifier_key=self.keys.authentication_key,
        )
        challenge = unhex(self.vector["challenge"])
        nonce = unhex(self.vector["client_nonce"])
        proof = unhex(self.vector["proof"])
        cases = [
            (bytes([challenge[0] ^ 1]) + challenge[1:], nonce, proof),
            (challenge, bytes([nonce[0] ^ 1]) + nonce[1:], proof),
            (challenge, nonce, bytes([proof[0] ^ 1]) + proof[1:]),
        ]
        for changed_challenge, changed_nonce, changed_proof in cases:
            with self.subTest(case=cases.index((changed_challenge, changed_nonce, changed_proof))):
                verifier = AccessChallengeVerifier(
                    self.context,
                    state,
                    challenge=changed_challenge,
                )
                self.assertFalse(
                    verifier.verify(
                        client_nonce=changed_nonce,
                        proof=changed_proof,
                    )
                )

    def test_wrong_phrase_cannot_authenticate(self) -> None:
        wrong = derive_access_keys("amber meadow signal copper", self.context)
        wrong_proof = create_access_proof(
            wrong.authentication_key,
            self.context,
            challenge=unhex(self.vector["challenge"]),
            client_nonce=unhex(self.vector["client_nonce"]),
        )
        state = AccessVerifierState(
            access_generation=self.context.access_generation,
            verifier_key=self.keys.authentication_key,
        )
        verifier = AccessChallengeVerifier(
            self.context,
            state,
            challenge=unhex(self.vector["challenge"]),
        )
        self.assertFalse(
            verifier.verify(
                client_nonce=unhex(self.vector["client_nonce"]),
                proof=wrong_proof,
            )
        )

    def test_server_state_contains_only_generation_and_verifier(self) -> None:
        state = AccessVerifierState(
            access_generation=self.context.access_generation,
            verifier_key=self.keys.authentication_key,
        )
        encoded = state.to_bytes()
        parsed = json.loads(encoded)
        self.assertEqual(set(parsed), {"access_generation", "v", "verifier_key"})
        self.assertNotIn(self.vector["phrase"].encode("ascii"), encoded)
        self.assertEqual(
            {item.name for item in fields(AccessVerifierState)},
            {"access_generation", "verifier_key", "version"},
        )
        self.assertFalse(hasattr(state, "board_master_key"))
        self.assertFalse(hasattr(state, "display_token_key"))
        self.assertEqual(AccessVerifierState.from_bytes(encoded), state)
        self.assertNotIn(self.vector["access_auth_key"], repr(state))

    def test_access_state_rejects_duplicates_unknown_fields_and_bad_lengths(self) -> None:
        state = AccessVerifierState(
            access_generation=self.context.access_generation,
            verifier_key=self.keys.authentication_key,
        )
        valid = json.loads(state.to_bytes())
        invalid = [
            b'{"v":1,"v":1,"access_generation":"x","verifier_key":"x"}',
            json.dumps({**valid, "extra": 1}).encode(),
            json.dumps({**valid, "access_generation": "AA"}).encode(),
            json.dumps({**valid, "v": 2}).encode(),
            json.dumps({**valid, "v": True}).encode(),
            json.dumps(valid, indent=2, sort_keys=True).encode(),
            state.to_bytes().rstrip(b"\n"),
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(AccessError):
                AccessVerifierState.from_bytes(value)

        with self.assertRaises(AccessError):
            AccessVerifierState(
                access_generation=self.context.access_generation,
                verifier_key=self.keys.authentication_key,
                version=True,
            )

    def test_display_ids_normalise_and_invalid_characters_are_rejected(self) -> None:
        self.assertEqual(normalise_display_id("j7k"), "J7K")
        for value in ("A0C", "AI1", "AB", "ABCD", "A-C", " A2"):
            with self.subTest(value=value), self.assertRaises(AccessError):
                normalise_display_id(value)

    def test_initialisation_api_does_not_accept_a_manual_phrase_factory(self) -> None:
        parameters = inspect.signature(create_initial_access).parameters
        self.assertNotIn("phrase", parameters)
        self.assertNotIn("phrase_factory", parameters)
        self.assertIn("phrase_sampler", parameters)
        with self.assertRaises(ValueError):
            create_initial_access(
                phrase_sampler=lambda _words, _count: (
                    "manual",
                    "phrase",
                    "outside",
                    "list",
                ),
                random_source=_SequenceSource(bytes(32), bytes(16)),
            )

    def test_initialisation_rotation_and_private_state_files(self) -> None:
        first_server_id = bytes(range(32))
        first_generation = bytes(range(32, 48))
        second_generation = bytes(range(48, 64))
        setup = create_initial_access(
            phrase_sampler=_sampler_for(self.vector["phrase"]),
            random_source=_SequenceSource(first_server_id, first_generation),
        )
        self.assertEqual(setup.context.server_id, first_server_id)
        self.assertEqual(setup.context.access_generation, first_generation)
        self.assertNotIn(self.vector["phrase"], repr(setup))

        rotated = rotate_access(
            setup.context.server_id,
            phrase_sampler=_sampler_for("amber meadow signal copper"),
            random_source=_SequenceSource(second_generation),
        )
        self.assertEqual(rotated.context.server_id, setup.context.server_id)
        self.assertNotEqual(rotated.context.access_generation, setup.context.access_generation)
        self.assertNotEqual(
            rotated.verifier_state.verifier_key,
            setup.verifier_state.verifier_key,
        )
        challenge = bytes(range(64, 96))
        nonce = bytes(range(16))
        old_generation_proof = create_access_proof(
            setup.verifier_state.verifier_key,
            rotated.context,
            challenge=challenge,
            client_nonce=nonce,
        )
        rotated_verifier = AccessChallengeVerifier(
            rotated.context,
            rotated.verifier_state,
            challenge=challenge,
        )
        self.assertFalse(
            rotated_verifier.verify(
                client_nonce=nonce,
                proof=old_generation_proof,
            )
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            server_path = root / "server-id.bin"
            state_path = root / "access-state.json"
            save_initial_access(
                setup,
                server_id_path=server_path,
                access_state_path=state_path,
            )
            self.assertEqual(load_server_id(server_path), setup.context.server_id)
            self.assertEqual(load_access_state(state_path), setup.verifier_state)
            with self.assertRaises(AccessError) as raised:
                save_access_state(state_path, setup.verifier_state)
            self.assertEqual(raised.exception.code, AccessErrorCode.ALREADY_INITIALISED)
            if os.name == "posix":
                self.assertEqual(server_path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(state_path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
