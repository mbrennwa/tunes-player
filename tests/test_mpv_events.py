"""Unit tests for mpv end-file helpers and CLI option mapping."""

from __future__ import annotations

import unittest

from tunes_player.core.playback.mpv_cli import mpv_cli_args_from_options
from tunes_player.core.playback.mpv_events import (
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
