from __future__ import annotations

import tempfile
import unittest
from unittest.mock import AsyncMock
from pathlib import Path

from phase4_support import FakeTerminal
from the_grid.interactive import InteractiveClientApp, ServerUnavailable, apply_ui_overrides, parse_server_endpoint
from the_grid.models import ClientConfig, UiSettings


class InteractiveHelpersTests(unittest.TestCase):
    def test_server_endpoint_parser_handles_default_port_and_bracketed_ipv6(self) -> None:
        self.assertEqual(parse_server_endpoint("grid.example.net"), ("grid.example.net", 7331))
        self.assertEqual(parse_server_endpoint("grid.example.net:7444"), ("grid.example.net", 7444))
        self.assertEqual(parse_server_endpoint("[::1]:7331"), ("::1", 7331))

    def test_server_endpoint_rejects_urls_and_invalid_ports(self) -> None:
        for value in ("https://grid.example.net", "host:0", "host:70000", "host:"):
            with self.subTest(value=value), self.assertRaises((ValueError, TypeError)):
                parse_server_endpoint(value)

    def test_ui_flags_only_tighten_persisted_preferences(self) -> None:
        config = ClientConfig(ui=UiSettings(color=True, plain=False))
        changed = apply_ui_overrides(config, plain=True, no_color=True)
        self.assertTrue(changed.ui.plain)
        self.assertFalse(changed.ui.color)


class InteractiveInstalledServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_normal_ui_never_prompts_for_or_saves_a_server(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            terminal = FakeTerminal()
            app = InteractiveClientApp(
                config=ClientConfig(),
                terminal=terminal,
                config_path=path,
            )
            with self.assertRaises(ServerUnavailable):
                app._resolve_server()
            self.assertFalse(path.exists())
            self.assertFalse(any("server" in prompt for prompt, _secret in terminal.prompts))



    async def test_successful_launch_replaces_connecting_view(self) -> None:
        terminal = FakeTerminal()
        app = InteractiveClientApp(
            config=ClientConfig(),
            terminal=terminal,
        )
        app._resolve_server = lambda: ("grid.example.net", 7331, None)
        app._watch_resize = AsyncMock()
        app._animate_dots = AsyncMock()
        app._connect = AsyncMock()
        app._authenticate = AsyncMock(return_value=True)
        app._select_display = AsyncMock(return_value=True)
        app._show_hub = AsyncMock()
        app._watch_board = AsyncMock()
        app._animate_cat = AsyncMock()
        app._hub_loop = AsyncMock()

        result = await app.run()

        self.assertEqual(result, 0)
        self.assertGreaterEqual(len(terminal.replacements), 2)

        connected_view = terminal.replacements[-1]
        self.assertIn("    status   connected", connected_view)
        self.assertFalse(any("connecting" in line for line in connected_view))

        # Successful launch no longer relies on a fixed-row cursor update.
        self.assertFalse(
            any(
                "status   connected" in line
                for _row, _column, lines in terminal.region_updates
                for line in lines
            )
        )

        # Do not print a second standalone "connected" after ID selection.
        self.assertNotIn("connected", terminal.lines)


if __name__ == "__main__":
    unittest.main()
