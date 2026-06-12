"""ALSA Use Case Manager lookup for playback mixer elements."""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass

_ALSAUCM_TIMEOUT_SEC = 3.0
_UNSUPPORTED = "UCM is not supported"
_UCM_CACHE: dict[int, UcmVolumeHint | None] = {}


@dataclass(frozen=True)
class UcmVolumeHint:
    mixer_elem: str | None
    master_type_soft: bool


def clear_ucm_cache() -> None:
    _UCM_CACHE.clear()


def discover_ucm_volume_hint(card: int) -> UcmVolumeHint | None:
    """Return UCM playback mixer hint, or None when UCM does not apply."""
    if card in _UCM_CACHE:
        return _UCM_CACHE[card]
    hint = _discover_ucm_volume_hint(card)
    _UCM_CACHE[card] = hint
    return hint


def _discover_ucm_volume_hint(card: int) -> UcmVolumeHint | None:
    if shutil.which("alsaucm") is None:
        return None
    card_id = f"hw:{card}"
    dump = _run_alsaucm(card_id, "dump", "text")
    if dump is None or _UNSUPPORTED in dump:
        return None
    if "HiFi" not in dump and "hifi" not in dump.casefold():
        return None
    if _run_alsaucm(card_id, "set", "_verb", "HiFi") is None:
        return None
    devices = _list_ucm_devices(card_id, dump)
    if not devices:
        devices = ["Speaker", "Headphones", "Line", "Line Out", "LineOut"]
    mixer_elem: str | None = None
    master_type_soft = False
    for device in devices:
        elem = _ucm_get(card_id, device, "PlaybackMixerElem")
        master_type = _ucm_get(card_id, device, "PlaybackMasterType")
        if master_type and master_type.casefold() == "soft":
            master_type_soft = True
            continue
        if elem:
            mixer_elem = elem
            break
    if master_type_soft and mixer_elem is None:
        return UcmVolumeHint(mixer_elem=None, master_type_soft=True)
    if mixer_elem is None:
        elem = _ucm_get(card_id, "", "PlaybackMixerElem")
        if elem:
            mixer_elem = elem
        master = _ucm_get(card_id, "", "PlaybackMasterType")
        if master and master.casefold() == "soft":
            master_type_soft = True
    if master_type_soft and not mixer_elem:
        return UcmVolumeHint(mixer_elem=None, master_type_soft=True)
    if not mixer_elem:
        return None
    return UcmVolumeHint(mixer_elem=mixer_elem, master_type_soft=master_type_soft)


def _list_ucm_devices(card_id: str, dump: str) -> list[str]:
    devices: list[str] = []
    for line in dump.splitlines():
        match = re.match(r"^\s*(\S+)\s+/\s*device:", line)
        if match is not None:
            name = match.group(1)
            if name not in devices:
                devices.append(name)
    listed = _run_alsaucm(card_id, "list", "_devs")
    if listed:
        for token in listed.split():
            if token not in devices and not token.startswith("_"):
                devices.append(token)
    return devices


def _ucm_get(card_id: str, device: str, variable: str) -> str | None:
    args = ["get", variable] if not device else ["get", f"{device}/{variable}"]
    result = _run_alsaucm(card_id, *args)
    if result is None:
        return None
    value = result.strip()
    return value or None


def _run_alsaucm(card_id: str, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["alsaucm", "-c", card_id, *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=_ALSAUCM_TIMEOUT_SEC,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout
