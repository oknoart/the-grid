from __future__ import annotations

import asyncio
import ssl
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from phase3_support import FakeClock, access_setup, start_plain_server, start_tls_server
from the_grid.client import ClientError, ClientErrorCode, HeadlessClient, create_client_ssl_context
from the_grid.protocol import encode_outer_frame, make_frame, read_outer_frame
from the_grid.relay import (
    RelayError,
    RelayLimits,
    RelayServer,
    _RelayConnection,
    _WindowRateLimiter,
)


class ServerProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_plain_transport_is_loopback_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            setup = access_setup()
            with self.assertRaises(RelayError):
                RelayServer(
                    context=setup.context,
                    verifier_state=setup.verifier_state,
                    database=Path(temporary) / "db.sqlite3",
                    host="0.0.0.0",
                    port=0,
                    allow_plain=True,
                )
        with self.assertRaises(ClientError):
            HeadlessClient("example.com", 7331, allow_plain=True)

    async def test_self_signed_tls_requires_explicit_trust(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            setup, server, host, port, _trusted = await start_tls_server(root)
            wrong_context = create_client_ssl_context()
            client = HeadlessClient(host, port, ssl_context=wrong_context)
            loop = asyncio.get_running_loop()
            previous_handler = loop.get_exception_handler()
            loop.set_exception_handler(lambda _loop, _context: None)
            try:
                with self.assertRaises(ClientError) as caught:
                    await client.connect()
                self.assertEqual(caught.exception.code, ClientErrorCode.CONNECTION)
            finally:
                await client.close()
                await server.close()
                await asyncio.sleep(0.05)
                loop.set_exception_handler(previous_handler)

    async def test_unsupported_protocol_gets_error_before_access_processing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            setup, server, host, port = await start_plain_server(Path(temporary))
            reader, writer = await asyncio.open_connection(host, port)
            try:
                writer.write(
                    encode_outer_frame(
                        {"v": 2, "type": "hello", "request_id": "r1", "client_version": "x", "capabilities": []}
                    )
                )
                await writer.drain()
                response = await asyncio.wait_for(read_outer_frame(reader), 2)
                self.assertEqual(response["type"], "error")
                self.assertEqual(response["code"], "unsupported_protocol")
                self.assertNotIn("access_challenge", response)
            finally:
                writer.close()
                await writer.wait_closed()
                await server.close()

    async def test_oversized_frame_closes_connection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _setup, server, host, port = await start_plain_server(Path(temporary))
            reader, writer = await asyncio.open_connection(host, port)
            try:
                writer.write(b"{" + b"x" * 17000 + b"}\n")
                await writer.drain()
                data = await asyncio.wait_for(reader.read(), 2)
                self.assertEqual(data, b"")
            finally:
                writer.close()
                await writer.wait_closed()
                await server.close()

    async def test_heartbeats_keep_headless_client_alive(self) -> None:
        limits = RelayLimits(
            heartbeat_interval=0.05,
            dead_timeout=0.20,
            maintenance_interval=0.02,
        )
        with tempfile.TemporaryDirectory() as temporary:
            setup, server, host, port = await start_plain_server(
                Path(temporary), limits=limits
            )
            client = HeadlessClient(host, port, allow_plain=True, request_timeout=2)
            try:
                await client.connect_ready(setup.phrase, "ABC")
                await asyncio.sleep(0.35)
                self.assertTrue(client.connected)
                await client.synchronise_board()
            finally:
                await client.close()
                await server.close()

    async def test_scrypt_work_does_not_starve_heartbeats(self) -> None:
        limits = RelayLimits(
            heartbeat_interval=0.03,
            dead_timeout=0.15,
            maintenance_interval=0.02,
        )
        with tempfile.TemporaryDirectory() as temporary:
            setup, server, host, port = await start_plain_server(
                Path(temporary), limits=limits
            )
            client = HeadlessClient(host, port, allow_plain=True, request_timeout=2)
            from the_grid import client as client_module

            original_access = client_module.derive_access_keys
            original_session = client_module.derive_session_phrase_material

            def slow_access(phrase, context):
                time.sleep(0.20)
                return original_access(phrase, context)

            def slow_session(phrase, *, server_id):
                time.sleep(0.20)
                return original_session(phrase, server_id=server_id)

            try:
                await client.connect()
                with patch(
                    "the_grid.client.derive_access_keys", side_effect=slow_access
                ):
                    await client.authenticate(setup.phrase)
                await client.reserve_display("ABC")
                with patch(
                    "the_grid.client.derive_session_phrase_material",
                    side_effect=slow_session,
                ):
                    phrase = await client.start_session()
                self.assertEqual(len(phrase.split()), 4)
                self.assertTrue(client.connected)
            finally:
                await client.close()
                await server.close()

    async def test_silent_raw_connection_is_closed_after_dead_timeout(self) -> None:
        limits = RelayLimits(
            heartbeat_interval=0.05,
            dead_timeout=0.16,
            maintenance_interval=0.02,
        )
        with tempfile.TemporaryDirectory() as temporary:
            _setup, server, host, port = await start_plain_server(
                Path(temporary), limits=limits
            )
            reader, writer = await asyncio.open_connection(host, port)
            try:
                writer.write(
                    encode_outer_frame(
                        make_frame(
                            "hello",
                            request_id="r1",
                            client_version="test",
                            capabilities=[],
                        )
                    )
                )
                await writer.drain()
                hello = await asyncio.wait_for(read_outer_frame(reader), 1)
                self.assertEqual(hello["type"], "hello")
                # Do not answer server pings. Read until EOF.
                while True:
                    data = await asyncio.wait_for(reader.readline(), 1)
                    if not data:
                        break
                self.assertEqual(data, b"")
            finally:
                writer.close()
                await writer.wait_closed()
                await server.close()

    async def test_display_lease_expires_after_disconnect(self) -> None:
        limits = RelayLimits(
            heartbeat_interval=0.05,
            dead_timeout=0.20,
            display_lease_timeout=0.08,
            maintenance_interval=0.02,
        )
        with tempfile.TemporaryDirectory() as temporary:
            setup, server, host, port = await start_plain_server(
                Path(temporary), limits=limits
            )
            first = HeadlessClient(host, port, allow_plain=True)
            second = HeadlessClient(host, port, allow_plain=True)
            third = HeadlessClient(host, port, allow_plain=True)
            try:
                await first.connect_ready(setup.phrase, "ABC")
                await second.connect()
                await second.authenticate(setup.phrase)
                with self.assertRaises(ClientError):
                    await second.reserve_display("ABC")
                await first.close()
                await asyncio.sleep(0.12)
                await third.connect()
                await third.authenticate(setup.phrase)
                await third.reserve_display("ABC")
                self.assertEqual(third.display_id, "ABC")
            finally:
                await asyncio.gather(first.close(), second.close(), third.close(), return_exceptions=True)
                await server.close()

    async def test_outbound_queue_is_bounded_and_slow_connection_is_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            setup = access_setup()
            server = RelayServer(
                context=setup.context,
                verifier_state=setup.verifier_state,
                database=Path(temporary) / "db.sqlite3",
                allow_plain=True,
                limits=RelayLimits(outbound_queue=1),
            )
            reader = asyncio.StreamReader()
            writer = Mock()
            writer.get_extra_info.return_value = ("127.0.0.1", 12345)
            writer.wait_closed = Mock(return_value=asyncio.sleep(0))
            connection = _RelayConnection(server, reader, writer)
            await connection.send_event(make_frame("ping", nonce="AA"))
            with self.assertRaises(ConnectionError):
                await connection.send_event(make_frame("ping", nonce="AQ"))
            await asyncio.sleep(0)
            self.assertTrue(connection.closed)
            server.board.close()


class RateLimiterTests(unittest.TestCase):
    def test_access_limiter_delays_after_five_failures_and_increases(self) -> None:
        clock = FakeClock(1000)
        limiter = _WindowRateLimiter(5, 600, clock=clock, increasing_delay=True)
        for _ in range(4):
            self.assertEqual(limiter.record("ip"), 0)
        first_delay = limiter.record("ip")
        self.assertGreaterEqual(first_delay, 1)
        clock.advance(first_delay)
        second_delay = limiter.record("ip")
        self.assertGreaterEqual(second_delay, first_delay)

    def test_window_limiter_prunes_old_attempts(self) -> None:
        clock = FakeClock(1000)
        limiter = _WindowRateLimiter(2, 10, clock=clock)
        self.assertEqual(limiter.record("ip"), 0)
        self.assertGreater(limiter.record("ip"), 0)
        clock.advance(11)
        limiter.prune()
        self.assertEqual(limiter.retry_after("ip"), 0)
        self.assertEqual(limiter.record("ip"), 0)


if __name__ == "__main__":
    unittest.main()
