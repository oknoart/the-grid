from __future__ import annotations

import unittest
from dataclasses import replace

from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

from phase2_support import unhex, vectors
from the_grid.access import AccessContext, derive_access_keys, derive_display_token
from the_grid.hub import (
    BoardCryptoError,
    BoardCryptoErrorCode,
    BoardMessage,
    EncryptedBoardRecord,
    board_associated_data,
    decrypt_board_record,
    derive_board_message_key,
    deserialise_board_message,
    encrypt_board_message,
    serialise_board_message,
)


class BoardCryptoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.access_vector = vectors()["access"]
        cls.board_vector = vectors()["board"]
        cls.context = AccessContext(
            unhex(cls.access_vector["server_id"]),
            unhex(cls.access_vector["access_generation"]),
        )
        cls.keys = derive_access_keys(cls.access_vector["phrase"], cls.context)
        cls.message = BoardMessage("J7K", "are you receiving this?")
        cls.record = encrypt_board_message(
            cls.message,
            cls.context,
            cls.keys,
            random_source=lambda length: unhex(cls.board_vector["message_id"]),
        )

    def test_board_fixed_vectors_and_round_trip(self) -> None:
        self.assertEqual(
            serialise_board_message(self.message).decode("utf-8"),
            self.board_vector["plaintext_utf8"],
        )
        self.assertEqual(
            derive_board_message_key(
                self.keys.board_master_key,
                message_id=self.record.message_id,
            ).hex(),
            self.board_vector["message_key"],
        )
        self.assertEqual(
            board_associated_data(
                self.context,
                message_id=self.record.message_id,
                display_token=self.record.display_token,
            ).hex(),
            self.board_vector["aad"],
        )
        self.assertEqual(self.record.ciphertext.hex(), self.board_vector["ciphertext"])
        self.assertNotIn(self.message.text.encode("utf-8"), self.record.ciphertext)
        self.assertEqual(decrypt_board_record(self.record, self.context, self.keys), self.message)
        self.assertNotIn("are you receiving this?", repr(self.message))
        self.assertNotIn(self.record.ciphertext.hex(), repr(self.record))
        self.assertNotIn(self.record.display_token.hex(), repr(self.record))

    def test_different_message_ids_produce_different_keys_and_ciphertexts(self) -> None:
        second = encrypt_board_message(
            self.message,
            self.context,
            self.keys,
            random_source=lambda length: b"\xff" * length,
        )
        self.assertNotEqual(second.message_id, self.record.message_id)
        self.assertNotEqual(second.ciphertext, self.record.ciphertext)
        self.assertEqual(decrypt_board_record(second, self.context, self.keys), self.message)

    def test_authorised_clients_decrypt_the_same_record(self) -> None:
        separately_derived = derive_access_keys(
            self.access_vector["phrase"],
            self.context,
        )
        self.assertEqual(
            decrypt_board_record(self.record, self.context, separately_derived),
            self.message,
        )

    def test_wrong_phrase_and_old_generation_cannot_decrypt(self) -> None:
        wrong_keys = derive_access_keys("amber meadow signal copper", self.context)
        with self.assertRaises(BoardCryptoError) as wrong_phrase:
            decrypt_board_record(self.record, self.context, wrong_keys)
        self.assertEqual(wrong_phrase.exception.code, BoardCryptoErrorCode.INTEGRITY)

        changed_generation = (
            bytes([self.context.access_generation[0] ^ 1])
            + self.context.access_generation[1:]
        )
        old_record_new_context = AccessContext(
            self.context.server_id,
            changed_generation,
        )
        with self.assertRaises(BoardCryptoError) as old_generation:
            decrypt_board_record(self.record, old_record_new_context, self.keys)
        self.assertEqual(old_generation.exception.code, BoardCryptoErrorCode.INTEGRITY)

    def test_ciphertext_message_id_token_and_metadata_tampering_fail(self) -> None:
        changed_ciphertext = bytes([self.record.ciphertext[0] ^ 1]) + self.record.ciphertext[1:]
        changed_message_id = bytes([self.record.message_id[0] ^ 1]) + self.record.message_id[1:]
        changed_token = bytes([self.record.display_token[0] ^ 1]) + self.record.display_token[1:]
        records = [
            replace(self.record, ciphertext=changed_ciphertext),
            replace(self.record, message_id=changed_message_id),
            replace(self.record, display_token=changed_token),
        ]
        for record in records:
            with self.subTest(record=record), self.assertRaises(BoardCryptoError) as raised:
                decrypt_board_record(record, self.context, self.keys)
            self.assertEqual(raised.exception.code, BoardCryptoErrorCode.INTEGRITY)

        changed_server = AccessContext(
            bytes([self.context.server_id[0] ^ 1]) + self.context.server_id[1:],
            self.context.access_generation,
        )
        with self.assertRaises(BoardCryptoError) as raised:
            decrypt_board_record(self.record, changed_server, self.keys)
        self.assertEqual(raised.exception.code, BoardCryptoErrorCode.INTEGRITY)

    def test_decrypted_id_must_recompute_to_outer_token(self) -> None:
        malicious_message = BoardMessage("ABC", "claimed with another token")
        outer_token = derive_display_token(self.keys.display_token_key, "J7K")
        key = derive_board_message_key(
            self.keys.board_master_key,
            message_id=self.record.message_id,
        )
        aad = board_associated_data(
            self.context,
            message_id=self.record.message_id,
            display_token=outer_token,
        )
        ciphertext = ChaCha20Poly1305(key).encrypt(
            b"\x00" * 12,
            serialise_board_message(malicious_message),
            aad,
        )
        malicious_record = EncryptedBoardRecord(
            message_id=self.record.message_id,
            display_token=outer_token,
            ciphertext=ciphertext,
        )
        with self.assertRaises(BoardCryptoError) as raised:
            decrypt_board_record(malicious_record, self.context, self.keys)
        self.assertEqual(raised.exception.code, BoardCryptoErrorCode.TOKEN_MISMATCH)

    def test_plaintext_json_is_strict_and_deterministic(self) -> None:
        self.assertEqual(
            deserialise_board_message(serialise_board_message(self.message)),
            self.message,
        )
        invalid = [
            b'{"id":"ABC","id":"J7K","text":"x","v":1}',
            b'{"id":"J7K","text":"x","v":1,"extra":1}',
            b'{"id":"J7K","text":"x","v":2}',
            b'{"id":"J7K","text":"x","v":true}',
            b'{"id":"J7K","text":1,"v":1}',
            b'{"id":"J7K","text":"x","v":NaN}',
            b'not json',
        ]
        for encoded in invalid:
            with self.subTest(encoded=encoded), self.assertRaises(BoardCryptoError):
                deserialise_board_message(encoded)

    def test_text_limit_uses_utf8_bytes(self) -> None:
        accepted = BoardMessage("ABC", "é" * 512)
        self.assertEqual(len(accepted.text.encode("utf-8")), 1024)
        for invalid in ("", "contains\x00nul", "é" * 513):
            with self.subTest(length=len(invalid)), self.assertRaises(BoardCryptoError):
                BoardMessage("ABC", invalid)


if __name__ == "__main__":
    unittest.main()
