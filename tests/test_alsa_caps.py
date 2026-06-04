"""Tests for ALSA codec capability probing."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tunes_player.core.playback.output_profile import HwAudioCaps, choose_output_format
from tunes_player.platform.linux.alsa_caps import _parse_codec_file


_CODEC_SNIPPET = """
Codec: Test Codec
Node 0x02 [Audio Output] wcaps 0x11: Stereo
  Device: name="Generic Analog", type="Audio", device=0
  PCM:
    rates [0x560]: 44100 48000 96000 192000
    bits [0xe]: 16 20 24
    formats [0x1]: PCM
"""


class AlsaCapsTests(unittest.TestCase):
    def test_parse_codec_file(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".codec", delete=False) as tmp:
            tmp.write(_CODEC_SNIPPET)
            path = Path(tmp.name)
        try:
            caps = _parse_codec_file(path)
        finally:
            path.unlink()
        self.assertIsNotNone(caps)
        assert caps is not None
        self.assertIn(192000, caps.sample_rates)
        self.assertIn(24, caps.bit_depths)

    def test_choose_output_format_resamples_when_rate_too_high(self) -> None:
        caps = HwAudioCaps(sample_rates=(44100, 48000, 96000), bit_depths=(16, 24))
        rate, bits, _channels, resampled = choose_output_format(
            file_rate=192000,
            file_bits=24,
            file_channels=2,
            caps=caps,
        )
        self.assertTrue(resampled)
        self.assertEqual(rate, 96000)
        self.assertEqual(bits, 24)

    def test_choose_output_format_no_resample_when_supported(self) -> None:
        caps = HwAudioCaps(sample_rates=(44100, 48000, 96000), bit_depths=(16, 24))
        rate, bits, _channels, resampled = choose_output_format(
            file_rate=96000,
            file_bits=24,
            file_channels=2,
            caps=caps,
        )
        self.assertFalse(resampled)
        self.assertEqual(rate, 96000)
        self.assertEqual(bits, 24)


if __name__ == "__main__":
    unittest.main()
