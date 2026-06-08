"""Playback engine factory backend selection (#46)."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from tunes_player.engines.factory import (
    playback_engine_backend,
    playback_engine_uses_worker_thread,
)


class PlaybackFactoryTests(unittest.TestCase):
    def test_default_backend_is_inprocess(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("TUNES_PLAYBACK_ENGINE", None)
            self.assertEqual(playback_engine_backend(), "inprocess")

    def test_subprocess_backend_from_env(self) -> None:
        with patch.dict(os.environ, {"TUNES_PLAYBACK_ENGINE": "subprocess"}):
            self.assertEqual(playback_engine_backend(), "subprocess")

    def test_inprocess_does_not_use_worker_thread(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("TUNES_PLAYBACK_ENGINE", None)
            self.assertFalse(playback_engine_uses_worker_thread())

    def test_subprocess_uses_worker_thread(self) -> None:
        with patch.dict(os.environ, {"TUNES_PLAYBACK_ENGINE": "subprocess"}):
            self.assertTrue(playback_engine_uses_worker_thread())
