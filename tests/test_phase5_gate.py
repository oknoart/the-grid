from __future__ import annotations

import asyncio
import os
import socket
import tempfile
import unittest
from pathlib import Path

from the_grid.access import create_initial_access, rotate_access
from the_grid.client import ClientError, HeadlessClient, create_client_ssl_context
from the_grid.server_admin import backup_server, export_client_profile, initialise_server
from the_grid.server_runtime import ServerRuntime, admin_request


def unused_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class PhaseFiveCompletionGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_owner_can_initialise_run_status_rotate_backup_and_export_without_source_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "server"
            port = unused_tcp_port()
            setup = create_initial_access()
            initialised = initialise_server(
                state_dir=root,
                public_host="127.0.0.1",
                public_port=port,
                listen_host="127.0.0.1",
                listen_port=port,
                setup=setup,
            )
            config = initialised.config
            runtime = ServerRuntime(config)
            await runtime.start()
            tls = create_client_ssl_context(config.ca_certificate)
            first = HeadlessClient("127.0.0.1", port, ssl_context=tls, request_timeout=3.0)
            try:
                await first.connect_ready(initialised.phrase, "ABC")
                self.assertTrue((await first.post_board("phase five gate")).accepted)
                live = await admin_request(config, "status")
                self.assertEqual(live["status"]["connections"], 1)
                self.assertEqual(live["status"]["messages"], 1)

                rotated = rotate_access(setup.context.server_id)
                await admin_request(config, "rotate", state=rotated.verifier_state)
                self.assertEqual(runtime.relay.board.counts(), (0, 0))
                await asyncio.sleep(0.05)
                with self.assertRaises(ClientError):
                    await first.post_board("old generation")

                fresh = HeadlessClient("127.0.0.1", port, ssl_context=tls, request_timeout=3.0)
                try:
                    await fresh.connect_ready(rotated.phrase, "J7K")
                    self.assertEqual(fresh.display_id, "J7K")
                finally:
                    await fresh.close()

                export_dir = Path(directory) / "client"
                export_client_profile(export_dir, root / "server.json")
                self.assertTrue((export_dir / "okno-grid-ca.pem").is_file())

                backup = backup_server(Path(directory) / "backup.tar.gz", root / "server.json")
                self.assertTrue(backup.is_file())
                self.assertEqual(os.stat(backup).st_mode & 0o777, 0o600)
            finally:
                await first.close()
                await runtime.close()


if __name__ == "__main__":
    unittest.main()
