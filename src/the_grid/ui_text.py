"""Central system-written user-facing copy.

All ordinary copy is lowercase. Uppercase is retained only where the approved
ID alphabet or an ID data placeholder requires it.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Final

from . import terms

THE_GRID: Final = terms.GRID
THE_HUB: Final = terms.HUB
PRIVATE_COMM: Final = terms.PRIVATE_COMM
END_OF_LINE: Final = terms.END_OF_LINE
EMPTY_HUB: Final = "you're on your own"
END_COMM_PROMPT: Final = "end comm? y/n"

CONNECTING: Final = "connecting..."
CONNECTED: Final = "connected"
WAITING: Final = "waiting..."
CONFIGURATION_SAVED: Final = "configuration saved"
PHASE_3_READY: Final = "phase 3 headless networking is installed"
PHASE_3_CLIENT_PENDING: Final = "the interactive terminal client is not implemented yet"
PHASE_3_STATUS_PENDING: Final = "interactive client status is not available before phase 4"

SERVER_UNREACHABLE: Final = "the server could not be reached"
TLS_VERIFICATION_FAILED: Final = "the server identity could not be verified"
UNSUPPORTED_PROTOCOL: Final = (
    "this client and server use incompatible protocol versions"
)
ACCESS_PHRASE_MALFORMED: Final = "enter a four-word access phrase"
ACCESS_AUTHENTICATION_FAILED: Final = "access could not be verified"
ID_INVALID: Final = "an id must contain three characters from A-Z and 2-9"
ID_ACTIVE: Final = "that id is already active"
HUB_MESSAGE_TOO_LONG: Final = "the hub message exceeds the 1,024-byte limit"
HUB_CIPHERTEXT_INVALID: Final = "one hub message could not be verified"
COMM_PHRASE_MALFORMED: Final = "enter a four-word comm phrase"
COMM_UNAVAILABLE: Final = "no matching comm is available"
COMM_HANDSHAKE_FAILED: Final = "a private comm could not be established"
COMM_INTEGRITY_FAILED: Final = "the comm failed an integrity check"
COMM_MESSAGE_TOO_LONG: Final = "the message exceeds the 4,096-byte limit"
SERVER_DISCONNECTED: Final = "connection to the server was lost. the comm has ended"
SLOW_CONNECTION: Final = "the connection could not keep up and was closed"
WORDLIST_INVALID: Final = "the bundled phrase list could not be verified"
CONFIG_INVALID: Final = "the configuration file could not be read"
PLATFORM_UNSUPPORTED: Final = "this platform is not supported in v1"

SYSTEM_TEXT = MappingProxyType(
    {
        name: value
        for name, value in globals().copy().items()
        if name.isupper() and isinstance(value, str)
    }
)


def post_cooldown(remaining: str) -> str:
    """Return the approved cooldown copy for a preformatted duration."""

    return f"you can post again in {remaining}"


def peer_ended(display_id: str) -> str:
    """Return the approved peer-close copy for an uppercase display ID."""

    return f"{display_id} ended the comm"
