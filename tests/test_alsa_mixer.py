"""Tests for ALSA mixer helpers."""

from __future__ import annotations

import unittest

from tunes_player.platform.linux.alsa_mixer import (
    alsa_card_from_endpoint_id,
    alsa_mixer_available,
)


class AlsaMixerTests(unittest.TestCase):
    def test_card_from_endpoint_id(self) -> None:
        self.assertEqual(alsa_card_from_endpoint_id("alsa:hw:0:0"), 0)
        self.assertEqual(alsa_card_from_endpoint_id("alsa:hw:1:2"), 1)
        self.assertIsNone(alsa_card_from_endpoint_id("48"))

    def test_mixer_available_on_typical_hda(self) -> None:
        """Integration: card 0 on dev machines with amixer + Master."""
        if not alsa_mixer_available(0):
            self.skipTest("no ALSA mixer on card 0")
        self.assertTrue(alsa_mixer_available(0))


if __name__ == "__main__":
    unittest.main()
