from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from phase3_support import start_tls_server
from the_grid.client import HeadlessClient
from the_grid.sessions import SessionEventType


class PhaseThreeCompletionGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_tls_headless_clients_share_live_board_and_encrypted_session(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            setup, server, host, port, tls = await start_tls_server(Path(temporary))
            first = HeadlessClient(host, port, ssl_context=tls)
            second = HeadlessClient(host, port, ssl_context=tls)
            try:
                await asyncio.gather(
                    first.connect_ready(setup.phrase, "ABC"),
                    second.connect_ready(setup.phrase, "J7K"),
                )
                outcome = await first.post_board("phase three gate")
                self.assertTrue(outcome.accepted)
                event = await asyncio.wait_for(second.board_events.get(), 2)
                self.assertEqual(event.record.message.text, "phase three gate")
                self.assertFalse((await first.post_board("blocked")).accepted)

                phrase = await first.start_session()
                await second.join_session(phrase)
                left, right = await asyncio.gather(
                    first.complete_session(), second.complete_session()
                )
                self.assertEqual(left.verification_code, right.verification_code)
                await first.send_session_text("routed ciphertext")
                private = await asyncio.wait_for(second.session_events.get(), 2)
                self.assertEqual(private.event_type, SessionEventType.TEXT)
                self.assertEqual(private.value, "routed ciphertext")
            finally:
                await asyncio.gather(first.close(), second.close(), return_exceptions=True)
                await server.close()


if __name__ == "__main__":
    unittest.main()
