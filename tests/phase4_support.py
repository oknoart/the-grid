from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from the_grid.terminal import RenderOptions, line_text


@dataclass
class FakeTerminal:
    width: int = 80
    height: int = 60
    options: RenderOptions = field(default_factory=lambda: RenderOptions(color=False, plain=False))

    def __post_init__(self) -> None:
        self.inputs: asyncio.Queue[str | BaseException] = asyncio.Queue()
        self.lines: list[str] = []
        self.prompts: list[tuple[str, bool]] = []
        self.replacements: list[list[str]] = []
        self.region_updates: list[tuple[int, int, list[str]]] = []
        self.clears = 0
        self.changed = asyncio.Event()

    def feed(self, value: str | BaseException) -> None:
        self.inputs.put_nowait(value)

    async def read_line(self, prompt: str = "", *, secret: bool = False) -> str:
        self.prompts.append((prompt, secret))
        self.changed.set()
        value = await self.inputs.get()
        if isinstance(value, BaseException):
            raise value
        return value

    async def write(self, text: str = "", *, heading: bool = False, status: bool = False) -> None:
        self.lines.append(line_text(text))
        self.changed.set()

    async def write_lines(self, lines) -> None:
        self.lines.extend(line_text(line) for line in lines)
        self.changed.set()

    async def notify(self, lines) -> None:
        if isinstance(lines, str):
            self.lines.append(lines)
        else:
            try:
                self.lines.extend(line_text(line) for line in lines)
            except TypeError:
                self.lines.append(line_text(lines))
        self.changed.set()

    async def clear(self) -> None:
        self.clears += 1
        self.changed.set()

    async def replace_view(self, lines) -> None:
        snapshot = [line_text(line) for line in lines]
        self.replacements.append(snapshot)
        self.lines.extend(snapshot)
        self.changed.set()

    async def update_region(self, *, row: int, column: int, lines) -> None:
        snapshot = [line_text(line) for line in lines]
        self.region_updates.append((row, column, snapshot))
        self.changed.set()

    async def wait_for_text(self, text: str, timeout: float = 5.0) -> None:
        async def wait() -> None:
            while not any(text in line for line in self.lines):
                self.changed.clear()
                if any(text in line for line in self.lines):
                    return
                await self.changed.wait()
        await asyncio.wait_for(wait(), timeout)

    async def wait_for_replacements(self, count: int, timeout: float = 5.0) -> None:
        async def wait() -> None:
            while len(self.replacements) < count:
                self.changed.clear()
                if len(self.replacements) >= count:
                    return
                await self.changed.wait()
        await asyncio.wait_for(wait(), timeout)
