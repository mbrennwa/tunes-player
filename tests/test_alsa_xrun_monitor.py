"""Tests for ALSA xrun / feed monitor."""

from __future__ import annotations

import unittest
import unittest.mock

from tunes_player.platform.linux import alsa_xrun_monitor


class AlsaXrunMonitorTests(unittest.TestCase):
    def test_parse_pcm_status(self) -> None:
        text = """
state: RUNNING
xruns: 3
hw_ptr      : 1000
appl_ptr    : 2000
delay       : 512
avail       : 128
"""
        status = alsa_xrun_monitor.parse_pcm_status(
            text, path="/proc/asound/card1/pcm0p/sub0/status"
        )
        self.assertEqual(status.state, "RUNNING")
        self.assertEqual(status.xruns, 3)
        self.assertEqual(status.hw_ptr, 1000)
        self.assertEqual(status.appl_ptr, 2000)
        self.assertEqual(status.delay, 512)
        self.assertEqual(status.avail, 128)
        self.assertEqual(status.path, "/proc/asound/card1/pcm0p/sub0/status")

    def test_pointer_delta_handles_wrap(self) -> None:
        self.assertEqual(alsa_xrun_monitor.pointer_delta(10, 25), 15)
        self.assertEqual(alsa_xrun_monitor.pointer_delta(100, 50), 1)

    def test_parse_card_from_endpoint_hw_form(self) -> None:
        self.assertEqual(
            alsa_xrun_monitor.parse_card_from_endpoint_id("alsa:hw:1:0"), 1
        )
        self.assertEqual(alsa_xrun_monitor.parse_card_from_endpoint_id("alsa:1"), 1)

    def test_poll_logs_xrun_state_transition(self) -> None:
        pcm_path = "/proc/asound/card1/pcm0p/sub0/status"
        running = "state: RUNNING\nxruns: 0\nhw_ptr: 1\nappl_ptr: 2\n"
        xrun = "state: XRUN\nxruns: 1\nhw_ptr: 2\nappl_ptr: 3\n"
        monitor = alsa_xrun_monitor.AlsaXrunMonitor()
        monitor.set_card(1)

        with unittest.mock.patch.object(
            alsa_xrun_monitor,
            "list_playback_pcm_statuses",
            side_effect=[
                [alsa_xrun_monitor.parse_pcm_status(running, path=pcm_path)],
                [alsa_xrun_monitor.parse_pcm_status(xrun, path=pcm_path)],
            ],
        ), self.assertLogs(
            "tunes_player.platform.linux.alsa_xrun_monitor", level="WARNING"
        ) as logs:
            monitor.poll(mpv_audio_device="alsa/hw:1,0")
            monitor.poll(mpv_audio_device="alsa/hw:1,0")

        self.assertTrue(
            any("entered XRUN state" in record.getMessage() for record in logs.records)
        )

    def test_poll_logs_xrun_counter_increase(self) -> None:
        pcm_path = "/proc/asound/card1/pcm0p/sub0/status"
        first = "state: RUNNING\nxruns: 2\nhw_ptr: 10\nappl_ptr: 20\n"
        second = "state: RUNNING\nxruns: 5\nhw_ptr: 100\nappl_ptr: 200\n"
        monitor = alsa_xrun_monitor.AlsaXrunMonitor()
        monitor.set_card(1)

        with unittest.mock.patch.object(
            alsa_xrun_monitor,
            "list_playback_pcm_statuses",
            side_effect=[
                [alsa_xrun_monitor.parse_pcm_status(first, path=pcm_path)],
                [alsa_xrun_monitor.parse_pcm_status(second, path=pcm_path)],
            ],
        ), self.assertLogs(
            "tunes_player.platform.linux.alsa_xrun_monitor", level="WARNING"
        ) as logs:
            monitor.poll(mpv_audio_device="alsa/hw:1,0")
            monitor.poll(mpv_audio_device="alsa/hw:1,0")

        self.assertTrue(
            any("xrun counter increased" in record.getMessage() for record in logs.records)
        )

    def test_expect_feeding_reports_stalled_pointers(self) -> None:
        pcm_path = "/proc/asound/card1/pcm0p/sub0/status"
        sample = "state: RUNNING\nxruns: 0\nhw_ptr: 500\nappl_ptr: 600\n"
        clock = {"t": 0.0}
        monitor = alsa_xrun_monitor.AlsaXrunMonitor(clock=lambda: clock["t"])
        monitor.set_card(1)

        with unittest.mock.patch.object(
            alsa_xrun_monitor,
            "list_playback_pcm_statuses",
            return_value=[alsa_xrun_monitor.parse_pcm_status(sample, path=pcm_path)],
        ):
            clock["t"] = 0.0
            first = monitor.poll(
                mpv_audio_device="alsa/hw:1,0", expect_feeding=True
            )
            self.assertEqual(first, [])
            clock["t"] = 1.0
            stalled = monitor.poll(
                mpv_audio_device="alsa/hw:1,0", expect_feeding=True
            )

        self.assertTrue(any(i.code == "alsa_feed_stalled" for i in stalled))

    def test_expect_feeding_ok_when_pointers_advance(self) -> None:
        pcm_path = "/proc/asound/card1/pcm0p/sub0/status"
        first = "state: RUNNING\nxruns: 0\nhw_ptr: 500\nappl_ptr: 600\n"
        second = "state: RUNNING\nxruns: 0\nhw_ptr: 5000\nappl_ptr: 6000\n"
        clock = {"t": 0.0}
        monitor = alsa_xrun_monitor.AlsaXrunMonitor(clock=lambda: clock["t"])
        monitor.set_card(1)

        with unittest.mock.patch.object(
            alsa_xrun_monitor,
            "list_playback_pcm_statuses",
            side_effect=[
                [alsa_xrun_monitor.parse_pcm_status(first, path=pcm_path)],
                [alsa_xrun_monitor.parse_pcm_status(second, path=pcm_path)],
            ],
        ):
            clock["t"] = 0.0
            monitor.poll(mpv_audio_device="alsa/hw:1,0", expect_feeding=True)
            clock["t"] = 1.0
            issues = monitor.poll(
                mpv_audio_device="alsa/hw:1,0", expect_feeding=True
            )
        self.assertEqual(issues, [])

    def test_expect_feeding_reports_not_running(self) -> None:
        pcm_path = "/proc/asound/card1/pcm0p/sub0/status"
        text = "state: SUSPENDED\nxruns: 0\nhw_ptr: 1\nappl_ptr: 1\n"
        monitor = alsa_xrun_monitor.AlsaXrunMonitor()
        monitor.set_card(1)
        with unittest.mock.patch.object(
            alsa_xrun_monitor,
            "list_playback_pcm_statuses",
            return_value=[alsa_xrun_monitor.parse_pcm_status(text, path=pcm_path)],
        ):
            issues = monitor.poll(
                mpv_audio_device="alsa/hw:1,0", expect_feeding=True
            )
        self.assertTrue(any(i.code == "alsa_not_running" for i in issues))

    def test_reset_clears_state(self) -> None:
        pcm_path = "/proc/asound/card1/pcm0p/sub0/status"
        text = "state: RUNNING\nxruns: 1\nhw_ptr: 1\nappl_ptr: 2\n"
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
