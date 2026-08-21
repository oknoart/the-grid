from __future__ import annotations

import unittest

from the_grid.phrases import generate_phrase, load_approved_words, normalise_phrase


class PhaseOneCompletionGateTests(unittest.TestCase):
    def test_repeated_generation_stays_within_exact_approved_source(self) -> None:
        approved = set(load_approved_words())
        for _ in range(256):
            words = generate_phrase().split(" ")
            self.assertEqual(len(words), 4)
            self.assertEqual(len(set(words)), 4)
            self.assertTrue(set(words).issubset(approved))

    def test_generated_phrase_round_trips_through_received_normalisation(self) -> None:
        for _ in range(32):
            phrase = generate_phrase()
            self.assertEqual(normalise_phrase(phrase.upper()), phrase)


if __name__ == "__main__":
    unittest.main()
