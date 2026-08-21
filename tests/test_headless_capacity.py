from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from phase3_support import FakeClock, start_tls_server
from the_grid.access import derive_access_keys
from the_grid.client import HeadlessClient
from the_grid.hub import BoardMessage, encrypt_board_message

ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ23456789"


def display_id(index: int) -> str:
    base = len(ALPHABET)
    a = ALPHABET[(index // (base * base)) % base]
    b = ALPHABET[(index // base) % base]
    c = ALPHABET[index % base]
    return a + b + c


class HeadlessCapacityTests(unittest.IsolatedAsyncioTestCase):

    async def test_full_large_board_snapshot_is_paginated_below_frame_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            setup, server, host, port, tls = await start_tls_server(Path(temporary))
            observer = HeadlessClient(host, port, ssl_context=tls)
            try:
                keys = derive_access_keys(setup.phrase, setup.context)
                large_text = "\\" * 1024
                for index in range(24):
                    encrypted = encrypt_board_message(
                        BoardMessage(display_id(index), large_text),
                        setup.context,
                        keys,
                    )
                    result = server.board.post(encrypted, now=int(server.clock()) + index)
                    self.assertTrue(result.accepted)
                await observer.connect_ready(setup.phrase, "ZZZ")
                self.assertEqual(len(observer.board_records), 24)
                self.assertTrue(all(item.message.text == large_text for item in observer.board_records))
            finally:
                await observer.close()
                await server.close()

    async def test_25th_post_evicts_oldest_and_does_not_reset_its_cooldown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            clock = FakeClock()
            setup, server, host, port, tls = await start_tls_server(
                Path(temporary), clock=clock
            )
            clients = [HeadlessClient(host, port, ssl_context=tls) for _ in range(25)]
            try:
                for index, client in enumerate(clients):
                    await client.connect_ready(setup.phrase, display_id(index))

                first_message_id = None
                for index, client in enumerate(clients):
                    if index:
                        clock.advance(1)
                    outcome = await client.post_board(f"message {index + 1}")
                    self.assertTrue(outcome.accepted)
                    event = await asyncio.wait_for(clients[-1].board_events.get(), 2)
                    if event.kind == "remove":
                        event = await asyncio.wait_for(clients[-1].board_events.get(), 2)
                    if index == 0:
                        first_message_id = event.record.stored.record.message_id

                # The 25th transaction emits remove then update. Let the observer
                # finish processing both events if the final update raced the response.
                for _ in range(100):
                    if len(clients[-1].board_records) == 24:
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(len(clients[-1].board_records), 24)
                current_ids = {
                    item.stored.record.message_id for item in clients[-1].board_records
                }
                self.assertNotIn(first_message_id, current_ids)

                blocked = await clients[0].post_board("still blocked after eviction")
                self.assertFalse(blocked.accepted)
                self.assertEqual(blocked.reason, "cooldown")
                self.assertGreater(blocked.remaining_seconds, 0)
            finally:
                await asyncio.gather(*(client.close() for client in clients), return_exceptions=True)
                await server.close()


if __name__ == "__main__":
    unittest.main()
