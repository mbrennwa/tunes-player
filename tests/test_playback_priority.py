"""Tests for playback process priority helpers."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from tunes_player.platform.linux import playback_priority


class PlaybackPriorityTests(unittest.TestCase):
    def test_mpv_subprocess_command_is_plain_mpv(self) -> None:
        cmd = playback_priority.mpv_subprocess_command(
            "/usr/bin/mpv",
            ["--idle=yes"],
        )
        self.assertEqual(cmd, ["/usr/bin/mpv", "--idle=yes"])

    def test_pin_mpv_subprocess_usb_uses_irq_cpu(self) -> None:
        with patch(
            "tunes_player.platform.linux.alsa_mixer.alsa_card_is_usb",
            return_value=True,
        ), patch(
            "tunes_player.platform.linux.usb_irq.preferred_playback_cpu_for_usb_card",
            return_value=2,
        ), patch("os.sched_setaffinity") as affinity, patch(
            "os.setpriority",
        ):
            status = playback_priority.pin_mpv_subprocess(4242, alsa_card=1)
        affinity.assert_called_once_with(4242, {2})
        self.assertEqual(status.cpu_affinity, 2)

    def test_pin_mpv_subprocess_non_usb_unpinned(self) -> None:
        with patch(
            "tunes_player.platform.linux.alsa_mixer.alsa_card_is_usb",
            return_value=False,
        ), patch("os.sched_setaffinity") as affinity, patch("os.setpriority"):
            status = playback_priority.pin_mpv_subprocess(4242, alsa_card=0)
        affinity.assert_not_called()
        self.assertIsNone(status.cpu_affinity)


if __name__ == "__main__":
    unittest.main()
