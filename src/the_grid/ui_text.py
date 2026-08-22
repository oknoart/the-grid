"""Central system-written user-facing copy for okno.

Ordinary copy remains lowercase. Uppercase section labels and the OKNO ASCII
wordmark are deliberate visual-art exceptions approved for the terminal UI.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Final

from . import terms

OKNO_LOGO: Final = (
    " ______  __  __   __   __  ______",
    '/\\  __ \\/\\ \\/ /  /\\ "-.\\ \\/\\  __ \\',
    '\\ \\ \\/\\ \\ \\  _"-.\\ \\ \\-.  \\ \\ \\/\\ \\',
    ' \\ \\_____\\ \\_\\ \\_\\\\ \\_\\\\"\\_\\ \\_____\\',
    '  \\/_____/\\/_/\\/_/ \\/_/ \\/_/\\/_____/',
)
OKNO_LOGO_WIDTH: Final = max(len(line) for line in OKNO_LOGO)

THE_HUB: Final = "THE HUB"
COMM: Final = "COMM"
STATUS: Final = "STATUS"
HELP: Final = "HELP"

END_OF_LINE: Final = terms.END_OF_LINE
CONNECTING: Final = "connecting"
CONNECTED: Final = "connected"
OFFLINE: Final = "offline"
UNABLE_TO_REACH_GRID: Final = "unable to reach the grid"
ACCESS_PHRASE_LABEL: Final = "access phrase"
ENTER_ID_LABEL: Final = "enter 3 character id"
COMM_PHRASE_LABEL: Final = "comm phrase"
WAITING_FOR_CONNECTION: Final = "waiting for connection"
CONNECTING_COMM: Final = "connecting"
CONNECTION_ESTABLISHED: Final = "connection established"
WRITE_HUB_MESSAGE: Final = "write a message with /post"
WRITE_COMM_MESSAGE: Final = "write a message"
MESSAGE_PROMPT: Final = "message > "
INPUT_PROMPT: Final = "> "
END_COMM_LABEL: Final = "end comm? [y/n]"

CONFIGURATION_SAVED: Final = "configuration saved"
COMMAND_UNKNOWN: Final = "unknown command"
ENTER_MESSAGE: Final = "enter a message"
HUB_MESSAGE_UNSAFE: Final = "message contains unsupported control text"
COMM_MESSAGE_UNSAFE: Final = "message contains unsupported control text"
HUB_SEND_FAILED: Final = "message could not be posted"
HUB_CHANGED: Final = "the hub changed"
COMM_ENDED: Final = "comm ended"
COMM_CANCELLED: Final = "comm cancelled"
COMM_EXPIRED: Final = "comm expired"
SERVER_CONNECTION_LOST: Final = "connection lost"

SERVER_UNREACHABLE: Final = UNABLE_TO_REACH_GRID
TLS_VERIFICATION_FAILED: Final = "server identity could not be verified"
UNSUPPORTED_PROTOCOL: Final = "client and server use incompatible protocol versions"
ACCESS_PHRASE_MALFORMED: Final = "enter a four-word access phrase"
ACCESS_AUTHENTICATION_FAILED: Final = "access denied"
ID_LENGTH_INVALID: Final = "id must be 3 characters"
ID_INVALID: Final = "invalid id"
ID_ACTIVE: Final = "id unavailable"
HUB_MESSAGE_TOO_LONG: Final = "message exceeds the 1,024-byte limit"
HUB_CIPHERTEXT_INVALID: Final = "one hub message could not be verified"
COMM_PHRASE_MALFORMED: Final = "enter a four-word comm phrase"
COMM_UNAVAILABLE: Final = "comm unavailable"
COMM_HANDSHAKE_FAILED: Final = "comm could not be established"
COMM_INTEGRITY_FAILED: Final = "comm failed an integrity check"
COMM_MESSAGE_TOO_LONG: Final = "message exceeds the 4,096-byte limit"
SERVER_DISCONNECTED: Final = "connection lost. comm ended"
SLOW_CONNECTION: Final = "connection could not keep up and was closed"
WORDLIST_INVALID: Final = "bundled phrase list could not be verified"
CONFIG_INVALID: Final = "configuration file could not be read"
PLATFORM_UNSUPPORTED: Final = "this platform is not supported in v1"
TERMINAL_TOO_NARROW: Final = "terminal too narrow"
MINIMUM_WIDTH: Final = "minimum width: 40 columns"
NO_MESSAGES: Final = "no messages"
PRESS_RETURN_TO_GO_BACK: Final = "press return to go back"

HUB_COMMAND_LIST: Final = ("/post", "/comm", "/status", "/help", "/exit")
COMM_COMMAND_LIST: Final = ("/status", "/end", "/help")
HUB_COMMANDS: Final = "    ".join(HUB_COMMAND_LIST)
COMM_COMMANDS: Final = "    ".join(COMM_COMMAND_LIST)
COMM_SETUP_COMMANDS: Final = "/new     /cancel"
WAIT_COMMANDS: Final = "/cancel"
OFFLINE_COMMANDS: Final = "/retry     /exit"

HUB_HELP: Final = (
    "/post       post a message to the hub",
    "/comm       open a comm",
    "/status     show connection status",
    "/help       show commands",
    "/exit       disconnect",
)

COMM_HELP: Final = (
    "/status     show comm status",
    "/end        end the comm",
    "/help       show commands",
)

SYSTEM_TEXT = MappingProxyType(
    {
        name: value
        for name, value in globals().copy().items()
        if name.isupper() and isinstance(value, str)
    }
)


def post_cooldown(remaining: str) -> str:
    """Return approved cooldown copy for a preformatted duration."""

    return f"post available in {remaining}"


def peer_ended(display_id: str) -> str:
    """Return approved peer-close copy for an uppercase display ID."""

    return f"{display_id} ended the comm"
