"""Stall watchdog should recover USB direct ALSA without blocking IPC polls."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tunes_player.core.config import ConfigManager
from tunes_player.core.playback.output_profile import PlaybackOutputProfile
from tunes_player.core.services import PlayerService


class _StallEngine:
    def __init__(self, *, stall_age: float) -> None:
        self._position_sec = 42.0
        self._stall_age = stall_age
        self.recover_calls: list[dict[str, bool]] = []

    def get_position(self) -> float:
        return self._position_sec

    def playback_stall_age_sec(self) -> float:
        return self._stall_age

    def recover_direct_alsa_output(
        self,
        *,
        full_reload: bool = False,
        ao_reload_only: bool = False,
    ) -> bool:
        self.recover_calls.append(
            {"full_reload": full_reload, "ao_reload_only": ao_reload_only}
        )
        self._stall_age = 0.0
        return True

    def is_playing(self) -> bool:
        return True

    def get_duration(self) -> float | None:
        return 180.0


class PlaybackStallRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        config = ConfigManager(Path(self._tmpdir.name) / "config.toml")
        config.load()
        self._service = PlayerService(config=config)
        self._profile = PlaybackOutputProfile(
            direct_alsa=True,
            use_exclusive=False,
            allow_resample=False,
            target_rate=44100,
            target_bit_depth=16,
            target_channels=2,
            audio_format="s16",
        )

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _arm_direct_alsa_playback(self, engine: _StallEngine) -> None:
        self._service._playback_intended = True
        self._service._is_playing = True
        self._service._output_profile = self._profile
        self._service._engine = engine

    def test_usb_stall_watchdog_triggers_ao_reload_recovery(self) -> None:
        engine = _StallEngine(stall_age=9.0)
        self._arm_direct_alsa_playback(engine)
        self._service._direct_alsa_watchdog_pos = engine.get_position()
        self._service._direct_alsa_watchdog_at = time.monotonic() - 15.0
        with patch.object(self._service, "_usb_direct_alsa_active", return_value=True):
            self._service._poll_direct_alsa_recovery()

        self.assertEqual(len(engine.recover_calls), 1)
        self.assertEqual(
            engine.recover_calls[0],
            {"full_reload": False, "ao_reload_only": True},
        )

    def test_usb_stall_watchdog_escalates_to_full_reload(self) -> None:
        engine = _StallEngine(stall_age=9.0)
        self._arm_direct_alsa_playback(engine)
        self._service._direct_alsa_watchdog_pos = engine.get_position()
        self._service._direct_alsa_watchdog_at = time.monotonic() - 15.0
        self._service._direct_alsa_light_recovery_failures = 2
        with patch.object(self._service, "_usb_direct_alsa_active", return_value=True):
            self._service._poll_direct_alsa_recovery()

        self.assertEqual(len(engine.recover_calls), 1)
        self.assertEqual(
            engine.recover_calls[0],
            {"full_reload": True, "ao_reload_only": False},
        )

    def test_recent_position_updates_do_not_trigger_recovery(self) -> None:
        engine = _StallEngine(stall_age=1.5)
        self._arm_direct_alsa_playback(engine)
        with patch.object(self._service, "_usb_direct_alsa_active", return_value=True):
            self._service._poll_direct_alsa_recovery()

        self.assertEqual(engine.recover_calls, [])


if __name__ == "__main__":
    unittest.main()
