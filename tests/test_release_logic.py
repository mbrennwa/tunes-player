"""Tests for release completeness and metadata-driven release type."""

from __future__ import annotations

import unittest

from tunes_player.core.library.release_logic import (
    infer_release_completeness,
    infer_release_metadata,
    release_type_from_metadata,
)
from tunes_player.core.models import ReleaseCompleteness, ReleaseType


class ReleaseTypeFromMetadataTests(unittest.TestCase):
    def test_synthetic(self) -> None:
        self.assertEqual(
            release_type_from_metadata(None, is_synthetic=True),
            ReleaseType.SYNTHETIC,
        )

    def test_defaults_to_album(self) -> None:
        self.assertEqual(
            release_type_from_metadata(None, is_synthetic=False),
            ReleaseType.ALBUM,
        )

    def test_ep_flag(self) -> None:
        self.assertEqual(
            release_type_from_metadata("EP", is_synthetic=False),
            ReleaseType.EP,
        )

    def test_single_flag(self) -> None:
        self.assertEqual(
            release_type_from_metadata("single", is_synthetic=False),
            ReleaseType.SINGLE,
        )

    def test_compilation_flag(self) -> None:
        self.assertEqual(
            release_type_from_metadata("compilation", is_synthetic=False),
            ReleaseType.COMPILATION,
        )


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

    def test_one_track_without_flag_is_album(self) -> None:
        completeness, expected = infer_release_completeness(
            track_count=1,
            is_synthetic=False,
            total_tracks_tag=None,
            max_track_number=None,
        )
        self.assertEqual(completeness, ReleaseCompleteness.COMPLETE)
        self.assertIsNone(expected)
        self.assertEqual(
            release_type_from_metadata(None, is_synthetic=False),
            ReleaseType.ALBUM,
        )
        _, release_type, _ = infer_release_metadata(
            track_count=1,
            is_synthetic=False,
            total_tracks_tag=None,
            max_track_number=None,
        )
        self.assertEqual(release_type, ReleaseType.ALBUM)

    def test_one_track_with_single_flag(self) -> None:
        _, release_type, _ = infer_release_metadata(
            track_count=1,
            is_synthetic=False,
            total_tracks_tag=1,
            max_track_number=1,
            release_type_tag="single",
        )
        self.assertEqual(release_type, ReleaseType.SINGLE)

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

    def test_ep_from_tag_on_multi_track(self) -> None:
        _, release_type, _ = infer_release_metadata(
            track_count=4,
            is_synthetic=False,
            total_tracks_tag=4,
            max_track_number=4,
            release_type_tag="ep",
        )
        self.assertEqual(release_type, ReleaseType.EP)


if __name__ == "__main__":
    unittest.main()
