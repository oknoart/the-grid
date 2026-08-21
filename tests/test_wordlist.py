from __future__ import annotations

import hashlib
import inspect
import re
import unittest
from importlib import resources

from the_grid import phrases


class ApprovedWordListTests(unittest.TestCase):
    def setUp(self) -> None:
        phrases.load_approved_words.cache_clear()

    def test_packaged_bytes_match_pinned_checksum(self) -> None:
        data = resources.files("the_grid.data").joinpath("grid_words.txt").read_bytes()
        self.assertEqual(
            hashlib.sha256(data).hexdigest(),
            phrases.APPROVED_WORDLIST_SHA256,
        )

    def test_wordlist_has_exact_approved_shape(self) -> None:
        words = phrases.load_approved_words()
        self.assertEqual(len(words), 2048)
        self.assertEqual(len(set(words)), 2048)
        self.assertTrue(all(re.fullmatch(r"[a-z]+", word) for word in words))

    def test_wordlist_is_loaded_from_package_resource(self) -> None:
        resource = resources.files("the_grid.data").joinpath("grid_words.txt")
        self.assertTrue(resource.is_file())
        self.assertEqual(resource.read_text(encoding="utf-8").splitlines()[0], "aardvark")
        self.assertEqual(resource.read_text(encoding="utf-8").splitlines()[-1], "zodiac")

    def test_tampered_bytes_fail_before_use(self) -> None:
        data = resources.files("the_grid.data").joinpath("grid_words.txt").read_bytes()
        with self.assertRaises(phrases.WordListError) as raised:
            phrases._validate_approved_wordlist(data + b"extra\n")
        self.assertEqual(raised.exception.code, phrases.WordListErrorCode.CHECKSUM)

    def test_loader_exposes_no_custom_path_parameter(self) -> None:
        self.assertEqual(tuple(inspect.signature(phrases.load_approved_words).parameters), ())


if __name__ == "__main__":
    unittest.main()
