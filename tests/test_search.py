"""Library and service search behavior."""

from __future__ import annotations

import unittest

from tunes_player.core.services import _artist_name_matches_query


class TestArtistNameMatchesQuery(unittest.TestCase):
    def test_case_insensitive_substring(self) -> None:
        self.assertTrue(_artist_name_matches_query("beatles", "The Beatles"))

    def test_empty_query_does_not_match(self) -> None:
        self.assertFalse(_artist_name_matches_query("  ", "Artist"))

    def test_title_like_artist_name_does_not_match_unrelated_artist(self) -> None:
        self.assertFalse(_artist_name_matches_query("Queen", "David Bowie"))


if __name__ == "__main__":
    unittest.main()
