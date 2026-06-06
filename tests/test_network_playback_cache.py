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
    warm_playback_cache,
)


def _stage_network_file_for_test(path: str | Path, *, cache_dir: Path) -> str:
    """Blocking staging helper for tests (production uses resolve_playback_target)."""
    from tunes_player.core.playback import network_playback_cache as cache

    target = cache.resolve_playback_target(path, cache_dir=cache_dir)
    source = Path(path)
    try:
        resolved = source.resolve()
    except OSError:
        return target
    if not cache._is_network_library_path(resolved):
        return target
    if target != str(path) and Path(target).is_file():
        return target
    return cache.warm_playback_cache(path, cache_dir=cache_dir)


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
                result = _stage_network_file_for_test(source, cache_dir=cache_dir)
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
                first = _stage_network_file_for_test(source, cache_dir=cache_dir)
                second = _stage_network_file_for_test(source, cache_dir=cache_dir)
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
                result = _stage_network_file_for_test(source, cache_dir=cache_dir)
            self.assertEqual(result, str(source))


if __name__ == "__main__":
    unittest.main()
