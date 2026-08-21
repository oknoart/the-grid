from __future__ import annotations

import asyncio
import os
import socket
import tempfile
import unittest
from pathlib import Path

from the_grid.access import (
    create_initial_access,
    load_access_state,
    rotate_access,
    save_initial_access,
)
from the_grid.client import ClientError, HeadlessClient, create_client_ssl_context
from the_grid.server_admin import ServerAdminError, rotate_server_access, server_status
from the_grid.server_config import make_server_config, save_server_config
from the_grid.server_runtime import (
    ServerRuntime,
    admin_request,
    pid_file_process,
    server_pid_lock_held,
)
from the_grid.server_tls import initialise_private_ca_tls


def unused_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class ServerRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def _initialised(self, root: Path):
        port = unused_tcp_port()
        config = make_server_config(
            root,
            public_host="127.0.0.1",
            public_port=port,
            listen_host="127.0.0.1",
            listen_port=port,
        )
        save_server_config(config, root / "server.json")
        initialise_private_ca_tls(config)
        setup = create_initial_access()
        save_initial_access(
            setup,
            server_id_path=config.server_id,
            access_state_path=config.access_state,
        )
        return config, setup

    async def test_runtime_exposes_owner_only_status_socket_and_pid_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config, _setup = self._initialised(Path(directory))
            runtime = ServerRuntime(config)
            await runtime.start()
            try:
                self.assertEqual(pid_file_process(config), os.getpid())
                self.assertTrue(server_pid_lock_held(config))
                self.assertEqual(os.stat(config.admin_socket).st_mode & 0o777, 0o600)
                response = await admin_request(config, "status")
                status = response["status"]
                self.assertTrue(status["running"])
                self.assertEqual(status["public_host"], "127.0.0.1")
                self.assertEqual(status["connections"], 0)
            finally:
                await runtime.close()
            self.assertFalse(config.admin_socket.exists())
            self.assertFalse(config.pid_file.exists())
            self.assertFalse(server_pid_lock_held(config))

    async def test_admin_socket_failure_never_falls_back_to_offline_state_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config, setup = self._initialised(Path(directory))
            runtime = ServerRuntime(config)
            await runtime.start()
            original_generation = load_access_state(config.access_state).access_generation
            try:
                # Removing the filesystem name makes new admin connections fail while
                # the runtime itself still holds its exclusive PID lock and network listener.
                config.admin_socket.unlink()
                with self.assertRaisesRegex(ServerAdminError, "admin channel is unavailable"):
                    await server_status(config.pid_file.parent / "server.json")
                with self.assertRaisesRegex(ServerAdminError, "admin channel is unavailable"):
                    await rotate_server_access(config.pid_file.parent / "server.json")
                self.assertEqual(
                    load_access_state(config.access_state).access_generation,
                    original_generation,
                )
                self.assertEqual(runtime.relay.context.access_generation, setup.context.access_generation)
            finally:
                await runtime.close()

    async def test_live_rotation_disconnects_old_clients_and_clears_generation_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config, setup = self._initialised(Path(directory))
            runtime = ServerRuntime(config)
            await runtime.start()
            tls = create_client_ssl_context(config.ca_certificate)
            old_client = HeadlessClient(
                "127.0.0.1",
                config.listen_port,
                ssl_context=tls,
                request_timeout=3.0,
            )
            try:
                await old_client.connect_ready(setup.phrase, "ABC")
                result = await old_client.post_board("before rotation")
                self.assertTrue(result.accepted)
                self.assertEqual(runtime.relay.board.counts(), (1, 1))

                rotated = rotate_access(setup.context.server_id)
                response = await admin_request(config, "rotate", state=rotated.verifier_state)
                self.assertEqual(response["rotation"]["cleared_messages"], 1)
                self.assertEqual(runtime.relay.board.counts(), (0, 0))
                self.assertEqual(
                    load_access_state(config.access_state).access_generation,
                    rotated.context.access_generation,
                )

                await asyncio.sleep(0.05)
                with self.assertRaises(ClientError):
                    await old_client.post_board("old session")

                rejected = HeadlessClient("127.0.0.1", config.listen_port, ssl_context=tls)
                try:
                    await rejected.connect()
                    with self.assertRaises(ClientError):
                        await rejected.authenticate(setup.phrase)
                finally:
                    await rejected.close()

                fresh = HeadlessClient("127.0.0.1", config.listen_port, ssl_context=tls)
                try:
                    await fresh.connect_ready(rotated.phrase, "J7K")
                    self.assertEqual(fresh.display_id, "J7K")
                finally:
                    await fresh.close()
            finally:
                await old_client.close()
                await runtime.close()

            log_text = config.log_file.read_text(encoding="utf-8")
            for secret in (
                setup.phrase,
                rotated.phrase,
                "ABC",
                "J7K",
                "before rotation",
            ):
                with self.subTest(secret=secret):
                    self.assertNotIn(secret, log_text)
            self.assertIn("server started", log_text)
            self.assertIn("access rotated", log_text)
            self.assertIn("server stopped", log_text)


if __name__ == "__main__":
    unittest.main()
