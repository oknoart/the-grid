"""User-facing okno terminal client built on the neutral headless client."""

from __future__ import annotations

import asyncio
import ssl
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol

from . import __version__, ui_text
from .access import AccessError, normalise_display_id
from .client import ClientError, ClientErrorCode, HeadlessClient, create_client_ssl_context
from .hub import BoardCryptoError, BoardCryptoErrorCode
from .models import ClientConfig, ServerSettings, UiSettings
from .phrases import PhraseError, normalise_phrase
from .sessions import SessionError, SessionErrorCode, SessionEventType
from .terminal import (
    MIN_TERMINAL_WIDTH,
    RenderOptions,
    RenderableLine,
    StyledLine,
    TerminalClosed,
    TerminalTextError,
    TextStyle,
    format_duration,
    render_message_lines,
    sanitise_user_text,
    styled_line,
)

PANEL_WIDTH = 56
CAT_MIN_WIDTH = 50
CAT_WIDTH = 11
CAT_TOP = "   /\\____/\\"
CAT_LEFT = "   >•   • <"
CAT_RIGHT = "   > •   •<"
CAT_INTERVAL_SECONDS = 0.5
DOT_INTERVAL_SECONDS = 0.5


class TerminalPort(Protocol):
    options: RenderOptions

    @property
    def width(self) -> int: ...

    @property
    def height(self) -> int: ...

    async def write(self, text: str = "", *, heading: bool = False, status: bool = False) -> None: ...
    async def write_lines(self, lines: tuple[RenderableLine, ...] | list[RenderableLine]) -> None: ...
    async def notify(
        self,
        lines: tuple[RenderableLine, ...] | list[RenderableLine] | RenderableLine,
    ) -> None: ...
    async def clear(self) -> None: ...
    async def replace_view(self, lines: tuple[RenderableLine, ...] | list[RenderableLine]) -> None: ...
    async def update_region(
        self,
        *,
        row: int,
        column: int,
        lines: tuple[RenderableLine, ...] | list[RenderableLine],
    ) -> None: ...
    async def read_line(self, prompt: str = "", *, secret: bool = False) -> str: ...


class InteractiveExit(Exception):
    pass


class ServerUnavailable(Exception):
    pass


class InteractiveClientApp:
    """The approved v1 okno user flow through the Phase 4 terminal boundary."""

    def __init__(
        self,
        *,
        config: ClientConfig,
        terminal: TerminalPort,
        config_path: Path | None = None,
        server_override: str | None = None,
        ca_file_override: Path | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config
        self.terminal = terminal
        self.config_path = config_path  # retained for the CLI boundary; normal UI never writes it
        self.server_override = server_override
        self.ca_file_override = ca_file_override
        self.now = datetime.now if now is None else now
        self.client: HeadlessClient | None = None
        self._board_task: asyncio.Task[None] | None = None
        self._cat_task: asyncio.Task[None] | None = None
        self._resize_task: asyncio.Task[None] | None = None
        self._hub_visible = False
        self._hub_dirty = False
        self._exiting = False
        self._hub_notice: tuple[str, TextStyle | None] | None = None
        self._comm_messages: list[tuple[str, str, str]] = []
        self._comm_peer_id: str | None = None
        self._current_view_kind = "launch"
        self._current_view_builder: Callable[[], list[RenderableLine]] | None = None
        self._cat_state = 0
        self._cat_animatable = False
        self._last_width = terminal.width

    async def run(self) -> int:
        try:
            self._resize_task = asyncio.create_task(self._watch_resize(), name="okno-terminal-resize")
            while True:
                try:
                    host, port, ca_file = self._resolve_server()
                except ServerUnavailable:
                    if not await self._offline_loop():
                        raise InteractiveExit()
                    continue

                await self._show_launch_connecting()
                launch_dots_task = asyncio.create_task(
                    self._animate_dots(
                        kind="launch",
                        row=8,
                        base="    status   " + ui_text.CONNECTING,
                    ),
                    name="okno-launch-dots",
                )
                try:
                    try:
                        await self._connect(host, port, ca_file)
                    except ServerUnavailable:
                        if not await self._offline_loop():
                            raise InteractiveExit()
                        continue

                    authenticated = await self._authenticate(host, port, ca_file)
                finally:
                    launch_dots_task.cancel()
                    await asyncio.gather(launch_dots_task, return_exceptions=True)

                if authenticated:
                    if self.terminal.options.plain:
                        await self.terminal.write_lines(["", ui_text.CONNECTED])
                    else:
                        launch_status_padding = " " * max(
                            0,
                            len(ui_text.CONNECTING) + 3 - len(ui_text.CONNECTED),
                        )
                        await self.terminal.update_region(
                            row=8,
                            column=1,
                            lines=[
                                styled_line(
                                    "    status   ",
                                    (ui_text.CONNECTED, TextStyle.SUCCESS),
                                    launch_status_padding,
                                )
                            ],
                        )
                    break
                if not await self._offline_loop():
                    raise InteractiveExit()
                await self._show_launch_connecting()

            if not await self._select_display():
                raise InteractiveExit()

            await self.terminal.write_lines(["", styled_line((ui_text.CONNECTED, TextStyle.SUCCESS)), ""])
            self._hub_visible = True
            await self._show_hub(replace=True)
            self._board_task = asyncio.create_task(self._watch_board(), name="okno-terminal-board")
            self._cat_task = asyncio.create_task(self._animate_cat(), name="okno-terminal-cat")
            await self._hub_loop()
            return 0
        except (TerminalClosed, KeyboardInterrupt, InteractiveExit):
            return 0
        finally:
            self._exiting = True
            for task_name in ("_cat_task", "_resize_task", "_board_task"):
                task = getattr(self, task_name)
                if task is not None:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    setattr(self, task_name, None)
            if self.client is not None:
                await self.client.close()
            await self.terminal.write(ui_text.END_OF_LINE)

    def _resolve_server(self) -> tuple[str, int, Path | None]:
        """Resolve the installed Grid target without exposing server choice in normal UI."""

        settings = self.config.server
        if self.server_override is not None:
            host, port = parse_server_endpoint(self.server_override, default_port=settings.port)
        elif settings.host is not None:
            host, port = settings.host, settings.port
        else:
            # The production installer/deployment is responsible for provisioning
            # this value. Normal users do not choose a Grid server inside okno.
            raise ServerUnavailable("grid server is not provisioned")
        ca_file = self.ca_file_override if self.ca_file_override is not None else settings.ca_file
        return host, port, ca_file

    async def _connect(self, host: str, port: int, ca_file: Path | None) -> None:
        try:
            tls = create_client_ssl_context(ca_file)
        except (OSError, ssl.SSLError) as exc:
            self._hub_notice = (ui_text.TLS_VERIFICATION_FAILED, TextStyle.ERROR)
            raise ServerUnavailable() from exc

        client = HeadlessClient(host, port, ssl_context=tls, client_version=__version__)
        try:
            await client.connect()
        except ClientError as exc:
            await client.close()
            self._hub_notice = (self._connection_error_text(exc), TextStyle.ERROR)
            raise ServerUnavailable() from exc
        self.client = client

    async def _reconnect_for_access(self, host: str, port: int, ca_file: Path | None) -> bool:
        if self.client is not None:
            await self.client.close()
            self.client = None
        try:
            await self._connect(host, port, ca_file)
            return True
        except ServerUnavailable:
            return False

    async def _show_launch_connecting(self) -> None:
        lines: list[RenderableLine] = [
            *(styled_line((line, TextStyle.HEADING)) for line in ui_text.OKNO_LOGO),
            styled_line(("─" * ui_text.OKNO_LOGO_WIDTH, TextStyle.HEADING)),
            "",
            styled_line("    status   ", (ui_text.CONNECTING + ".", TextStyle.DIM)),
            "",
            "    access phrase",
        ]
        await self._display_view("launch", lambda: lines, replace=True)

    async def _authenticate(self, host: str, port: int, ca_file: Path | None) -> bool:
        while True:
            assert self.client is not None
            raw = await self.terminal.read_line("    > ", secret=True)
            try:
                phrase = normalise_phrase(raw)
            except PhraseError:
                await self.terminal.write_lines(
                    [styled_line((ui_text.ACCESS_PHRASE_MALFORMED, TextStyle.ERROR)), "", "    access phrase"]
                )
                continue
            try:
                await self.client.authenticate(phrase)
                return True
            except ClientError as exc:
                if exc.code is ClientErrorCode.ACCESS:
                    await self.terminal.write_lines(
                        ["", styled_line((ui_text.ACCESS_AUTHENTICATION_FAILED, TextStyle.ERROR)), "", "    access phrase"]
                    )
                    if not await self._reconnect_for_access(host, port, ca_file):
                        return False
                    continue
                self._hub_notice = (self._connection_error_text(exc), TextStyle.ERROR)
                return False

    async def _select_display(self) -> bool:
        assert self.client is not None
        await self.terminal.write_lines(["", "    enter 3 character id"])
        while True:
            raw = (await self.terminal.read_line("    > ")).strip()
            if len(raw) != 3:
                await self.terminal.write_lines(
                    [styled_line((ui_text.ID_LENGTH_INVALID, TextStyle.ERROR)), "", "    enter 3 character id"]
                )
                continue
            try:
                selected = normalise_display_id(raw)
            except (AccessError, ValueError, TypeError):
                await self.terminal.write_lines(
                    [styled_line((ui_text.ID_INVALID, TextStyle.ERROR)), "", "    enter 3 character id"]
                )
                continue
            try:
                await self.client.reserve_display(selected)
                return True
            except ClientError as exc:
                if exc.code is ClientErrorCode.DISPLAY_UNAVAILABLE:
                    await self.terminal.write_lines(
                        [styled_line((ui_text.ID_ACTIVE, TextStyle.ERROR)), "", "    enter 3 character id"]
                    )
                    continue
                await self.terminal.write_lines([styled_line((self._connection_error_text(exc), TextStyle.ERROR))])
                return False

    async def _offline_loop(self) -> bool:
        self._current_view_kind = "offline"

        def lines() -> list[RenderableLine]:
            return [
                *(styled_line((line, TextStyle.HEADING)) for line in ui_text.OKNO_LOGO),
                styled_line(("─" * ui_text.OKNO_LOGO_WIDTH, TextStyle.HEADING)),
                "",
                styled_line("    status   ", (ui_text.OFFLINE, TextStyle.ERROR)),
                "",
                f"    {ui_text.UNABLE_TO_REACH_GRID}",
                "",
                f"    {ui_text.OFFLINE_COMMANDS}",
                "",
            ]

        await self._display_view("offline", lines, replace=True)
        while True:
            command = (await self.terminal.read_line("    > ")).strip()
            if command == "/retry":
                return True
            if command == "/exit":
                return False
            await self.terminal.write_lines([styled_line((ui_text.COMMAND_UNKNOWN, TextStyle.ERROR))])

    async def _hub_loop(self) -> None:
        while not self._exiting:
            line = (await self._read_connected(ui_text.INPUT_PROMPT)).strip()
            await self._handle_hub_line(line)

    async def _handle_hub_line(self, line: str) -> None:
        if not line:
            return
        if line == "/exit":
            raise InteractiveExit()
        if line == "/post":
            await self._post()
        elif line == "/start":
            await self._start_comm()
        elif line == "/join":
            await self._join_comm()
        elif line == "/status":
            next_line = await self._show_status_and_read(in_comm=False)
            await self._show_hub(replace=not self.terminal.options.plain)
            await self._handle_hub_line(next_line.strip())
        elif line == "/help":
            next_line = await self._show_help_and_read(in_comm=False)
            await self._show_hub(replace=not self.terminal.options.plain)
            await self._handle_hub_line(next_line.strip())
        else:
            self._hub_notice = (ui_text.COMMAND_UNKNOWN, TextStyle.ERROR)
            await self._show_hub(replace=not self.terminal.options.plain)

    async def _post(self) -> None:
        assert self.client is not None
        remaining = self.client.post_remaining_seconds
        if remaining > 0:
            self._hub_notice = (ui_text.post_cooldown(format_duration(remaining)), TextStyle.WARNING)
            await self._show_hub(replace=not self.terminal.options.plain)
            return

        await self._show_hub(replace=not self.terminal.options.plain, compose=True)
        raw = await self._read_connected(ui_text.MESSAGE_PROMPT)
        try:
            text = sanitise_user_text(raw)
        except TerminalTextError:
            self._hub_notice = (ui_text.HUB_MESSAGE_UNSAFE, TextStyle.ERROR)
            await self._show_hub(replace=not self.terminal.options.plain)
            return
        if not text:
            self._hub_notice = (ui_text.ENTER_MESSAGE, TextStyle.ERROR)
            await self._show_hub(replace=not self.terminal.options.plain)
            return
        try:
            outcome = await self.client.post_board(text)
        except BoardCryptoError as exc:
            if exc.code is BoardCryptoErrorCode.TOO_LONG:
                self._hub_notice = (ui_text.HUB_MESSAGE_TOO_LONG, TextStyle.ERROR)
            else:
                self._hub_notice = (ui_text.HUB_SEND_FAILED, TextStyle.ERROR)
            await self._show_hub(replace=not self.terminal.options.plain)
            return
        except ClientError as exc:
            self._hub_notice = (self._connection_error_text(exc), TextStyle.ERROR)
            await self._show_hub(replace=not self.terminal.options.plain)
            return
        if not outcome.accepted:
            if outcome.reason == "cooldown":
                self._hub_notice = (
                    ui_text.post_cooldown(format_duration(outcome.remaining_seconds)),
                    TextStyle.WARNING,
                )
            else:
                self._hub_notice = (ui_text.HUB_SEND_FAILED, TextStyle.ERROR)
        else:
            self._hub_notice = None
        await self._show_hub(replace=not self.terminal.options.plain)

    async def _start_comm(self) -> None:
        assert self.client is not None
        self._hub_visible = False
        self._cat_state = 0
        try:
            phrase = await self.client.start_session()
        except ClientError as exc:
            self._hub_notice = (self._session_setup_error(exc), TextStyle.ERROR)
            self._hub_visible = True
            await self._reload_hub_after_comm()
            return

        notice: tuple[str, TextStyle | None] | None = None

        def waiting_lines() -> list[RenderableLine]:
            return self._start_comm_lines(phrase, notice=notice)

        await self._display_view("start_comm", waiting_lines, replace=True)
        complete_task = asyncio.create_task(self.client.complete_session())
        dots_task = asyncio.create_task(
            self._animate_dots(
                kind="start_comm",
                row=7,
                base=ui_text.WAITING_FOR_CONNECTION,
            ),
            name="okno-start-comm-dots",
        )
        try:
            while True:
                read_task = asyncio.create_task(self.terminal.read_line(ui_text.INPUT_PROMPT))
                done, pending = await asyncio.wait(
                    {complete_task, read_task}, return_when=asyncio.FIRST_COMPLETED
                )
                if complete_task in done:
                    read_task.cancel()
                    await asyncio.gather(read_task, return_exceptions=True)
                    await complete_task
                    break

                command = read_task.result().strip()
                if command != "/cancel":
                    notice = (ui_text.COMMAND_UNKNOWN, TextStyle.ERROR)
                    await self._display_view("start_comm", waiting_lines, replace=not self.terminal.options.plain)
                    continue

                try:
                    cancelled = await self.client.cancel_waiting_session()
                except ClientError:
                    cancelled = False
                if cancelled:
                    complete_task.cancel()
                    await asyncio.gather(complete_task, return_exceptions=True)
                    self._hub_notice = (ui_text.COMM_CANCELLED, TextStyle.DIM)
                    self._hub_visible = True
                    await self._reload_hub_after_comm()
                    return
                # Pairing won the race. Do not report cancellation; finish the handshake.
                await complete_task
                break
        except ClientError as exc:
            if exc.code is ClientErrorCode.TIMEOUT:
                self._hub_notice = (ui_text.COMM_EXPIRED, TextStyle.WARNING)
            else:
                self._hub_notice = (self._session_setup_error(exc), TextStyle.ERROR)
            self._hub_visible = True
            await self._reload_hub_after_comm()
            return
        finally:
            dots_task.cancel()
            await asyncio.gather(dots_task, return_exceptions=True)
            if not complete_task.done():
                complete_task.cancel()
                await asyncio.gather(complete_task, return_exceptions=True)

        await self._comm_loop()

    async def _join_comm(self) -> None:
        assert self.client is not None
        self._hub_visible = False
        notice: tuple[str, TextStyle | None] | None = None

        while True:
            def prompt_lines() -> list[RenderableLine]:
                return self._join_comm_lines(notice=notice, connecting=False)

            await self._display_view("join_comm", prompt_lines, replace=True)
            raw = await self.terminal.read_line(ui_text.INPUT_PROMPT)
            if raw.strip() == "/cancel":
                self._hub_notice = None
                self._hub_visible = True
                await self._reload_hub_after_comm()
                return
            try:
                phrase = normalise_phrase(raw)
                break
            except PhraseError:
                notice = (ui_text.COMM_PHRASE_MALFORMED, TextStyle.ERROR)

        await self._display_view(
            "join_comm",
            lambda: self._join_comm_lines(notice=None, connecting=True),
            replace=True,
        )
        dots_task = asyncio.create_task(
            self._animate_dots(kind="join_comm", row=3, base=ui_text.CONNECTING_COMM),
            name="okno-join-comm-dots",
        )
        try:
            await self.client.join_session(phrase)
            await self.client.complete_session()
        except ClientError as exc:
            self._hub_notice = (self._session_setup_error(exc), TextStyle.ERROR)
            self._hub_visible = True
            await self._reload_hub_after_comm()
            return
        finally:
            dots_task.cancel()
            await asyncio.gather(dots_task, return_exceptions=True)
        await self._comm_loop()

    async def _comm_loop(self) -> None:
        assert self.client is not None
        channel = self.client.session_channel
        if channel is None or channel.peer_display_id is None or self.client.display_id is None:
            self._hub_notice = (ui_text.COMM_HANDSHAKE_FAILED, TextStyle.ERROR)
            self._hub_visible = True
            await self._reload_hub_after_comm()
            return

        local_id = self.client.display_id
        peer_id = channel.peer_display_id
        self._comm_peer_id = peer_id
        self._comm_messages = []
        self._hub_visible = False
        await self._show_comm(peer_id, replace=True)

        closed = asyncio.Event()
        close_notice: list[tuple[str, TextStyle | None] | None] = [None]
        event_task = asyncio.create_task(
            self._watch_comm_events(peer_id, closed, close_notice), name="okno-terminal-comm"
        )
        try:
            while not closed.is_set():
                read_task = asyncio.create_task(self.terminal.read_line(ui_text.INPUT_PROMPT))
                close_task = asyncio.create_task(closed.wait())
                done, pending = await asyncio.wait(
                    {read_task, close_task}, return_when=asyncio.FIRST_COMPLETED
                )
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
                if close_task in done and close_task.result():
                    if not read_task.done():
                        read_task.cancel()
                    break
                try:
                    raw = read_task.result()
                except asyncio.CancelledError:
                    break
                await self._handle_comm_line(raw, local_id, peer_id, closed, close_notice)
        finally:
            event_task.cancel()
            await asyncio.gather(event_task, return_exceptions=True)
            self._comm_peer_id = None
            self._comm_messages = []
            self._hub_visible = True
            if close_notice[0] is not None:
                self._hub_notice = close_notice[0]
            await self._reload_hub_after_comm()

    async def _handle_comm_line(
        self,
        raw: str,
        local_id: str,
        peer_id: str,
        closed: asyncio.Event,
        close_notice: list[tuple[str, TextStyle | None] | None],
    ) -> None:
        assert self.client is not None
        line = raw.strip() if raw.startswith("/") else raw
        if line == "/status":
            next_line = await self._show_status_and_read(in_comm=True, peer_id=peer_id)
            await self._show_comm(peer_id, replace=not self.terminal.options.plain)
            await self._handle_comm_line(next_line, local_id, peer_id, closed, close_notice)
            return
        if line == "/help":
            next_line = await self._show_help_and_read(in_comm=True)
            await self._show_comm(peer_id, replace=not self.terminal.options.plain)
            await self._handle_comm_line(next_line, local_id, peer_id, closed, close_notice)
            return
        if line == "/end":
            await self._show_comm(peer_id, replace=not self.terminal.options.plain, ending=True)
            answer = (await self.terminal.read_line(ui_text.INPUT_PROMPT)).strip().lower()
            if answer != "y":
                await self._show_comm(peer_id, replace=not self.terminal.options.plain)
                return
            try:
                await self.client.end_session()
            except ClientError:
                pass
            close_notice[0] = (ui_text.COMM_ENDED, TextStyle.DIM)
            closed.set()
            return
        if line.startswith("/"):
            await self._show_comm(
                peer_id,
                replace=not self.terminal.options.plain,
                notice=(ui_text.COMMAND_UNKNOWN, TextStyle.ERROR),
            )
            return
        try:
            text = sanitise_user_text(raw)
        except TerminalTextError:
            await self._show_comm(
                peer_id,
                replace=not self.terminal.options.plain,
                notice=(ui_text.COMM_MESSAGE_UNSAFE, TextStyle.ERROR),
            )
            return
        if not text:
            return
        try:
            await self.client.send_session_text(text)
        except SessionError as exc:
            if exc.code is SessionErrorCode.TOO_LONG:
                await self._show_comm(
                    peer_id,
                    replace=not self.terminal.options.plain,
                    notice=(ui_text.COMM_MESSAGE_TOO_LONG, TextStyle.ERROR),
                )
            else:
                close_notice[0] = (ui_text.COMM_INTEGRITY_FAILED, TextStyle.ERROR)
                closed.set()
            return
        except ClientError:
            close_notice[0] = (ui_text.SERVER_DISCONNECTED, TextStyle.ERROR)
            closed.set()
            return

        self._comm_messages.append((local_id, ">", text))
        if self.terminal.options.plain:
            await self.terminal.write_lines([*render_message_lines(local_id, ">", text, self._panel_width()), ""])
        else:
            await self._show_comm(peer_id, replace=True)

    async def _watch_comm_events(
        self,
        peer_id: str,
        closed: asyncio.Event,
        close_notice: list[tuple[str, TextStyle | None] | None],
    ) -> None:
        assert self.client is not None
        while not closed.is_set():
            session_task = asyncio.create_task(self.client.session_events.get())
            close_task = asyncio.create_task(self.client.session_closed_events.get())
            conn_task = asyncio.create_task(self.client.wait_closed())
            done, pending = await asyncio.wait(
                {session_task, close_task, conn_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            if conn_task in done:
                close_notice[0] = (ui_text.SERVER_DISCONNECTED, TextStyle.ERROR)
                closed.set()
                return
            if close_task in done:
                reason = close_task.result().reason
                if reason in {"peer_close", "peer_disconnect"}:
                    close_notice[0] = (ui_text.peer_ended(peer_id), TextStyle.DIM)
                elif reason == "integrity_failure":
                    close_notice[0] = (ui_text.COMM_INTEGRITY_FAILED, TextStyle.ERROR)
                elif reason != "user_close":
                    close_notice[0] = (ui_text.COMM_ENDED, TextStyle.DIM)
                closed.set()
                return
            event = session_task.result()
            if event.event_type is SessionEventType.TEXT:
                self._comm_messages.append((peer_id, "<", event.value))
                if self.terminal.options.plain:
                    await self.terminal.notify(
                        [*render_message_lines(peer_id, "<", event.value, self._panel_width()), ""]
                    )
                else:
                    await self._show_comm(peer_id, replace=True)
            elif event.event_type is SessionEventType.CLOSE:
                close_notice[0] = (ui_text.peer_ended(peer_id), TextStyle.DIM)
                closed.set()
                return

    async def _watch_board(self) -> None:
        assert self.client is not None
        while True:
            board_task = asyncio.create_task(self.client.board_events.get())
            warning_task = asyncio.create_task(self.client.board_warnings.get())
            done, pending = await asyncio.wait(
                {board_task, warning_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            if warning_task in done:
                if self._hub_visible:
                    self._hub_notice = (ui_text.HUB_CIPHERTEXT_INVALID, TextStyle.ERROR)
                    await self._show_hub(replace=not self.terminal.options.plain)
                continue
            event = board_task.result()
            if not self._hub_visible:
                self._hub_dirty = True
                continue
            if self.terminal.options.plain:
                if event.kind == "update" and event.record is not None:
                    await self.terminal.notify(
                        [
                            *render_message_lines(
                                event.record.message.display_id,
                                "<",
                                event.record.message.text,
                                self._panel_width(),
                            ),
                            "",
                        ]
                    )
                else:
                    await self.terminal.notify(ui_text.HUB_CHANGED)
            else:
                await self._show_hub(replace=True)

    async def _reload_hub_after_comm(self) -> None:
        assert self.client is not None
        if self._hub_dirty:
            try:
                await self.client.synchronise_board()
            except ClientError:
                pass
            self._hub_dirty = False
        await self._show_hub(replace=not self.terminal.options.plain)

    async def _show_hub(self, *, replace: bool, compose: bool = False) -> None:
        self._cat_state = 0
        self._current_view_kind = "hub"
        builder = lambda: self._hub_lines(cat_state=self._cat_state, compose=compose)
        await self._display_view("hub", builder, replace=replace)
        lines = builder()
        self._cat_animatable = (
            not self.terminal.options.plain
            and self.terminal.width >= CAT_MIN_WIDTH
            and len(lines) + 1 <= self.terminal.height
        )

    def _hub_lines(self, *, cat_state: int = 0, compose: bool = False) -> list[RenderableLine]:
        assert self.client is not None
        width = self._panel_width()
        header = self._title_rule(ui_text.THE_HUB, width)
        separator = "─" * width
        records = self.client.board_records
        count = len(records)
        message_word = "message" if count == 1 else "messages"
        time_text = self.now().strftime("%H:%M")
        left_top = f"    {self.client.display_id or '-'}   connected"
        left_bottom = f"    {count} {message_word} / {time_text}"
        show_cat = width >= CAT_MIN_WIDTH
        cat_bottom = CAT_LEFT if cat_state == 0 else CAT_RIGHT

        if show_cat:
            top_plain = self._overlay_right(left_top, CAT_TOP.ljust(CAT_WIDTH), width)
            bottom_plain = self._overlay_right(left_bottom, cat_bottom, width)
            # Keep the metadata styling simple; cat remains normal foreground.
            top = styled_line(
                (top_plain[:4], TextStyle.DIM),
                top_plain[4 : 4 + len(self.client.display_id or "-")],
                (top_plain[4 + len(self.client.display_id or "-") : top_plain.find("connected")], TextStyle.DIM),
                ("connected", TextStyle.SUCCESS),
                top_plain[top_plain.find("connected") + len("connected") :],
            )
            bottom = styled_line((bottom_plain[: len(left_bottom)], TextStyle.DIM), bottom_plain[len(left_bottom) :])
        else:
            top = styled_line("    ", self.client.display_id or "-", "   ", ("connected", TextStyle.SUCCESS))
            bottom = styled_line((left_bottom, TextStyle.DIM))

        lines: list[RenderableLine] = [
            styled_line((header, TextStyle.HEADING)),
            "",
            top,
            bottom,
            "",
            styled_line((separator, TextStyle.HEADING)),
            "",
        ]
        if self._hub_notice is not None:
            text, style = self._hub_notice
            lines.extend([styled_line((text, style)), ""])

        if not records:
            lines.extend([styled_line(("    " + ui_text.NO_MESSAGES, TextStyle.DIM)), ""])
        else:
            for index, item in enumerate(records):
                lines.extend(
                    render_message_lines(
                        item.message.display_id,
                        "<",
                        item.message.text,
                        width,
                    )
                )
                if index != len(records) - 1:
                    lines.append("")
            lines.append("")

        lines.extend(
            [
                styled_line((separator, TextStyle.HEADING)),
                "",
                *(styled_line((line, TextStyle.DIM)) for line in self._command_lines(ui_text.HUB_COMMAND_LIST, width)),
                "",
            ]
        )
        if not compose:
            lines.append(styled_line((ui_text.WRITE_HUB_MESSAGE, TextStyle.DIM)))
        return lines

    async def _show_comm(
        self,
        peer_id: str,
        *,
        replace: bool,
        ending: bool = False,
        notice: tuple[str, TextStyle | None] | None = None,
    ) -> None:
        self._current_view_kind = "comm"
        builder = lambda: self._comm_lines(peer_id, ending=ending, notice=notice)
        await self._display_view("comm", builder, replace=replace)

    def _comm_lines(
        self,
        peer_id: str,
        *,
        ending: bool = False,
        notice: tuple[str, TextStyle | None] | None = None,
    ) -> list[RenderableLine]:
        assert self.client is not None
        width = self._panel_width()
        separator = "─" * width
        local_id = self.client.display_id or "-"
        lines: list[RenderableLine] = [
            styled_line((self._title_rule(ui_text.COMM, width), TextStyle.HEADING)),
            "",
            styled_line("    ", local_id, " × ", peer_id, " / ", ("encrypted", TextStyle.SUCCESS)),
            "",
            styled_line((separator, TextStyle.HEADING)),
            "",
        ]
        if notice is not None:
            text, style = notice
            lines.extend([styled_line((text, style)), ""])
        if not self._comm_messages:
            lines.extend([styled_line(("    " + ui_text.NO_MESSAGES, TextStyle.DIM)), ""])
        else:
            for index, (display_id, marker, text) in enumerate(self._comm_messages):
                lines.extend(render_message_lines(display_id, marker, text, width))
                if index != len(self._comm_messages) - 1:
                    lines.append("")
            lines.append("")
        lines.extend(
            [
                styled_line((separator, TextStyle.HEADING)),
                "",
                *(styled_line((line, TextStyle.DIM)) for line in self._command_lines(ui_text.COMM_COMMAND_LIST, width)),
                "",
                styled_line(((ui_text.END_COMM_LABEL if ending else ui_text.WRITE_COMM_MESSAGE), TextStyle.DIM)),
            ]
        )
        return lines

    def _start_comm_lines(
        self,
        phrase: str,
        *,
        notice: tuple[str, TextStyle | None] | None = None,
    ) -> list[RenderableLine]:
        width = self._panel_width()
        lines: list[RenderableLine] = [
            styled_line((self._title_rule(ui_text.START_COMM, width), TextStyle.HEADING)),
            "",
            ui_text.COMM_PHRASE_LABEL,
            "",
            styled_line((f"    {phrase}", TextStyle.HEADING)),
            "",
            ui_text.WAITING_FOR_CONNECTION + ".",
            "",
            styled_line((ui_text.WAIT_COMMANDS, TextStyle.DIM)),
        ]
        if notice is not None:
            text, style = notice
            lines.extend(["", styled_line((text, style))])
        lines.extend(["", styled_line(("─" * width, TextStyle.HEADING)), ""])
        return lines

    def _join_comm_lines(
        self,
        *,
        notice: tuple[str, TextStyle | None] | None,
        connecting: bool,
    ) -> list[RenderableLine]:
        width = self._panel_width()
        lines: list[RenderableLine] = [
            styled_line((self._title_rule(ui_text.JOIN_COMM, width), TextStyle.HEADING)),
            "",
        ]
        if connecting:
            lines.append(ui_text.CONNECTING_COMM + ".")
        else:
            lines.extend(
                [
                    ui_text.COMM_PHRASE_LABEL,
                    "",
                    styled_line((ui_text.WAIT_COMMANDS, TextStyle.DIM)),
                ]
            )
        if notice is not None:
            text, style = notice
            lines.extend(["", styled_line((text, style))])
        lines.extend(["", styled_line(("─" * width, TextStyle.HEADING)), ""])
        return lines

    async def _show_status_and_read(self, *, in_comm: bool, peer_id: str | None = None) -> str:
        await self._display_view(
            "status",
            lambda: self._status_lines(in_comm=in_comm, peer_id=peer_id),
            replace=not self.terminal.options.plain,
        )
        return await self._read_connected(ui_text.INPUT_PROMPT)

    def _status_lines(self, *, in_comm: bool, peer_id: str | None) -> list[RenderableLine]:
        assert self.client is not None
        width = self._panel_width()
        separator = "─" * width
        remaining = self.client.post_remaining_seconds
        lines: list[RenderableLine] = [
            styled_line((self._title_rule(ui_text.STATUS, width), TextStyle.HEADING)),
            "",
            self._status_row("server", "connected", TextStyle.SUCCESS),
            self._status_row("id", self.client.display_id or "-", None),
        ]
        if in_comm:
            channel = self.client.session_channel
            lines.extend(
                [
                    self._status_row("comm", peer_id or "-", None),
                    self._status_row("encrypted", "yes", TextStyle.SUCCESS),
                    self._status_row(
                        "verification",
                        channel.verification_code if channel else "-",
                        None,
                    ),
                ]
            )
        else:
            post_text = "available" if remaining == 0 else "available in " + format_duration(remaining)
            post_style = TextStyle.SUCCESS if remaining == 0 else TextStyle.WARNING
            lines.extend(
                [
                    self._status_row("hub", "connected", TextStyle.SUCCESS),
                    self._status_row("post", post_text, post_style),
                ]
            )
        lines.extend(
            [
                "",
                styled_line((separator, TextStyle.HEADING)),
                "",
                styled_line((ui_text.PRESS_RETURN_TO_GO_BACK, TextStyle.DIM)),
            ]
        )
        return lines

    async def _show_help_and_read(self, *, in_comm: bool) -> str:
        await self._display_view(
            "help",
            lambda: self._help_lines(in_comm=in_comm),
            replace=not self.terminal.options.plain,
        )
        return await self._read_connected(ui_text.INPUT_PROMPT)

    def _help_lines(self, *, in_comm: bool) -> list[RenderableLine]:
        width = self._panel_width()
        separator = "─" * width
        entries = ui_text.COMM_HELP if in_comm else ui_text.HUB_HELP
        return [
            styled_line((self._title_rule(ui_text.HELP, width), TextStyle.HEADING)),
            "",
            *(styled_line((line, TextStyle.DIM)) for line in entries),
            "",
            styled_line((separator, TextStyle.HEADING)),
            "",
            styled_line((ui_text.PRESS_RETURN_TO_GO_BACK, TextStyle.DIM)),
        ]

    async def _read_connected(self, prompt: str) -> str:
        assert self.client is not None
        read_task = asyncio.create_task(self.terminal.read_line(prompt))
        closed_task = asyncio.create_task(self.client.wait_closed())
        done, pending = await asyncio.wait(
            {read_task, closed_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if closed_task in done:
            if not read_task.done():
                read_task.cancel()
            await self.terminal.write(ui_text.SERVER_CONNECTION_LOST)
            raise InteractiveExit()
        return read_task.result()

    async def _display_view(
        self,
        kind: str,
        builder: Callable[[], list[RenderableLine]],
        *,
        replace: bool,
    ) -> None:
        self._current_view_kind = kind
        self._current_view_builder = builder
        lines = self._render_current(builder)
        if replace:
            await self.terminal.replace_view(lines)
        else:
            await self.terminal.write_lines(lines)

    def _render_current(self, builder: Callable[[], list[RenderableLine]]) -> list[RenderableLine]:
        if self.terminal.width < MIN_TERMINAL_WIDTH:
            return [
                styled_line((ui_text.TERMINAL_TOO_NARROW, TextStyle.ERROR)),
                "",
                styled_line((ui_text.MINIMUM_WIDTH, TextStyle.DIM)),
            ]
        return builder()

    async def _watch_resize(self) -> None:
        try:
            while True:
                await asyncio.sleep(0.25)
                width = self.terminal.width
                if width == self._last_width:
                    continue
                self._last_width = width
                if self.terminal.options.plain or self._current_view_builder is None:
                    continue
                await self.terminal.replace_view(self._render_current(self._current_view_builder))
        except asyncio.CancelledError:
            raise

    async def _animate_cat(self) -> None:
        try:
            while True:
                await asyncio.sleep(CAT_INTERVAL_SECONDS)
                if (
                    self._current_view_kind != "hub"
                    or not self._hub_visible
                    or not self._cat_animatable
                    or self.terminal.options.plain
                    or self.terminal.width < CAT_MIN_WIDTH
                ):
                    continue
                self._cat_state = 1 - self._cat_state
                width = self._panel_width()
                column = width - CAT_WIDTH + 1
                bottom = CAT_LEFT if self._cat_state == 0 else CAT_RIGHT
                await self.terminal.update_region(
                    row=3,
                    column=column,
                    lines=[CAT_TOP.ljust(CAT_WIDTH), bottom],
                )
        except asyncio.CancelledError:
            raise

    async def _animate_dots(self, *, kind: str, row: int, base: str) -> None:
        """Animate one-to-three passive status dots without moving input."""

        dot_count = 1
        try:
            while True:
                await asyncio.sleep(DOT_INTERVAL_SECONDS)
                if self._current_view_kind != kind:
                    return
                dot_count = 1 if dot_count == 3 else dot_count + 1
                text = base + "." * dot_count + " " * (3 - dot_count)
                await self.terminal.update_region(row=row, column=1, lines=[text])
        except asyncio.CancelledError:
            raise

    def _panel_width(self) -> int:
        return min(PANEL_WIDTH, max(MIN_TERMINAL_WIDTH, self.terminal.width))

    @staticmethod
    def _title_rule(title: str, width: int) -> str:
        label = f" {title} "
        remaining = max(0, width - len(label))
        left = remaining // 2
        right = remaining - left
        return "─" * left + label + "─" * right

    @staticmethod
    def _overlay_right(left: str, right: str, width: int) -> str:
        if len(left) + len(right) >= width:
            return left[: max(0, width - len(right) - 1)] + " " + right
        return left + " " * (width - len(left) - len(right)) + right

    @staticmethod
    def _command_lines(commands: tuple[str, ...], width: int) -> list[str]:
        for spacing in (4, 3, 2, 1):
            joined = (" " * spacing).join(commands)
            if len(joined) <= width:
                return [joined]
        lines: list[str] = []
        current = ""
        for command in commands:
            candidate = command if not current else current + "  " + command
            if current and len(candidate) > width:
                lines.append(current)
                current = command
            else:
                current = candidate
        if current:
            lines.append(current)
        return lines

    @staticmethod
    def _status_row(label: str, value: str, value_style: TextStyle | None) -> StyledLine:
        return styled_line((f"{label:<16}", TextStyle.DIM), (value, value_style))

    def _connection_error_text(self, error: ClientError) -> str:
        cause = error.__cause__
        if isinstance(cause, ssl.SSLCertVerificationError):
            return ui_text.TLS_VERIFICATION_FAILED
        if error.code is ClientErrorCode.PROTOCOL:
            return ui_text.UNSUPPORTED_PROTOCOL
        return ui_text.SERVER_UNREACHABLE

    @staticmethod
    def _session_setup_error(error: ClientError) -> str:
        if error.code in {ClientErrorCode.SESSION_UNAVAILABLE, ClientErrorCode.TIMEOUT}:
            return ui_text.COMM_UNAVAILABLE
        if error.code is ClientErrorCode.CONNECTION:
            return ui_text.SERVER_DISCONNECTED
        return ui_text.COMM_HANDSHAKE_FAILED


def parse_server_endpoint(value: str, *, default_port: int = 7331) -> tuple[str, int]:
    """Parse HOST[:PORT] including bracketed IPv6 without accepting URLs."""

    if not isinstance(value, str):
        raise TypeError("server endpoint must be a string")
    value = value.strip()
    if not value or "://" in value or any(char.isspace() for char in value):
        raise ValueError("invalid server endpoint")

    if value.startswith("["):
        end = value.find("]")
        if end <= 1:
            raise ValueError("invalid server endpoint")
        host = value[1:end]
        remainder = value[end + 1 :]
        if not remainder:
            port = default_port
        elif remainder.startswith(":") and remainder[1:]:
            port = int(remainder[1:], 10)
        else:
            raise ValueError("invalid server endpoint")
    elif value.count(":") == 1:
        host, port_text = value.rsplit(":", 1)
        if not host or not port_text:
            raise ValueError("invalid server endpoint")
        port = int(port_text, 10)
    else:
        host, port = value, default_port

    ServerSettings(host=host, port=port)
    return host, port


def apply_ui_overrides(config: ClientConfig, *, plain: bool, no_color: bool) -> ClientConfig:
    """Apply non-persistent presentation flags and NO_COLOR-compatible behavior."""

    return replace(
        config,
        ui=UiSettings(
            color=(config.ui.color and not no_color),
            plain=(config.ui.plain or plain),
        ),
    )


__all__ = [
    "CAT_LEFT",
    "CAT_RIGHT",
    "CAT_TOP",
    "InteractiveClientApp",
    "ServerUnavailable",
    "apply_ui_overrides",
    "parse_server_endpoint",
]
