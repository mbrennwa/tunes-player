"""Search query parsing: AND-of-tokens with quoted phrases (#79)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from tunes_player.core.models import Release


@dataclass(frozen=True, slots=True)
class SearchTerm:
    text: str
    phrase: bool


@dataclass(frozen=True, slots=True)
class ParsedSearchQuery:
    terms: tuple[SearchTerm, ...]

    @property
    def plain_query(self) -> str:
        """Space-joined terms without quote markers (for streaming APIs)."""
        return " ".join(term.text for term in self.terms)


def parse_search_query(raw: str) -> ParsedSearchQuery:
    """Parse *raw* into required terms.

    Unquoted whitespace-separated tokens are AND terms (substring match).
    Double-quoted spans are phrase terms (contiguous substring).
    An unbalanced opening quote treats the remainder of the string as a phrase.
    Empty tokens and empty phrases are skipped.
    """
    text = raw.strip()
    if not text:
        return ParsedSearchQuery(terms=())

    terms: list[SearchTerm] = []
    i = 0
    n = len(text)
    while i < n:
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            break
        if text[i] == '"':
            i += 1
            start = i
            while i < n and text[i] != '"':
                i += 1
            phrase = text[start:i].strip()
            if i < n and text[i] == '"':
                i += 1
            if phrase:
                terms.append(SearchTerm(text=phrase, phrase=True))
            continue
        start = i
        while i < n and not text[i].isspace() and text[i] != '"':
            i += 1
        token = text[start:i]
        if token:
            terms.append(SearchTerm(text=token, phrase=False))
    return ParsedSearchQuery(terms=tuple(terms))


def text_matches_terms(haystack: str, terms: Sequence[SearchTerm]) -> bool:
    """True when every term's text is a casefold substring of *haystack*."""
    if not terms:
        return False
    folded = haystack.casefold()
    return all(term.text.casefold() in folded for term in terms)


def release_matches_query(
    release: Release,
    parsed: ParsedSearchQuery,
    *,
    artists_only: bool = False,
) -> bool:
    """True when *release* satisfies all terms in *parsed*."""
    if not parsed.terms:
        return False
    if artists_only:
        haystack = release.artist_name
    else:
        haystack = f"{release.title} {release.artist_name}"
    return text_matches_terms(haystack, parsed.terms)
