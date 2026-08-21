from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from phase3_support import start_tls_server
from phase4_support import FakeTerminal
from the_grid.interactive import InteractiveClientApp
from the_grid.models import ClientConfig, ServerSettings, UiSettings
from the_grid.terminal import RenderOptions


class SlowCancelTerminal(FakeTerminal):
    """Expose overlapping reads if a cancelled state read is not awaited."""

    def __post_init__(self) -> None:
        super().__post_init__()
        self.active_reads = 0
        self.overlapping_reads = False

    async def read_line(self, prompt: str = "", *, secret: bool = False) -> str:
        self.active_reads += 1
        if self.active_reads > 1:
            self.overlapping_reads = True
        try:
            return await super().read_line(prompt, secret=secret)
        except asyncio.CancelledError:
            await asyncio.sleep(0.05)
            raise
        finally:
            self.active_reads -= 1


def _hub_count(terminal: FakeTerminal) -> int:
    return sum("THE HUB" in line for line in terminal.lines)


class PhaseFourCompletionGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_terminal_clients_complete_hub_and_comm_flow(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            setup, server, host, port, _tls = await start_tls_server(root)
            cert = root / "cert.pem"
            config = ClientConfig(
                server=ServerSettings(host=host, port=port, ca_file=cert),
                ui=UiSettings(color=False, plain=False),
            )
            left_terminal = FakeTerminal(options=RenderOptions(color=False, plain=False))
            right_terminal = FakeTerminal(options=RenderOptions(color=False, plain=False))
            left = InteractiveClientApp(config=config, terminal=left_terminal)
            right = InteractiveClientApp(config=config, terminal=right_terminal)

            left_terminal.feed(setup.phrase)
            left_terminal.feed("ABC")
            right_terminal.feed(setup.phrase)
            right_terminal.feed("J7K")

            with patch("the_grid.client.generate_phrase", return_value="velvet orbit green cabin"):
                left_task = asyncio.create_task(left.run())
                right_task = asyncio.create_task(right.run())
                try:
                    await asyncio.gather(
                        left_terminal.wait_for_text("THE HUB"),
                        right_terminal.wait_for_text("THE HUB"),
                    )

                    left_terminal.feed("/post")
                    left_terminal.feed("are you receiving this?")
                    await right_terminal.wait_for_text("ABC < are you receiving this?")

                    left_terminal.feed("/start")
                    await left_terminal.wait_for_text("waiting for connection")
                    right_terminal.feed("/join")
                    right_terminal.feed("velvet orbit green cabin")

                    await asyncio.gather(
                        left_terminal.wait_for_text("ABC × J7K / encrypted"),
                        right_terminal.wait_for_text("J7K × ABC / encrypted"),
                    )

                    left_terminal.feed("yes.")
                    await right_terminal.wait_for_text("ABC < yes.")
                    right_terminal.feed("confirmed.")
                    await left_terminal.wait_for_text("J7K < confirmed.")

                    left_terminal.feed("/end")
                    left_terminal.feed("n")
                    left_terminal.feed("still connected")
                    await right_terminal.wait_for_text("ABC < still connected")

                    left_terminal.feed("/status")
                    await left_terminal.wait_for_text("verification")

                    left_replacements = len(left_terminal.replacements)
                    right_replacements = len(right_terminal.replacements)
                    # /end is the next input from the status overlay and should be
                    # processed after the COMM view is restored.
                    left_terminal.feed("/end")
                    left_terminal.feed("y")
                    await right_terminal.wait_for_text("ABC ended the comm")
                    await asyncio.gather(
                        left_terminal.wait_for_replacements(left_replacements + 1),
                        right_terminal.wait_for_replacements(right_replacements + 1),
                    )

                    left_terminal.feed("/exit")
                    right_terminal.feed("/exit")
                    self.assertEqual(await asyncio.wait_for(left_task, 5), 0)
                    self.assertEqual(await asyncio.wait_for(right_task, 5), 0)
                    self.assertIn("end of line", left_terminal.lines)
                    self.assertIn("end of line", right_terminal.lines)
                finally:
                    for task in (left_task, right_task):
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(left_task, right_task, return_exceptions=True)
                    await server.close()

    async def test_remote_comm_end_finishes_old_input_reader_before_hub_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            setup, server, host, port, _tls = await start_tls_server(root)
            cert = root / "cert.pem"
            config = ClientConfig(
                server=ServerSettings(host=host, port=port, ca_file=cert),
                ui=UiSettings(color=False, plain=True),
            )
            left_terminal = FakeTerminal(options=RenderOptions(color=False, plain=False))
            right_terminal = SlowCancelTerminal(options=RenderOptions(color=False, plain=True))
            left = InteractiveClientApp(config=config, terminal=left_terminal)
            right = InteractiveClientApp(config=config, terminal=right_terminal)

            left_terminal.feed(setup.phrase)
            left_terminal.feed("ABC")
            right_terminal.feed(setup.phrase)
            right_terminal.feed("J7K")

            with patch("the_grid.client.generate_phrase", return_value="velvet orbit green cabin"):
                left_task = asyncio.create_task(left.run())
                right_task = asyncio.create_task(right.run())
                try:
                    await asyncio.gather(
                        left_terminal.wait_for_text("THE HUB"),
                        right_terminal.wait_for_text("THE HUB"),
                    )
                    left_terminal.feed("/start")
                    await left_terminal.wait_for_text("waiting for connection")
                    right_terminal.feed("/join")
                    right_terminal.feed("velvet orbit green cabin")
                    await asyncio.gather(
                        left_terminal.wait_for_text("ABC × J7K / encrypted"),
                        right_terminal.wait_for_text("J7K × ABC / encrypted"),
                    )

                    hub_count = _hub_count(right_terminal)
                    left_terminal.feed("/end")
                    left_terminal.feed("y")
                    await right_terminal.wait_for_text("ABC ended the comm")

                    async def wait_for_hub_return() -> None:
                        while _hub_count(right_terminal) <= hub_count:
                            right_terminal.changed.clear()
                            if _hub_count(right_terminal) > hub_count:
                                return
                            await right_terminal.changed.wait()

                    await asyncio.wait_for(wait_for_hub_return(), 5)
                    right_terminal.feed("/exit")
                    self.assertEqual(await asyncio.wait_for(right_task, 5), 0)
                    self.assertFalse(right_terminal.overlapping_reads)

                    left_terminal.feed("/exit")
                    self.assertEqual(await asyncio.wait_for(left_task, 5), 0)
                finally:
                    for task in (left_task, right_task):
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(left_task, right_task, return_exceptions=True)
                    await server.close()

    async def test_plain_mode_remains_line_oriented_and_understandable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            setup, server, host, port, _tls = await start_tls_server(root)
            config = ClientConfig(
                server=ServerSettings(host=host, port=port, ca_file=root / "cert.pem"),
                ui=UiSettings(color=False, plain=True),
            )
            terminal = FakeTerminal(options=RenderOptions(color=False, plain=True), width=40)
            app = InteractiveClientApp(config=config, terminal=terminal)
            terminal.feed(setup.phrase)
            terminal.feed("ABC")
            terminal.feed("/help")
            terminal.feed("/exit")
            try:
                self.assertEqual(await asyncio.wait_for(app.run(), 5), 0)
                self.assertTrue(any("THE HUB" in line for line in terminal.lines))
                self.assertIn("    no messages", terminal.lines)
                self.assertTrue(any(line.startswith("/post") for line in terminal.lines))
                self.assertIn("end of line", terminal.lines)
            finally:
                await server.close()


if __name__ == "__main__":
    unittest.main()
