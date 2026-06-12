"""Three-tier ALSA hardware volume discovery (quirks, UCM, heuristics)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from tunes_player.platform.linux.alsa_mixer import (
    AlsaVolumeControl,
    alsa_card_is_usb,
    alsa_pcm_device_is_digital_output,
    discover_alsa_heuristic_volume_control,
    lookup_mixer_control_by_name,
)
from tunes_player.platform.linux.volume_quirks import CardIdentity, match_quirk
from tunes_player.platform.linux.volume_ucm import discover_ucm_volume_hint

log = logging.getLogger(__name__)

DiscoverySource = Literal["quirk", "ucm", "alsa", "none"]

_DISCOVERY_CACHE: dict[tuple[int, int | None, bool], VolumeDiscoveryResult] = {}


@dataclass(frozen=True)
class VolumeDiscoveryResult:
    control: AlsaVolumeControl | None
    source: DiscoverySource
    reason: str


def clear_volume_discovery_cache() -> None:
    _DISCOVERY_CACHE.clear()
    from tunes_player.platform.linux.volume_quirks import clear_quirk_cache
    from tunes_player.platform.linux.volume_ucm import clear_ucm_cache

    clear_quirk_cache()
    clear_ucm_cache()


def read_card_identity(card: int, *, device: int | None = None) -> CardIdentity:
    return CardIdentity(
        card=card,
        device=device,
        usb_id=_read_usb_id(card),
        firmware=_read_usb_firmware(card),
        long_name=_read_card_long_name(card),
    )


def discover_hardware_volume(
    card: int,
    *,
    device: int | None = None,
    verify: bool = False,
) -> VolumeDiscoveryResult:
    cache_key = (card, device, verify)
    if cache_key in _DISCOVERY_CACHE:
        return _DISCOVERY_CACHE[cache_key]
    result = _discover_hardware_volume(card, device=device, verify=verify)
    _DISCOVERY_CACHE[cache_key] = result
    if verify and result.control is not None:
        _DISCOVERY_CACHE[(card, device, False)] = result
    if result.source != "none" or result.control is not None:
        log.info(
            "hardware volume discovery card=%s device=%s verify=%s source=%s reason=%s",
            card,
            device,
            verify,
            result.source,
            result.reason,
        )
    return result


def _discover_hardware_volume(
    card: int,
    *,
    device: int | None,
    verify: bool,
) -> VolumeDiscoveryResult:
    if device is not None and alsa_pcm_device_is_digital_output(card, device):
        return VolumeDiscoveryResult(
            control=None,
            source="none",
            reason="HDMI/DP digital output has no local playback volume",
        )

    identity = read_card_identity(card, device=device)

    quirk = match_quirk(identity)
    if quirk is not None:
        if not quirk.hardware_volume:
            return VolumeDiscoveryResult(
                control=None,
                source="quirk",
                reason=(
                    "quirk table: no usable hardware volume"
                    f" (usb={identity.usb_id}, fw={identity.firmware})"
                ),
            )
        if quirk.mixer:
            control = lookup_mixer_control_by_name(card, quirk.mixer, verify=verify)
            if control is None:
                return VolumeDiscoveryResult(
                    control=None,
                    source="quirk",
                    reason=f"quirk mixer={quirk.mixer!r} not found or failed verify",
                )
            return VolumeDiscoveryResult(
                control=control,
                source="quirk",
                reason=(
                    f"quirk mixer={quirk.mixer!r}"
                    f" (usb={identity.usb_id}, fw={identity.firmware})"
                ),
            )

    ucm = discover_ucm_volume_hint(card)
    if ucm is not None:
        if ucm.master_type_soft or not ucm.mixer_elem:
            return VolumeDiscoveryResult(
                control=None,
                source="ucm",
                reason="UCM marks playback volume as software-only or absent",
            )
        control = lookup_mixer_control_by_name(card, ucm.mixer_elem, verify=verify)
        if control is None:
            return VolumeDiscoveryResult(
                control=None,
                source="ucm",
                reason=f"UCM PlaybackMixerElem={ucm.mixer_elem!r} not found or failed verify",
            )
        return VolumeDiscoveryResult(
            control=control,
            source="ucm",
            reason=f"UCM PlaybackMixerElem={ucm.mixer_elem!r}",
        )

    tier3_verify = verify
    control = discover_alsa_heuristic_volume_control(
        card,
        device=device,
        verify=tier3_verify,
    )
    if control is None:
        return VolumeDiscoveryResult(
            control=None,
            source="alsa",
            reason="ALSA heuristic discovery found no verified playback volume",
        )
    return VolumeDiscoveryResult(
        control=control,
        source="alsa",
        reason=f"ALSA heuristic control={control.scontrol!r}",
    )


def _read_usb_id(card: int) -> str | None:
    if not alsa_card_is_usb(card):
        return None
    path = Path(f"/proc/asound/card{card}/usbid")
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text.casefold() if text else None


def _read_usb_firmware(card: int) -> str | None:
    device = Path(f"/sys/class/sound/card{card}/device")
    try:
        current = device.resolve()
    except OSError:
        return None
    for _ in range(8):
        bcd = current / "bcdDevice"
        if bcd.is_file():
            try:
                return bcd.read_text(encoding="utf-8").strip()
            except OSError:
                return None
        if current.parent == current:
            break
        current = current.parent
    return None


def _read_card_long_name(card: int) -> str | None:
    try:
        text = Path("/proc/asound/cards").read_text(encoding="utf-8")
    except OSError:
        return None
    lines = text.splitlines()
    header = re.compile(rf"^\s*{card}\s+\[")
    for index, line in enumerate(lines):
        if not header.match(line):
            continue
        parts = line.split(":", 1)
        name = parts[1].strip() if len(parts) > 1 else ""
        if index + 1 < len(lines) and lines[index + 1][:1].isspace():
            continuation = lines[index + 1].strip()
            if continuation:
                name = f"{name} {continuation}".strip()
        return name or None
    return None
