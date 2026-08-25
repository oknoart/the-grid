from __future__ import annotations

import asyncio
import os
import termios
import unittest
from unittest.mock import patch

from the_grid.terminal import (
    PosixTerminal,
    RenderOptions,
    TerminalClosed,
    TerminalTextError,
    format_duration,
    render_message_lines,
    sanitise_user_text,
)


def _normalise_termios_attrs(attrs: list[object]) -> tuple[object, ...]:
    """Compare tty settings semantically across POSIX tcgetattr variants."""

    control = []
    for value in attrs[6]:
        if isinstance(value, int):
            control.append(value)
        elif isinstance(value, (bytes, bytearray)) and len(value) == 1:
            control.append(value[0])
        else:
            control.append(value)
    semantic = list(attrs[:6])
    # Darwin can set PENDIN as transient terminal-driver state after a tcsetattr
    # round trip. It is not persistent configuration owned by this backend.
    semantic[3] = int(semantic[3]) & ~getattr(termios, "PENDIN", 0)
    return (*semantic, tuple(control))


class TerminalTextTests(unittest.TestCase):
    def test_control_sequences_are_neutralised_and_newlines_normalised(self) -> None:
        value = sanitise_user_text("Hello\r\n\tWorld\x1b[31m\x07")
        self.assertEqual(value, "Hello\n    World\\x1b[31m\\x07")
        self.assertNotIn("\x1b", value)

    def test_nul_is_rejected(self) -> None:
        with self.assertRaises(TerminalTextError):
            sanitise_user_text("hello\x00world")

    def test_wrapping_repeats_prefix_for_explicit_and_visual_lines(self) -> None:
        lines = render_message_lines(
            "ABC", "<", "one two three four five six seven eight nine ten\nsecond", 40
        )
        self.assertGreater(len(lines), 2)
        self.assertTrue(all(line.startswith("ABC <") for line in lines))
        self.assertIn("ABC < second", lines)
        self.assertTrue(all(len(line) <= 40 for line in lines))

    def test_duration_copy_is_concise(self) -> None:
        self.assertEqual(format_duration(22_320), "6h 12m")
        self.assertEqual(format_duration(120), "2m")
        self.assertEqual(format_duration(9), "less than 1m")


@unittest.skipUnless(os.name == "posix", "POSIX terminal backend required")
class PosixTerminalTests(unittest.IsolatedAsyncioTestCase):
    async def test_incoming_output_preserves_active_input_and_restores_terminal(self) -> None:
        master_fd, slave_fd = os.openpty()
        original = termios.tcgetattr(slave_fd)
        input_stream = os.fdopen(os.dup(slave_fd), "r", encoding="utf-8", buffering=1)
        output_stream = os.fdopen(os.dup(slave_fd), "w", encoding="utf-8", buffering=1)
        terminal = PosixTerminal(
            input_stream=input_stream,
            output_stream=output_stream,
            options=RenderOptions(color=False, plain=False),
        )
        try:
            terminal.enter()
            task = asyncio.create_task(terminal.read_line("prompt: "))
            await asyncio.sleep(0.02)
            os.write(master_fd, b"hel")
            await asyncio.sleep(0.02)
            await terminal.notify("ABC < incoming")
            os.write(master_fd, b"lo\n")
            self.assertEqual(await asyncio.wait_for(task, 1), "hello")
            terminal.restore()
            self.assertEqual(
                _normalise_termios_attrs(termios.tcgetattr(slave_fd)),
                _normalise_termios_attrs(original),
            )

            os.set_blocking(master_fd, False)
            captured = bytearray()
            for _ in range(10):
                try:
                    captured.extend(os.read(master_fd, 4096))
                except BlockingIOError:
                    break
            text = captured.decode("utf-8", errors="replace")
            self.assertIn("ABC < incoming", text)
            self.assertIn("prompt: hel", text)
            self.assertIn("prompt: hello", text)
        finally:
            terminal.restore()
            input_stream.close()
            output_stream.close()
            os.close(master_fd)
            os.close(slave_fd)

    async def test_region_update_uses_dec_cursor_save_restore_during_active_input(self) -> None:
        master_fd, slave_fd = os.openpty()
        input_stream = os.fdopen(os.dup(slave_fd), "r", encoding="utf-8", buffering=1)
        output_stream = os.fdopen(os.dup(slave_fd), "w", encoding="utf-8", buffering=1)
        terminal = PosixTerminal(
            input_stream=input_stream,
            output_stream=output_stream,
            options=RenderOptions(color=False, plain=False),
        )
        try:
            terminal.enter()
            task = asyncio.create_task(terminal.read_line("> "))
            await asyncio.sleep(0.02)
            os.write(master_fd, b"hel")
            await asyncio.sleep(0.02)
            await terminal.update_region(
                row=3,
                column=46,
                lines=["  /\\____/\\ ", "   > •   •<"],
            )
            os.write(master_fd, b"lo\n")
            self.assertEqual(await asyncio.wait_for(task, 1), "hello")

            os.set_blocking(master_fd, False)
            captured = bytearray()
            for _ in range(10):
                try:
                    captured.extend(os.read(master_fd, 4096))
                except BlockingIOError:
                    break
            text = captured.decode("utf-8", errors="replace")
            self.assertIn("\x1b7", text)
            self.assertIn("\x1b8", text)
            self.assertNotIn("\x1b[s", text)
            self.assertNotIn("\x1b[u", text)
        finally:
            terminal.restore()
            input_stream.close()
            output_stream.close()
            os.close(master_fd)
            os.close(slave_fd)

    async def test_visible_submitted_input_is_erased_before_application_render(self) -> None:
        master_fd, slave_fd = os.openpty()
        input_stream = os.fdopen(os.dup(slave_fd), "r", encoding="utf-8", buffering=1)
        output_stream = os.fdopen(os.dup(slave_fd), "w", encoding="utf-8", buffering=1)
        terminal = PosixTerminal(
            input_stream=input_stream,
            output_stream=output_stream,
            options=RenderOptions(color=False, plain=False),
        )
        try:
            terminal.enter()
            task = asyncio.create_task(terminal.read_line())
            await asyncio.sleep(0.02)
            os.write(master_fd, b"hello\n")
            self.assertEqual(await asyncio.wait_for(task, 1), "hello")
            await terminal.write_lines(["ABC > hello"])

            os.set_blocking(master_fd, False)
            captured = bytearray()
            for _ in range(10):
                try:
                    captured.extend(os.read(master_fd, 4096))
                except BlockingIOError:
                    break
            text = captured.decode("utf-8", errors="replace")
            self.assertIn("hello\r\x1b[2KABC > hello\r\n", text)
            self.assertNotIn("hello\r\nABC > hello", text)
        finally:
            terminal.restore()
            input_stream.close()
            output_stream.close()
            os.close(master_fd)
            os.close(slave_fd)

    async def test_pasted_multiple_lines_are_consumed_without_losing_tail(self) -> None:
        master_fd, slave_fd = os.openpty()
        input_stream = os.fdopen(os.dup(slave_fd), "r", encoding="utf-8", buffering=1)
        output_stream = os.fdopen(os.dup(slave_fd), "w", encoding="utf-8", buffering=1)
        terminal = PosixTerminal(
            input_stream=input_stream,
            output_stream=output_stream,
            options=RenderOptions(color=False, plain=False),
        )
        try:
            terminal.enter()
            first = asyncio.create_task(terminal.read_line())
            await asyncio.sleep(0.02)
            os.write(master_fd, b"first\nsecond\n")
            self.assertEqual(await asyncio.wait_for(first, 1), "first")
            second = asyncio.create_task(terminal.read_line())
            self.assertEqual(await asyncio.wait_for(second, 1), "second")
        finally:
            terminal.restore()
            input_stream.close()
            output_stream.close()
            os.close(master_fd)
            os.close(slave_fd)

    async def test_async_context_restores_after_exception(self) -> None:
        master_fd, slave_fd = os.openpty()
        original = termios.tcgetattr(slave_fd)
        input_stream = os.fdopen(os.dup(slave_fd), "r", encoding="utf-8", buffering=1)
        output_stream = os.fdopen(os.dup(slave_fd), "w", encoding="utf-8", buffering=1)
        terminal = PosixTerminal(
            input_stream=input_stream,
            output_stream=output_stream,
            options=RenderOptions(color=False, plain=False),
        )
        try:
            with self.assertRaises(RuntimeError):
                async with terminal:
                    active = termios.tcgetattr(slave_fd)
                    self.assertFalse(active[3] & termios.ICANON)
                    self.assertFalse(active[3] & termios.ECHO)
                    raise RuntimeError("boom")
            self.assertEqual(
                _normalise_termios_attrs(termios.tcgetattr(slave_fd)),
                _normalise_termios_attrs(original),
            )
        finally:
            terminal.restore()
            input_stream.close()
            output_stream.close()
            os.close(master_fd)
            os.close(slave_fd)


    async def test_plain_cancelled_read_releases_tty_for_next_state(self) -> None:
        master_fd, slave_fd = os.openpty()
        input_stream = os.fdopen(os.dup(slave_fd), "r", encoding="utf-8", buffering=1)
        output_stream = os.fdopen(os.dup(slave_fd), "w", encoding="utf-8", buffering=1)
        terminal = PosixTerminal(
            input_stream=input_stream,
            output_stream=output_stream,
            options=RenderOptions(color=False, plain=True),
        )
        try:
            terminal.enter()
            stale = asyncio.create_task(terminal.read_line())
            await asyncio.sleep(0.02)
            stale.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await stale

            current = asyncio.create_task(terminal.read_line())
            await asyncio.sleep(0.02)
            os.write(master_fd, b"/exit\n")
            self.assertEqual(await asyncio.wait_for(current, 1), "/exit")
        finally:
            terminal.restore()
            input_stream.close()
            output_stream.close()
            os.close(master_fd)
            os.close(slave_fd)

    async def test_secret_input_shows_fixed_hidden_marker_without_revealing_secret(self) -> None:
        master_fd, slave_fd = os.openpty()
        input_stream = os.fdopen(os.dup(slave_fd), "r", encoding="utf-8", buffering=1)
        output_stream = os.fdopen(os.dup(slave_fd), "w", encoding="utf-8", buffering=1)
        terminal = PosixTerminal(
            input_stream=input_stream,
            output_stream=output_stream,
            options=RenderOptions(color=False, plain=False),
        )
        try:
            terminal.enter()
            task = asyncio.create_task(
                terminal.read_line("phrase: ", secret=True)
            )
            await asyncio.sleep(0.02)
            os.write(master_fd, b"velvet orbit cabin cedar")
            await asyncio.sleep(0.02)
            os.write(master_fd, b"\n")

            self.assertEqual(
                await asyncio.wait_for(task, 1),
                "velvet orbit cabin cedar",
            )

            os.set_blocking(master_fd, False)
            captured = bytearray()
            for _ in range(10):
                try:
                    captured.extend(os.read(master_fd, 4096))
                except BlockingIOError:
                    break

            rendered = captured.decode("utf-8", errors="replace")
            self.assertIn("phrase: [hidden]", rendered)
            self.assertNotIn("velvet", rendered)
            self.assertNotIn("orbit", rendered)
            self.assertNotIn("cabin", rendered)
            self.assertNotIn("cedar", rendered)
        finally:
            terminal.restore()
            input_stream.close()
            output_stream.close()
            os.close(master_fd)
            os.close(slave_fd)

    async def test_plain_secret_read_restores_echo_when_cancelled(self) -> None:
        master_fd, slave_fd = os.openpty()
        original = termios.tcgetattr(slave_fd)
        input_stream = os.fdopen(os.dup(slave_fd), "r", encoding="utf-8", buffering=1)
        output_stream = os.fdopen(os.dup(slave_fd), "w", encoding="utf-8", buffering=1)
        terminal = PosixTerminal(
            input_stream=input_stream,
            output_stream=output_stream,
            options=RenderOptions(color=False, plain=True),
        )
        try:
            terminal.enter()
            task = asyncio.create_task(terminal.read_line("phrase: ", secret=True))
            await asyncio.sleep(0.02)
            hidden = termios.tcgetattr(slave_fd)
            self.assertFalse(hidden[3] & termios.ECHO)

            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(
                _normalise_termios_attrs(termios.tcgetattr(slave_fd)),
                _normalise_termios_attrs(original),
            )
        finally:
            terminal.restore()
            input_stream.close()
            output_stream.close()
            os.close(master_fd)
            os.close(slave_fd)

    async def test_plain_paste_keeps_second_line_for_next_read(self) -> None:
        master_fd, slave_fd = os.openpty()
        input_stream = os.fdopen(os.dup(slave_fd), "r", encoding="utf-8", buffering=1)
        output_stream = os.fdopen(os.dup(slave_fd), "w", encoding="utf-8", buffering=1)
        terminal = PosixTerminal(
            input_stream=input_stream,
            output_stream=output_stream,
            options=RenderOptions(color=False, plain=True),
        )
        try:
            terminal.enter()
            first = asyncio.create_task(terminal.read_line())
            await asyncio.sleep(0.02)
            os.write(master_fd, b"/comm\nphrase words\n")
            self.assertEqual(await asyncio.wait_for(first, 1), "/comm")
            second = asyncio.create_task(terminal.read_line())
            self.assertEqual(await asyncio.wait_for(second, 1), "phrase words")
        finally:
            terminal.restore()
            input_stream.close()
            output_stream.close()
            os.close(master_fd)
            os.close(slave_fd)

    async def test_cbreak_editor_supports_left_arrow_and_ctrl_u(self) -> None:
        master_fd, slave_fd = os.openpty()
        input_stream = os.fdopen(os.dup(slave_fd), "r", encoding="utf-8", buffering=1)
        output_stream = os.fdopen(os.dup(slave_fd), "w", encoding="utf-8", buffering=1)
        terminal = PosixTerminal(
            input_stream=input_stream,
            output_stream=output_stream,
            options=RenderOptions(color=False, plain=False),
        )
        try:
            terminal.enter()
            first = asyncio.create_task(terminal.read_line())
            await asyncio.sleep(0.02)
            os.write(master_fd, b"helo\x1b[Dl\x15right\n")
            self.assertEqual(await asyncio.wait_for(first, 1), "right")

            second = asyncio.create_task(terminal.read_line())
            await asyncio.sleep(0.02)
            os.write(master_fd, b"helo\x1b[D" + b"l\n")
            self.assertEqual(await asyncio.wait_for(second, 1), "hello")
        finally:
            terminal.restore()
            input_stream.close()
            output_stream.close()
            os.close(master_fd)
            os.close(slave_fd)


    async def test_wrapped_input_redraw_returns_to_first_visual_row(self) -> None:
        master_fd, slave_fd = os.openpty()
        input_stream = os.fdopen(os.dup(slave_fd), "r", encoding="utf-8", buffering=1)
        output_stream = os.fdopen(os.dup(slave_fd), "w", encoding="utf-8", buffering=1)
        terminal = PosixTerminal(
            input_stream=input_stream,
            output_stream=output_stream,
            options=RenderOptions(color=False, plain=False),
        )
        try:
            with patch(
                "the_grid.terminal.shutil.get_terminal_size",
                return_value=os.terminal_size((20, 24)),
            ):
                terminal.enter()
                task = asyncio.create_task(terminal.read_line("message > "))
                await asyncio.sleep(0.02)

                # Prompt + eleven characters exceeds the 20-column terminal.
                os.write(master_fd, b"abcdefghijk")
                await asyncio.sleep(0.02)

                # Force another redraw after the input has wrapped.
                os.write(master_fd, b"\x7f\n")
                self.assertEqual(
                    await asyncio.wait_for(task, 1),
                    "abcdefghij",
                )

            os.set_blocking(master_fd, False)
            captured = bytearray()
            for _ in range(10):
                try:
                    captured.extend(os.read(master_fd, 4096))
                except BlockingIOError:
                    break

            rendered = captured.decode("utf-8", errors="replace")

            # A wrapped redraw must move back above the current visual row
            # before repainting; clearing only the current row leaves copies
            # of the prompt behind on narrow terminals.
            self.assertIn("\x1b[1A", rendered)
        finally:
            terminal.restore()
            input_stream.close()
            output_stream.close()
            os.close(master_fd)
            os.close(slave_fd)


    async def test_ctrl_d_with_text_does_not_exit(self) -> None:
        master_fd, slave_fd = os.openpty()
        input_stream = os.fdopen(os.dup(slave_fd), "r", encoding="utf-8", buffering=1)
        output_stream = os.fdopen(os.dup(slave_fd), "w", encoding="utf-8", buffering=1)
        terminal = PosixTerminal(
            input_stream=input_stream,
            output_stream=output_stream,
            options=RenderOptions(color=False, plain=False),
        )
        try:
            terminal.enter()
            task = asyncio.create_task(terminal.read_line())
            await asyncio.sleep(0.02)
            os.write(master_fd, b"abc\x04\n")
            self.assertEqual(await asyncio.wait_for(task, 1), "abc")
        finally:
            terminal.restore()
            input_stream.close()
            output_stream.close()
            os.close(master_fd)
            os.close(slave_fd)

    async def test_ctrl_d_ends_cbreak_input(self) -> None:
        master_fd, slave_fd = os.openpty()
        input_stream = os.fdopen(os.dup(slave_fd), "r", encoding="utf-8", buffering=1)
        output_stream = os.fdopen(os.dup(slave_fd), "w", encoding="utf-8", buffering=1)
        terminal = PosixTerminal(
            input_stream=input_stream,
            output_stream=output_stream,
            options=RenderOptions(color=False, plain=False),
        )
        try:
            terminal.enter()
            task = asyncio.create_task(terminal.read_line())
            await asyncio.sleep(0.02)
            os.write(master_fd, b"\x04")
            with self.assertRaises(TerminalClosed):
                await asyncio.wait_for(task, 1)
        finally:
            terminal.restore()
            input_stream.close()
            output_stream.close()
            os.close(master_fd)
            os.close(slave_fd)


if __name__ == "__main__":
    unittest.main()
