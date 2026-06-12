"""Probe Linux audio stack for Settings hints and ALSA fallback enumeration."""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass

_APLAY_CARD = re.compile(
    r"^card (\d+): ([^,]+?)(?:\s+\[([^\]]+)\])?, device (\d+): "
    r"(.+?)(?:\s+\[([^\]]+)\])?\s*$"
)


@dataclass(frozen=True, slots=True)
class LinuxAudioStackInfo:
    """Human-readable audio stack state for Settings."""

    backend: str
    detail: str
    settings_hint: str


def probe_linux_audio_stack() -> LinuxAudioStackInfo:
    """Inspect wpctl/pactl/aplay to explain what Tunes can use for output."""
    wpctl_path = shutil.which("wpctl")
    pactl_path = shutil.which("pactl")
    aplay_path = shutil.which("aplay")

    if wpctl_path:
        result = subprocess.run(
            [wpctl_path, "status"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            from tunes_player.platform.linux.audio import _parse_wpctl_status_sinks

            if _parse_wpctl_status_sinks(result.stdout):
                return LinuxAudioStackInfo(
                    backend="PipeWire",
                    detail="WirePlumber (wpctl)",
                    settings_hint="",
                )
        combined = f"{result.stdout}\n{result.stderr}".lower()
        if "could not connect" in combined or "failed to connect" in combined:
            if aplay_path and list_alsa_playback_endpoints():
                return LinuxAudioStackInfo(
                    backend="ALSA",
                    detail="PipeWire is installed but not running",
                    settings_hint="",
                )
            return LinuxAudioStackInfo(
                backend="Unavailable",
                detail="PipeWire is installed but not running",
                settings_hint=(
                    "Start PipeWire: systemctl --user start pipewire wireplumber. "
                    "Or enable software volume below and use System default."
                ),
            )

    if pactl_path:
        result = subprocess.run(
            [pactl_path, "info"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return LinuxAudioStackInfo(
                backend="PulseAudio",
                detail="pactl",
                settings_hint="Sink volume and device list come from PulseAudio.",
            )

    if aplay_path and list_alsa_playback_endpoints():
        return LinuxAudioStackInfo(
            backend="ALSA",
            detail="Direct hardware (aplay)",
            settings_hint=(
                "PipeWire/Pulse are not active. Tunes routes mpv to the selected "
                "ALSA device; use software volume if the slider has no effect."
            ),
        )

    return LinuxAudioStackInfo(
        backend="mpv default",
        detail="No PipeWire, Pulse, or ALSA device list",
        settings_hint=(
            "Install and start pipewire + wireplumber, or ensure ALSA sees a "
            "playback card (aplay -l). Playback may still work via mpv's default path."
        ),
    )


def alsa_card_description(card: int) -> str:
    """Return the long or short name for an ALSA card number."""
    for entry in _parse_aplay_playback_devices():
        if entry[0] == card:
            card_long = entry[2]
            card_name = entry[1]
            return (card_long or card_name).strip()
    return ""


def list_alsa_playback_endpoints() -> list[tuple[str, str, str]]:
    """Return (endpoint_id, mpv_device, description) for each ALSA playback device."""
    devices = _parse_aplay_playback_devices()
    return [
        (
            f"alsa:hw:{card}:{device}",
            f"hw:{card},{device}",
            _alsa_description(card_name, card_long, device_name, device_long),
        )
        for card, card_name, card_long, device, device_name, device_long in devices
    ]


def _alsa_description(
    card_name: str,
    card_long: str | None,
    device_name: str,
    device_long: str | None,
) -> str:
    card = (card_long or card_name).strip()
    dev = (device_long or device_name).strip()
    return f"{card} — {dev}"


def _parse_aplay_playback_devices() -> list[tuple[int, str, str | None, int, str, str | None]]:
    if shutil.which("aplay") is None:
        return []
    result = subprocess.run(
        ["aplay", "-l"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    devices: list[tuple[int, str, str | None, int, str, str | None]] = []
    for line in result.stdout.splitlines():
        match = _APLAY_CARD.match(line.strip())
        if match is None:
            continue
        card = int(match.group(1))
        card_name = match.group(2).strip()
        card_long = match.group(3)
        device = int(match.group(4))
        device_name = match.group(5).strip()
        device_long = match.group(6)
        devices.append((card, card_name, card_long, device, device_name, device_long))
    return devices
