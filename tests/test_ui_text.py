from __future__ import annotations

import unittest

from the_grid import terms, ui_text


class UiTextTests(unittest.TestCase):
    def test_approved_exact_copy(self) -> None:
        self.assertEqual(ui_text.EMPTY_HUB, "you're on your own")
        self.assertEqual(ui_text.END_COMM_PROMPT, "end comm? y/n")
        self.assertEqual(ui_text.END_OF_LINE, "end of line")
        self.assertEqual(ui_text.COMM_UNAVAILABLE, "no matching comm is available")

    def test_system_copy_is_lowercase_except_approved_id_alphabet(self) -> None:
        for name, value in ui_text.SYSTEM_TEXT.items():
            with self.subTest(name=name):
                comparable = value.replace("A-Z", "a-z")
                self.assertEqual(comparable, comparable.lower())

    def test_dynamic_id_copy_preserves_uppercase_data(self) -> None:
        self.assertEqual(ui_text.peer_ended("J7K"), "J7K ended the comm")

    def test_public_terms_are_centralised(self) -> None:
        self.assertEqual(terms.PUBLIC_TERMS["environment"], "the grid")
        self.assertEqual(terms.PUBLIC_TERMS["board"], "the hub")
        self.assertEqual(terms.PUBLIC_TERMS["live_session"], "comm")
        self.assertEqual(terms.END_COMMAND, "/end")


if __name__ == "__main__":
    unittest.main()
