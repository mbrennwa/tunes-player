"""Direct ALSA error recovery after playback_error (#46) and soft stall (#67)."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from tunes_player.core.config import ConfigManager
from tunes_player.core.playback.health_monitor import HealthIssue
from tunes_player.core.playback.output_profile import PlaybackOutputProfile
from tunes_player.core.services import PlayerService


class _RecoveryEngine:
    def __init__(self) -> None:
        self._position_sec = 12.0
        self.recover_calls: list[dict[str, bool]] = []

    def query_time_pos(self) -> float:
        return self._position_sec

    def get_position(self) -> float:
        return self._position_sec

    def get_duration(self) -> float | None:
        return 180.0

    def is_playing(self) -> bool:
        return True

    def quit(self) -> None:
        return None

    def recover_direct_alsa_output(
        self,
        *,
        full_reload: bool = False,
        ao_reload_only: bool = False,
    ) -> bool:
        self.recover_calls.append(
            {"full_reload": full_reload, "ao_reload_only": ao_reload_only}
        )
        return full_reload


class PlaybackAlsaRecoveryTests(unittest.TestCase):
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
        self._service._engine = None
        self._service.shutdown()
        self._tmpdir.cleanup()

    def test_error_recovery_tries_ao_reload_then_full_reload(self) -> None:
        engine = _RecoveryEngine()
        self._service._playback_intended = True
        self._service._output_profile = self._profile
        self._service._engine = engine

        self.assertTrue(self._service._try_recover_direct_alsa_on_error())
        self.assertEqual(
            engine.recover_calls,
            [
                {"full_reload": False, "ao_reload_only": True},
                {"full_reload": True, "ao_reload_only": False},
            ],
        )

    def test_error_recovery_is_limited_to_one_attempt_per_track(self) -> None:
        engine = _RecoveryEngine()
        self._service._playback_intended = True
        self._service._output_profile = self._profile
        self._service._engine = engine
        self._service._direct_alsa_recovery_attempts = 1

        self.assertFalse(self._service._try_recover_direct_alsa_on_error())
        self.assertEqual(engine.recover_calls, [])

    def test_error_recovery_honors_cooldown(self) -> None:
        engine = _RecoveryEngine()
        self._service._playback_intended = True
        self._service._output_profile = self._profile
        self._service._engine = engine
        self._service._direct_alsa_recovery_at = time.monotonic()

        self.assertFalse(self._service._try_recover_direct_alsa_on_error())
        self.assertEqual(engine.recover_calls, [])


class PlaybackSoftStallRecoveryTests(unittest.TestCase):
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
        self._service._engine = None
        self._service.shutdown()
        self._tmpdir.cleanup()

    def test_soft_stall_recovers_with_ao_then_full_reload(self) -> None:
        engine = _RecoveryEngine()
        self._service._playback_intended = True
        self._service._output_profile = self._profile
        self._service._engine = engine
        events: list[str] = []
        self._service.subscribe(events.append)

        self._service._handle_soft_stall({"alsa_feed_stalled"})

        self.assertEqual(
            engine.recover_calls,
            [
                {"full_reload": False, "ao_reload_only": True},
                {"full_reload": True, "ao_reload_only": False},
            ],
        )
        self.assertEqual(self._service._direct_alsa_soft_stall_attempts, 1)
        self.assertFalse(self._service.get_playback_state().position_stalled)
        self.assertIn("playback_stalled", events)
        self.assertIsNone(self._service.soft_stall_message())

    def test_soft_stall_limited_to_three_attempts_per_track(self) -> None:
        engine = _RecoveryEngine()
        self._service._playback_intended = True
        self._service._output_profile = self._profile
        self._service._engine = engine
        self._service._direct_alsa_soft_stall_attempts = 3
        self._service._direct_alsa_recovery_at = time.monotonic() - 30.0

        self._service._handle_soft_stall({"alsa_feed_stalled"})

        self.assertEqual(engine.recover_calls, [])
        self.assertTrue(self._service.get_playback_state().position_stalled)
        self.assertEqual(self._service.soft_stall_message(), "Audio output stalled.")

    def test_time_pos_only_stall_freezes_ui_without_ao_reload(self) -> None:
        engine = _RecoveryEngine()
        self._service._playback_intended = True
        self._service._output_profile = self._profile
        self._service._engine = engine

        self._service._handle_soft_stall({"time_pos_stalled"})

        self.assertEqual(engine.recover_calls, [])
        self.assertTrue(self._service.get_playback_state().position_stalled)
        self.assertEqual(
            self._service.soft_stall_message(), "Playback position stalled."
        )

    def test_health_callback_routes_to_soft_stall_handler(self) -> None:
        called: list[set[str]] = []
        self._service._handle_soft_stall = (  # type: ignore[method-assign]
            lambda codes: called.append(set(codes))
        )
        self._service._on_playback_health_issues(
            [HealthIssue("alsa_feed_stalled", "pointers frozen")]
        )
        self.assertEqual(called, [{"alsa_feed_stalled"}])

    def test_soft_stall_near_track_end_advances_instead_of_recover(self) -> None:
        from tunes_player.core.models import Source, Track

        track_a = Track(
            id="local:/music/a.flac",
            title="A",
            artist_name="Artist",
            release_title="Album",
            source=Source.LOCAL,
            duration_sec=300.0,
        )
        track_b = Track(
            id="local:/music/b.flac",
            title="B",
            artist_name="Artist",
            release_title="Album",
            source=Source.LOCAL,
            duration_sec=200.0,
        )
        engine = _RecoveryEngine()
        engine._position_sec = 297.3
        engine.get_duration = lambda: 300.0  # type: ignore[method-assign]
        self._service._playback_intended = True
        self._service._output_profile = self._profile
        self._service._engine = engine
        self._service._duration_sec = 300.0
        self._service._playlist_meta = [track_a, track_b]
        self._service._queue_index = 0
        self._service._current_track = track_a
        advanced: list[int] = []
        self._service._play_queue_index = (  # type: ignore[method-assign]
            lambda index, **kwargs: advanced.append(index)
        )

        self._service._handle_soft_stall({"alsa_feed_stalled", "time_pos_stalled"})

        self.assertEqual(engine.recover_calls, [])
        self.assertEqual(advanced, [1])
        self.assertFalse(self._service.get_playback_state().position_stalled)
        self.assertIsNone(self._service.soft_stall_message())

    def test_time_pos_stall_near_end_also_advances(self) -> None:
        from tunes_player.core.models import Source, Track

        track_a = Track(
            id="local:/music/a.flac",
            title="A",
            artist_name="Artist",
            release_title="Album",
            source=Source.LOCAL,
            duration_sec=300.0,
        )
        track_b = Track(
            id="local:/music/b.flac",
            title="B",
            artist_name="Artist",
            release_title="Album",
            source=Source.LOCAL,
            duration_sec=200.0,
        )
        engine = _RecoveryEngine()
        engine._position_sec = 296.0
        self._service._playback_intended = True
        self._service._output_profile = self._profile
        self._service._engine = engine
        self._service._duration_sec = 300.0
        self._service._playlist_meta = [track_a, track_b]
        self._service._queue_index = 0
        advanced: list[int] = []
        self._service._play_queue_index = (  # type: ignore[method-assign]
            lambda index, **kwargs: advanced.append(index)
        )

        self._service._handle_soft_stall({"time_pos_stalled"})

        self.assertEqual(engine.recover_calls, [])
        self.assertEqual(advanced, [1])


if __name__ == "__main__":
    unittest.main()
