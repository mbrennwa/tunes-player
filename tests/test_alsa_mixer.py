"""Tests for ALSA mixer helpers."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from tunes_player.platform.linux.alsa_mixer import (
    alsa_card_from_endpoint_id,
    alsa_card_is_usb,
    alsa_mixer_adjustable,
    alsa_mixer_available,
    clear_alsa_mixer_cache,
    _likely_fixed_usb_dac,
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

    def test_card_is_usb_from_proc_usbid(self) -> None:
        usbid = Path("/proc/asound/card1/usbid")
        if not usbid.is_file():
            self.skipTest("no USB ALSA card on card 1")
        self.assertTrue(alsa_card_is_usb(1))

    def test_likely_fixed_usb_dac_with_pcm_only(self) -> None:
        with (
            patch(
                "tunes_player.platform.linux.alsa_mixer.alsa_card_is_usb",
                return_value=True,
            ),
            patch(
                "tunes_player.platform.linux.alsa_mixer._available_mixer_controls",
                return_value={"PCM"},
            ),
        ):
            self.assertTrue(_likely_fixed_usb_dac(3))

    def test_usb_dac_with_master_is_not_fixed(self) -> None:
        with (
            patch(
                "tunes_player.platform.linux.alsa_mixer.alsa_card_is_usb",
                return_value=True,
            ),
            patch(
                "tunes_player.platform.linux.alsa_mixer._available_mixer_controls",
                return_value={"PCM", "Master"},
            ),
        ):
            self.assertFalse(_likely_fixed_usb_dac(3))

    def test_mixer_not_adjustable_when_level_unchanged(self) -> None:
        clear_alsa_mixer_cache()
        with (
            patch(
                "tunes_player.platform.linux.alsa_mixer._likely_fixed_usb_dac",
                return_value=False,
            ),
            patch(
                "tunes_player.platform.linux.alsa_mixer.alsa_mixer_available",
                return_value=True,
            ),
            patch(
                "tunes_player.platform.linux.alsa_mixer.alsa_get_level",
                return_value=1.0,
            ),
            patch(
                "tunes_player.platform.linux.alsa_mixer.alsa_set_level",
            ),
        ):
            self.assertFalse(alsa_mixer_adjustable(7))

    def test_mixer_not_adjustable_when_set_does_not_reach_target(self) -> None:
        clear_alsa_mixer_cache()
        levels = iter([1.0, 1.0, 1.0])

        def fake_get_level(_card: int) -> float:
            return next(levels)

        with (
            patch(
                "tunes_player.platform.linux.alsa_mixer._likely_fixed_usb_dac",
                return_value=False,
            ),
            patch(
                "tunes_player.platform.linux.alsa_mixer.alsa_mixer_available",
                return_value=True,
            ),
            patch(
                "tunes_player.platform.linux.alsa_mixer.alsa_get_level",
                side_effect=fake_get_level,
            ),
            patch(
                "tunes_player.platform.linux.alsa_mixer.alsa_set_level",
            ),
        ):
            self.assertFalse(alsa_mixer_adjustable(9))

    def test_holo_like_usb_dac_is_not_hardware_volume(self) -> None:
        if not alsa_card_is_usb(1) or not _likely_fixed_usb_dac(1):
            self.skipTest("no Holo-like USB DAC on card 1")
        clear_alsa_mixer_cache()
        self.assertFalse(alsa_mixer_adjustable(1))

    def test_mixer_adjustable_when_level_changes(self) -> None:
        clear_alsa_mixer_cache()
        levels = iter([0.8, 0.6, 0.8])

        def fake_get_level(_card: int) -> float:
            return next(levels)

        with (
            patch(
                "tunes_player.platform.linux.alsa_mixer._likely_fixed_usb_dac",
                return_value=False,
            ),
            patch(
                "tunes_player.platform.linux.alsa_mixer.alsa_mixer_available",
                return_value=True,
            ),
            patch(
                "tunes_player.platform.linux.alsa_mixer.alsa_get_level",
                side_effect=fake_get_level,
            ),
            patch(
                "tunes_player.platform.linux.alsa_mixer.alsa_set_level",
            ),
        ):
            self.assertTrue(alsa_mixer_adjustable(8))


if __name__ == "__main__":
    unittest.main()
