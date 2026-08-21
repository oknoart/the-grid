from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from phase3_support import start_plain_server
from the_grid.client import ClientError, HeadlessClient
from the_grid.protocol import b64url_encode
from the_grid.relay import RelayLimits
from the_grid.sessions import SessionEventType, derive_session_phrase_material


class SessionRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def _ready_pair(self, root: Path, *, limits: RelayLimits | None = None):
        setup, server, host, port = await start_plain_server(root, limits=limits)
        creator = HeadlessClient(host, port, allow_plain=True, request_timeout=2)
        joiner = HeadlessClient(host, port, allow_plain=True, request_timeout=2)
        await asyncio.gather(
            creator.connect_ready(setup.phrase, "ABC"),
            joiner.connect_ready(setup.phrase, "J7K"),
        )
        return setup, server, creator, joiner

    async def test_waiting_room_expires_and_client_can_start_again(self) -> None:
        limits = RelayLimits(
            heartbeat_interval=0.05,
            dead_timeout=0.30,
            session_wait_timeout=0.08,
            maintenance_interval=0.02,
        )
        with tempfile.TemporaryDirectory() as temporary:
            setup, server, creator, joiner = await self._ready_pair(
                Path(temporary), limits=limits
            )
            try:
                await creator.start_session()
                closed = await asyncio.wait_for(creator.session_closed_events.get(), 1)
                self.assertEqual(closed.reason, "wait_timeout")
                # Expired waiting state is gone; starting a fresh room is allowed.
                phrase = await creator.start_session()
                self.assertEqual(len(phrase.split()), 4)
            finally:
                await asyncio.gather(creator.close(), joiner.close(), return_exceptions=True)
                await server.close()

    async def test_paired_but_unfinished_handshake_expires(self) -> None:
        limits = RelayLimits(
            heartbeat_interval=0.05,
            dead_timeout=0.30,
            session_handshake_timeout=0.08,
            maintenance_interval=0.02,
        )
        with tempfile.TemporaryDirectory() as temporary:
            setup, server, creator, joiner = await self._ready_pair(
                Path(temporary), limits=limits
            )
            try:
                phrase = await creator.start_session()
                await joiner.join_session(phrase)
                left, right = await asyncio.gather(
                    asyncio.wait_for(creator.session_closed_events.get(), 1),
                    asyncio.wait_for(joiner.session_closed_events.get(), 1),
                )
                self.assertEqual(left.reason, "handshake_timeout")
                self.assertEqual(right.reason, "handshake_timeout")
                self.assertEqual(server._routes, {})
            finally:
                await asyncio.gather(creator.close(), joiner.close(), return_exceptions=True)
                await server.close()

    async def test_wrong_phrase_proof_closes_route_without_leaking_pair(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            setup, server, creator, joiner = await self._ready_pair(Path(temporary))
            try:
                phrase = await creator.start_session()
                await joiner.join_session(phrase)
                joiner._session_phrase_material = derive_session_phrase_material(
                    "alpha beta gamma delta",
                    server_id=setup.context.server_id,
                )
                results = await asyncio.gather(
                    creator.complete_session(),
                    joiner.complete_session(),
                    return_exceptions=True,
                )
                self.assertTrue(any(isinstance(result, ClientError) for result in results))
                for _ in range(50):
                    if not server._routes:
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(server._routes, {})
                self.assertFalse(
                    creator.session_channel is not None and creator.session_channel.active
                )
                self.assertFalse(
                    joiner.session_channel is not None and joiner.session_channel.active
                )
            finally:
                await asyncio.gather(creator.close(), joiner.close(), return_exceptions=True)
                await server.close()

    async def test_replayed_encrypted_frame_ends_comm_but_keeps_grid_connection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            setup, server, creator, joiner = await self._ready_pair(Path(temporary))
            try:
                phrase = await creator.start_session()
                await joiner.join_session(phrase)
                await asyncio.gather(creator.complete_session(), joiner.complete_session())
                channel = creator.session_channel
                self.assertIsNotNone(channel)
                frame = channel.encrypt_text("once only")
                fields = {
                    "pair_id": b64url_encode(creator.pair_id),
                    "session_id": b64url_encode(frame.session_id),
                    "direction": frame.direction.value,
                    "counter": frame.counter,
                    "body": b64url_encode(frame.body),
                }
                await creator._request("session_data", **fields)
                event = await asyncio.wait_for(joiner.session_events.get(), 1)
                self.assertEqual(event.event_type, SessionEventType.TEXT)
                self.assertEqual(event.value, "once only")
                await creator._request("session_data", **fields)
                failure = await asyncio.wait_for(joiner.session_closed_events.get(), 1)
                self.assertEqual(failure.reason, "integrity_failure")
                await asyncio.sleep(0.05)
                self.assertTrue(joiner.connected)
                await joiner.synchronise_board()
                self.assertIsNone(joiner.pair_id)
            finally:
                await asyncio.gather(creator.close(), joiner.close(), return_exceptions=True)
                await server.close()


if __name__ == "__main__":
    unittest.main()
