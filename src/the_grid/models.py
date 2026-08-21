"""Neutral typed models shared by application layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Final

DEFAULT_SERVER_PORT: Final = 7331


class ClientState(StrEnum):
    STARTING = "starting"
    CONNECTING = "connecting"
    AUTHENTICATING = "authenticating"
    SELECTING_DISPLAY = "selecting_display"
    BOARD_LOADING = "board_loading"
    BOARD_ACTIVE = "board_active"
    LIVE_SESSION_WAITING = "live_session_waiting"
    LIVE_SESSION_HANDSHAKE = "live_session_handshake"
    LIVE_SESSION_ACTIVE = "live_session_active"
    DISCONNECTED = "disconnected"
    EXITING = "exiting"


class LiveSessionState(StrEnum):
    NONE = "none"
    WAITING = "waiting"
    PAIRING = "pairing"
    AUTHENTICATING = "authenticating"
    ACTIVE = "active"
    CLOSING = "closing"
    CLOSED = "closed"
    FAILED = "failed"


class BoardState(StrEnum):
    LOADING = "loading"
    ACTIVE = "active"
    POSTING = "posting"
    POST_BLOCKED = "post_blocked"
    REDRAWING = "redrawing"
    ERROR = "error"


class ServerState(StrEnum):
    UNINITIALISED = "uninitialised"
    READY = "ready"
    RUNNING = "running"
    ROTATING_ACCESS = "rotating_access"
    STOPPING = "stopping"


class CloseReason(StrEnum):
    USER_END = "user_end"
    APPLICATION_EXIT = "application_exit"
    PEER_CLOSE = "peer_close"
    SERVER_DISCONNECT = "server_disconnect"
    TIMEOUT = "timeout"
    INTEGRITY_FAILURE = "integrity_failure"


@dataclass(frozen=True, slots=True)
class ServerSettings:
    """Non-secret client connection preferences."""

    host: str | None = None
    port: int = DEFAULT_SERVER_PORT
    ca_file: Path | None = None

    def __post_init__(self) -> None:
        if self.host is not None:
            if not isinstance(self.host, str):
                raise TypeError("host must be a string or none")
            if not self.host or self.host != self.host.strip():
                raise ValueError("host must be non-empty and trimmed")
            if any(char.isspace() for char in self.host) or "\x00" in self.host:
                raise ValueError("host must not contain whitespace or nul")

        if isinstance(self.port, bool) or not isinstance(self.port, int):
            raise TypeError("port must be an integer")
        if not 1 <= self.port <= 65535:
            raise ValueError("port must be between 1 and 65535")

        if self.ca_file is not None and not isinstance(self.ca_file, Path):
            raise TypeError("ca_file must be a path or none")


@dataclass(frozen=True, slots=True)
class UiSettings:
    """Non-secret terminal presentation preferences."""

    color: bool = True
    plain: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.color, bool):
            raise TypeError("color must be a boolean")
        if not isinstance(self.plain, bool):
            raise TypeError("plain must be a boolean")


@dataclass(frozen=True, slots=True)
class ClientConfig:
    """The complete persisted client configuration for v1."""

    server: ServerSettings = field(default_factory=ServerSettings)
    ui: UiSettings = field(default_factory=UiSettings)
