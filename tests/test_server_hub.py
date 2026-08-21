from __future__ import annotations

import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path

from phase3_support import FakeClock
from the_grid.hub import (
    BOARD_COOLDOWN_SECONDS,
    BOARD_LIFETIME_SECONDS,
    BoardStore,
    BoardStoreError,
    BoardStoreErrorCode,
    EncryptedBoardRecord,
)


def record(number: int, token_number: int | None = None) -> EncryptedBoardRecord:
    token_number = number if token_number is None else token_number
    return EncryptedBoardRecord(
        message_id=number.to_bytes(16, "big"),
        display_token=token_number.to_bytes(16, "big"),
        ciphertext=b"x" * 32,
    )


class BoardStoreTests(unittest.TestCase):
    def test_first_generation_binding_clears_ambiguous_legacy_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "board.sqlite3"
            legacy = BoardStore(database)
            try:
                self.assertTrue(legacy.post(record(1), now=1_700_000_000).accepted)
                self.assertEqual(legacy.counts(), (1, 1))
            finally:
                legacy.close()

            bound = BoardStore(database)
            try:
                self.assertTrue(bound.bind_access_generation(b"A" * 16))
                self.assertEqual(bound.counts(), (0, 0))
                self.assertFalse(bound.bind_access_generation(b"A" * 16))
            finally:
                bound.close()

    def test_access_generation_change_clears_messages_cooldowns_and_spent_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "board.sqlite3"
            first_generation = b"A" * 16
            second_generation = b"B" * 16
            store = BoardStore(database, access_generation=first_generation)
            try:
                record = EncryptedBoardRecord(
                    message_id=b"M" * 16,
                    display_token=b"T" * 16,
                    ciphertext=b"ciphertext" + b"0" * 16,
                )
                self.assertTrue(store.post(record, now=1_700_000_000).accepted)
                self.assertEqual(store.counts(), (1, 1))
                self.assertTrue(store.bind_access_generation(second_generation))
                self.assertEqual(store.counts(), (0, 0))
                self.assertFalse(store.bind_access_generation(second_generation))
                self.assertTrue(store.post(record, now=1_700_000_001).accepted)
            finally:
                store.close()

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.clock = FakeClock()
        self.path = Path(self.temp.name) / "board.sqlite3"
        self.store = BoardStore(self.path, clock=self.clock)

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def test_post_sets_server_timestamps_and_cooldown(self) -> None:
        result = self.store.post(record(1))
        self.assertTrue(result.accepted)
        self.assertIsNotNone(result.stored)
        self.assertEqual(result.stored.created_at, int(self.clock()))
        self.assertEqual(result.stored.expires_at, int(self.clock()) + BOARD_LIFETIME_SECONDS)
        self.assertEqual(self.store.cooldown_remaining(record(1).display_token), BOARD_COOLDOWN_SECONDS)

    def test_second_same_token_post_is_blocked_without_changing_board(self) -> None:
        self.assertTrue(self.store.post(record(1, 7)).accepted)
        self.clock.advance(10)
        blocked = self.store.post(record(2, 7))
        self.assertFalse(blocked.accepted)
        self.assertEqual(blocked.next_post_at, int(self.clock()) - 10 + BOARD_COOLDOWN_SECONDS)
        current, _ = self.store.list_current()
        self.assertEqual([item.record.message_id for item in current], [record(1).message_id])

    def test_25th_message_evicts_oldest_but_keeps_its_cooldown(self) -> None:
        first = record(1, 100)
        self.assertTrue(self.store.post(first).accepted)
        for number in range(2, 26):
            self.clock.advance(1)
            self.assertTrue(self.store.post(record(number, 100 + number)).accepted)
        current, _ = self.store.list_current()
        self.assertEqual(len(current), 24)
        self.assertNotIn(first.message_id, {item.record.message_id for item in current})
        self.assertGreater(self.store.cooldown_remaining(first.display_token), 0)

    def test_expiry_and_cooldown_cleanup_are_independent_of_visibility(self) -> None:
        item = record(1, 9)
        self.store.post(item)
        self.clock.advance(BOARD_LIFETIME_SECONDS)
        current, removed = self.store.list_current()
        self.assertEqual(current, ())
        self.assertEqual(removed, (item.message_id,))
        self.assertEqual(self.store.cooldown_remaining(item.display_token), 0)
        self.assertEqual(self.store.counts(), (0, 0))

    def test_restart_preserves_unexpired_ciphertext_and_cooldown(self) -> None:
        item = record(1, 9)
        self.store.post(item)
        self.store.close()
        self.store = BoardStore(self.path, clock=self.clock)
        current, _ = self.store.list_current()
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0].record, item)
        self.assertEqual(self.store.cooldown_remaining(item.display_token), BOARD_COOLDOWN_SECONDS)

    def test_duplicate_message_id_is_rejected_even_after_expiry(self) -> None:
        self.store.post(record(1, 1))
        self.clock.advance(BOARD_LIFETIME_SECONDS)
        self.store.cleanup()
        with self.assertRaises(BoardStoreError) as caught:
            self.store.post(record(1, 2))
        self.assertEqual(caught.exception.code, BoardStoreErrorCode.DUPLICATE_MESSAGE)

    def test_database_contains_ciphertext_and_tokens_not_plaintext_fields(self) -> None:
        item = record(1, 1)
        self.store.post(item)
        with closing(sqlite3.connect(self.path)) as db:
            columns = [row[1] for row in db.execute("PRAGMA table_info(board_messages)")]
            row = db.execute(
                "SELECT message_id, id_token, ciphertext FROM board_messages"
            ).fetchone()
        self.assertEqual(
            columns,
            ["message_id", "id_token", "created_at", "expires_at", "ciphertext"],
        )
        self.assertEqual(tuple(bytes(value) for value in row), (item.message_id, item.display_token, item.ciphertext))
        self.assertNotIn("display_id", columns)
        self.assertNotIn("plaintext", columns)


if __name__ == "__main__":
    unittest.main()
