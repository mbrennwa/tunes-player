"""Tests for mpv playback duration, seek bar, and queue advance."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from tunes_player.core.config import ConfigManager
from tunes_player.core.models import Source, Track
from tunes_player.core.services import PlaybackState, PlayerService
from tunes_player.ui.gtk.now_playing import NowPlayingBar, _SEEK_END_MARGIN_SEC


class _DurationEngine:
    def __init__(
        self,
        *,
        duration: float | None = 237.0,
        position: float = 0.0,
        playing: bool = True,
    ) -> None:
        self._duration_sec = duration
        self._time_pos_sec = position
        self._playing = playing

    def get_position(self) -> float:
        return self._time_pos_sec

    def query_time_pos(self) -> float:
        return self._time_pos_sec

    def get_duration(self) -> float | None:
        return self._duration_sec

    def is_playing(self) -> bool:
        return self._playing


class PlaybackDurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        config = ConfigManager(Path(self._tmpdir.name) / "config.toml")
        self._service = PlayerService(config=config)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _track(self, *, duration_sec: float = 240.0) -> Track:
        return Track(
            id="local:/music/track.flac",
            title="Track",
            artist_name="Artist",
            release_title="Album",
            source=Source.LOCAL,
            duration_sec=duration_sec,
        )

    def test_set_current_track_clears_playback_duration(self) -> None:
        self._service._set_current_track(self._track())
        self.assertIsNone(self._service.get_playback_state().duration_sec)

    def test_sync_duration_from_engine_uses_mpv_not_catalog(self) -> None:
        self._service._current_track = self._track(duration_sec=240.0)
        self._service._engine = _DurationEngine(duration=237.0)
        self._service._sync_duration_from_engine()
        self.assertAlmostEqual(self._service.get_playback_state().duration_sec, 237.0)

    def test_playback_state_uses_cached_position(self) -> None:
        self._service._current_track = self._track()
        self._service._engine = _DurationEngine(duration=240.0, position=237.0)
        self._service._duration_sec = 240.0
        self._service._position_sec = 12.5
        state = self._service.get_playback_state()
        self.assertAlmostEqual(state.position_sec, 12.5)
        self.assertAlmostEqual(state.duration_sec, 240.0)

    def test_clamp_seek_position_respects_end_margin(self) -> None:
        bar = NowPlayingBar.__new__(NowPlayingBar)
        bar._service = Mock(max_seek_position_sec=Mock(return_value=None))
        self.assertAlmostEqual(
            bar._clamp_seek_position(239.5, 240.0),
            240.0 - _SEEK_END_MARGIN_SEC,
        )
        self.assertAlmostEqual(bar._clamp_seek_position(200.0, 240.0), 200.0)

    def test_auto_advance_uses_time_pos_near_end(self) -> None:
        track_a = self._track(duration_sec=240.0)
        track_b = Track(
            id="local:/music/next.flac",
            title="Next",
            artist_name="Artist",
            release_title="Album",
            source=Source.LOCAL,
            duration_sec=200.0,
        )
        self._service._playlist_meta = [track_a, track_b]
        self._service._queue_index = 0
        self._service._current_track = track_a
        self._service._playback_intended = True
        self._service._is_playing = True
        self._service._duration_sec = 240.0
        self._service._engine = _DurationEngine(duration=240.0, position=239.5)
        self._service._play_queue_index = lambda index, **kwargs: setattr(  # type: ignore[method-assign]
            self._service, "_queue_index", index
        )
        self._service._maybe_auto_advance_queue()
        self.assertEqual(self._service._queue_index, 1)

    def test_auto_advance_waits_until_time_pos_reaches_end(self) -> None:
        self._service._playlist_meta = [self._track(), self._track()]
        self._service._queue_index = 0
        self._service._playback_intended = True
        self._service._is_playing = True
        self._service._duration_sec = 240.0
        self._service._engine = _DurationEngine(duration=240.0, position=216.0)
        advanced: list[int] = []
        self._service._play_queue_index = lambda index, **kwargs: advanced.append(index)  # type: ignore[method-assign]
        self._service._maybe_auto_advance_queue()
        self.assertEqual(advanced, [])

    def test_refresh_playback_position_uses_live_time_pos(self) -> None:
        class _Engine(_DurationEngine):
            def query_time_pos(self) -> float:
                return 42.0

        self._service._engine = _Engine()
        self._service._position_sec = 1.0
        self._service.refresh_playback_position_for_ui()
        self.assertAlmostEqual(self._service._position_sec, 42.0)

    def test_refresh_follows_engine_position_on_track_change(self) -> None:
        self._service._position_sec = 239.0
        self._service._engine = _DurationEngine(duration=240.0, position=0.5)
        self._service.refresh_playback_position_for_ui()
        self.assertAlmostEqual(self._service._position_sec, 0.5)

    def test_auto_advance_uses_engine_duration_when_service_cache_empty(self) -> None:
        track_a = self._track(duration_sec=240.0)
        track_b = self._track(duration_sec=200.0)
        self._service._playlist_meta = [track_a, track_b]
        self._service._queue_index = 0
        self._service._playback_intended = True
        self._service._is_playing = False
        self._service._duration_sec = None
        self._service._engine = _DurationEngine(
            duration=240.0,
            position=239.6,
            playing=False,
        )
        self._service._play_queue_index = lambda index, **kwargs: setattr(  # type: ignore[method-assign]
            self._service, "_queue_index", index
        )
        self._service._maybe_auto_advance_queue()
        self.assertEqual(self._service._queue_index, 1)
        self.assertAlmostEqual(self._service._duration_sec, 240.0)

    def test_auto_advance_does_not_fire_before_track_end(self) -> None:
        self._service._playlist_meta = [self._track(), self._track()]
        self._service._queue_index = 0
        self._service._playback_intended = True
        self._service._is_playing = True
        self._service._duration_sec = 240.0
        self._service._engine = _DurationEngine(duration=240.0, position=196.0)
        advanced: list[int] = []
        self._service._play_queue_index = lambda index, **kwargs: advanced.append(index)  # type: ignore[method-assign]
        self._service._maybe_auto_advance_queue()
        self.assertEqual(advanced, [])

    def test_track_eof_advances_even_when_time_pos_below_margin(self) -> None:
        """EOF from mpv is enough; do not require time-pos near duration (#66)."""
        track_a = self._track(duration_sec=240.0)
        track_b = Track(
            id="local:/music/next.flac",
            title="Next",
            artist_name="Artist",
            release_title="Album",
            source=Source.LOCAL,
            duration_sec=200.0,
        )
        self._service._playlist_meta = [track_a, track_b]
        self._service._queue_index = 0
        self._service._current_track = track_a
        self._service._playback_intended = True
        self._service._is_playing = False
        self._service._duration_sec = 240.0
        # Stalled short of the poll threshold (duration - 1s).
        self._service._engine = _DurationEngine(
            duration=240.0,
            position=230.0,
            playing=False,
        )
        advanced: list[int] = []
        self._service._play_queue_index = lambda index, **kwargs: advanced.append(index)  # type: ignore[method-assign]

        self._service._handle_engine_event("track_eof")

        self.assertEqual(advanced, [1])
        self.assertEqual(self._service._auto_advanced_from_index, 0)

    def test_poll_auto_advance_still_requires_near_end_time_pos(self) -> None:
        self._service._playlist_meta = [self._track(), self._track()]
        self._service._queue_index = 0
        self._service._playback_intended = True
        self._service._is_playing = True
        self._service._duration_sec = 240.0
        self._service._engine = _DurationEngine(duration=240.0, position=230.0)
        advanced: list[int] = []
        self._service._play_queue_index = lambda index, **kwargs: advanced.append(index)  # type: ignore[method-assign]
        self._service._maybe_auto_advance_queue()
        self.assertEqual(advanced, [])

    def test_progress_bar_fraction_from_mpv_position_and_duration(self) -> None:
        state = PlaybackState(
            current_track=None,
            is_playing=True,
            volume=1.0,
            muted=False,
            queue=(),
            queue_index=-1,
            quality_hint="",
            bit_perfect_playback=False,
            playback_note=None,
            device_volume=False,
            mpv_soft_volume=False,
            no_volume_control=False,
            volume_mode="software",
            output_using_fallback=False,
            position_sec=60.0,
            duration_sec=240.0,
        )
        bar = NowPlayingBar.__new__(NowPlayingBar)
        duration = bar._playback_duration(state)
        assert duration is not None
        self.assertAlmostEqual(duration, 240.0)
        fraction = max(0.0, min(state.position_sec, duration)) / duration
        self.assertAlmostEqual(fraction, 0.25)


if __name__ == "__main__":
    unittest.main()
