"""Tests for playback buffer policy."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from tunes_player.core.playback.buffer_policy import (
    InputClass,
    classify_playback_uri,
    merge_playback_note,
    mpv_options_for_input,
)


class BufferPolicyTests(unittest.TestCase):
    def test_classify_stream(self) -> None:
        self.assertEqual(
            classify_playback_uri("https://example.com/track.flac"),
            InputClass.STREAM,
        )

    def test_classify_local_path(self) -> None:
        with patch(
            "tunes_player.platform.linux.mount_info.is_network_mount_path",
            return_value=False,
        ):
            self.assertEqual(
                classify_playback_uri("/home/user/Music/track.flac"),
                InputClass.LOCAL,
            )

    def test_classify_network_file(self) -> None:
        with patch(
            "tunes_player.platform.linux.mount_info.is_network_mount_path",
            return_value=True,
        ):
            self.assertEqual(
                classify_playback_uri("/mnt/nfs/album/track.flac"),
                InputClass.NETWORK_FILE,
            )

    def test_mpv_options_local(self) -> None:
        options = mpv_options_for_input(InputClass.LOCAL, direct_alsa=False)
        self.assertEqual(options["cache"], "no")
        self.assertEqual(options["demuxer_readahead_secs"], 1.0)
        self.assertNotIn("audio_buffer", options)

    def test_mpv_options_network_file_direct_alsa(self) -> None:
        options = mpv_options_for_input(InputClass.NETWORK_FILE, direct_alsa=True)
        self.assertEqual(options["cache"], "yes")
        self.assertGreater(options["demuxer_readahead_secs"], 10.0)
        self.assertIn("demuxer_max_bytes", options)
        self.assertGreaterEqual(options["audio_buffer"], 10.0)
        self.assertLessEqual(options["audio_buffer"], 10.0)
        self.assertEqual(options["alsa_buffer_time"], 10_000_000)
        self.assertEqual(options["alsa_periods"], 8)
        self.assertEqual(options["demuxer_thread"], "yes")
        self.assertEqual(options["cache_pause_initial"], "yes")
        self.assertEqual(options["cache_pause_wait"], 8.0)

    def test_mpv_options_local_direct_alsa(self) -> None:
        options = mpv_options_for_input(InputClass.LOCAL, direct_alsa=True)
        self.assertEqual(options["cache"], "no")
        self.assertEqual(options["cache_pause"], "no")
        self.assertGreaterEqual(options["audio_buffer"], 10.0)
        self.assertLessEqual(options["audio_buffer"], 10.0)
        self.assertEqual(options["alsa_buffer_time"], 10_000_000)
        self.assertNotIn("cache_pause_initial", options)
        self.assertEqual(options["demuxer_readahead_secs"], 1.0)

    def test_mpv_options_local_direct_alsa_format_change_warmup(self) -> None:
        options = mpv_options_for_input(
            InputClass.LOCAL,
            direct_alsa=True,
            warmup=True,
        )
        self.assertEqual(options["cache"], "no")
        self.assertNotIn("cache_pause_initial", options)

    def test_mpv_options_direct_alsa_recovery_skips_warmup(self) -> None:
        options = mpv_options_for_input(
            InputClass.NETWORK_FILE,
            direct_alsa=True,
            warmup=False,
        )
        self.assertEqual(options["cache_pause_initial"], "no")
        self.assertNotIn("cache_pause_wait", options)

    def test_mpv_options_stream(self) -> None:
        options = mpv_options_for_input(InputClass.STREAM, direct_alsa=True)
        self.assertEqual(options["cache"], "yes")
        self.assertIn("audio_buffer", options)

    def test_merge_playback_note(self) -> None:
        merged = merge_playback_note("ALSA bit-perfect", InputClass.NETWORK_FILE)
        assert merged is not None
        self.assertIn("ALSA bit-perfect", merged)
        self.assertIn("Network library (buffered)", merged)

    def test_merge_playback_note_stream_only(self) -> None:
        self.assertEqual(
            merge_playback_note(None, InputClass.STREAM),
            "Streaming (buffered)",
        )


if __name__ == "__main__":
    unittest.main()
