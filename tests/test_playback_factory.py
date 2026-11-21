"""Playback engine factory (#46)."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from tunes_player.core.remote_output import RemoteMpdOutput
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

    def test_create_playback_engine_returns_mpd_engine_for_remote_output(self) -> None:
        remote = RemoteMpdOutput(name="Den", host="den.local", port=6601)
        with (
            patch("tunes_player.engines.factory.MpdEngine") as mpd_cls,
            patch("tunes_player.engines.factory.MpvEngine") as mpv_cls,
        ):
            engine = create_playback_engine(
                volume=0.5, on_event=None, remote_output=remote
            )
        self.assertIs(engine, mpd_cls.return_value)
        mpv_cls.assert_not_called()
        args, kwargs = mpd_cls.call_args
        self.assertEqual(args, ("den.local", 6601))
        self.assertEqual(kwargs["name"], "Den")


if __name__ == "__main__":
    unittest.main()
