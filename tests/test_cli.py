from __future__ import annotations

import tempfile
import unittest
from io import StringIO
from pathlib import Path

from the_grid.cli import build_parser, main
from the_grid.config import load_config


class CliTests(unittest.TestCase):
    def test_help_exposes_no_phrase_or_custom_list_argument(self) -> None:
        help_text = build_parser().format_help().lower()
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

    def test_no_argument_entry_point_reports_phase_three(self) -> None:
        output = StringIO()
        errors = StringIO()
        result = main([], stdout=output, stderr=errors)
        self.assertEqual(result, 0)
        self.assertEqual(errors.getvalue(), "")
        self.assertEqual(
            output.getvalue(),
            "the grid\n\nphase 3 headless networking is installed\n"
            "the interactive terminal client is not implemented yet\n",
        )

    def test_status_is_explicitly_pending(self) -> None:
        errors = StringIO()
        result = main(["status"], stdout=StringIO(), stderr=errors)
        self.assertEqual(result, 2)
        self.assertEqual(errors.getvalue(), "interactive client status is not available before phase 4\n")

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
                ["config", "set", "server.host", "grid.example.net"],
                config_file=target,
                stdout=output,
                stderr=StringIO(),
            )
            self.assertEqual(result, 0)
            self.assertEqual(output.getvalue(), "configuration saved\n")
            self.assertEqual(load_config(target).server.host, "grid.example.net")

    def test_config_without_action_returns_usage_error(self) -> None:
        errors = StringIO()
        result = main(
            ["config"],
            config_file=Path("unused"),
            stdout=StringIO(),
            stderr=errors,
        )
        self.assertEqual(result, 2)
        self.assertEqual(errors.getvalue(), "use grid config show or grid config set\n")


if __name__ == "__main__":
    unittest.main()
