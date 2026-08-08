"""Unit tests for in-process MpvEngine timeline semantics (#46)."""

from __future__ import annotations

import threading
import unittest
from unittest.mock import MagicMock

from tunes_player.core.playback.output_profile import PlaybackOutputProfile
from tunes_player.engines.mpv import MpvEngine


class MpvEngineTimelineTests(unittest.TestCase):
    def _engine(self) -> MpvEngine:
        engine = object.__new__(MpvEngine)
        engine._loaded_uri = "/music/track.flac"
        engine._load_in_progress = False
        engine._last_position_update_at = 0.0
        engine._on_event = None
        engine._time_pos_sec = 0.0
        engine._time_pos_lock = threading.Lock()
        engine._last_position_poll_log_sec = None
        engine._shutting_down = False
        engine._terminated = False
        engine._playing = True
        engine._duration_sec = 250.0
        return engine

    def test_get_position_uses_time_pos(self) -> None:
        engine = self._engine()
        engine._get_property = MagicMock(return_value=216.0)  # type: ignore[method-assign]
        self.assertAlmostEqual(engine.get_position(), 216.0)
        self.assertAlmostEqual(engine.query_time_pos(), 216.0)

    def test_snap_positions_to_track_end(self) -> None:
        engine = self._engine()
        engine._time_pos_sec = 233.0
        engine._duration_sec = 240.0
        engine._snap_positions_to_track_end()
        self.assertAlmostEqual(engine._cached_time_pos(), 239.99, places=2)

    def test_seek_updates_position(self) -> None:
        engine = self._engine()
        engine._time_pos_sec = 240.0
        engine._player = MagicMock()
        engine._emit = MagicMock()  # type: ignore[method-assign]
        engine.seek(120.0)
        self.assertAlmostEqual(engine._cached_time_pos(), 120.0)
        engine._player.time_pos = 120.0

    def test_time_pos_observer_ignored_during_load(self) -> None:
        engine = self._engine()
        engine._load_in_progress = True
        engine._time_pos_sec = 0.0
        engine._set_cached_time_pos(42.0)
        self.assertAlmostEqual(engine._cached_time_pos(), 42.0)
        engine._load_in_progress = False

    def test_notify_track_started_seeds_zero(self) -> None:
        engine = self._engine()
        engine._loaded_uri = "/music/next.flac"
        engine._last_track_started_uri = None
        engine._time_pos_sec = 180.0
        engine._emit = MagicMock()  # type: ignore[method-assign]
        engine._notify_track_started()
        self.assertAlmostEqual(engine._cached_time_pos(), 0.0)

    def test_notify_track_started_preserves_resume_start(self) -> None:
        engine = self._engine()
        engine._loaded_uri = "/music/next.flac"
        engine._last_track_started_uri = None
        engine._time_pos_sec = 0.0
        engine._emit = MagicMock()  # type: ignore[method-assign]
        engine._notify_track_started(start_sec=42.5)
        self.assertAlmostEqual(engine._cached_time_pos(), 42.5)

    def test_load_with_start_sec_uses_loadfile_start(self) -> None:
        engine = self._engine()
        engine._loaded_uri = None
        engine._last_track_started_uri = None
        engine._output_profile = None
        engine._keep_alsa_open_on_track_change = False
        engine._direct_alsa_device_open = False
        engine._player = MagicMock()
        engine._emit = MagicMock()  # type: ignore[method-assign]
        engine.refresh_playback_path_info = MagicMock()  # type: ignore[method-assign]
        engine._apply_buffer_policy = MagicMock()  # type: ignore[method-assign]

        engine.load("/music/track.flac", start_sec=15.0)

        engine._player.loadfile.assert_called_once_with(
            "/music/track.flac", "replace", start=15.0
        )
        engine._player.play.assert_not_called()
        self.assertAlmostEqual(engine._cached_time_pos(), 15.0)

    def test_load_without_start_sec_uses_play(self) -> None:
        engine = self._engine()
        engine._loaded_uri = None
        engine._last_track_started_uri = None
        engine._output_profile = None
        engine._keep_alsa_open_on_track_change = False
        engine._direct_alsa_device_open = False
        engine._player = MagicMock()
        engine._emit = MagicMock()  # type: ignore[method-assign]
        engine.refresh_playback_path_info = MagicMock()  # type: ignore[method-assign]
        engine._apply_buffer_policy = MagicMock()  # type: ignore[method-assign]

        engine.load("/music/track.flac")

        engine._player.play.assert_called_once_with("/music/track.flac")
        engine._player.loadfile.assert_not_called()
        self.assertAlmostEqual(engine._cached_time_pos(), 0.0)

    def test_snapshot_health_properties(self) -> None:
        engine = self._engine()
        values = {
            "ao": "pulse",
            "audio-device": "pulse/demo",
            "core-idle": False,
            "paused-for-cache": True,
            "mute": False,
        }
        engine._get_property = MagicMock(side_effect=values.__getitem__)  # type: ignore[method-assign]
        snap = engine.snapshot_health_properties()
        self.assertEqual(snap["ao"], "pulse")
        self.assertEqual(snap["paused-for-cache"], True)
        engine._get_property.assert_any_call("core-idle")


class MpvEngineTrackReplaceTests(unittest.TestCase):
    def _direct_alsa_engine(self) -> MpvEngine:
        engine = object.__new__(MpvEngine)
        engine._loaded_uri = "/music/a.flac"
        engine._load_in_progress = False
        engine._last_position_update_at = 0.0
        engine._on_event = None
        engine._time_pos_sec = 0.0
        engine._time_pos_lock = threading.Lock()
        engine._last_position_poll_log_sec = None
        engine._shutting_down = False
        engine._terminated = False
        engine._playing = True
        engine._duration_sec = 180.0
        engine._output_profile = None
        engine._audio_device = "alsa/hw:1,0"
        engine._endpoint_id = "alsa:hw:1:0"
        engine._unity_gain = True
        engine._software_volume = False
        engine._volume = 1.0
        engine._direct_alsa_device_open = True
        engine._opened_exclusive = False
        engine._keep_alsa_open_on_track_change = True
        engine._last_output_format_key = (44100, "s16", 2, False)
        engine._last_track_started_uri = "/music/a.flac"
        engine._path_context = None
        engine._path_info = None
        engine._player = MagicMock()
        engine._emit = MagicMock()  # type: ignore[method-assign]
        engine._set_property = MagicMock()  # type: ignore[method-assign]
        engine.refresh_playback_path_info = MagicMock()  # type: ignore[method-assign]
        return engine

    def test_track_replace_always_reloads_ao_when_usb_keep_open(self) -> None:
        engine = self._direct_alsa_engine()
        profile = PlaybackOutputProfile(
            direct_alsa=True,
            use_exclusive=False,
            allow_resample=False,
            target_rate=44100,
            target_bit_depth=16,
            target_channels=2,
            audio_format="s16",
        )
        engine.load("/music/b.flac", output_profile=profile)
        engine._player.command.assert_any_call("stop")
        engine._player.command.assert_any_call("ao-reload")
        engine._player.play.assert_called_with("/music/b.flac")
        self.assertEqual(engine._loaded_uri, "/music/b.flac")


if __name__ == "__main__":
    unittest.main()
