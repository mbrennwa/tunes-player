"""Unit tests for bitperfect_harness PCM helpers."""

from __future__ import annotations

import tempfile
import unittest
import wave
from array import array
from pathlib import Path

from bitperfect_harness import (
    FIXTURES_DIR,
    WavPcm,
    align_pcm,
    assert_pcm_equal,
    read_wav_pcm,
)
from bitperfect_matrix import pattern_frame_count, pattern_start_frame


class BitPerfectHarnessTests(unittest.TestCase):
    def test_read_24_fixture(self) -> None:
        pcm = read_wav_pcm(FIXTURES_DIR / "noise_24_44100.wav")
        self.assertEqual(pcm.sample_rate, 44100)
        self.assertEqual(pcm.bit_depth, 24)

    def test_read_32_bit_capture_wav(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "capture.wav"
            samples = array("i", [100 << 8, -200 << 8, 300 << 8, -400 << 8])
            with wave.open(str(path), "wb") as handle:
                handle.setnchannels(2)
                handle.setsampwidth(4)
                handle.setframerate(44100)
                handle.writeframes(samples.tobytes())
            pcm = read_wav_pcm(path)
            self.assertEqual(pcm.bit_depth, 32)
            self.assertEqual(pcm.samples[0], 100 << 8)

    def test_assert_pcm_equal_24_via_s32_capture(self) -> None:
        reference = read_wav_pcm(FIXTURES_DIR / "noise_24_44100.wav")
        shifted = array("i", (sample << 8 for sample in reference.samples))
        capture = WavPcm(
            samples=shifted,
            sample_rate=reference.sample_rate,
            channels=reference.channels,
            bit_depth=32,
        )
        assert_pcm_equal(reference, capture)

    def test_read_32_fixture(self) -> None:
        pcm = read_wav_pcm(FIXTURES_DIR / "noise_32_44100.wav")
        self.assertEqual(pcm.sample_rate, 44100)
        self.assertEqual(pcm.bit_depth, 32)

    def test_assert_pcm_equal_32_direct_s32_capture(self) -> None:
        reference = read_wav_pcm(FIXTURES_DIR / "noise_32_44100.wav")
        capture = WavPcm(
            samples=array("i", reference.samples),
            sample_rate=reference.sample_rate,
            channels=reference.channels,
            bit_depth=32,
        )
        assert_pcm_equal(reference, capture)

    def test_align_pcm_finds_pattern_in_latency_padded_capture(self) -> None:
        reference = read_wav_pcm(FIXTURES_DIR / "noise_16_44100.wav")
        lag_frames = 1200
        pattern_start = pattern_start_frame(reference.sample_rate)
        pattern_frames = pattern_frame_count(reference.sample_rate)
        pattern = reference.samples[
            pattern_start * 2 : (pattern_start + pattern_frames) * 2
        ]
        padded = array("h", [0] * (lag_frames * 2))
        padded.extend(pattern)
        padded.extend(array("h", [0] * 4000))
        capture = WavPcm(
            samples=padded,
            sample_rate=reference.sample_rate,
            channels=reference.channels,
            bit_depth=16,
        )
        ref_aligned, cap_aligned = align_pcm(reference, capture)
        self.assertEqual(len(ref_aligned), len(cap_aligned))
        self.assertEqual(len(ref_aligned), pattern_frames * 2)
        self.assertEqual(list(ref_aligned), list(cap_aligned))


if __name__ == "__main__":
    unittest.main()
