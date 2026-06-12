"""Unit tests for bit-perfect fixture matrix helpers."""

from __future__ import annotations

import unittest

from bitperfect_matrix import (
    PATTERN_DURATION_SEC,
    fixture_duration_sec,
    fixture_filename,
    is_hires_sample_rate,
    pattern_frame_count,
    pattern_start_frame,
    post_roll_sec,
    pre_roll_sec,
)


class BitPerfectMatrixTests(unittest.TestCase):
    def test_fixture_filename(self) -> None:
        self.assertEqual(fixture_filename(24, 192000), "noise_24_192000.wav")

    def test_pattern_is_point_two_seconds(self) -> None:
        self.assertEqual(pattern_frame_count(44100), int(PATTERN_DURATION_SEC * 44100))
        self.assertEqual(pattern_frame_count(192000), int(PATTERN_DURATION_SEC * 192000))

    def test_fixture_longer_than_pattern(self) -> None:
        self.assertAlmostEqual(pre_roll_sec(48000), 0.15)
        self.assertAlmostEqual(post_roll_sec(48000), 0.1)
        self.assertAlmostEqual(fixture_duration_sec(48000), 0.45)
        self.assertAlmostEqual(fixture_duration_sec(192000), 0.6)
        self.assertGreater(pattern_start_frame(48000), 0)

    def test_is_hires(self) -> None:
        self.assertFalse(is_hires_sample_rate(48000))
        self.assertTrue(is_hires_sample_rate(88200))
        self.assertTrue(is_hires_sample_rate(352800))


if __name__ == "__main__":
    unittest.main()
