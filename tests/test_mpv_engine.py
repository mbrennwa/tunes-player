"""Unit tests for in-process MpvEngine timeline semantics (#46)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from tunes_player.engines.mpv import MpvEngine


class MpvEngineTimelineTests(unittest.TestCase):
    def _engine(self) -> MpvEngine:
        engine = object.__new__(MpvEngine)
        engine._loaded_uri = "/music/track.flac"
        engine._load_in_progress = False
        engine._last_position_update_at = 0.0
        engine._on_event = None
        engine._time_pos_sec = 0.0
        engine._time_pos_lock = __import__("threading").Lock()
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


if __name__ == "__main__":
    unittest.main()
