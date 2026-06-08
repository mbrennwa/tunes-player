"""Unit tests for mpv IPC playback helpers."""

from __future__ import annotations

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

    def test_eof_does_not_advance_queue_or_signal_track_finished(self) -> None:
        from tunes_player.engines.playback_client import MpvPlaybackClient

        client = object.__new__(MpvPlaybackClient)
        client._loaded_uri = "/music/track.flac"
        client._active_playlist_entry_id = 1
        client._track_end_signaled = False
        client._playing = True
        client._on_event = None
        client._position_sec = 196.88
        client._duration_sec = 220.07
        events: list[str] = []
        advanced: list[bool] = []

        def capture(event: str) -> None:
            events.append(event)

        client._on_event = capture
        client.playlist_next = lambda: advanced.append(True)  # type: ignore[method-assign]
        client._playlist_pos = 0
        client._playlist_count = 4
        client._playlist_uris = ["/a", "/b", "/c", "/d"]
        client._handle_end_file("eof", playlist_entry_id=1)
        self.assertFalse(client._track_end_signaled)
        self.assertEqual(advanced, [])
        self.assertEqual(events, [])
        self.assertAlmostEqual(client._position_sec, 196.88)
        self.assertAlmostEqual(client._duration_sec, 220.07)

    def test_load_replace_emits_track_started(self) -> None:
        from tunes_player.engines.playback_client import MpvPlaybackClient

        client = object.__new__(MpvPlaybackClient)
        client._loaded_uri = "/music/first.flac"
        client._track_end_signaled = False
        client._load_in_progress = False
        client._playing = True
        client._playlist_pos = 0
        client._last_track_started_at_pos = -2
        client._playlist_uris = ["/music/first.flac"]
        client._on_event = None
        events: list[str] = []

        def capture(event: str) -> None:
            events.append(event)

        client._on_event = capture
        client._notify_playlist_track_changed()
        self.assertAlmostEqual(client._position_sec, 0.0)
        self.assertIsNone(client._duration_sec)
        self.assertIn("track_started", events)

    def test_position_accepts_backward_jump_from_mpv(self) -> None:
        from tunes_player.engines.playback_client import MpvPlaybackClient

        client = object.__new__(MpvPlaybackClient)
        client._loaded_uri = "/music/track.flac"
        client._time_pos_sec = 198.0
        client._audio_pts_sec = None
        client._position_sec = 198.0
        client._load_in_progress = False
        client._last_position_emit = 0.0
        client._last_position_update_at = 0.0
        client._on_event = None
        client._apply_time_pos_update(0.5)
        self.assertAlmostEqual(client._position_sec, 0.5)
        self.assertAlmostEqual(client._time_pos_sec, 0.5)

    def test_playback_position_uses_audio_pts_when_available(self) -> None:
        from tunes_player.engines.playback_client import MpvPlaybackClient

        client = object.__new__(MpvPlaybackClient)
        client._loaded_uri = "/music/track.flac"
        client._load_in_progress = False
        client._last_position_emit = 0.0
        client._last_position_update_at = 0.0
        client._on_event = None
        client._time_pos_sec = 216.0
        client._audio_pts_sec = None
        client._position_sec = 216.0
        client._apply_audio_pts_update(219.5)
        self.assertAlmostEqual(client.get_position(), 219.5)
        self.assertAlmostEqual(client.get_time_pos(), 216.0)

    def test_playback_position_falls_back_to_time_pos_without_audio_pts(self) -> None:
        from tunes_player.engines.playback_client import MpvPlaybackClient

        client = object.__new__(MpvPlaybackClient)
        client._loaded_uri = "/music/track.flac"
        client._load_in_progress = False
        client._last_position_emit = 0.0
        client._last_position_update_at = 0.0
        client._on_event = None
        client._time_pos_sec = 0.0
        client._audio_pts_sec = None
        client._position_sec = 0.0
        client._apply_time_pos_update(42.0)
        self.assertAlmostEqual(client.get_position(), 42.0)

    def test_negative_audio_pts_is_ignored(self) -> None:
        from tunes_player.engines.playback_client import MpvPlaybackClient

        client = object.__new__(MpvPlaybackClient)
        client._loaded_uri = "/music/track.flac"
        client._load_in_progress = False
        client._last_position_emit = 0.0
        client._last_position_update_at = 0.0
        client._on_event = None
        client._time_pos_sec = 100.0
        client._audio_pts_sec = None
        client._position_sec = 100.0
        client._apply_audio_pts_update(-0.5)
        self.assertIsNone(client._audio_pts_sec)
        self.assertAlmostEqual(client.get_position(), 100.0)

    def test_seek_updates_position(self) -> None:
        from tunes_player.engines.playback_client import MpvPlaybackClient

        client = object.__new__(MpvPlaybackClient)
        client._loaded_uri = "/music/track.flac"
        client._track_end_signaled = False
        client._duration_sec = 250.0
        client._position_sec = 240.0
        client._playing = True
        client._load_in_progress = False
        client._last_position_emit = 0.0
        client._last_position_update_at = 0.0
        client._on_event = None
        calls: list[tuple[object, ...]] = []

        def fake_command(*args: object, **kwargs: object) -> dict[str, object]:
            calls.append(args)
            return {"error": "success"}

        client.command = fake_command  # type: ignore[method-assign]
        client.seek(120.0)
        self.assertIn(("seek", 120.0, "absolute"), calls)
        self.assertAlmostEqual(client._position_sec, 120.0)

    def test_startup_args_use_keep_open_always(self) -> None:
        from tunes_player.core.playback.output_profile import PlaybackOutputProfile
        from tunes_player.engines.playback_client import MpvPlaybackClient

        client = object.__new__(MpvPlaybackClient)
        profile = PlaybackOutputProfile(
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
        client._output_profile = profile
        client._socket_path = Path("/tmp/tunes-mpv-test.sock")
        args = client._build_startup_args()
        joined = " ".join(args)
        self.assertIn("--keep-open=always", joined)
        self.assertNotIn("--gapless-audio=", joined)

    def test_ao_is_alsa(self) -> None:
        from tunes_player.engines.playback_client import MpvPlaybackClient

        self.assertTrue(MpvPlaybackClient._ao_is_alsa("alsa"))
        self.assertTrue(
            MpvPlaybackClient._ao_is_alsa(
                [{"name": "alsa", "enabled": True, "params": {}}]
            )
        )
        self.assertFalse(MpvPlaybackClient._ao_is_alsa(None))

    def test_coerce_negotiated_ao_from_mpv_list(self) -> None:
        from tunes_player.engines.playback_client import MpvPlaybackClient

        ao = MpvPlaybackClient._coerce_negotiated_ao(
            [{"name": "alsa", "enabled": True, "params": {}}]
        )
        self.assertEqual(ao, "alsa")
        self.assertTrue(MpvPlaybackClient._ao_is_alsa(ao))

    def test_get_playlist_pos_returns_cached_value(self) -> None:
        from tunes_player.engines.playback_client import MpvPlaybackClient

        client = object.__new__(MpvPlaybackClient)
        client._playlist_pos = 3
        self.assertEqual(client.get_playlist_pos(), 3)
