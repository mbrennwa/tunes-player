"""Tests for portable direct-ALSA playback device selection."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from tunes_player.platform.linux import alsa_playback


class AlsaPlaybackTests(unittest.TestCase):
    def setUp(self) -> None:
        alsa_playback.clear_playback_device_cache()

    def tearDown(self) -> None:
        alsa_playback.clear_playback_device_cache()

    def test_plughw_mpv_device(self) -> None:
        self.assertEqual(
            alsa_playback.plughw_mpv_device("alsa/hw:1,0"),
            "alsa/plughw:1,0",
        )
        self.assertEqual(
            alsa_playback.plughw_mpv_device("hw:2,0"),
            "alsa/plughw:2,0",
        )

    @patch.object(alsa_playback, "alsa_card_is_usb", return_value=True)
    def test_effective_mpv_alsa_device_usb(self, _usb: object) -> None:
        self.assertEqual(
            alsa_playback.effective_mpv_alsa_device("alsa/hw:1,0"),
            "alsa/hw:1,0",
        )

    @patch.object(alsa_playback, "alsa_card_is_usb", return_value=False)
    def test_effective_mpv_alsa_device_pci(self, _usb: object) -> None:
        self.assertEqual(
            alsa_playback.effective_mpv_alsa_device("alsa/hw:0,0"),
            "alsa/hw:0,0",
        )

    @patch.object(alsa_playback, "alsa_card_is_usb", return_value=True)
    @patch.object(alsa_playback.LOG, "info")
    def test_effective_mpv_alsa_device_logs_once(self, log_info: object, _usb: object) -> None:
        for _ in range(5):
            alsa_playback.effective_mpv_alsa_device("alsa/hw:1,0")
        self.assertEqual(log_info.call_count, 1)
        self.assertIn("exclusive disabled", log_info.call_args.args[0])

    @patch.object(alsa_playback, "is_usb_alsa_playback", return_value=True)
    def test_direct_alsa_use_exclusive_false_for_usb(self, _usb: object) -> None:
        self.assertFalse(
            alsa_playback.direct_alsa_use_exclusive(
                True,
                "alsa:hw:1:0",
                "alsa/hw:1,0",
            )
        )

    @patch.object(alsa_playback, "is_usb_alsa_playback", return_value=False)
    def test_direct_alsa_use_exclusive_true_for_pci(self, _usb: object) -> None:
        self.assertTrue(
            alsa_playback.direct_alsa_use_exclusive(
                True,
                "alsa:hw:0:0",
                "alsa/hw:0,0",
            )
        )


if __name__ == "__main__":
    unittest.main()
