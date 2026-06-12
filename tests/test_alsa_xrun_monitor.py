"""Tests for ALSA xrun monitor."""

from __future__ import annotations

import unittest
import unittest.mock

from tunes_player.platform.linux import alsa_xrun_monitor


class AlsaXrunMonitorTests(unittest.TestCase):
    def test_parse_pcm_status(self) -> None:
        text = """
state: RUNNING
xruns: 3
"""
        status = alsa_xrun_monitor.parse_pcm_status(text, path="/proc/asound/card1/pcm0p/sub0/status")
        self.assertEqual(status.state, "RUNNING")
        self.assertEqual(status.xruns, 3)
        self.assertEqual(status.path, "/proc/asound/card1/pcm0p/sub0/status")

    def test_poll_logs_xrun_state_transition(self) -> None:
        pcm_path = "/proc/asound/card1/pcm0p/sub0/status"
        running = "state: RUNNING\nxruns: 0\n"
        xrun = "state: XRUN\nxruns: 1\n"
        monitor = alsa_xrun_monitor.AlsaXrunMonitor()
        monitor.set_card(1)

        with unittest.mock.patch.object(
            alsa_xrun_monitor,
            "list_playback_pcm_statuses",
            side_effect=[
                [alsa_xrun_monitor.parse_pcm_status(running, path=pcm_path)],
                [alsa_xrun_monitor.parse_pcm_status(xrun, path=pcm_path)],
            ],
        ), self.assertLogs("tunes_player.platform.linux.alsa_xrun_monitor", level="WARNING") as logs:
            monitor.poll(mpv_audio_device="alsa/hw:1,0")
            monitor.poll(mpv_audio_device="alsa/hw:1,0")

        self.assertTrue(
            any("entered XRUN state" in record.getMessage() for record in logs.records)
        )

    def test_poll_logs_xrun_counter_increase(self) -> None:
        pcm_path = "/proc/asound/card1/pcm0p/sub0/status"
        first = "state: RUNNING\nxruns: 2\n"
        second = "state: RUNNING\nxruns: 5\n"
        monitor = alsa_xrun_monitor.AlsaXrunMonitor()
        monitor.set_card(1)

        with unittest.mock.patch.object(
            alsa_xrun_monitor,
            "list_playback_pcm_statuses",
            side_effect=[
                [alsa_xrun_monitor.parse_pcm_status(first, path=pcm_path)],
                [alsa_xrun_monitor.parse_pcm_status(second, path=pcm_path)],
            ],
        ), self.assertLogs("tunes_player.platform.linux.alsa_xrun_monitor", level="WARNING") as logs:
            monitor.poll(mpv_audio_device="alsa/hw:1,0")
            monitor.poll(mpv_audio_device="alsa/hw:1,0")

        self.assertTrue(
            any("xrun counter increased" in record.getMessage() for record in logs.records)
        )

    def test_reset_clears_state(self) -> None:
        pcm_path = "/proc/asound/card1/pcm0p/sub0/status"
        text = "state: RUNNING\nxruns: 1\n"
        monitor = alsa_xrun_monitor.AlsaXrunMonitor()
        monitor.set_card(1)

        with unittest.mock.patch.object(
            alsa_xrun_monitor,
            "list_playback_pcm_statuses",
            return_value=[alsa_xrun_monitor.parse_pcm_status(text, path=pcm_path)],
        ):
            monitor.poll(mpv_audio_device="alsa/hw:1,0")
            monitor.reset()
            with self.assertNoLogs(
                "tunes_player.platform.linux.alsa_xrun_monitor",
                level="WARNING",
            ):
                monitor.poll(mpv_audio_device="alsa/hw:1,0")


if __name__ == "__main__":
    unittest.main()
