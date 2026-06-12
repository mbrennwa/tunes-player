"""Playback engine factory (#46)."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from tunes_player.engines.factory import create_playback_engine, probe_playback_engine


class PlaybackFactoryTests(unittest.TestCase):
    def test_probe_playback_engine_delegates_to_mpv(self) -> None:
        with patch(
            "tunes_player.engines.mpv.probe_playback_engine",
            return_value=None,
        ) as probe:
            self.assertIsNone(probe_playback_engine())
            probe.assert_called_once()

    def test_create_playback_engine_returns_mpv_engine(self) -> None:
        with patch("tunes_player.engines.factory.MpvEngine") as mpv_cls:
            mpv_cls.return_value = object()
            engine = create_playback_engine(volume=0.5, on_event=None)
            self.assertIs(engine, mpv_cls.return_value)
            mpv_cls.assert_called_once()


if __name__ == "__main__":
    unittest.main()
