"""Tests for PipeWire-claimed ALSA PCM detection."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from tunes_player.platform.linux.pipewire_claimed_alsa import (
    parse_claimed_alsa_pcms,
    parse_claimed_alsa_pcms_from_pwdump,
    pipewire_claimed_alsa_pcms,
)


_PW_CLAIMED_SAMPLE = """
	id 40, type PipeWire:Interface:Node
		media.class = "Audio/Device"
	id 54, type PipeWire:Interface:Node
		alsa.card = "1"
		alsa.device = "0"
		api.alsa.path = "hw:1,0"
		media.class = "Audio/Sink"
	id 60, type PipeWire:Interface:Node
		alsa.card = "1"
		api.alsa.path = "hw:AppleJ413,1"
		media.class = "Audio/Sink"
	id 70, type PipeWire:Interface:Node
*		alsa.card = "2"
*		alsa.device = "0"
*		media.class = "Audio/Sink"
"""


class PipewireClaimedAlsaTests(unittest.TestCase):
    def test_parse_card_device_and_named_path(self) -> None:
        claimed = parse_claimed_alsa_pcms(_PW_CLAIMED_SAMPLE)
        self.assertEqual(claimed, {(1, 0), (1, 1), (2, 0)})

    def test_parse_numeric_path_only(self) -> None:
        sample = """
	id 10, type PipeWire:Interface:Node
		api.alsa.path = "hw:3,2"
"""
        self.assertEqual(parse_claimed_alsa_pcms(sample), {(3, 2)})

    def test_parse_star_prefixed_pw_cli_info_props(self) -> None:
        sample = """
id 68, type PipeWire:Interface:Node
*		alsa.card = "0"
*		alsa.device = "0"
*		api.alsa.path = "hw:AppleJ413,0"
"""
        self.assertEqual(parse_claimed_alsa_pcms(sample), {(0, 0)})

    def test_pwdump_claims_whole_card_devices(self) -> None:
        dump = json.dumps(
            [
                {
                    "id": 54,
                    "type": "PipeWire:Interface:Device",
                    "info": {
                        "props": {
                            "media.class": "Audio/Device",
                            "alsa.card": 0,
                            "api.alsa.path": "hw:0",
                            "node.name": "alsa_card.platform-sound",
                        }
                    },
                },
                {
                    "id": 68,
                    "type": "PipeWire:Interface:Node",
                    "info": {
                        "props": {
                            "media.class": "Audio/Sink",
                            "alsa.card": 0,
                            "alsa.device": 0,
                            "api.alsa.path": "hw:AppleJ413,0",
                        }
                    },
                },
            ]
        )
        with patch(
            "tunes_player.platform.linux.pipewire_claimed_alsa._playback_devices_on_card",
            return_value={(0, 0), (0, 1)},
        ):
            claimed = parse_claimed_alsa_pcms_from_pwdump(dump)
        self.assertEqual(claimed, {(0, 0), (0, 1)})

    def test_pwdump_keeps_exact_pcm_without_whole_card(self) -> None:
        dump = json.dumps(
            [
                {
                    "id": 99,
                    "info": {
                        "props": {
                            "alsa.card": 2,
                            "alsa.device": 0,
                            "api.alsa.path": "hw:2,0",
                        }
                    },
                }
            ]
        )
        with patch(
            "tunes_player.platform.linux.pipewire_claimed_alsa._playback_devices_on_card",
        ) as expand:
            claimed = parse_claimed_alsa_pcms_from_pwdump(dump)
        expand.assert_not_called()
        self.assertEqual(claimed, {(2, 0)})

    def test_pipewire_claimed_fail_open_when_tools_missing(self) -> None:
        with patch("shutil.which", return_value=None):
            self.assertEqual(pipewire_claimed_alsa_pcms(), set())

    def test_pipewire_claimed_fail_open_when_pw_down(self) -> None:
        with (
            patch("shutil.which", side_effect=lambda c: f"/usr/bin/{c}"),
            patch(
                "subprocess.run",
                side_effect=OSError("failed to connect"),
            ),
        ):
            self.assertEqual(pipewire_claimed_alsa_pcms(), set())


if __name__ == "__main__":
    unittest.main()
