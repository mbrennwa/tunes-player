"""ALSA mixer volume via amixer (hardware control on direct ALSA output)."""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

_ALSA_HW_ID = re.compile(r"^alsa:hw:(\d+):(\d+)$")
_CONTENTS_NUMID = re.compile(
    r"^numid=(\d+),iface=MIXER,name='([^']+)'$",
)
_CONTENTS_INTEGER = re.compile(
    r"^\s*;\s*type=INTEGER,access=([^,]+),values=(\d+),min=(\d+),max=(\d+)",
)
_DBMINMAX = re.compile(r"dBminmax-min=([-.\d]+)dB,max=([-.\d]+)dB")
_DBSCALE = re.compile(
    r"dBscale-min=([-.\d]+)dB,step=([-.\d]+)dB,mute=(\d+)",
)
_AMIXER_PERCENT = re.compile(r"Playback.*?\[(\d+)%\]|Mono:.*?Playback.*?\[(\d+)%\]")
_AMIXER_LIMITS = re.compile(r"Limits:\s*(?:Playback\s+)?(\d+)\s*-\s*(\d+)", re.IGNORECASE)
_PLAYBACK_CHANNELS = re.compile(
    r"Playback channels:\s*(.+)$",
    re.IGNORECASE,
)
_USB_MIXER_CONTROL = re.compile(r'Control: name="([^"]+)"')
_USB_MIXER_INFO = re.compile(
    r"Info:.*channels=(\d+).*cmask=0x([0-9a-f]+)",
    re.IGNORECASE,
)
_DIGITAL_PCM_MARKERS = ("hdmi", "displayport", "iec958", "spdif")

_AMIXER_TIMEOUT_SEC = 2.0
_VOLUME_CONTROL_CACHE: dict[tuple[int, int | None, bool], AlsaVolumeControl | None] = {}


@dataclass(frozen=True)
class AlsaVolumeControl:
    card: int
    numid: int
    scontrol: str
    min_val: int
    max_val: int


@dataclass(frozen=True)
class _MixerCandidate:
    numid: int
    element_name: str
    scontrol: str
    min_val: int
    max_val: int
    db_range: float
    playback_channels: int
    joined_volume: bool


@dataclass(frozen=True)
class _ControlDetails:
    playback_channels: int
    db_range: float
    joined_volume: bool


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


def alsa_card_from_endpoint_id(endpoint_id: str) -> int | None:
    match = _ALSA_HW_ID.match(endpoint_id)
    if match is None:
        return None
    return int(match.group(1))


def alsa_device_from_endpoint_id(endpoint_id: str) -> int | None:
    match = _ALSA_HW_ID.match(endpoint_id)
    if match is None:
        return None
    return int(match.group(2))


def clear_alsa_mixer_cache() -> None:
    _VOLUME_CONTROL_CACHE.clear()


def discover_output_volume_control(
    card: int,
    *,
    device: int | None = None,
    verify: bool = False,
) -> AlsaVolumeControl | None:
    """Return the best main output volume control for this card/device, if any."""
    cache_key = (card, device, verify)
    if cache_key in _VOLUME_CONTROL_CACHE:
        return _VOLUME_CONTROL_CACHE[cache_key]
    discovered = _discover_output_volume_control(card, device=device, verify=verify)
    _VOLUME_CONTROL_CACHE[cache_key] = discovered
    return discovered


def discover_output_volume_control_for_endpoint(
    endpoint_id: str,
    *,
    verify: bool = False,
) -> AlsaVolumeControl | None:
    card = alsa_card_from_endpoint_id(endpoint_id)
    device = alsa_device_from_endpoint_id(endpoint_id)
    if card is None or device is None:
        return None
    return discover_output_volume_control(card, device=device, verify=verify)


def alsa_mixer_control_for_endpoint(endpoint_id: str) -> str | None:
    control = discover_output_volume_control_for_endpoint(endpoint_id)
    if control is None:
        return None
    return control.scontrol


def alsa_mixer_control_for_card(card: int) -> str | None:
    control = discover_output_volume_control(card)
    if control is None:
        return None
    return control.scontrol


def alsa_mixer_available_for_endpoint(endpoint_id: str) -> bool:
    return discover_output_volume_control_for_endpoint(endpoint_id) is not None


def alsa_mixer_available(card: int) -> bool:
    return discover_output_volume_control(card) is not None


def alsa_mixer_adjustable_for_endpoint(endpoint_id: str) -> bool:
    return (
        discover_output_volume_control_for_endpoint(endpoint_id, verify=True)
        is not None
    )


def alsa_mixer_adjustable(card: int) -> bool:
    return discover_output_volume_control(card, verify=True) is not None


def alsa_get_level_for_endpoint(endpoint_id: str) -> float:
    control = discover_output_volume_control_for_endpoint(endpoint_id)
    if control is None:
        return 0.72
    return _read_normalized_level(control.card, control)


def alsa_set_level_for_endpoint(endpoint_id: str, level: float) -> None:
    control = discover_output_volume_control_for_endpoint(endpoint_id)
    if control is None:
        return
    _write_normalized_level(control.card, control, max(0.0, min(1.0, level)))


def alsa_get_level(card: int) -> float:
    control = discover_output_volume_control(card)
    if control is None:
        return 0.72
    return _read_normalized_level(card, control)


def alsa_set_level(card: int, level: float) -> None:
    control = discover_output_volume_control(card)
    if control is None:
        return
    _write_normalized_level(card, control, max(0.0, min(1.0, level)))


def alsa_pcm_device_is_digital_output(card: int, device: int) -> bool:
    from tunes_player.platform.linux.audio_probe import _parse_aplay_playback_devices

    for entry in _parse_aplay_playback_devices():
        if entry[0] != card or entry[3] != device:
            continue
        device_name = (entry[5] or entry[4] or "").casefold()
        card_name = (entry[2] or entry[1] or "").casefold()
        label = f"{card_name} {device_name}"
        return any(marker in label for marker in _DIGITAL_PCM_MARKERS)
    return False


def _run_amixer(card: int, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["amixer", "-c", str(card), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=_AMIXER_TIMEOUT_SEC,
    )


def _discover_output_volume_control(
    card: int,
    *,
    device: int | None,
    verify: bool,
) -> AlsaVolumeControl | None:
    if device is not None and alsa_pcm_device_is_digital_output(card, device):
        return None
    if shutil.which("amixer") is None:
        return None
    candidates = _list_mixer_candidates(card)
    if not candidates:
        return None
    usb_channels = _usb_mixer_channel_map(card)
    ranked = sorted(
        candidates,
        key=lambda item: _candidate_score(item, usb_channels),
        reverse=True,
    )
    for candidate in ranked:
        control = AlsaVolumeControl(
            card=card,
            numid=candidate.numid,
            scontrol=candidate.scontrol,
            min_val=candidate.min_val,
            max_val=candidate.max_val,
        )
        if not verify:
            return control
        if _verify_control(card, control):
            return control
    return None


def _list_mixer_candidates(card: int) -> list[_MixerCandidate]:
    try:
        result = _run_amixer(card, "contents")
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return []

    candidates: list[_MixerCandidate] = []
    current_numid: int | None = None
    current_name: str | None = None

    for line in result.stdout.splitlines():
        header = _CONTENTS_NUMID.match(line.strip())
        if header is not None:
            current_numid = int(header.group(1))
            current_name = header.group(2)
            continue
        if current_numid is None or current_name is None:
            continue
        integer = _CONTENTS_INTEGER.match(line)
        if integer is None:
            continue
        access, _values, min_raw, max_raw = integer.groups()
        if "w" not in access:
            current_numid = None
            current_name = None
            continue
        min_val = int(min_raw)
        max_val = int(max_raw)
        if max_val <= min_val:
            current_numid = None
            current_name = None
            continue
        if not current_name.endswith(" Playback Volume"):
            current_numid = None
            current_name = None
            continue
        if current_name.endswith(" Capture Volume"):
            current_numid = None
            current_name = None
            continue

        scontrol = current_name[: -len(" Playback Volume")]
        details = _control_details(card, scontrol)
        if details is None:
            current_numid = None
            current_name = None
            continue

        candidates.append(
            _MixerCandidate(
                numid=current_numid,
                element_name=current_name,
                scontrol=scontrol,
                min_val=min_val,
                max_val=max_val,
                db_range=details.db_range,
                playback_channels=details.playback_channels,
                joined_volume=details.joined_volume,
            )
        )
        current_numid = None
        current_name = None

    return candidates


def _control_details(card: int, scontrol: str) -> _ControlDetails | None:
    try:
        result = _run_amixer(card, "get", scontrol)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    output = result.stdout
    capabilities = ""
    for line in output.splitlines():
        if line.strip().startswith("Capabilities:"):
            capabilities = line.casefold()
            break
    if "pvolume" not in capabilities:
        return None
    if "cvolume" in capabilities:
        return None
    match = _AMIXER_LIMITS.search(output)
    if match is not None:
        low, high = int(match.group(1)), int(match.group(2))
        if high <= low:
            return None
    playback_channels = 1
    for line in output.splitlines():
        channel_match = _PLAYBACK_CHANNELS.match(line.strip())
        if channel_match is None:
            continue
        channels = channel_match.group(1).casefold()
        if "front left" in channels and "front right" in channels:
            playback_channels = 2
        break
    db_range = _db_range_from_get_output(output)
    if db_range <= 0 and match is not None:
        db_range = float(int(match.group(2)) - int(match.group(1)))
    return _ControlDetails(
        playback_channels=playback_channels,
        db_range=db_range,
        joined_volume="pvolume-joined" in capabilities,
    )


def _db_range_from_get_output(output: str) -> float:
    for line in output.splitlines():
        minmax = _DBMINMAX.search(line)
        if minmax is not None:
            return abs(float(minmax.group(2)) - float(minmax.group(1)))
        scale = _DBSCALE.search(line)
        if scale is not None:
            low = float(scale.group(1))
            step = float(scale.group(2))
            match = _AMIXER_LIMITS.search(output)
            if match is not None:
                steps = int(match.group(2)) - int(match.group(1))
                return abs(steps * step) if steps > 0 else abs(low)
            return abs(low)
    return 0.0


def _usb_mixer_channel_map(card: int) -> dict[str, int]:
    path = Path(f"/proc/asound/card{card}/usbmixer")
    try:
        text = path.read_text()
    except OSError:
        return {}

    channels: dict[str, int] = {}
    pending_name: str | None = None
    for line in text.splitlines():
        control_match = _USB_MIXER_CONTROL.search(line)
        if control_match is not None:
            pending_name = control_match.group(1)
            continue
        if pending_name is None:
            continue
        info_match = _USB_MIXER_INFO.search(line)
        if info_match is not None:
            channel_count = int(info_match.group(1))
            channel_mask = int(info_match.group(2), 16)
            channels[pending_name] = max(channel_count, channel_mask.bit_count())
            pending_name = None
    return channels


def _candidate_score(
    candidate: _MixerCandidate,
    usb_channels: dict[str, int],
) -> tuple[int, int, float, int]:
    usb_channel_count = usb_channels.get(candidate.element_name, 0)
    stereo_bonus = (
        1 if max(candidate.playback_channels, usb_channel_count) >= 2 else 0
    )
    joined_bonus = 1 if candidate.joined_volume else 0
    return (
        joined_bonus,
        stereo_bonus,
        candidate.db_range,
        candidate.max_val - candidate.min_val,
    )


def _verify_control(card: int, control: AlsaVolumeControl) -> bool:
    before = _read_normalized_level(card, control)
    if before >= 0.55:
        target = max(0.0, before - 0.2)
    else:
        target = min(1.0, before + 0.2)
    if abs(target - before) < 0.05:
        target = 0.35 if before >= 0.5 else 0.85
    _write_normalized_level(card, control, target)
    after = _read_normalized_level(card, control)
    _write_normalized_level(card, control, before)
    return abs(after - before) >= 0.05 and abs(after - target) <= 0.1


def _read_normalized_level(card: int, control: AlsaVolumeControl) -> float:
    try:
        result = _run_amixer(card, "get", control.scontrol)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return 0.72
    percents: list[int] = []
    for line in result.stdout.splitlines():
        if "Playback" not in line and "Mono:" not in line:
            continue
        match = _AMIXER_PERCENT.search(line)
        if match is not None:
            percent = match.group(1) or match.group(2)
            if percent is not None:
                percents.append(int(percent))
    if percents:
        return max(0.0, min(1.0, sum(percents) / len(percents) / 100))
    return _level_from_raw_values(result.stdout, control)


def _level_from_raw_values(output: str, control: AlsaVolumeControl) -> float:
    values: list[int] = []
    for line in output.splitlines():
        if "Playback" not in line and "Mono:" not in line:
            continue
        match = re.search(r"Playback\s+(\d+)", line)
        if match is not None:
            values.append(int(match.group(1)))
    if not values:
        return 0.72
    span = control.max_val - control.min_val
    if span <= 0:
        return 0.72
    normalized = (sum(values) / len(values) - control.min_val) / span
    return max(0.0, min(1.0, normalized))


def _write_normalized_level(card: int, control: AlsaVolumeControl, level: float) -> None:
    span = control.max_val - control.min_val
    step = control.min_val + int(round(level * span))
    step = max(control.min_val, min(control.max_val, step))
    try:
        _run_amixer(card, "set", control.scontrol, str(step))
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return
