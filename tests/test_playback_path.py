"""Tests for playback path derivation from negotiated mpv state."""

from __future__ import annotations

import unittest

from tunes_player.core.library.store import FileMetadata
from tunes_player.core.playback.output_profile import PlaybackOutputProfile
from tunes_player.core.playback.playback_path import (
    NegotiatedPlaybackState,
    derive_playback_path_info,
)
from tunes_player.core.volume import pipewire_endpoint_id


class DerivePlaybackPathTests(unittest.TestCase):
    def _profile(self, **kwargs: object) -> PlaybackOutputProfile:
        defaults = {
            "direct_alsa": True,
            "use_exclusive": True,
            "allow_resample": False,
            "target_rate": 96000,
            "target_bit_depth": 24,
            "target_channels": 2,
            "audio_format": "s32",
        }
        defaults.update(kwargs)
        return PlaybackOutputProfile(**defaults)  # type: ignore[arg-type]

    def test_pipewire_endpoint_not_bit_perfect(self) -> None:
        path = derive_playback_path_info(
            file_meta=None,
            profile=PlaybackOutputProfile(
                direct_alsa=False,
                use_exclusive=False,
                allow_resample=True,
            ),
            negotiated=NegotiatedPlaybackState(),
            endpoint_id=pipewire_endpoint_id("alsa_output.pci.analog-stereo"),
            device_volume=True,
            mpv_soft_volume=False,
        )
        self.assertFalse(path.bit_perfect_playback)
        self.assertEqual(path.playback_note, "via PipeWire")

    def test_direct_alsa_bit_perfect_from_negotiated_state(self) -> None:
        path = derive_playback_path_info(
            file_meta=FileMetadata(
                path="/a.flac",
                codec="flac",
                duration_sec=1.0,
                sample_rate=96000,
                bit_depth=24,
                channels=2,
            ),
            profile=self._profile(),
            negotiated=NegotiatedPlaybackState(
                ao="alsa",
                audio_samplerate=96000,
                audio_format="s32",
                alsa_resample=False,
            ),
            endpoint_id="alsa:hw:0:0",
            device_volume=True,
            mpv_soft_volume=False,
        )
        self.assertTrue(path.bit_perfect_playback)
        self.assertEqual(path.playback_note, "ALSA bit-perfect")

    def test_resample_note_when_negotiated_rate_differs(self) -> None:
        path = derive_playback_path_info(
            file_meta=FileMetadata(
                path="/a.flac",
                codec="flac",
                duration_sec=1.0,
                sample_rate=192000,
                bit_depth=24,
                channels=2,
            ),
            profile=self._profile(
                allow_resample=True,
                target_rate=96000,
            ),
            negotiated=NegotiatedPlaybackState(
                audio_samplerate=96000,
                audio_format="s32",
                alsa_resample=True,
            ),
            endpoint_id="alsa:hw:0:0",
            device_volume=True,
            mpv_soft_volume=False,
        )
        self.assertFalse(path.bit_perfect_playback)
        assert path.playback_note is not None
        self.assertIn("resampling", path.playback_note)

    def test_alsa_resample_flag_without_rate_mismatch_is_bit_perfect(self) -> None:
        path = derive_playback_path_info(
            file_meta=FileMetadata(
                path="/a.flac",
                codec="flac",
                duration_sec=1.0,
                sample_rate=96000,
                bit_depth=24,
                channels=2,
            ),
            profile=self._profile(),
            negotiated=NegotiatedPlaybackState(
                ao="alsa",
                audio_samplerate=96000,
                audio_format="s32",
                alsa_resample=True,
            ),
            endpoint_id="alsa:hw:0:0",
            device_volume=True,
            mpv_soft_volume=False,
        )
        self.assertTrue(path.bit_perfect_playback)
        self.assertEqual(path.playback_note, "ALSA bit-perfect")


    def test_direct_alsa_bit_perfect_with_fixed_output_dac(self) -> None:
        path = derive_playback_path_info(
            file_meta=FileMetadata(
                path="/a.flac",
                codec="flac",
                duration_sec=1.0,
                sample_rate=192000,
                bit_depth=24,
                channels=2,
            ),
            profile=self._profile(target_rate=192000),
            negotiated=NegotiatedPlaybackState(
                ao="alsa",
                audio_samplerate=192000,
                audio_format="s32",
                alsa_resample=False,
            ),
            endpoint_id="alsa:hw:1:0",
            device_volume=False,
            mpv_soft_volume=False,
        )
        self.assertTrue(path.bit_perfect_playback)
        self.assertEqual(path.playback_note, "ALSA bit-perfect")


class PlaybackPathNoteMergeTests(unittest.TestCase):
    def test_apply_path_info_keeps_network_buffer_note(self) -> None:
        import tempfile
        from pathlib import Path

        from tunes_player.core.config import ConfigManager
        from tunes_player.core.models import Source, Track
        from tunes_player.core.playback.buffer_policy import InputClass
        from tunes_player.core.playback.output_profile import PlaybackPathInfo
        from tunes_player.core.services import PlayerService

        with tempfile.TemporaryDirectory() as tmp:
            config = ConfigManager(Path(tmp) / "config.toml")
            config.load()
            service = PlayerService(config=config, prewarm_engine=False)
            service._current_track = Track(
                id="local:file:test",
                title="Test",
                artist_name="Artist",
                album_title="Album",
                source=Source.LOCAL,
            )
            service._playback_input_class = InputClass.NETWORK_FILE
            service._apply_path_info(
                PlaybackPathInfo(
                    bit_perfect_playback=True,
                    playback_note="ALSA bit-perfect",
                )
            )
            self.assertIn("Network library (buffered)", service._playback_note or "")
            self.assertIn("Network library (buffered)", service._quality_hint)


if __name__ == "__main__":
    unittest.main()
