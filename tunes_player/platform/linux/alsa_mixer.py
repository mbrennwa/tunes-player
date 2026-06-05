"""ALSA mixer volume via amixer (hardware control on direct ALSA output)."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

_ALSA_HW_ID = re.compile(r"^alsa:hw:(\d+):(\d+)$")
_AMIXER_PERCENT = re.compile(r"Playback.*?\[(\d+)%\]|Mono:.*?Playback.*?\[(\d+)%\]")
_AMIXER_LIMITS = re.compile(r"Limits:\s*(?:Playback\s+)?(\d+)\s*-\s*(\d+)", re.IGNORECASE)
_MIXER_CONTROLS = ("Master", "PCM", "Front")
_ADJUSTABLE_CACHE: dict[int, bool] = {}


def _available_mixer_controls(card: int) -> set[str]:
    if shutil.which("amixer") is None:
        return set()
    try:
        result = subprocess.run(
            ["amixer", "-c", str(card), "scontrols"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return set()
    available: set[str] = set()
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line.startswith("Simple mixer control"):
            continue
        name = line.split("'", 2)[1] if "'" in line else ""
        if name:
            available.add(name)
    return available


def alsa_card_is_usb(card: int) -> bool:
    """True when the kernel exposes this ALSA card as a USB audio device."""
    usbid_path = Path(f"/proc/asound/card{card}/usbid")
    try:
        if usbid_path.read_text().strip():
            return True
    except OSError:
        pass
    device_path = Path(f"/sys/class/sound/card{card}/device")
    try:
        return "/usb" in device_path.resolve().as_posix().casefold()
    except OSError:
        return False


def _likely_fixed_usb_dac(card: int) -> bool:
    """USB DACs with PCM-only mixers are usually fixed-output for bit-perfect use."""
    if not alsa_card_is_usb(card):
        return False
    controls = _available_mixer_controls(card)
    if "Master" in controls or "Front" in controls:
        return False
    return "PCM" in controls


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


def clear_alsa_mixer_cache() -> None:
    _ADJUSTABLE_CACHE.clear()


def alsa_mixer_available(card: int) -> bool:
    return alsa_mixer_control_for_card(card) is not None


def alsa_mixer_adjustable(card: int) -> bool:
    """True when amixer can read and write a playback level on this card."""
    cached = _ADJUSTABLE_CACHE.get(card)
    if cached is not None:
        return cached
    if _likely_fixed_usb_dac(card):
        _ADJUSTABLE_CACHE[card] = False
        return False
    if not alsa_mixer_available(card):
        _ADJUSTABLE_CACHE[card] = False
        return False
    before = alsa_get_level(card)
    if before >= 0.55:
        target = max(0.0, before - 0.2)
    else:
        target = min(1.0, before + 0.2)
    if abs(target - before) < 0.05:
        target = 0.35 if before >= 0.5 else 0.85
    alsa_set_level(card, target)
    after = alsa_get_level(card)
    alsa_set_level(card, before)
    adjustable = abs(after - before) >= 0.05 and abs(after - target) <= 0.1
    _ADJUSTABLE_CACHE[card] = adjustable
    return adjustable


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
    output = result.stdout
    if not any(
        "pvolume" in line and line.strip().startswith("Capabilities:")
        for line in output.splitlines()
    ):
        return False
    match = _AMIXER_LIMITS.search(output)
    if match is not None:
        low, high = int(match.group(1)), int(match.group(2))
        if high <= low:
            return False
    return True
