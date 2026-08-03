"""Tests for three-tier hardware volume discovery."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from tunes_player.platform.linux.alsa_mixer import (
    clear_alsa_mixer_cache,
)
from tunes_player.platform.linux.volume_discovery import (
    discover_hardware_volume,
)
from tunes_player.platform.linux.volume_quirks import CardIdentity, QuirkMatch, QuirkRule
from tunes_player.platform.linux.volume_ucm import UcmVolumeHint


class VolumeDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_alsa_mixer_cache()

    def tearDown(self) -> None:
        clear_alsa_mixer_cache()

    def test_quirk_nohw_skips_amixer(self) -> None:
        identity = CardIdentity(
            card=9,
            device=0,
            usb_id="2188:6537",
            firmware=None,
            long_name="Holo May",
        )
        quirk = QuirkMatch(hardware_volume=False, mixer=None, rule=_dummy_rule())
        with (
            patch(
                "tunes_player.platform.linux.volume_discovery.read_card_identity",
                return_value=identity,
            ),
            patch(
                "tunes_player.platform.linux.volume_discovery.match_quirk",
                return_value=quirk,
            ),
            patch(
                "tunes_player.platform.linux.volume_discovery.alsa_pcm_device_is_digital_output",
                return_value=False,
            ),
            patch(
                "tunes_player.platform.linux.alsa_mixer._run_amixer",
            ) as amixer,
        ):
            result = discover_hardware_volume(9, device=0, verify=True)
        amixer.assert_not_called()
        self.assertIsNone(result.control)
        self.assertEqual(result.source, "quirk")

    def test_quirk_hw_mixer_uses_named_control(self) -> None:
        contents = """
numid=6,iface=MIXER,name='Speaker Playback Volume'
  ; type=INTEGER,access=rw---R--,values=2,min=0,max=44,step=0
"""
        speaker_get = """
Simple mixer control 'Speaker',0
  Capabilities: pvolume pswitch pswitch-joined
  Playback channels: Front Left - Front Right
  Limits: Playback 0 - 44
  Front Left: Playback 21 [48%] [-23.00dB] [on]
  Front Right: Playback 21 [48%] [-23.00dB] [on]
"""
        quirk = QuirkMatch(
            hardware_volume=True,
            mixer="Speaker",
            rule=_dummy_rule(mixer="Speaker"),
        )

        def fake_run(cmd, **_kwargs):
            if cmd == ["amixer", "-c", "7", "contents"]:
                return _completed(contents)
            if cmd == ["amixer", "-c", "7", "get", "Speaker"]:
                return _completed(speaker_get)
            raise AssertionError(cmd)

        with (
            patch(
                "tunes_player.platform.linux.volume_discovery.match_quirk",
                return_value=quirk,
            ),
            patch(
                "tunes_player.platform.linux.volume_discovery.discover_ucm_volume_hint",
                return_value=None,
            ),
            patch("tunes_player.platform.linux.alsa_mixer.shutil.which", return_value="/usr/bin/amixer"),
            patch("tunes_player.platform.linux.alsa_mixer.subprocess.run", side_effect=fake_run),
            patch(
                "tunes_player.platform.linux.alsa_mixer._verify_control",
                return_value=True,
            ),
        ):
            result = discover_hardware_volume(7, device=0, verify=True)
        self.assertEqual(result.source, "quirk")
        assert result.control is not None
        self.assertEqual(result.control.scontrol, "Speaker")

    def test_ucm_mixer_used_when_no_quirk(self) -> None:
        contents = """
numid=3,iface=MIXER,name='Master Playback Volume'
  ; type=INTEGER,access=rw---R--,values=2,min=0,max=100,step=0
"""
        master_get = """
Simple mixer control 'Master',0
  Capabilities: pvolume
  Playback channels: Front Left - Front Right
  Limits: Playback 0 - 100
  Front Left: Playback 80 [80%] [on]
  Front Right: Playback 80 [80%] [on]
"""

        def fake_run(cmd, **_kwargs):
            if cmd == ["amixer", "-c", "2", "contents"]:
                return _completed(contents)
            if cmd == ["amixer", "-c", "2", "get", "Master"]:
                return _completed(master_get)
            raise AssertionError(cmd)

        with (
            patch(
                "tunes_player.platform.linux.volume_discovery.match_quirk",
                return_value=None,
            ),
            patch(
                "tunes_player.platform.linux.volume_discovery.discover_ucm_volume_hint",
                return_value=UcmVolumeHint(mixer_elem="Master", master_type_soft=False),
            ),
            patch("tunes_player.platform.linux.alsa_mixer.shutil.which", return_value="/usr/bin/amixer"),
            patch("tunes_player.platform.linux.alsa_mixer.subprocess.run", side_effect=fake_run),
            patch(
                "tunes_player.platform.linux.alsa_mixer._verify_control",
                return_value=True,
            ),
        ):
            result = discover_hardware_volume(2, device=0, verify=True)
        self.assertEqual(result.source, "ucm")
        assert result.control is not None
        self.assertEqual(result.control.scontrol, "Master")

    def test_ucm_soft_master_has_no_hardware_volume(self) -> None:
        with (
            patch(
                "tunes_player.platform.linux.volume_discovery.match_quirk",
                return_value=None,
            ),
            patch(
                "tunes_player.platform.linux.volume_discovery.discover_ucm_volume_hint",
                return_value=UcmVolumeHint(mixer_elem=None, master_type_soft=True),
            ),
        ):
            result = discover_hardware_volume(2, device=0, verify=True)
        self.assertIsNone(result.control)
        self.assertEqual(result.source, "ucm")

    def test_tier3_requires_verify(self) -> None:
        contents = """
numid=3,iface=MIXER,name='PCM Playback Volume'
  ; type=INTEGER,access=rw---R--,values=2,min=0,max=512,step=0
"""
        pcm_get = """
Simple mixer control 'PCM',0
  Capabilities: pvolume pswitch pswitch-joined
  Playback channels: Front Left - Front Right
  Limits: Playback 0 - 512
  Front Left: Playback 512 [100%] [0.00dB] [on]
  Front Right: Playback 512 [100%] [0.00dB] [on]
"""

        def fake_run(cmd, **_kwargs):
            if cmd == ["amixer", "-c", "9", "contents"]:
                return _completed(contents)
            if cmd == ["amixer", "-c", "9", "get", "PCM"]:
                return _completed(pcm_get)
            raise AssertionError(cmd)

        with (
            patch(
                "tunes_player.platform.linux.volume_discovery.match_quirk",
                return_value=None,
            ),
            patch(
                "tunes_player.platform.linux.volume_discovery.discover_ucm_volume_hint",
                return_value=None,
            ),
            patch("tunes_player.platform.linux.alsa_mixer.shutil.which", return_value="/usr/bin/amixer"),
            patch("tunes_player.platform.linux.alsa_mixer.subprocess.run", side_effect=fake_run),
            patch(
                "tunes_player.platform.linux.alsa_mixer._read_normalized_level",
                return_value=1.0,
            ),
            patch("tunes_player.platform.linux.alsa_mixer._write_normalized_level"),
        ):
            self.assertIsNone(discover_hardware_volume(9, device=0, verify=True).control)
            unverified = discover_hardware_volume(9, device=0, verify=False)
            self.assertIsNotNone(unverified.control)


def _dummy_rule(*, mixer: str | None = None) -> QuirkRule:
    return QuirkRule(
        usb_id="2188:6537",
        firmware=None,
        device=None,
        name_pattern=None,
        hardware_volume=mixer is not None,
        mixer=mixer,
        user_rule=False,
    )


def _completed(stdout: str):
    from types import SimpleNamespace

    return SimpleNamespace(returncode=0, stdout=stdout, stderr="")


if __name__ == "__main__":
    unittest.main()
