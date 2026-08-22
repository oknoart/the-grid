from __future__ import annotations

import asyncio
import unittest
from datetime import datetime
from types import SimpleNamespace

from phase4_support import FakeTerminal
from the_grid import terms, ui_text
from the_grid.hub import BoardMessage
from the_grid.interactive import (
    CAT_INTERVAL_SECONDS,
    CAT_LEFT,
    CAT_RIGHT,
    CAT_TOP,
    InteractiveClientApp,
)
from the_grid.models import ClientConfig
from the_grid.terminal import RenderOptions, TextStyle, line_text


class _ReserveClient:
    display_id = None

    async def reserve_display(self, display_id: str) -> None:
        self.display_id = display_id


class VisualPolishTests(unittest.IsolatedAsyncioTestCase):
    def _app(self, *, width: int = 80, plain: bool = False) -> InteractiveClientApp:
        terminal = FakeTerminal(
            width=width,
            options=RenderOptions(color=False, plain=plain),
        )
        app = InteractiveClientApp(
            config=ClientConfig(),
            terminal=terminal,
            now=lambda: datetime(2026, 8, 21, 14, 32),
        )
        app.client = SimpleNamespace(
            display_id="ABC",
            board_records=(
                SimpleNamespace(message=BoardMessage("K9R", "hello")),
            ),
            post_remaining_seconds=0,
            connected=True,
        )
        return app

    def test_user_facing_application_is_okno_but_environment_remains_the_grid(self) -> None:
        self.assertEqual(terms.APP_DISPLAY_NAME, "okno")
        self.assertEqual(terms.EXECUTABLE_NAME, "okno")
        self.assertEqual(terms.GRID, "the grid")
        self.assertEqual(terms.HUB, "the hub")

    def test_hub_layout_matches_approved_cat_geometry_and_metadata(self) -> None:
        app = self._app()
        left = [line_text(line) for line in app._hub_lines(cat_state=0)]
        right = [line_text(line) for line in app._hub_lines(cat_state=1)]

        self.assertEqual(left[0], "─────────────────────── THE HUB ────────────────────────")
        self.assertEqual(len(left[0]), 56)
        self.assertEqual(left[2][-11:], CAT_TOP)
        self.assertEqual(left[3][-11:], CAT_LEFT)
        self.assertEqual(right[3][-11:], CAT_RIGHT)
        self.assertEqual(CAT_TOP.index("/"), CAT_LEFT.index(">"))
        self.assertEqual(CAT_TOP.rindex("\\"), CAT_LEFT.index("<"))
        self.assertEqual(CAT_LEFT.index("•") + 1, CAT_RIGHT.index("•"))
        self.assertEqual(CAT_LEFT.rindex("•") + 1, CAT_RIGHT.rindex("•"))
        self.assertTrue(left[3].startswith("    1 message / 14:32"))
        self.assertIn("K9R < hello", left)
        self.assertIn("write a message with /post", left)
        self.assertNotIn("ABC >", "\n".join(left))
        self.assertTrue(all(len(line) <= 56 for line in left if line))

    def test_empty_hub_uses_no_messages_and_plural_count(self) -> None:
        app = self._app()
        app.client.board_records = ()
        lines = [line_text(line) for line in app._hub_lines()]
        self.assertTrue(lines[3].startswith("    0 messages / 14:32"))
        self.assertIn("    no messages", lines)

    def test_narrow_hub_hides_cat_and_wraps_command_bar(self) -> None:
        app = self._app(width=40)
        lines = [line_text(line) for line in app._hub_lines()]
        self.assertFalse(any("/\\____/\\" in line for line in lines))
        command_lines = [line for line in lines if line.startswith("/post") or line.startswith("/help")]
        self.assertGreaterEqual(len(command_lines), 1)
        self.assertTrue(all(len(line) <= 40 for line in lines if line))

    def test_comm_layout_uses_approved_header_and_no_clear_command(self) -> None:
        app = self._app()
        app._comm_messages = [
            ("J7K", "<", "hello"),
            ("ABC", ">", "hello. can you hear me?"),
        ]
        lines = [line_text(line) for line in app._comm_lines("J7K")]
        self.assertEqual(lines[0], "───────────────────────── COMM ─────────────────────────")
        self.assertIn("    ABC × J7K / encrypted", lines)
        self.assertIn("J7K < hello", lines)
        self.assertIn("ABC > hello. can you hear me?", lines)
        self.assertIn("/status    /end    /help", lines)
        self.assertFalse(any("/clear" in line for line in lines))
        self.assertIn("write a message", lines)

    def test_empty_comm_explicitly_says_no_messages(self) -> None:
        app = self._app()
        app._comm_messages = []
        lines = [line_text(line) for line in app._comm_lines("J7K")]
        self.assertIn("    no messages", lines)

    def test_hub_cooldown_is_not_shown_unless_requested(self) -> None:
        app = self._app()
        app.client.post_remaining_seconds = 86_340
        lines = [line_text(line) for line in app._hub_lines()]
        self.assertFalse(any("post available in" in line for line in lines))

    def test_status_and_help_match_approved_reduced_copy(self) -> None:
        app = self._app()
        hub_status = [line_text(line) for line in app._status_lines(in_comm=False, peer_id=None)]
        self.assertIn("server          connected", hub_status)
        self.assertIn("id              ABC", hub_status)
        self.assertIn("hub             connected", hub_status)
        self.assertIn("post            available", hub_status)
        self.assertFalse(any("tls" in line for line in hub_status))
        self.assertIn("press return to go back", hub_status)

        app.client.session_channel = SimpleNamespace(verification_code="QTCE-9QSD")
        comm_status = [line_text(line) for line in app._status_lines(in_comm=True, peer_id="J7K")]
        self.assertIn("comm            J7K", comm_status)
        self.assertIn("encrypted       yes", comm_status)
        self.assertIn("verification    QTCE-9QSD", comm_status)

        comm_help = [line_text(line) for line in app._help_lines(in_comm=True)]
        self.assertTrue(any("/end        end the comm" in line for line in comm_help))
        self.assertFalse(any("/clear" in line for line in comm_help))
        self.assertIn("press return to go back", comm_help)

    async def test_id_selection_requires_explicit_three_character_input_without_suggestion(self) -> None:
        terminal = FakeTerminal()
        app = InteractiveClientApp(config=ClientConfig(), terminal=terminal)
        client = _ReserveClient()
        app.client = client  # type: ignore[assignment]
        terminal.feed("abc")
        self.assertTrue(await app._select_display())
        self.assertEqual(client.display_id, "ABC")
        self.assertIn("    enter 3 character id", terminal.lines)
        self.assertEqual(terminal.prompts, [("    > ", False)])
        self.assertFalse(any("id [" in line.lower() for line in terminal.lines))



    async def test_launch_connecting_animates_status_dots_without_moving_input(self) -> None:
        app = self._app()
        terminal = app.terminal
        await app._show_launch_connecting()
        launch = terminal.replacements[-1]
        self.assertEqual(launch[7], "    status   connecting.")

        task = asyncio.create_task(
            app._animate_dots(
                kind="launch",
                row=8,
                base="    status   " + ui_text.CONNECTING,
            )
        )
        try:
            await asyncio.sleep(0.58)
            self.assertTrue(terminal.region_updates)
            row, column, lines = terminal.region_updates[-1]
            self.assertEqual((row, column), (8, 1))
            self.assertEqual(lines, ["    status   connecting.. "])
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_comm_can_be_cancelled_before_entering_a_phrase(self) -> None:
        app = self._app()
        terminal = app.terminal
        terminal.feed("/cancel")

        await app._comm()

        self.assertTrue(app._hub_visible)
        self.assertTrue(any("/cancel" in line for view in terminal.replacements for line in view))
        self.assertIn("─────────────────────── THE HUB ────────────────────────", terminal.replacements[-1])

    async def test_passive_dot_animation_updates_only_status_line(self) -> None:
        app = self._app()
        terminal = app.terminal
        app._current_view_kind = "comm_waiting"
        task = asyncio.create_task(
            app._animate_dots(kind="comm_waiting", row=7, base="waiting for connection")
        )
        try:
            await asyncio.sleep(0.58)
            self.assertTrue(terminal.region_updates)
            row, column, lines = terminal.region_updates[-1]
            self.assertEqual((row, column), (7, 1))
            self.assertEqual(lines, ["waiting for connection.. "])
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_cat_animation_updates_only_fixed_header_region(self) -> None:
        app = self._app()
        terminal = app.terminal
        app._hub_visible = True
        app._current_view_kind = "hub"
        app._cat_animatable = True
        task = asyncio.create_task(app._animate_cat())
        try:
            await asyncio.sleep(CAT_INTERVAL_SECONDS + 0.08)
            self.assertTrue(terminal.region_updates)
            row, column, lines = terminal.region_updates[-1]
            self.assertEqual(row, 3)
            self.assertEqual(column, 46)
            self.assertEqual(lines, [CAT_TOP.ljust(11), CAT_RIGHT])
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
