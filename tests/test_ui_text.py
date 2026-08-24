from __future__ import annotations

import unittest

from the_grid import terms, ui_text


class UiTextTests(unittest.TestCase):
    def test_approved_exact_copy(self) -> None:
        self.assertEqual(ui_text.NO_MESSAGES, "no messages")
        self.assertEqual(ui_text.END_COMM_LABEL, "end comm? [y/n]")
        self.assertEqual(ui_text.END_OF_LINE, "end of line")
        self.assertEqual(ui_text.COMM_UNAVAILABLE, "comm unavailable")
        self.assertEqual(ui_text.ENTER_ID_LABEL, "choose a 3 character id")

    def test_okno_wordmark_is_exact_and_underlined_to_its_width(self) -> None:
        self.assertEqual(ui_text.OKNO_LOGO_WIDTH, 36)
        self.assertEqual(len(ui_text.OKNO_LOGO), 5)
        self.assertTrue(all(len(line) <= ui_text.OKNO_LOGO_WIDTH for line in ui_text.OKNO_LOGO))
        self.assertEqual(len("─" * ui_text.OKNO_LOGO_WIDTH), ui_text.OKNO_LOGO_WIDTH)

    def test_visual_uppercase_is_limited_to_approved_headings(self) -> None:
        self.assertEqual(
            {ui_text.THE_HUB, ui_text.COMM, ui_text.INFO, ui_text.HELP},
            {"THE HUB", "COMM", "INFO", "HELP"},
        )
        ordinary = [
            ui_text.CONNECTING,
            ui_text.CONNECTED,
            ui_text.ACCESS_AUTHENTICATION_FAILED,
            ui_text.ID_INVALID,
            ui_text.COMM_UNAVAILABLE,
            ui_text.WRITE_HUB_MESSAGE,
            ui_text.WRITE_COMM_MESSAGE,
        ]
        self.assertTrue(all(value == value.lower() for value in ordinary))

    def test_dynamic_id_copy_preserves_uppercase_data(self) -> None:
        self.assertEqual(ui_text.peer_ended("J7K"), "J7K ended the comm")

    def test_public_terms_are_centralised(self) -> None:
        self.assertEqual(terms.APP_DISPLAY_NAME, "okno")
        self.assertEqual(terms.EXECUTABLE_NAME, "okno")
        self.assertEqual(terms.PUBLIC_TERMS["environment"], "the grid")
        self.assertEqual(terms.PUBLIC_TERMS["board"], "the hub")
        self.assertEqual(terms.PUBLIC_TERMS["live_session"], "comm")
        self.assertEqual(terms.END_COMMAND, "/end")


if __name__ == "__main__":
    unittest.main()
