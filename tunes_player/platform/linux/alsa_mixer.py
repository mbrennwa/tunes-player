"""ALSA mixer volume via amixer (hardware control on direct ALSA output)."""

from __future__ import annotations

import re
import shutil
import subprocess

_ALSA_HW_ID = re.compile(r"^alsa:hw:(\d+):(\d+)$")
_AMIXER_PERCENT = re.compile(r"Playback.*?\[(\d+)%\]|Mono:.*?Playback.*?\[(\d+)%\]")
_MIXER_CONTROLS = ("Master", "PCM", "Front")


def alsa_card_from_endpoint_id(endpoint_id: str) -> int | None:
    match = _ALSA_HW_ID.match(endpoint_id)
    if match is None:
        return None
    return int(match.group(1))


def alsa_mixer_control_for_card(card: int) -> str | None:
    """Return the first usable playback mixer control on this card, if any."""
    if shutil.which("amixer") is None:
        return None
    try:
        result = subprocess.run(
            ["amixer", "-c", str(card), "scontrols"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    available = set()
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line.startswith("Simple mixer control"):
            continue
        name = line.split("'", 2)[1] if "'" in line else ""
        if name:
            available.add(name)
    for control in _MIXER_CONTROLS:
        if control not in available:
            continue
        try:
            subprocess.run(
                ["amixer", "-c", str(card), "get", control],
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError):
            continue
        if _control_has_playback_volume(card, control):
            return control
    return None


def alsa_mixer_available(card: int) -> bool:
    return alsa_mixer_control_for_card(card) is not None


def alsa_get_level(card: int) -> float:
    control = alsa_mixer_control_for_card(card)
    if control is None:
        return 0.72
    try:
        result = subprocess.run(
            ["amixer", "-c", str(card), "get", control],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return 0.72
    for line in result.stdout.splitlines():
        if "Playback" not in line and "Mono:" not in line:
            continue
        match = _AMIXER_PERCENT.search(line)
        if match is not None:
            percent = match.group(1) or match.group(2)
            if percent is not None:
                return max(0.0, min(1.0, int(percent) / 100))
    return 0.72


def alsa_set_level(card: int, level: float) -> None:
    control = alsa_mixer_control_for_card(card)
    if control is None:
        return
    percent = int(round(max(0.0, min(1.0, level)) * 100))
    try:
        subprocess.run(
            ["amixer", "-c", str(card), "set", control, f"{percent}%"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return


def _control_has_playback_volume(card: int, control: str) -> bool:
    try:
        result = subprocess.run(
            ["amixer", "-c", str(card), "get", control],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return False
    return "pvolume" in result.stdout or "Playback" in result.stdout
