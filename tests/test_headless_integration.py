from __future__ import annotations

import asyncio
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path

from phase3_support import FakeClock, start_tls_server
from the_grid.client import ClientError, ClientErrorCode, HeadlessClient
from the_grid.relay import RelayLimits, RelayServer, create_server_ssl_context
from the_grid.sessions import SessionEventType


class HeadlessTlsIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.setup, self.server, self.host, self.port, self.tls = await start_tls_server(
            self.root
        )
        self.clients: list[HeadlessClient] = []

    async def asyncTearDown(self) -> None:
        await asyncio.gather(*(client.close() for client in self.clients), return_exceptions=True)
        await self.server.close()
        self.temp.cleanup()

    def client(self) -> HeadlessClient:
        client = HeadlessClient(self.host, self.port, ssl_context=self.tls)
        self.clients.append(client)
        return client

    async def ready(self, display_id: str) -> HeadlessClient:
        client = self.client()
        await client.connect_ready(self.setup.phrase, display_id)
        return client

    async def test_correct_access_reserves_id_and_live_board_updates_both_clients(self) -> None:
        first, second = await asyncio.gather(self.ready("ABC"), self.ready("J7K"))
        outcome = await first.post_board("are you receiving this?")
        self.assertTrue(outcome.accepted)
        first_event, second_event = await asyncio.gather(
            asyncio.wait_for(first.board_events.get(), 2),
            asyncio.wait_for(second.board_events.get(), 2),
        )
        self.assertEqual(first_event.kind, "update")
        self.assertEqual(second_event.kind, "update")
        self.assertEqual(second_event.record.message.display_id, "ABC")
        self.assertEqual(second_event.record.message.text, "are you receiving this?")
        self.assertEqual(
            [(item.message.display_id, item.message.text) for item in second.board_records],
            [("ABC", "are you receiving this?")],
        )

        blocked = await first.post_board("too soon")
        self.assertFalse(blocked.accepted)
        self.assertEqual(blocked.reason, "cooldown")
        self.assertGreater(blocked.remaining_seconds, 0)

    async def test_wrong_access_is_rejected_without_entering_grid(self) -> None:
        client = self.client()
        await client.connect()
        with self.assertRaises(ClientError) as caught:
            await client.authenticate("alpha beta gamma delta")
        self.assertEqual(caught.exception.code, ClientErrorCode.ACCESS)
        self.assertIsNone(client.display_token)

    async def test_active_display_collision_is_rejected(self) -> None:
        first = await self.ready("ABC")
        second = self.client()
        await second.connect()
        await second.authenticate(self.setup.phrase)
        with self.assertRaises(ClientError) as caught:
            await second.reserve_display("ABC")
        self.assertEqual(caught.exception.code, ClientErrorCode.DISPLAY_UNAVAILABLE)
        self.assertIsNotNone(first.display_token)


    async def test_server_restart_preserves_board_and_cooldown_but_not_reservations(self) -> None:
        first = await self.ready("ABC")
        self.assertTrue((await first.post_board("survives restart")).accepted)
        await asyncio.wait_for(first.board_events.get(), 2)
        await first.close()
        await self.server.close()

        self.server = RelayServer(
            context=self.setup.context,
            verifier_state=self.setup.verifier_state,
            database=self.root / "grid.sqlite3",
            host="127.0.0.1",
            port=0,
            ssl_context=create_server_ssl_context(
                self.root / "cert.pem", self.root / "key.pem"
            ),
        )
        await self.server.start()
        self.host, self.port = self.server.address

        returning = HeadlessClient(self.host, self.port, ssl_context=self.tls)
        self.clients.append(returning)
        await returning.connect_ready(self.setup.phrase, "ABC")
        self.assertEqual(
            [(item.message.display_id, item.message.text) for item in returning.board_records],
            [("ABC", "survives restart")],
        )
        blocked = await returning.post_board("still on cooldown")
        self.assertFalse(blocked.accepted)
        self.assertEqual(blocked.reason, "cooldown")

    async def test_two_user_session_routes_only_ciphertext_and_ends_cleanly(self) -> None:
        creator, joiner = await asyncio.gather(self.ready("ABC"), self.ready("J7K"))
        phrase = await creator.start_session()
        await joiner.join_session(phrase)
        creator_channel, joiner_channel = await asyncio.gather(
            creator.complete_session(), joiner.complete_session()
        )
        self.assertTrue(creator_channel.active)
        self.assertTrue(joiner_channel.active)
        self.assertEqual(creator_channel.peer_display_id, "J7K")
        self.assertEqual(joiner_channel.peer_display_id, "ABC")
        self.assertEqual(
            creator_channel.verification_code,
            joiner_channel.verification_code,
        )

        await creator.send_session_text("private hello")
        event = await asyncio.wait_for(joiner.session_events.get(), 2)
        self.assertEqual(event.event_type, SessionEventType.TEXT)
        self.assertEqual(event.value, "private hello")

        await joiner.send_session_text("received")
        event = await asyncio.wait_for(creator.session_events.get(), 2)
        self.assertEqual(event.event_type, SessionEventType.TEXT)
        self.assertEqual(event.value, "received")

        third = await self.ready("M8Q")
        with self.assertRaises(ClientError) as caught:
            await third.join_session(phrase)
        self.assertEqual(caught.exception.code, ClientErrorCode.SESSION_UNAVAILABLE)

        await creator.end_session()
        close_event = await asyncio.wait_for(joiner.session_events.get(), 2)
        self.assertEqual(close_event.event_type, SessionEventType.CLOSE)
        self.assertEqual(close_event.value, "user_end")
        closed = await asyncio.wait_for(joiner.session_closed_events.get(), 2)
        self.assertEqual(closed.reason, "peer_close")
        self.assertTrue(creator_channel.discarded)
        self.assertTrue(joiner_channel.discarded)

        with closing(sqlite3.connect(self.root / "grid.sqlite3")) as db:
            tables = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        self.assertNotIn("session_messages", tables)
        self.assertNotIn("session_history", tables)

    async def test_disconnect_ends_live_session_without_resume(self) -> None:
        creator, joiner = await asyncio.gather(self.ready("ABC"), self.ready("J7K"))
        phrase = await creator.start_session()
        await joiner.join_session(phrase)
        await asyncio.gather(creator.complete_session(), joiner.complete_session())
        await creator.close()
        closed = await asyncio.wait_for(joiner.session_closed_events.get(), 2)
        self.assertEqual(closed.reason, "peer_disconnect")
        self.assertIsNone(joiner.pair_id)


class HeadlessExpiryIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_natural_expiry_is_broadcast_and_cooldown_expires_independently(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            clock = FakeClock()
            setup, server, host, port, tls = await start_tls_server(
                root,
                clock=clock,
                limits=RelayLimits(maintenance_interval=0.02),
            )
            client = HeadlessClient(host, port, ssl_context=tls)
            try:
                await client.connect_ready(setup.phrase, "ABC")
                result = await client.post_board("expires naturally")
                self.assertTrue(result.accepted)
                update = await asyncio.wait_for(client.board_events.get(), 2)
                self.assertEqual(update.kind, "update")

                clock.advance(86_400)
                removal = await asyncio.wait_for(client.board_events.get(), 2)
                self.assertEqual(removal.kind, "remove")
                self.assertEqual(client.board_records, ())

                available = await client.post_board("available again")
                self.assertTrue(available.accepted)
            finally:
                await client.close()
                await server.close()


if __name__ == "__main__":
    unittest.main()
