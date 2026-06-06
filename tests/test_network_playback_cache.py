"""Tests for network-library playback staging cache."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tunes_player.core.playback.network_playback_cache import (
    clear_warmup_state_for_tests,
    resolve_playback_target,
    schedule_playback_cache_warmup,
    stage_network_file_if_needed,
    warm_playback_cache,
)


class NetworkPlaybackCacheTests(unittest.TestCase):
    def tearDown(self) -> None:
        clear_warmup_state_for_tests()

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

    def test_resolve_does_not_block_on_cache_miss(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "track.flac"
            source.write_bytes(b"network-audio-payload")
            cache_dir = root / "cache"
            with patch(
                "tunes_player.core.playback.network_playback_cache._is_network_library_path",
                return_value=True,
            ):
                with patch(
                    "tunes_player.core.playback.network_playback_cache.shutil.copyfile",
                    side_effect=AssertionError("resolve must not copy"),
                ):
                    result = resolve_playback_target(source, cache_dir=cache_dir)
            self.assertEqual(result, str(source))
            self.assertFalse(cache_dir.exists())

    def test_schedule_warmup_populates_cache_in_background(self) -> None:
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
                schedule_playback_cache_warmup(source, cache_dir=cache_dir)
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    cached = resolve_playback_target(source, cache_dir=cache_dir)
                    if cached != str(source):
                        break
                    time.sleep(0.05)
                cached = resolve_playback_target(source, cache_dir=cache_dir)
            self.assertNotEqual(cached, str(source))
            self.assertEqual(Path(cached).read_bytes(), payload)

    def test_warm_playback_cache_blocks_until_ready(self) -> None:
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
                result = warm_playback_cache(source, cache_dir=cache_dir)
            self.assertNotEqual(result, str(source))
            self.assertEqual(Path(result).read_bytes(), payload)

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
