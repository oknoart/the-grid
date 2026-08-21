from __future__ import annotations

import unittest
from pathlib import Path

from the_grid.models import (
    BoardState,
    ClientConfig,
    ClientState,
    CloseReason,
    LiveSessionState,
    ServerSettings,
    ServerState,
    UiSettings,
)


class ModelTests(unittest.TestCase):
    def test_client_states_use_neutral_internal_names(self) -> None:
        self.assertEqual(ClientState.BOARD_ACTIVE, "board_active")
        self.assertEqual(ClientState.LIVE_SESSION_ACTIVE, "live_session_active")
        names = {member.name for member in ClientState}
        self.assertNotIn("PORTAL", names)
        self.assertNotIn("GRID_SELECT", names)

    def test_approved_state_sets_are_present(self) -> None:
        self.assertEqual(
            {state.value for state in LiveSessionState},
            {
                "none",
                "waiting",
                "pairing",
                "authenticating",
                "active",
                "closing",
                "closed",
                "failed",
            },
        )
        self.assertIn(BoardState.POST_BLOCKED, BoardState)
        self.assertIn(ServerState.ROTATING_ACCESS, ServerState)
        self.assertIn(CloseReason.USER_END, CloseReason)

    def test_default_config_is_small_and_non_secret(self) -> None:
        config = ClientConfig()
        self.assertIsNone(config.server.host)
        self.assertEqual(config.server.port, 7331)
        self.assertIsNone(config.server.ca_file)
        self.assertEqual(config.ui, UiSettings())
        fields = set(config.__dataclass_fields__)
        self.assertEqual(fields, {"server", "ui"})

    def test_endpoint_validation(self) -> None:
        self.assertEqual(
            ServerSettings("grid.example.net", 7331, Path("ca.pem")).port,
            7331,
        )
        for port in (0, 65536):
            with self.subTest(port=port), self.assertRaises(ValueError):
                ServerSettings(port=port)
        with self.assertRaises(ValueError):
            ServerSettings(host="bad host")


if __name__ == "__main__":
    unittest.main()
