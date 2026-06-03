"""Tests for Linux audio stack probe and ALSA device parsing."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from tunes_player.platform.linux.audio_probe import (
    _parse_aplay_playback_devices,
    list_alsa_playback_endpoints,
    probe_linux_audio_stack,
)

_APLAY_SAMPLE = """**** List of PLAYBACK Hardware Devices ****
card 0: Generic [HD-Audio Generic], device 0: Generic Analog [Generic Analog]
  Subdevices: 1/1
  Subdevice #0: subdevice #0
card 1: Device [USB Audio], device 0: USB Audio [USB Audio]
  Subdevices: 1/1
  Subdevice #0: subdevice #0
"""


class AudioProbeTests(unittest.TestCase):
    def test_parse_aplay_playback_devices(self) -> None:
        with patch(
            "tunes_player.platform.linux.audio_probe.subprocess.run",
        ) as run_mock:
            run_mock.return_value.returncode = 0
            run_mock.return_value.stdout = _APLAY_SAMPLE
            devices = _parse_aplay_playback_devices()
        self.assertEqual(len(devices), 2)
        self.assertEqual(devices[0][0], 0)
        self.assertEqual(devices[0][3], 0)
        self.assertEqual(devices[0][2], "HD-Audio Generic")

    def test_list_alsa_playback_endpoints(self) -> None:
        with patch(
            "tunes_player.platform.linux.audio_probe._parse_aplay_playback_devices",
            return_value=[
                (0, "Generic", "HD-Audio Generic", 0, "Generic Analog", "Generic Analog"),
            ],
        ):
            endpoints = list_alsa_playback_endpoints()
        self.assertEqual(endpoints[0][0], "alsa:hw:0:0")
        self.assertEqual(endpoints[0][1], "hw:0,0")
        self.assertIn("HD-Audio", endpoints[0][2])

    def test_probe_pipewire_not_running(self) -> None:
        with patch("shutil.which") as which_mock:
            which_mock.side_effect = (
                lambda name: f"/usr/bin/{name}" if name in ("wpctl", "aplay") else None
            )
            with patch(
                "tunes_player.platform.linux.audio_probe.subprocess.run",
            ) as run_mock:
                run_mock.return_value.returncode = 1
                run_mock.return_value.stdout = ""
                run_mock.return_value.stderr = "Could not connect to PipeWire"
                with patch(
                    "tunes_player.platform.linux.audio_probe.list_alsa_playback_endpoints",
                    return_value=[("alsa:hw:0:0", "hw:0,0", "DAC")],
                ):
                    info = probe_linux_audio_stack()
        self.assertEqual(info.backend, "ALSA")
        self.assertIn("not running", info.detail)


if __name__ == "__main__":
    unittest.main()
