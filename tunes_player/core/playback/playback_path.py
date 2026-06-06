"""Derive Now Playing path labels from negotiated mpv output state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, TYPE_CHECKING

from tunes_player.core.playback.output_profile import (
    PlaybackOutputProfile,
    PlaybackPathInfo,
    bit_depth_to_mpv_format,
)
from tunes_player.core.playback_quality import format_rate_label
from tunes_player.core.volume import is_alsa_endpoint_id, is_pipewire_endpoint_id

if TYPE_CHECKING:
    from tunes_player.core.library.store import FileMetadata


@dataclass(frozen=True, slots=True)
class PlaybackPathContext:
    """Main-process context needed to interpret negotiated playback state."""

    endpoint_id: str | None
    device_volume: bool
    mpv_soft_volume: bool
    file_meta: FileMetadata | None = None


@dataclass(frozen=True, slots=True)
class NegotiatedPlaybackState:
    """Observed mpv output properties after load."""

    ao: str | None = None
    audio_device: str | None = None
    audio_samplerate: int | None = None
    audio_format: str | None = None
    alsa_resample: bool | None = None
    audio_channels: int | None = None


def _coerce_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _coerce_bool(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.casefold()
        if lowered in ("yes", "true", "1", "on"):
            return True
        if lowered in ("no", "false", "0", "off"):
            return False
    return None


def read_negotiated_playback_state(
    get_property: Callable[[str], object],
) -> NegotiatedPlaybackState:
    """Read mpv properties that describe the active output path."""
    return NegotiatedPlaybackState(
        ao=_coerce_text(get_property("ao")),
        audio_device=_coerce_text(get_property("audio-device")),
        audio_samplerate=_coerce_int(get_property("audio-samplerate")),
        audio_format=_coerce_text(get_property("audio-format")),
        alsa_resample=_coerce_bool(get_property("alsa-resample")),
        audio_channels=_coerce_int(get_property("audio-channels")),
    )


def _coerce_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def derive_playback_path_info(
    *,
    file_meta: FileMetadata | None,
    profile: PlaybackOutputProfile,
    negotiated: NegotiatedPlaybackState,
    endpoint_id: str | None,
    device_volume: bool,
    mpv_soft_volume: bool,
) -> PlaybackPathInfo:
    """Build authoritative playback path info from mpv negotiation."""
    if not profile.direct_alsa or not is_alsa_endpoint_id(endpoint_id):
        note = "via PipeWire" if is_pipewire_endpoint_id(endpoint_id) else None
        return PlaybackPathInfo(bit_perfect_playback=False, playback_note=note)

    file_rate = file_meta.sample_rate if file_meta else None
    file_bits = file_meta.bit_depth if file_meta else None
    output_rate = negotiated.audio_samplerate or profile.target_rate
    output_format = negotiated.audio_format or profile.audio_format

    resampled = profile.allow_resample
    if negotiated.alsa_resample is True:
        resampled = True
    if file_rate and output_rate and file_rate != output_rate:
        resampled = True

    playback_note: str | None = None
    if resampled and file_rate and output_rate and file_rate != output_rate:
        playback_note = (
            f"ALSA {format_rate_label(file_rate)} → {format_rate_label(output_rate)} resampling"
        )

    unity_path = not mpv_soft_volume and device_volume
    output_bits_match = True
    if file_bits is not None and output_format is not None:
        output_bits_match = bit_depth_to_mpv_format(file_bits) == output_format

    bit_perfect = (
        unity_path
        and not resampled
        and file_rate is not None
        and output_rate == file_rate
        and output_bits_match
    )

    if bit_perfect:
        playback_note = "ALSA bit-perfect"
    elif playback_note is None:
        if unity_path and not resampled:
            playback_note = "ALSA bit-perfect"
        else:
            playback_note = "ALSA"

    return PlaybackPathInfo(
        bit_perfect_playback=bit_perfect,
        playback_note=playback_note,
    )
