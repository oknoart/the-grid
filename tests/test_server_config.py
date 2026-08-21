from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from the_grid.protocol import MAX_OUTER_FRAME_BYTES
from the_grid.server_config import (
    ServerConfigError,
    default_server_config_path,
    default_server_state_dir,
    format_server_config,
    load_server_config,
    make_server_config,
    save_server_config,
    server_config_from_mapping,
)


class ServerConfigTests(unittest.TestCase):
    def test_macos_state_path_is_separate_from_client_config(self) -> None:
        home = Path("/Users/example")
        state = default_server_state_dir(platform_name="darwin", home=home)
        self.assertEqual(state, home / "Library" / "Application Support" / "okno" / "server")
        self.assertEqual(
            default_server_config_path(platform_name="darwin", home=home),
            state / "server.json",
        )

    def test_linux_state_path_uses_xdg_state_home(self) -> None:
        state = default_server_state_dir(
            platform_name="linux",
            home=Path("/home/example"),
            environ={"XDG_STATE_HOME": "/srv/state"},
        )
        self.assertEqual(state, Path("/srv/state/okno/server"))

    def test_canonical_config_round_trips_with_private_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = make_server_config(root, public_host="grid.example.net")
            path = save_server_config(config, root / "server.json")
            self.assertEqual(load_server_config(path), config)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(root).st_mode & 0o777, 0o700)
            parsed = json.loads(format_server_config(config))
            self.assertEqual(parsed["v"], 1)
            self.assertEqual(parsed["limits"]["max_frame_bytes"], MAX_OUTER_FRAME_BYTES)
            self.assertEqual(parsed["public"]["host"], "grid.example.net")

    def test_default_paths_live_under_one_state_root(self) -> None:
        config = make_server_config(Path("/state/okno"), public_host="grid.example.net")
        for path in (
            config.certificate,
            config.private_key,
            config.ca_certificate,
            config.database,
            config.server_id,
            config.access_state,
            config.admin_socket,
            config.pid_file,
            config.log_file,
        ):
            self.assertTrue(path.is_relative_to(Path("/state/okno")))

    def test_unknown_or_missing_fields_are_rejected(self) -> None:
        config = make_server_config(Path("/state"), public_host="grid.example.net")
        parsed = json.loads(format_server_config(config))
        parsed["extra"] = True
        with self.assertRaises(ServerConfigError):
            server_config_from_mapping(parsed)
        parsed.pop("extra")
        parsed["tls"].pop("private_key")
        with self.assertRaises(ServerConfigError):
            server_config_from_mapping(parsed)

    def test_network_hosts_are_restricted_to_ip_or_ascii_dns_names(self) -> None:
        valid = (
            "grid.example.net",
            "localhost",
            "127.0.0.1",
            "::1",
            "0.0.0.0",
        )
        for host in valid:
            with self.subTest(host=host):
                make_server_config(Path("/state"), public_host=host, listen_host=host)

        invalid = (
            "https://grid.example.net",
            "grid.example.net/path",
            "grid_example.net",
            "-grid.example.net",
            "grid.example.net.",
            "gríd.example.net",
        )
        for host in invalid:
            with self.subTest(host=host):
                with self.assertRaises(ServerConfigError):
                    make_server_config(Path("/state"), public_host=host)

    def test_overwrite_requires_explicit_flag(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = make_server_config(root, public_host="grid.example.net")
            path = root / "server.json"
            save_server_config(config, path)
            with self.assertRaises(ServerConfigError):
                save_server_config(config, path)
            save_server_config(config, path, overwrite=True)


if __name__ == "__main__":
    unittest.main()
