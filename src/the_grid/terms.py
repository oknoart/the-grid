"""Central public terminology.

Protocol and storage code must not import this module. Public wording can then
change without forcing a protocol or database migration.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Final

APP_DISPLAY_NAME: Final = "okno"
APP_SLUG: Final = "okno"
EXECUTABLE_NAME: Final = "okno"

GRID: Final = "the grid"
HUB: Final = "the hub"
SERVER: Final = "server"
DISPLAY_ID: Final = "id"
COMM: Final = "comm"
ACCESS_PHRASE: Final = "access phrase"
COMM_PHRASE: Final = "comm phrase"
STATUS: Final = "status"
END_COMMAND: Final = "/end"
EXIT_COMMAND: Final = "/exit"
END_OF_LINE: Final = "end of line"

PUBLIC_TERMS = MappingProxyType(
    {
        "application": APP_DISPLAY_NAME,
        "environment": GRID,
        "board": HUB,
        "server": SERVER,
        "display_id": DISPLAY_ID,
        "live_session": COMM,
        "access_phrase": ACCESS_PHRASE,
        "session_phrase": COMM_PHRASE,
        "user_close": END_COMMAND,
        "status": STATUS,
        "application_exit": END_OF_LINE,
    }
)
