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
