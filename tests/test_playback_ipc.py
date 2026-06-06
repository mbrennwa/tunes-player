"""Unit tests for mpv IPC playback helpers."""

from __future__ import annotations

import time
import unittest
from pathlib import Path

from tunes_player.core.playback.mpv_cli import mpv_cli_args_from_options
from tunes_player.engines.playback_ipc import (
    END_FILE_EOF,
    END_FILE_ERROR,
    END_FILE_STOP,
    end_file_applies_to_playlist_entry,
    end_file_triggers_playback_error,
    end_file_triggers_track_finished,
)


class MpvCliArgsTests(unittest.TestCase):
    def test_bool_and_scalar_options(self) -> None:
        args = mpv_cli_args_from_options(
            {
                "ao": "alsa",
                "audio_exclusive": True,
                "replaygain": "no",
                "volume": 72.5,
                "skip_none": None,
            }
        )
        self.assertIn("--ao=alsa", args)
        self.assertIn("--audio-exclusive=yes", args)
        self.assertIn("--replaygain=no", args)
        self.assertIn("--volume=72.5", args)
        self.assertNotIn("--skip-none", args)

    def test_base_audio_options_direct_alsa(self) -> None:
        from tunes_player.core.playback.mpv_cli import base_audio_options
        from tunes_player.core.playback.output_profile import PlaybackOutputProfile

        profile = PlaybackOutputProfile(
            direct_alsa=True,
            use_exclusive=True,
            allow_resample=False,
            target_rate=48000,
            audio_format="s32",
            target_channels=2,
        )
        options = base_audio_options(profile, use_device_output=False)
        args = mpv_cli_args_from_options(options)
        self.assertIn("--ao=alsa", args)
        self.assertIn("--audio-exclusive=yes", args)


class EndFileReasonTests(unittest.TestCase):
    def test_eof_triggers_track_finished(self) -> None:
        self.assertTrue(end_file_triggers_track_finished(END_FILE_EOF))
        self.assertTrue(end_file_triggers_track_finished("0"))

    def test_stop_does_not_trigger_error_or_finished(self) -> None:
        self.assertFalse(end_file_triggers_playback_error(END_FILE_STOP))
        self.assertFalse(end_file_triggers_track_finished(END_FILE_STOP))

    def test_error_triggers_playback_error_not_finished(self) -> None:
        self.assertTrue(end_file_triggers_playback_error(END_FILE_ERROR))
        self.assertTrue(end_file_triggers_playback_error("error"))
        self.assertFalse(end_file_triggers_track_finished(END_FILE_ERROR))
        self.assertFalse(end_file_triggers_track_finished("error"))

    def test_eof_string_reason(self) -> None:
        self.assertTrue(end_file_triggers_track_finished("eof"))


class PlaylistEntryEndFileTests(unittest.TestCase):
    def test_ignores_events_while_no_active_entry(self) -> None:
        self.assertFalse(
            end_file_applies_to_playlist_entry(
                active_entry_id=None,
                event_entry_id=1,
            )
        )

    def test_ignores_stale_entry(self) -> None:
        self.assertFalse(
            end_file_applies_to_playlist_entry(
                active_entry_id=2,
                event_entry_id=1,
            )
        )

    def test_accepts_current_entry(self) -> None:
        self.assertTrue(
            end_file_applies_to_playlist_entry(
                active_entry_id=2,
                event_entry_id=2,
            )
        )

    def test_invalid_reason_is_ignored(self) -> None:
        self.assertFalse(end_file_triggers_playback_error(None))
        self.assertFalse(end_file_triggers_track_finished("bad"))


if __name__ == "__main__":
    unittest.main()


class PlaybackClientEndFileTests(unittest.TestCase):
    def test_playback_error_keeps_loaded_uri_for_recovery(self) -> None:
        from tunes_player.engines.playback_client import MpvPlaybackClient

        client = object.__new__(MpvPlaybackClient)
        client._loaded_uri = "/music/track.m4a"
        client._playing = True
        client._active_playlist_entry_id = 1
        client._on_event = None
        client._handle_end_file(
            "error",
            file_error="audio output initialization failed",
            playlist_entry_id=1,
        )
        self.assertEqual(client._loaded_uri, "/music/track.m4a")
        self.assertFalse(client._playing)

    def test_is_available_false_after_ipc_disconnect(self) -> None:
        from tunes_player.engines.playback_client import MpvPlaybackClient

        class FakeSock:
            def close(self) -> None:
                return None

        client = object.__new__(MpvPlaybackClient)
        client._shutdown = False
        client._running = True
        client._proc = type("Proc", (), {"poll": lambda self: None})()
        client._sock_file = FakeSock()
        client._sock = None
        client._playing = True
        self.assertTrue(client.is_available())
        client._mark_ipc_disconnected()
        self.assertFalse(client.is_available())
        self.assertFalse(client._playing)

    def test_direct_alsa_startup_defers_device_open(self) -> None:
        from tunes_player.core.playback.output_profile import PlaybackOutputProfile
        from tunes_player.engines.playback_client import MpvPlaybackClient

        client = object.__new__(MpvPlaybackClient)
        client._output_profile = PlaybackOutputProfile(
            direct_alsa=True,
            use_exclusive=True,
            allow_resample=False,
            target_rate=48000,
            audio_format="s32",
            target_channels=2,
        )
        client._unity_gain = True
        client._volume = 1.0
        client._audio_device = "alsa/hw:1,0"
        client._use_device_output = False
        client._socket_path = Path("/tmp/tunes-mpv-test.sock")
        args = client._build_startup_args()
        joined = " ".join(args)
        self.assertIn("--keep-open=no", joined)
        self.assertIn("--ao=null", joined)
        self.assertNotIn("--audio-device=", joined)
        self.assertNotIn("--audio-exclusive=", joined)
        self.assertNotIn("--audio-buffer=", joined)

    def test_ao_is_alsa(self) -> None:
        from tunes_player.engines.playback_client import MpvPlaybackClient

        self.assertTrue(MpvPlaybackClient._ao_is_alsa("alsa"))
        self.assertTrue(
            MpvPlaybackClient._ao_is_alsa([{"name": "alsa", "enabled": True}])
        )
        self.assertFalse(
            MpvPlaybackClient._ao_is_alsa([{"name": "null", "enabled": True}])
        )
        self.assertFalse(MpvPlaybackClient._ao_is_alsa(None))

    def test_coerce_negotiated_ao_from_mpv_list(self) -> None:
        from tunes_player.engines.playback_client import MpvPlaybackClient

        ao = MpvPlaybackClient._coerce_negotiated_ao(
            [{"name": "alsa", "enabled": True, "params": {}}]
        )
        self.assertEqual(ao, "alsa")
        self.assertTrue(MpvPlaybackClient._ao_is_alsa(ao))

    def test_eof_defers_track_finished_until_playback_catches_up(self) -> None:
        from tunes_player.engines.playback_client import MpvPlaybackClient

        client = object.__new__(MpvPlaybackClient)
        client._loaded_uri = "/music/track.flac"
        client._active_playlist_entry_id = 1
        client._track_end_signaled = False
        client._demuxer_eof_reached = False
        client._duration_sec = 300.0
        client._position_sec = 289.0
        client._playing = True
        client._eof_completion_timer = None
        client._on_event = None
        events: list[str] = []

        def capture(event: str) -> None:
            events.append(event)

        client._on_event = capture
        client._handle_end_file("eof", playlist_entry_id=1)
        self.assertTrue(client._demuxer_eof_reached)
        self.assertFalse(client._track_end_signaled)
        self.assertEqual(events, [])

        client._position_sec = 299.75
        client._try_complete_track()
        self.assertTrue(client._track_end_signaled)
        self.assertEqual(events, ["track_finished"])

    def test_eof_track_finished_on_pause_after_demuxer_eof(self) -> None:
        from tunes_player.engines.playback_client import MpvPlaybackClient

        client = object.__new__(MpvPlaybackClient)
        client._loaded_uri = "/music/track.flac"
        client._active_playlist_entry_id = 1
        client._track_end_signaled = False
        client._demuxer_eof_reached = True
        client._duration_sec = None
        client._position_sec = 290.0
        client._load_in_progress = False
        client._playing = True
        client._shutdown = False
        client._last_position_update_at = 0.0
        client._eof_completion_timer = None
        client._on_event = None
        events: list[str] = []

        def capture(event: str) -> None:
            events.append(event)

        client._on_event = capture
        client._handle_property_change("pause", True)
        self.assertTrue(client._track_end_signaled)
        self.assertEqual(events, ["track_finished", "playing_changed"])

    def test_eof_tail_advances_without_ipc(self) -> None:
        from tunes_player.engines.playback_client import MpvPlaybackClient

        client = object.__new__(MpvPlaybackClient)
        client._loaded_uri = "/music/track.flac"
        client._track_end_signaled = False
        client._demuxer_eof_reached = True
        client._demuxer_eof_position_sec = 240.0
        client._demuxer_eof_at = time.monotonic() - 5.0
        client._duration_sec = 250.0
        client._position_sec = 240.0
        client._last_position_emit = 0.0
        client._last_position_update_at = 0.0
        client._on_event = None
        client._eof_completion_timer = None

        pos = client.get_position()
        self.assertGreater(pos, 244.0)
        self.assertIsNone(client.playback_stall_age_sec())

    def test_eof_completion_timer_fires_track_finished(self) -> None:
        from tunes_player.engines.playback_client import MpvPlaybackClient

        client = object.__new__(MpvPlaybackClient)
        client._loaded_uri = "/music/track.flac"
        client._track_end_signaled = False
        client._demuxer_eof_reached = True
        client._demuxer_eof_position_sec = 249.9
        client._demuxer_eof_at = time.monotonic()
        client._duration_sec = 250.0
        client._position_sec = 249.9
        client._playing = True
        client._eof_completion_timer = None
        events: list[str] = []

        def capture(event: str) -> None:
            events.append(event)

        client._on_event = capture
        client._schedule_eof_completion_timer()
        time.sleep(0.6)
        self.assertEqual(events, ["track_finished"])

    def test_time_pos_fallback_before_audio_pts(self) -> None:
        from tunes_player.engines.playback_client import MpvPlaybackClient

        client = object.__new__(MpvPlaybackClient)
        client._loaded_uri = "/music/track.flac"
        client._track_end_signaled = False
        client._demuxer_eof_reached = False
        client._audio_pts_available = False
        client._position_sec = 0.0
        client._load_in_progress = False
        client._last_position_emit = 0.0
        client._last_position_update_at = 0.0
        client._on_event = None
        client._apply_position_update(12.5, source="time-pos")
        self.assertAlmostEqual(client._position_sec, 12.5)

    def test_observer_updates_ignored_during_demuxer_eof(self) -> None:
        from tunes_player.engines.playback_client import MpvPlaybackClient

        client = object.__new__(MpvPlaybackClient)
        client._loaded_uri = "/music/track.flac"
        client._track_end_signaled = False
        client._demuxer_eof_reached = True
        client._duration_sec = 250.0
        client._position_sec = 240.0
        client._audio_pts_available = True
        client._load_in_progress = False
        client._last_position_emit = 0.0
        client._last_position_update_at = 0.0
        client._on_event = None
        client._apply_position_update(241.0, source="time-pos")
        client._apply_position_update(241.0, source="audio-pts")
        self.assertEqual(client._position_sec, 240.0)

    def test_direct_alsa_open_switches_ao_from_null(self) -> None:
        import shutil
        import tempfile

        if shutil.which("mpv") is None:
            self.skipTest("mpv not installed")

        from tunes_player.core.playback.output_profile import PlaybackOutputProfile
        from tunes_player.engines.playback_client import MpvPlaybackClient

        profile = PlaybackOutputProfile(
            direct_alsa=True,
            use_exclusive=False,
            allow_resample=True,
            target_rate=44100,
            audio_format=None,
            target_channels=2,
        )
        with tempfile.TemporaryDirectory() as tmp:
            sock = Path(tmp) / "mpv.sock"
            client = MpvPlaybackClient(
                unity_gain=True,
                output_profile=profile,
                ipc_socket_path=sock,
            )
            try:
                self.assertFalse(client._direct_alsa_device_open)
                client.set_output_profile(profile)
                self.assertTrue(client._direct_alsa_device_open)
                self.assertTrue(client._ao_is_alsa(client.get_property("ao")))
            finally:
                client.quit()
