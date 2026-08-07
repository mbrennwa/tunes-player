"""Tests for WirePlumber hide-parent ALSA PCM detection."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tunes_player.platform.linux.pipewire_hidden_parent_alsa import (
    hidden_parent_alsa_paths_from_wireplumber_configs,
    parse_asound_cards,
    parse_hidden_parent_alsa_paths,
    pipewire_hidden_parent_alsa_pcms,
    resolve_alsa_hw_path,
)


_ASAHI_SNIPPET = """
node.software-dsp.rules = [
    {
        matches = [
            { api.alsa.path = "hw:AppleJ413,1" }
        ]
        actions = {
            create-filter = {
                filter-path = "/usr/share/asahi-audio/j413/graph.json"
                hide-parent = true
            }
        }
    }
    {
        matches = [
            { api.alsa.path = "hw:2,0" }
        ]
        actions = {
            create-filter = {
                filter-path = "/tmp/unused.json"
                hide-parent = false
            }
        }
    }
]
"""


class PipewireHiddenParentAlsaTests(unittest.TestCase):
    def test_parse_hide_parent_paths(self) -> None:
        self.assertEqual(
            parse_hidden_parent_alsa_paths(_ASAHI_SNIPPET),
            {"hw:AppleJ413,1"},
        )

    def test_resolve_named_and_numeric_paths(self) -> None:
        cards = {"AppleJ413": 0, "Enhanc": 2}
        self.assertEqual(
            resolve_alsa_hw_path("hw:AppleJ413,1", cards=cards),
            (0, 1),
        )
        self.assertEqual(resolve_alsa_hw_path("hw:2,0", cards=cards), (2, 0))
        self.assertIsNone(resolve_alsa_hw_path("~hw:AppleJ[0-9]+,1", cards=cards))

    def test_parse_asound_cards(self) -> None:
        text = """
 0 [AppleJ413      ]: macaudio - MacBook Air J413
 2 [Enhanc         ]: USB-Audio - Holo
"""
        self.assertEqual(parse_asound_cards(text), {"AppleJ413": 0, "Enhanc": 2})

    def test_configs_dir_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conf_dir = Path(tmp)
            (conf_dir / "99-asahi.conf").write_text(_ASAHI_SNIPPET, encoding="utf-8")
            paths = hidden_parent_alsa_paths_from_wireplumber_configs(
                config_dirs=[conf_dir]
            )
        self.assertEqual(paths, {"hw:AppleJ413,1"})

    def test_pipewire_hidden_parent_expands_whole_card(self) -> None:
        with (
            patch(
                "tunes_player.platform.linux.pipewire_hidden_parent_alsa."
                "hidden_parent_alsa_paths_from_wireplumber_configs",
                return_value={"hw:AppleJ413,1"},
            ),
            patch(
                "tunes_player.platform.linux.pipewire_hidden_parent_alsa."
                "_alsa_card_name_to_index",
                return_value={"AppleJ413": 0},
            ),
            patch(
                "tunes_player.platform.linux.pipewire_hidden_parent_alsa."
                "_playback_devices_on_card",
                return_value={(0, 0), (0, 1)},
            ),
        ):
            self.assertEqual(pipewire_hidden_parent_alsa_pcms(), {(0, 0), (0, 1)})


if __name__ == "__main__":
    unittest.main()
