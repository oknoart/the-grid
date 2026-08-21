from __future__ import annotations

import inspect
import unittest

from the_grid.phrases import (
    PhraseError,
    PhraseErrorCode,
    generate_phrase,
    load_approved_words,
    normalise_phrase,
    normalise_phrase_words,
)


class PhraseTests(unittest.TestCase):
    def test_deterministic_sampler_produces_four_distinct_words(self) -> None:
        def first_four(population: tuple[str, ...], count: int) -> tuple[str, ...]:
            return population[:count]

        phrase = generate_phrase(sampler=first_four)
        self.assertEqual(phrase, "aardvark aardwolf abacus abalone")
        self.assertEqual(len(set(phrase.split())), 4)

    def test_default_generation_uses_only_approved_distinct_words(self) -> None:
        approved = set(load_approved_words())
        for _ in range(64):
            words = generate_phrase().split(" ")
            self.assertEqual(len(words), 4)
            self.assertEqual(len(set(words)), 4)
            self.assertTrue(set(words) <= approved)

    def test_generation_rejects_duplicate_sampler_output(self) -> None:
        def duplicates(population: tuple[str, ...], count: int) -> list[str]:
            return [population[0]] * count

        with self.assertRaises(PhraseError) as raised:
            generate_phrase(sampler=duplicates)
        self.assertEqual(raised.exception.code, PhraseErrorCode.GENERATOR)

    def test_generation_rejects_non_approved_sampler_output(self) -> None:
        def invented(population: tuple[str, ...], count: int) -> list[str]:
            return ["notaword", *population[: count - 1]]

        with self.assertRaises(PhraseError):
            generate_phrase(sampler=invented)

    def test_generation_rejects_wrong_sampler_count(self) -> None:
        def too_short(population: tuple[str, ...], count: int) -> tuple[str, ...]:
            return population[: count - 1]

        with self.assertRaises(PhraseError):
            generate_phrase(sampler=too_short)

    def test_normalisation_accepts_uppercase_spaces_and_hyphens(self) -> None:
        value = "  Velvet--ORBIT\tgreen - cabin  "
        self.assertEqual(normalise_phrase(value), "velvet orbit green cabin")
        self.assertEqual(
            normalise_phrase_words(value),
            ("velvet", "orbit", "green", "cabin"),
        )

    def test_received_phrase_does_not_require_list_membership(self) -> None:
        self.assertEqual(
            normalise_phrase("wibble wobble quux zorch"),
            "wibble wobble quux zorch",
        )

    def test_received_phrase_rejects_repeated_words(self) -> None:
        with self.assertRaises(PhraseError):
            normalise_phrase("alpha alpha beta gamma")

    def test_non_ascii_is_rejected(self) -> None:
        with self.assertRaises(PhraseError):
            normalise_phrase("cafe orbit green cabin".replace("cafe", "caf\u00e9"))

    def test_empty_wrong_count_and_punctuation_are_rejected(self) -> None:
        invalid = [
            "",
            "one two three",
            "one two three four five",
            "one two three four!",
            "one_two three four five",
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(PhraseError):
                normalise_phrase(value)

    def test_leading_and_trailing_hyphens_are_rejected(self) -> None:
        for value in ("-one two three four", "one two three four-"):
            with self.subTest(value=value), self.assertRaises(PhraseError):
                normalise_phrase(value)

    def test_public_generation_api_has_no_custom_word_source(self) -> None:
        parameters = inspect.signature(generate_phrase).parameters
        self.assertEqual(tuple(parameters), ("sampler",))
        self.assertEqual(parameters["sampler"].kind, inspect.Parameter.KEYWORD_ONLY)


if __name__ == "__main__":
    unittest.main()
