"""Approved word-list loading and four-word phrase handling."""

from __future__ import annotations

import hashlib
import re
import secrets
from collections.abc import Callable, Sequence
from enum import StrEnum
from functools import lru_cache
from importlib import resources
from typing import Final

APPROVED_WORD_COUNT: Final = 2048
PHRASE_WORD_COUNT: Final = 4
APPROVED_WORDLIST_SHA256: Final = (
    "99b2c78777db24127047b1535e13da44b7c89f24d387a1041ef09627c7ca0bc5"
)

_WORD_PATTERN: Final = re.compile(r"[a-z]+\Z")
_ALLOWED_RECEIVED_PATTERN: Final = re.compile(r"[A-Za-z \t\r\n\f\v-]+\Z")
_SEPARATOR_PATTERN: Final = re.compile(r"[ \t\r\n\f\v-]+")
_ASCII_WHITESPACE: Final = " \t\r\n\f\v"
_DEFAULT_RANDOM = secrets.SystemRandom()

Sampler = Callable[[Sequence[str], int], Sequence[str]]


class WordListErrorCode(StrEnum):
    MISSING = "missing"
    CHECKSUM = "checksum"
    ENCODING = "encoding"
    CONTENT = "content"


class PhraseErrorCode(StrEnum):
    MALFORMED = "malformed"
    GENERATOR = "generator"


class WordListError(RuntimeError):
    """Raised when the immutable bundled generation source is invalid."""

    def __init__(self, code: WordListErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


class PhraseError(ValueError):
    """Raised when a received or generated phrase violates v1 rules."""

    def __init__(self, code: PhraseErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


@lru_cache(maxsize=1)
def load_approved_words() -> tuple[str, ...]:
    """Load and validate the exact approved packaged word list."""

    try:
        data = (
            resources.files("the_grid.data")
            .joinpath("grid_words.txt")
            .read_bytes()
        )
    except (FileNotFoundError, ModuleNotFoundError, OSError) as exc:
        raise WordListError(WordListErrorCode.MISSING) from exc

    return _validate_approved_wordlist(data)


def approved_wordlist_sha256() -> str:
    """Return the pinned digest used by release tests."""

    return APPROVED_WORDLIST_SHA256


def generate_phrase(*, sampler: Sampler | None = None) -> str:
    """Generate four distinct approved words using a secure default sampler."""

    words = load_approved_words()
    sample = _DEFAULT_RANDOM.sample if sampler is None else sampler

    try:
        selected = tuple(sample(words, PHRASE_WORD_COUNT))
    except Exception as exc:
        raise PhraseError(PhraseErrorCode.GENERATOR) from exc

    if len(selected) != PHRASE_WORD_COUNT:
        raise PhraseError(PhraseErrorCode.GENERATOR)
    if len(set(selected)) != PHRASE_WORD_COUNT:
        raise PhraseError(PhraseErrorCode.GENERATOR)
    if any(word not in words for word in selected):
        raise PhraseError(PhraseErrorCode.GENERATOR)

    return " ".join(selected)


def normalise_phrase(value: str) -> str:
    """Normalise a received access or session phrase without list membership."""

    return " ".join(normalise_phrase_words(value))


def normalise_phrase_words(value: str) -> tuple[str, str, str, str]:
    """Return exactly four normalised lowercase ASCII words."""

    if not isinstance(value, str):
        raise TypeError("phrase must be a string")
    if not value.isascii():
        raise PhraseError(PhraseErrorCode.MALFORMED)

    stripped = value.strip(_ASCII_WHITESPACE)
    if not stripped or stripped.startswith("-") or stripped.endswith("-"):
        raise PhraseError(PhraseErrorCode.MALFORMED)
    if _ALLOWED_RECEIVED_PATTERN.fullmatch(stripped) is None:
        raise PhraseError(PhraseErrorCode.MALFORMED)

    collapsed = _SEPARATOR_PATTERN.sub(" ", stripped.lower())
    parts = tuple(collapsed.split(" "))
    if len(parts) != PHRASE_WORD_COUNT:
        raise PhraseError(PhraseErrorCode.MALFORMED)
    if len(set(parts)) != PHRASE_WORD_COUNT:
        raise PhraseError(PhraseErrorCode.MALFORMED)
    if any(_WORD_PATTERN.fullmatch(word) is None for word in parts):
        raise PhraseError(PhraseErrorCode.MALFORMED)

    first, second, third, fourth = parts
    return first, second, third, fourth


def _validate_approved_wordlist(data: bytes) -> tuple[str, ...]:
    digest = hashlib.sha256(data).hexdigest()
    if digest != APPROVED_WORDLIST_SHA256:
        raise WordListError(WordListErrorCode.CHECKSUM)

    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise WordListError(WordListErrorCode.ENCODING) from exc

    words = tuple(text.splitlines())
    if len(words) != APPROVED_WORD_COUNT:
        raise WordListError(WordListErrorCode.CONTENT)
    if len(set(words)) != APPROVED_WORD_COUNT:
        raise WordListError(WordListErrorCode.CONTENT)
    if any(_WORD_PATTERN.fullmatch(word) is None for word in words):
        raise WordListError(WordListErrorCode.CONTENT)

    return words
