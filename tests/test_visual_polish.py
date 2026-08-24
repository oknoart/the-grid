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
        self.assertIn("/info    /end    /help", lines)
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

    def test_info_and_help_match_approved_copy(self) -> None:
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

        hub_help = [line_text(line) for line in app._help_lines(in_comm=False)]
        self.assertTrue(any("/comm       open a private encrypted comm" in line for line in hub_help))
        self.assertTrue(any("/info       show connection and grid info" in line for line in hub_help))

        comm_help = [line_text(line) for line in app._help_lines(in_comm=True)]
        self.assertTrue(any("/info       show comm and connection info" in line for line in comm_help))
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
        self.assertIn("    choose a 3 character id", terminal.lines)
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
        self.assertTrue(
            any("enter a comm phrase" in line for view in terminal.replacements for line in view)
        )
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

    async def test_height_only_resize_redraws_hub_and_recalculates_cat(self) -> None:
        app = self._app()
        terminal = app.terminal
        app._hub_visible = True

        await app._show_hub(replace=True)
        self.assertTrue(app._cat_animatable)
        initial_replacements = len(terminal.replacements)

        task = asyncio.create_task(app._watch_resize())
        try:
            terminal.height = 8
            await asyncio.sleep(0.32)

            self.assertGreater(len(terminal.replacements), initial_replacements)
            self.assertEqual(app._last_height, 8)
            self.assertFalse(app._cat_animatable)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_cat_waits_for_resize_redraw_before_using_fixed_rows(self) -> None:
        app = self._app()
        terminal = app.terminal
        app._hub_visible = True

        await app._show_hub(replace=True)
        terminal.region_updates.clear()

        # Simulate a terminal resize that the resize watcher has not redrawn yet.
        terminal.height -= 1

        task = asyncio.create_task(app._animate_cat())
        try:
            await asyncio.sleep(CAT_INTERVAL_SECONDS + 0.08)
            self.assertFalse(terminal.region_updates)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_hub_clock_refreshes_metadata_without_full_redraw(self) -> None:
        app = self._app()
        terminal = app.terminal
        app._hub_visible = True

        current = [datetime(2026, 8, 21, 14, 32)]
        app.now = lambda: current[0]

        await app._show_hub(replace=True)
        initial_replacements = len(terminal.replacements)
        terminal.region_updates.clear()

        current[0] = datetime(2026, 8, 21, 14, 33)
        await app._refresh_hub_clock_if_needed()

        self.assertEqual(len(terminal.replacements), initial_replacements)
        self.assertEqual(len(terminal.region_updates), 1)

        row, column, lines = terminal.region_updates[0]
        self.assertEqual((row, column), (4, 1))
        self.assertIn("1 message / 14:33", lines[0])

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
