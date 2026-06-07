"""Tests for ALSA mixer helpers."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from tunes_player.platform.linux.alsa_mixer import (
    AlsaVolumeControl,
    alsa_card_from_endpoint_id,
    alsa_card_is_usb,
    alsa_mixer_adjustable,
    alsa_mixer_available,
    clear_alsa_mixer_cache,
    discover_output_volume_control,
)


class AlsaMixerTests(unittest.TestCase):
    def test_card_from_endpoint_id(self) -> None:
        self.assertEqual(alsa_card_from_endpoint_id("alsa:hw:0:0"), 0)
        self.assertEqual(alsa_card_from_endpoint_id("alsa:hw:1:2"), 1)
        self.assertIsNone(alsa_card_from_endpoint_id("48"))

    def test_mixer_available_on_typical_hda(self) -> None:
        """Integration: card 0 on dev machines with amixer + Master."""
        clear_alsa_mixer_cache()
        if not alsa_mixer_available(0):
            self.skipTest("no ALSA mixer on card 0")
        self.assertTrue(alsa_mixer_available(0))

    def test_card_is_usb_from_proc_usbid(self) -> None:
        usbid = Path("/proc/asound/card1/usbid")
        if not usbid.is_file():
            self.skipTest("no USB ALSA card on card 1")
        self.assertTrue(alsa_card_is_usb(1))

    def test_caldigit_like_speaker_volume_is_discovered(self) -> None:
        clear_alsa_mixer_cache()
        contents = """
numid=4,iface=MIXER,name='Mic Playback Volume'
  ; type=INTEGER,access=rw---R--,values=1,min=0,max=47,step=0
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
        mic_get = """
Simple mixer control 'Mic',0
  Capabilities: pvolume pvolume-joined cvolume cvolume-joined pswitch pswitch-joined cswitch cswitch-joined
  Playback channels: Mono
  Limits: Playback 0 - 47
  Mono: Playback 15 [32%] [0.00dB] [off]
"""

        def fake_run(cmd, **_kwargs):
            if cmd == ["amixer", "-c", "7", "contents"]:
                return subprocess_completed(contents)
            if cmd == ["amixer", "-c", "7", "get", "Speaker"]:
                return subprocess_completed(speaker_get)
            if cmd == ["amixer", "-c", "7", "get", "Mic"]:
                return subprocess_completed(mic_get)
            raise AssertionError(f"unexpected amixer call: {cmd}")

        levels = iter([0.48, 0.68, 0.48])

        def fake_read(card: int, control: AlsaVolumeControl) -> float:
            del card, control
            return next(levels)

        with (
            patch("tunes_player.platform.linux.alsa_mixer.shutil.which", return_value="/usr/bin/amixer"),
            patch("tunes_player.platform.linux.alsa_mixer.subprocess.run", side_effect=fake_run),
            patch("tunes_player.platform.linux.alsa_mixer._read_normalized_level", side_effect=fake_read),
            patch("tunes_player.platform.linux.alsa_mixer._write_normalized_level"),
        ):
            control = discover_output_volume_control(7, verify=True)
        self.assertIsNotNone(control)
        assert control is not None
        self.assertEqual(control.scontrol, "Speaker")
        self.assertTrue(alsa_mixer_adjustable(7))

    def test_capture_volume_elements_are_ignored(self) -> None:
        clear_alsa_mixer_cache()
        contents = """
numid=8,iface=MIXER,name='Mic Capture Volume'
  ; type=INTEGER,access=rw---R--,values=1,min=0,max=63,step=0
"""
        with (
            patch("tunes_player.platform.linux.alsa_mixer.shutil.which", return_value="/usr/bin/amixer"),
            patch(
                "tunes_player.platform.linux.alsa_mixer.subprocess.run",
                return_value=subprocess_completed(contents),
            ),
        ):
            self.assertIsNone(discover_output_volume_control(5))

    def test_fixed_output_candidate_fails_verification(self) -> None:
        clear_alsa_mixer_cache()
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
                return subprocess_completed(contents)
            if cmd == ["amixer", "-c", "9", "get", "PCM"]:
                return subprocess_completed(pcm_get)
            raise AssertionError(f"unexpected amixer call: {cmd}")

        with (
            patch("tunes_player.platform.linux.alsa_mixer.shutil.which", return_value="/usr/bin/amixer"),
            patch("tunes_player.platform.linux.alsa_mixer.subprocess.run", side_effect=fake_run),
            patch(
                "tunes_player.platform.linux.alsa_mixer._read_normalized_level",
                return_value=1.0,
            ),
            patch("tunes_player.platform.linux.alsa_mixer._write_normalized_level"),
        ):
            self.assertIsNone(discover_output_volume_control(9, verify=True))

    def test_caldigit_integration_when_present(self) -> None:
        if not Path("/proc/asound/card1/usbid").is_file():
            self.skipTest("no USB ALSA card on card 1")
        if Path("/proc/asound/card1/usbid").read_text().strip() != "2188:6537":
            self.skipTest("card 1 is not CalDigit TS4")
        clear_alsa_mixer_cache()
        control = discover_output_volume_control(1, verify=True)
        self.assertIsNotNone(control)
        assert control is not None
        self.assertEqual(control.scontrol, "Speaker")
        self.assertTrue(alsa_mixer_adjustable(1))


def subprocess_completed(stdout: str):
    from types import SimpleNamespace

    return SimpleNamespace(returncode=0, stdout=stdout, stderr="")


if __name__ == "__main__":
    unittest.main()
