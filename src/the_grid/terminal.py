"""POSIX terminal boundary and safe line-oriented rendering for okno v1."""

from __future__ import annotations

import asyncio
import codecs
import os
import shutil
import sys
import termios
import textwrap
import tty
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, TextIO

DEFAULT_TERMINAL_WIDTH: Final = 80
DEFAULT_TERMINAL_HEIGHT: Final = 24
MIN_TERMINAL_WIDTH: Final = 40
TAB_SPACES: Final = 4
ANSI_CLEAR_LINE: Final = "\x1b[2K"
ANSI_CLEAR_SCREEN: Final = "\x1b[2J\x1b[H"
ANSI_PURGE_SCREEN: Final = "\x1b[2J\x1b[3J\x1b[H"
ANSI_RESET: Final = "\x1b[0m"
ANSI_SAVE_CURSOR: Final = "\x1b7"
ANSI_RESTORE_CURSOR: Final = "\x1b8"
ANSI_CURSOR_BLINK_UNDERLINE: Final = "\x1b[3 q"
ANSI_CURSOR_DEFAULT: Final = "\x1b[0 q"

STYLE_CODES: Final = {
    "heading": "\x1b[1;36m",
    "dim": "\x1b[2m",
    "success": "\x1b[32m",
    "error": "\x1b[31m",
    "warning": "\x1b[33m",
}


class TerminalTextError(ValueError):
    """Raised when user text cannot safely enter the terminal/application."""


class TerminalClosed(EOFError):
    """Raised when Ctrl-D or end-of-input closes terminal input."""


class TextStyle(StrEnum):
    HEADING = "heading"
    DIM = "dim"
    SUCCESS = "success"
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class StyledSpan:
    text: str
    style: TextStyle | None = None


@dataclass(frozen=True, slots=True)
class StyledLine:
    spans: tuple[StyledSpan, ...]

    @property
    def text(self) -> str:
        return "".join(span.text for span in self.spans)


RenderableLine = str | StyledLine


@dataclass(frozen=True, slots=True)
class RenderOptions:
    color: bool = True
    plain: bool = False


def styled_line(*parts: str | tuple[str, TextStyle | None]) -> StyledLine:
    """Build one styled line while keeping layout text independent of ANSI codes."""

    spans: list[StyledSpan] = []
    for part in parts:
        if isinstance(part, tuple):
            text, style = part
            spans.append(StyledSpan(text, style))
        else:
            spans.append(StyledSpan(part))
    return StyledLine(tuple(spans))


def line_text(line: RenderableLine) -> str:
    return line if isinstance(line, str) else line.text


def sanitise_user_text(value: str) -> str:
    """Normalise terminal-safe user text while preserving printable Unicode/case."""

    if not isinstance(value, str):
        raise TypeError("text must be a string")
    value = value.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " " * TAB_SPACES)
    if "\x00" in value:
        raise TerminalTextError("nul is not allowed")

    safe: list[str] = []
    for char in value:
        codepoint = ord(char)
        if char == "\n":
            safe.append(char)
        elif char == "\x1b":
            safe.append("\\x1b")
        elif codepoint < 0x20 or codepoint == 0x7F:
            safe.append(f"\\x{codepoint:02x}")
        else:
            safe.append(char)
    return "".join(safe)


def render_message_lines(display_id: str, marker: str, text: str, width: int) -> tuple[str, ...]:
    """Wrap one message with the complete ID/marker prefix on every visual line."""

    if not isinstance(display_id, str) or not display_id:
        raise ValueError("display_id must be non-empty")
    if marker not in {"<", ">"}:
        raise ValueError("marker must be < or >")
    if type(width) is not int:
        raise TypeError("width must be an integer")
    width = max(MIN_TERMINAL_WIDTH, width)

    safe = sanitise_user_text(text)
    prefix = f"{display_id} {marker} "
    body_width = max(1, width - len(prefix))
    rendered: list[str] = []

    # split preserves intentional empty logical lines between newlines.
    for logical_line in safe.split("\n"):
        if logical_line == "":
            rendered.append(prefix.rstrip())
            continue
        segments = textwrap.wrap(
            logical_line,
            width=body_width,
            expand_tabs=False,
            replace_whitespace=False,
            drop_whitespace=False,
            break_long_words=True,
            break_on_hyphens=False,
        )
        if not segments:
            segments = [""]
        for segment in segments:
            rendered.append(prefix + segment.rstrip())
    return tuple(rendered)


def format_duration(seconds: int) -> str:
    """Format a non-negative duration for concise lowercase UI copy."""

    if type(seconds) is not int:
        raise TypeError("seconds must be an integer")
    seconds = max(0, seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m" if minutes >= 1 else "less than 1m"
    if secs == 0:
        return "available"
    return "less than 1m"


class PosixTerminal:
    """Small async POSIX line editor with redraw-safe incoming notifications."""

    def __init__(
        self,
        *,
        input_stream: TextIO | None = None,
        output_stream: TextIO | None = None,
        options: RenderOptions | None = None,
        environ: dict[str, str] | None = None,
    ) -> None:
        self.input = sys.stdin if input_stream is None else input_stream
        self.output = sys.stdout if output_stream is None else output_stream
        self.options = RenderOptions() if options is None else options
        self.environ = os.environ if environ is None else environ
        self._fd = self.input.fileno()
        self._isatty = os.isatty(self._fd)
        self._original_attrs: list[object] | None = None
        self._entered = False
        self._write_lock = asyncio.Lock()
        self._prompt = ""
        self._buffer = ""
        self._cursor_index = 0
        self._secret = False
        self._reading = False
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._pending_chars: list[str] = []
        self._input_render_rows = 1
        self._input_cursor_row = 0

    @property
    def width(self) -> int:
        try:
            columns = shutil.get_terminal_size((DEFAULT_TERMINAL_WIDTH, DEFAULT_TERMINAL_HEIGHT)).columns
        except OSError:
            columns = DEFAULT_TERMINAL_WIDTH
        return max(1, columns)

    @property
    def height(self) -> int:
        try:
            rows = shutil.get_terminal_size((DEFAULT_TERMINAL_WIDTH, DEFAULT_TERMINAL_HEIGHT)).lines
        except OSError:
            rows = DEFAULT_TERMINAL_HEIGHT
        return max(1, rows)

    @property
    def color_enabled(self) -> bool:
        return self.options.color and "NO_COLOR" not in self.environ

    async def __aenter__(self) -> "PosixTerminal":
        self.enter()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.restore()

    def enter(self) -> None:
        if self._entered:
            return
        if self._isatty and not self.options.plain:
            self._original_attrs = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd, termios.TCSANOW)
            self.output.write(ANSI_CURSOR_BLINK_UNDERLINE)
            self.output.flush()
        self._entered = True

    def restore(self) -> None:
        if not self._entered:
            return
        if self._original_attrs is not None:
            try:
                termios.tcsetattr(self._fd, termios.TCSANOW, self._original_attrs)
            except termios.error:
                pass
            self._original_attrs = None
        if not self.options.plain:
            try:
                self.output.write(ANSI_CURSOR_DEFAULT)
                self.output.flush()
            except (OSError, ValueError):
                pass
        self._entered = False
        self._reading = False
        self._prompt = ""
        self._buffer = ""
        self._cursor_index = 0
        self._secret = False
        self._input_render_rows = 1
        self._input_cursor_row = 0

    async def write(
        self,
        text: str = "",
        *,
        heading: bool = False,
        status: bool = False,
    ) -> None:
        # heading/status are retained for compatibility with the Phase 4 boundary.
        style = TextStyle.HEADING if heading else (TextStyle.DIM if status else None)
        line: RenderableLine = styled_line((text, style)) if style else text
        async with self._write_lock:
            self._erase_input_line_if_needed()
            self._write_renderable(line)
            self.output.write("\n")
            self._redraw_input_if_needed()
            self.output.flush()

    async def write_lines(self, lines: tuple[RenderableLine, ...] | list[RenderableLine]) -> None:
        async with self._write_lock:
            self._erase_input_line_if_needed()
            for line in lines:
                self._write_renderable(line)
                self.output.write("\n")
            self._redraw_input_if_needed()
            self.output.flush()

    async def notify(self, lines: tuple[RenderableLine, ...] | list[RenderableLine] | RenderableLine) -> None:
        """Display incoming output without losing the current editable buffer."""

        values: list[RenderableLine]
        if isinstance(lines, (str, StyledLine)):
            values = [lines]
        else:
            values = list(lines)
        async with self._write_lock:
            if self.options.plain:
                self.output.write("\n")
                for line in values:
                    self._write_renderable(line)
                    self.output.write("\n")
                if self._reading:
                    self.output.write(self._prompt)
                    if not self._secret:
                        self.output.write(self._buffer)
            else:
                self._erase_input_line_if_needed()
                for line in values:
                    self._write_renderable(line)
                    self.output.write("\n")
                self._redraw_input_if_needed()
            self.output.flush()

    async def clear(self) -> None:
        async with self._write_lock:
            if self.options.plain:
                self.output.write("\n")
            else:
                self._erase_input_line_if_needed()
                self.output.write(ANSI_CLEAR_SCREEN)
                self._redraw_input_if_needed()
            self.output.flush()

    async def purge(self) -> None:
        """Erase the visible ANSI display and its saved scrollback."""

        if self.options.plain:
            return
        async with self._write_lock:
            self._erase_input_line_if_needed()
            self.output.write(ANSI_PURGE_SCREEN)
            self.output.flush()

    async def replace_view(self, lines: tuple[RenderableLine, ...] | list[RenderableLine]) -> None:
        """Replace the visible ANSI view while preserving an active input buffer."""

        if self.options.plain:
            await self.notify(lines)
            return
        async with self._write_lock:
            self._erase_input_line_if_needed()
            self.output.write(ANSI_CLEAR_SCREEN)
            for line in lines:
                self._write_renderable(line)
                self.output.write("\n")
            self._redraw_input_if_needed()
            self.output.flush()

    async def update_region(
        self,
        *,
        row: int,
        column: int,
        lines: tuple[RenderableLine, ...] | list[RenderableLine],
    ) -> None:
        """Update a fixed ANSI screen region without disturbing active input."""

        if self.options.plain or row < 1 or column < 1:
            return
        async with self._write_lock:
            self.output.write(ANSI_SAVE_CURSOR)
            for offset, line in enumerate(lines):
                self.output.write(f"\x1b[{row + offset};{column}H")
                self._write_renderable(line)
            self.output.write(ANSI_RESTORE_CURSOR)
            self.output.flush()

    async def read_line(self, prompt: str = "", *, secret: bool = False) -> str:
        if self._reading:
            raise RuntimeError("terminal is already reading a line")
        if not self._entered:
            self.enter()

        self._prompt = prompt
        self._buffer = ""
        self._cursor_index = 0
        self._secret = secret
        self._reading = True
        self._input_render_rows = 1
        self._input_cursor_row = 0
        self._render_current_input()
        self.output.flush()
        try:
            if self._isatty:
                if self.options.plain:
                    return await self._readline_plain_tty(secret)
                return await self._readline_cbreak()
            return await asyncio.to_thread(self._blocking_readline, secret)
        finally:
            self._reading = False
            self._prompt = ""
            self._buffer = ""
            self._cursor_index = 0
            self._secret = False
            self._input_render_rows = 1
            self._input_cursor_row = 0

    def _blocking_readline(self, secret: bool) -> str:
        line = self.input.readline()
        if line == "":
            raise TerminalClosed()
        return line.rstrip("\r\n")

    async def _readline_plain_tty(self, secret: bool) -> str:
        """Read one canonical TTY line without an uncancellable worker thread."""

        original: list[object] | None = None
        if secret:
            original = termios.tcgetattr(self._fd)
            hidden = original.copy()
            hidden[3] &= ~termios.ECHO
            termios.tcsetattr(self._fd, termios.TCSANOW, hidden)
        try:
            return await self._readline_canonical_fd()
        finally:
            if original is not None:
                try:
                    termios.tcsetattr(self._fd, termios.TCSANOW, original)
                except termios.error:
                    pass
                self.output.write("\n")
                self.output.flush()

    async def _readline_canonical_fd(self) -> str:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()

        def process_pending(*, canonical_read_complete: bool = False) -> None:
            while self._pending_chars and not future.done():
                char = self._pending_chars.pop(0)
                if char in {"\r", "\n"}:
                    if self._pending_chars:
                        next_char = self._pending_chars[0]
                        if (char, next_char) in {("\r", "\n"), ("\n", "\r")}:
                            self._pending_chars.pop(0)
                    future.set_result(self._buffer)
                    return
                self._buffer += char
                self._cursor_index = len(self._buffer)

            if canonical_read_complete and self._buffer and not future.done():
                future.set_result(self._buffer)

        def on_readable() -> None:
            try:
                raw = os.read(self._fd, 64 * 1024)
            except OSError as exc:
                if not future.done():
                    future.set_exception(TerminalClosed(str(exc)))
                return
            if not raw:
                if not future.done():
                    future.set_exception(TerminalClosed())
                return
            text = self._decoder.decode(raw)
            self._pending_chars.extend(text)
            process_pending(canonical_read_complete="\n" not in text and "\r" not in text)

        loop.add_reader(self._fd, on_readable)
        try:
            if self._pending_chars:
                loop.call_soon(process_pending)
            return await future
        finally:
            loop.remove_reader(self._fd)

    async def _readline_cbreak(self) -> str:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()

        def process_pending() -> None:
            while self._pending_chars and not future.done():
                char = self._pending_chars[0]

                if char == "\x1b":
                    sequence = self._consume_escape_sequence()
                    if sequence is None:
                        return
                    self._apply_edit_sequence(sequence)
                    continue

                self._pending_chars.pop(0)
                if char in {"\r", "\n"}:
                    if self._secret:
                        self.output.write("\n")
                    else:
                        self._erase_input_line_if_needed()
                    self.output.flush()
                    future.set_result(self._buffer)
                    return
                if char == "\x04":
                    if not self._buffer:
                        future.set_exception(TerminalClosed())
                        return
                    # Ctrl-D with text present is deliberately a no-op.
                    continue
                if char == "\x15":  # Ctrl-U
                    if self._buffer:
                        self._buffer = ""
                        self._cursor_index = 0
                        self._redraw_current_input()
                    continue
                if char in {"\x7f", "\x08"}:
                    if self._cursor_index > 0:
                        position = self._cursor_index - 1
                        self._buffer = self._buffer[:position] + self._buffer[self._cursor_index :]
                        self._cursor_index = position
                        self._redraw_current_input()
                    continue
                if char == "\x00":
                    continue

                self._buffer = (
                    self._buffer[: self._cursor_index]
                    + char
                    + self._buffer[self._cursor_index :]
                )
                self._cursor_index += len(char)
                self._redraw_current_input()

        def on_readable() -> None:
            try:
                raw = os.read(self._fd, 4096)
            except OSError as exc:
                if not future.done():
                    future.set_exception(TerminalClosed(str(exc)))
                return
            if not raw:
                if not future.done():
                    future.set_exception(TerminalClosed())
                return
            text = self._decoder.decode(raw)
            self._pending_chars.extend(text)
            process_pending()

        loop.add_reader(self._fd, on_readable)
        try:
            if self._pending_chars:
                loop.call_soon(process_pending)
            return await future
        finally:
            loop.remove_reader(self._fd)

    def _consume_escape_sequence(self) -> str | None:
        """Consume one common CSI editing sequence, retaining partial input."""

        chars = self._pending_chars
        if len(chars) < 2:
            return None
        if chars[1] != "[":
            chars.pop(0)
            return ""
        if len(chars) < 3:
            return None

        # Arrow/Home/End short forms.
        if chars[2] in {"D", "C", "H", "F"}:
            sequence = "".join(chars[:3])
            del chars[:3]
            return sequence

        # Common xterm Home/End/Delete forms: ESC [ 1 ~ / 4 ~ / 3 ~.
        if chars[2].isdigit():
            for index in range(3, min(len(chars), 8)):
                if chars[index] == "~":
                    sequence = "".join(chars[: index + 1])
                    del chars[: index + 1]
                    return sequence
                if not chars[index].isdigit() and chars[index] != ";":
                    break
            if len(chars) < 5:
                return None

        # Unknown complete CSI sequence: drop ESC only so printable tail survives.
        chars.pop(0)
        return ""

    def _apply_edit_sequence(self, sequence: str) -> None:
        if not sequence:
            return
        if sequence == "\x1b[D":
            self._cursor_index = max(0, self._cursor_index - 1)
        elif sequence == "\x1b[C":
            self._cursor_index = min(len(self._buffer), self._cursor_index + 1)
        elif sequence in {"\x1b[H", "\x1b[1~"}:
            self._cursor_index = 0
        elif sequence in {"\x1b[F", "\x1b[4~"}:
            self._cursor_index = len(self._buffer)
        elif sequence == "\x1b[3~" and self._cursor_index < len(self._buffer):
            self._buffer = (
                self._buffer[: self._cursor_index]
                + self._buffer[self._cursor_index + 1 :]
            )
        self._redraw_current_input()

    def _render_current_input(self) -> None:
        if self.options.plain:
            return

        visible = "[hidden]" if self._secret and self._buffer else (
            "" if self._secret else self._buffer
        )
        self.output.write(self._prompt)
        self.output.write(visible)

        width = max(1, self.width)
        total = len(self._prompt) + len(visible)
        self._input_render_rows = max(1, (total + width - 1) // width)

        if self._secret:
            self._input_cursor_row = (total - 1) // width if total else 0
            return

        cursor_offset = len(self._prompt) + self._cursor_index
        if cursor_offset == total:
            self._input_cursor_row = (total - 1) // width if total else 0
            return

        end_row = (total - 1) // width if total else 0
        target_row = cursor_offset // width
        target_column = cursor_offset % width

        self.output.write("\r")
        if end_row > target_row:
            self.output.write(f"\x1b[{end_row - target_row}A")
        if target_column:
            self.output.write(f"\x1b[{target_column}C")

        self._input_cursor_row = target_row

    def _redraw_current_input(self) -> None:
        if self.options.plain:
            return
        self._erase_input_line_if_needed()
        self._render_current_input()
        self.output.flush()

    def _erase_input_line_if_needed(self) -> None:
        if not self._reading or self.options.plain:
            return

        rows = max(1, self._input_render_rows)
        cursor_row = min(max(0, self._input_cursor_row), rows - 1)

        self.output.write("\r")
        if cursor_row:
            self.output.write(f"\x1b[{cursor_row}A")

        for row in range(rows):
            self.output.write(ANSI_CLEAR_LINE)
            if row < rows - 1:
                self.output.write("\x1b[1B\r")

        if rows > 1:
            self.output.write(f"\x1b[{rows - 1}A\r")

    def _redraw_input_if_needed(self) -> None:
        if self._reading and not self.options.plain:
            self._render_current_input()

    def _write_renderable(self, line: RenderableLine) -> None:
        if isinstance(line, str):
            self.output.write(line)
            return
        for span in line.spans:
            if self.color_enabled and span.style is not None:
                self.output.write(STYLE_CODES[span.style.value] + span.text + ANSI_RESET)
            else:
                self.output.write(span.text)


__all__ = [
    "MIN_TERMINAL_WIDTH",
    "PosixTerminal",
    "RenderableLine",
    "RenderOptions",
    "StyledLine",
    "StyledSpan",
    "TerminalClosed",
    "TerminalTextError",
    "TextStyle",
    "format_duration",
    "line_text",
    "render_message_lines",
    "sanitise_user_text",
    "styled_line",
]
