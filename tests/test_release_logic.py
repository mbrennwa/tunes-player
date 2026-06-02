"""Tests for release completeness inference."""

from __future__ import annotations

import unittest

from tunes_player.core.library.release_logic import infer_release_metadata
from tunes_player.core.models import ReleaseCompleteness, ReleaseType


class ReleaseLogicTests(unittest.TestCase):
    def test_synthetic_release(self) -> None:
        completeness, release_type, expected = infer_release_metadata(
            track_count=1,
            is_synthetic=True,
            total_tracks_tag=None,
            max_track_number=None,
        )
        self.assertEqual(completeness, ReleaseCompleteness.SYNTHETIC)
        self.assertEqual(release_type, ReleaseType.SYNTHETIC)
        self.assertEqual(expected, 1)

    def test_partial_from_tag(self) -> None:
        completeness, release_type, expected = infer_release_metadata(
            track_count=3,
            is_synthetic=False,
            total_tracks_tag=5,
            max_track_number=3,
        )
        self.assertEqual(completeness, ReleaseCompleteness.PARTIAL)
        self.assertEqual(release_type, ReleaseType.ALBUM)
        self.assertEqual(expected, 5)

    def test_partial_from_gap_heuristic(self) -> None:
        completeness, _, expected = infer_release_metadata(
            track_count=3,
            is_synthetic=False,
            total_tracks_tag=None,
            max_track_number=5,
        )
        self.assertEqual(completeness, ReleaseCompleteness.PARTIAL)
        self.assertEqual(expected, 5)

    def test_complete_album(self) -> None:
        completeness, release_type, expected = infer_release_metadata(
            track_count=10,
            is_synthetic=False,
            total_tracks_tag=10,
            max_track_number=10,
        )
        self.assertEqual(completeness, ReleaseCompleteness.COMPLETE)
        self.assertEqual(release_type, ReleaseType.ALBUM)
        self.assertEqual(expected, 10)


if __name__ == "__main__":
    unittest.main()
