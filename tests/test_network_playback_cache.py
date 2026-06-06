"""Tests for network-library playback staging cache."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tunes_player.core.playback.network_playback_cache import stage_network_file_if_needed


class NetworkPlaybackCacheTests(unittest.TestCase):
    def test_local_file_is_not_staged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "track.flac"
            source.write_bytes(b"local-audio")
            cache_dir = root / "cache"
            with patch(
                "tunes_player.core.playback.network_playback_cache._is_network_library_path",
                return_value=False,
            ):
                result = stage_network_file_if_needed(source, cache_dir=cache_dir)
            self.assertEqual(result, str(source))
            self.assertFalse(cache_dir.exists())

    def test_network_file_is_staged_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "track.flac"
            payload = b"network-audio-payload"
            source.write_bytes(payload)
            cache_dir = root / "cache"
            with patch(
                "tunes_player.core.playback.network_playback_cache._is_network_library_path",
                return_value=True,
            ):
                first = stage_network_file_if_needed(source, cache_dir=cache_dir)
                second = stage_network_file_if_needed(source, cache_dir=cache_dir)
            self.assertNotEqual(first, str(source))
            self.assertEqual(first, second)
            self.assertEqual(Path(first).read_bytes(), payload)

    def test_staging_failure_falls_back_to_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "track.flac"
            source.write_bytes(b"x")
            cache_dir = root / "cache"
            with (
                patch(
                    "tunes_player.core.playback.network_playback_cache._is_network_library_path",
                    return_value=True,
                ),
                patch(
                    "tunes_player.core.playback.network_playback_cache.shutil.copyfile",
                    side_effect=OSError("disk full"),
                ),
            ):
                result = stage_network_file_if_needed(source, cache_dir=cache_dir)
            self.assertEqual(result, str(source))


if __name__ == "__main__":
    unittest.main()
