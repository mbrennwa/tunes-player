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
        engine._last_position_emit = 0.0
        engine._last_position_update_at = 0.0
        engine._on_event = None
        engine._time_pos_sec = 0.0
        engine._audio_pts_sec = None
        engine._position_sec = 0.0
        engine._ui_position_sec = 0.0
        engine._time_pos_lock = __import__("threading").Lock()
        engine._shutting_down = False
        engine._terminated = False
        engine._playing = True
        engine._duration_sec = 250.0
        return engine

    def test_audible_position_prefers_audio_pts(self) -> None:
        engine = self._engine()
        engine._time_pos_sec = 216.0
        engine._audio_pts_sec = 219.5
        engine._position_sec = 219.5
        engine._player = MagicMock()
        engine._player.time_pos = 216.0
        self.assertAlmostEqual(engine.get_position(), 219.5)
        self.assertAlmostEqual(engine.query_time_pos(), 216.0)

    def test_negative_audio_pts_is_ignored(self) -> None:
        self.assertIsNone(MpvEngine._coerce_optional_seconds(-0.5))
        self.assertIsNone(MpvEngine._coerce_optional_seconds(-0.0))

    def test_seek_updates_position(self) -> None:
        engine = self._engine()
        engine._position_sec = 240.0
        engine._player = MagicMock()
        engine._emit = MagicMock()  # type: ignore[method-assign]
        engine.seek(120.0)
        self.assertAlmostEqual(engine._position_sec, 120.0)
        engine._player.time_pos = 120.0


if __name__ == "__main__":
    unittest.main()
