from __future__ import annotations

import tempfile
import unittest
from io import StringIO
from pathlib import Path

from the_grid.cli import build_parser, main
from the_grid.config import load_config, save_config
from the_grid.models import ClientConfig, ServerSettings


class CliTests(unittest.TestCase):
    def test_help_exposes_phase_four_flags_but_no_phrase_or_custom_list_argument(self) -> None:
        help_text = build_parser().format_help().lower()
        for expected in ("--server", "--ca-file", "--plain", "--no-color"):
            self.assertIn(expected, help_text)
        forbidden = (
            "access-phrase",
            "comm-phrase",
            "wordlist",
            "word-list",
            "custom-list",
            "generate-phrase",
        )
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, help_text)

    def test_status_reports_configured_target_without_claiming_live_connection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "config.json"
            save_config(
                ClientConfig(server=ServerSettings(host="grid.example.net")),
                target,
            )
            output = StringIO()
            result = main(["status"], config_file=target, stdout=output, stderr=StringIO())
            self.assertEqual(result, 0)
            self.assertEqual(
                output.getvalue(),
                "status\nserver: grid.example.net:7331\nconnection: not active\n",
            )

    def test_config_show_uses_defaults_without_creating_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "config.json"
            output = StringIO()
            result = main(
                ["config", "show"],
                config_file=target,
                stdout=output,
                stderr=StringIO(),
            )
            self.assertEqual(result, 0)
            self.assertIn('"port": 7331', output.getvalue())
            self.assertFalse(target.exists())

    def test_config_set_persists_only_requested_value(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "config.json"
            output = StringIO()
            result = main(
                ["config", "set", "ui.plain", "true"],
                config_file=target,
                stdout=output,
                stderr=StringIO(),
            )
            self.assertEqual(result, 0)
            self.assertEqual(output.getvalue(), "configuration saved\n")
            self.assertTrue(load_config(target).ui.plain)
            self.assertIsNone(load_config(target).server.host)

    def test_server_init_status_rotate_and_export_are_local_admin_flows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "server"
            output = StringIO()
            errors = StringIO()
            result = main(
                [
                    "server",
                    "init",
                    "--public-host",
                    "127.0.0.1",
                    "--state-dir",
                    str(root),
                ],
                stdin=StringIO("y\n"),
                stdout=output,
                stderr=errors,
            )
            self.assertEqual(result, 0, errors.getvalue())
            self.assertIn("access phrase:\n", output.getvalue())
            self.assertTrue((root / "server.json").is_file())
            self.assertTrue((root / "server-id.bin").is_file())
            self.assertTrue((root / "access-state.json").is_file())
            self.assertTrue((root / "tls" / "grid-ca.pem").is_file())

            status = StringIO()
            result = main(
                ["server", "status", "--config", str(root / "server.json")],
                stdout=status,
                stderr=errors,
            )
            self.assertEqual(result, 0, errors.getvalue())
            self.assertIn("server: stopped", status.getvalue())
            self.assertIn("public: 127.0.0.1:7331", status.getvalue())

            rotated = StringIO()
            result = main(
                ["server", "rotate-access", "--config", str(root / "server.json")],
                stdin=StringIO("y\n"),
                stdout=rotated,
                stderr=errors,
            )
            self.assertEqual(result, 0, errors.getvalue())
            self.assertIn("new access phrase:\n", rotated.getvalue())

            export = Path(directory) / "client"
            result = main(
                [
                    "server",
                    "export-client",
                    "--config",
                    str(root / "server.json"),
                    "--output",
                    str(export),
                ],
                stdout=StringIO(),
                stderr=errors,
            )
            self.assertEqual(result, 0, errors.getvalue())
            self.assertEqual((export / "okno-grid-host.txt").read_text(), "127.0.0.1\n")
            self.assertTrue((export / "okno-grid-ca.pem").is_file())


    def test_server_init_validates_endpoint_before_revealing_phrase(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = StringIO()
            errors = StringIO()
            result = main(
                [
                    "server",
                    "init",
                    "--public-host",
                    "https://not-a-host.example",
                    "--state-dir",
                    str(Path(directory) / "server"),
                ],
                stdin=StringIO(""),
                stdout=output,
                stderr=errors,
            )
            self.assertEqual(result, 1)
            self.assertEqual(output.getvalue(), "")
            self.assertIn("public_host", errors.getvalue())
            self.assertNotIn("access phrase", errors.getvalue())

    def test_server_init_refuses_nonempty_custom_directory_before_revealing_phrase(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "not-a-server"
            root.mkdir()
            sentinel = root / "keep-me.txt"
            sentinel.write_text("unrelated", encoding="utf-8")
            output = StringIO()
            errors = StringIO()
            result = main(
                [
                    "server",
                    "init",
                    "--public-host",
                    "grid.example.net",
                    "--state-dir",
                    str(root),
                ],
                stdin=StringIO(""),
                stdout=output,
                stderr=errors,
            )
            self.assertEqual(result, 1)
            self.assertEqual(output.getvalue(), "")
            self.assertEqual(errors.getvalue(), "server state directory is not empty\n")
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "unrelated")

    def test_server_init_refuses_existing_state_before_revealing_a_new_phrase(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "server"
            root.mkdir()
            (root / "server-id.bin").write_bytes(b"partial-state")
            output = StringIO()
            errors = StringIO()
            result = main(
                [
                    "server",
                    "init",
                    "--public-host",
                    "grid.example.net",
                    "--state-dir",
                    str(root),
                ],
                stdin=StringIO(""),
                stdout=output,
                stderr=errors,
            )
            self.assertEqual(result, 1)
            self.assertEqual(output.getvalue(), "")
            self.assertEqual(errors.getvalue(), "server is already initialised\n")

    def test_config_without_action_returns_usage_error(self) -> None:
        errors = StringIO()
        result = main(
            ["config"],
            config_file=Path("unused"),
            stdout=StringIO(),
            stderr=errors,
        )
        self.assertEqual(result, 2)
        self.assertEqual(errors.getvalue(), "use okno config show or okno config set\n")


if __name__ == "__main__":
    unittest.main()
