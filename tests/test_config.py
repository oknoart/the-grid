from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from the_grid.config import (
    ConfigError,
    config_from_mapping,
    config_to_mapping,
    default_config_path,
    format_config,
    load_config,
    save_config,
    set_config_value,
)
from the_grid.models import ClientConfig, ServerSettings, UiSettings


class ConfigTests(unittest.TestCase):
    def test_linux_xdg_path(self) -> None:
        path = default_config_path(
            platform_name="linux",
            environ={"XDG_CONFIG_HOME": "/tmp/example-config"},
            home=Path("/home/example"),
        )
        self.assertEqual(path, Path("/tmp/example-config/the-grid/config.json"))

    def test_linux_fallback_path(self) -> None:
        path = default_config_path(
            platform_name="linux",
            environ={},
            home=Path("/home/example"),
        )
        self.assertEqual(path, Path("/home/example/.config/the-grid/config.json"))

    def test_macos_path(self) -> None:
        path = default_config_path(
            platform_name="darwin",
            environ={},
            home=Path("/Users/example"),
        )
        self.assertEqual(
            path,
            Path("/Users/example/Library/Application Support/the-grid/config.json"),
        )

    def test_unsupported_platform_is_explicit(self) -> None:
        with self.assertRaises(ConfigError):
            default_config_path(platform_name="win32", environ={}, home=Path("/tmp"))

    def test_missing_file_returns_non_secret_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = load_config(Path(directory) / "missing.json")
        self.assertEqual(config, ClientConfig())
        self.assertIsNone(config.server.host)
        self.assertEqual(config.server.port, 7331)

    def test_round_trip(self) -> None:
        expected = ClientConfig(
            server=ServerSettings(
                host="grid.example.net",
                port=7331,
                ca_file=Path("/tmp/ca.pem"),
            ),
            ui=UiSettings(color=False, plain=True),
        )
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "nested" / "config.json"
            save_config(expected, target)
            actual = load_config(target)
            self.assertEqual(actual, expected)
            self.assertEqual(list(target.parent.glob(".config.json.*")), [])

    @unittest.skipUnless(os.name == "posix", "POSIX permissions are required")
    def test_saved_config_is_owner_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "config" / "config.json"
            save_config(ClientConfig(), target)
            mode = stat.S_IMODE(target.stat().st_mode)
            self.assertEqual(mode, 0o600)

    def test_strict_unknown_keys_are_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            config_from_mapping({"server": {}, "ui": {}, "secret": "no"})
        with self.assertRaises(ConfigError):
            config_from_mapping({"server": {"phrase": "no"}})

    def test_invalid_types_are_rejected(self) -> None:
        invalid = [
            [],
            {"server": []},
            {"server": {"port": True}},
            {"server": {"port": 70000}},
            {"ui": {"plain": "yes"}},
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ConfigError):
                config_from_mapping(value)

    def test_only_approved_values_can_be_set(self) -> None:
        config = ClientConfig()
        config = set_config_value(config, "server.host", "grid.example.net")
        config = set_config_value(config, "server.port", "8443")
        config = set_config_value(config, "server.ca_file", "/tmp/ca.pem")
        config = set_config_value(config, "ui.color", "false")
        config = set_config_value(config, "ui.plain", "yes")
        self.assertEqual(config.server.host, "grid.example.net")
        self.assertEqual(config.server.port, 8443)
        self.assertEqual(config.server.ca_file, Path("/tmp/ca.pem"))
        self.assertFalse(config.ui.color)
        self.assertTrue(config.ui.plain)

    def test_secret_or_identity_keys_are_not_supported(self) -> None:
        for key in ("access_phrase", "comm_phrase", "id", "history", "key"):
            with self.subTest(key=key), self.assertRaises(ConfigError):
                set_config_value(ClientConfig(), key, "value")

    def test_serialised_shape_contains_only_approved_sections(self) -> None:
        serialised = format_config(ClientConfig())
        decoded = json.loads(serialised)
        self.assertEqual(set(decoded), {"server", "ui"})
        self.assertEqual(
            set(decoded["server"]),
            {"host", "port", "ca_file"},
        )
        self.assertEqual(set(decoded["ui"]), {"color", "plain"})
        self.assertEqual(config_to_mapping(ClientConfig()), decoded)


if __name__ == "__main__":
    unittest.main()
