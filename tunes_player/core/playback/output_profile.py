"""Playback output profile — format matching and bit-perfect path semantics."""

from __future__ import annotations

from dataclasses import dataclass

from tunes_player.core.library.store import FileMetadata
from tunes_player.core.volume import is_alsa_endpoint_id


@dataclass(frozen=True, slots=True)
class HwAudioCaps:
    """Supported PCM parameters from ALSA codec probe."""

    sample_rates: tuple[int, ...]
    bit_depths: tuple[int, ...]
    max_channels: int = 2


@dataclass(frozen=True, slots=True)
class PlaybackOutputProfile:
    """mpv ALSA output parameters for one track load."""

    direct_alsa: bool
    use_exclusive: bool
    allow_resample: bool
    target_rate: int | None = None
    target_bit_depth: int | None = None
    target_channels: int | None = None
    audio_format: str | None = None  # mpv audio-format, e.g. s24le

    @property
    def unity_gain(self) -> bool:
        """No mpv soft volume — device/sink or fixed output."""
        return self.direct_alsa or not self.allow_resample


@dataclass(frozen=True, slots=True)
class PlaybackPathInfo:
    """Honest playback path status for UI."""

    bit_perfect_playback: bool
    playback_note: str | None = None


def bit_depth_to_mpv_format(bit_depth: int) -> str:
    if bit_depth <= 16:
        return "s16le"
    if bit_depth <= 24:
        return "s24le"
    return "s32le"


def choose_output_format(
    *,
    file_rate: int | None,
    file_bits: int | None,
    file_channels: int | None,
    caps: HwAudioCaps | None,
) -> tuple[int | None, int | None, int | None, bool]:
    """Return (rate, bit_depth, channels, resampled)."""
    rate = file_rate
    bits = file_bits
    channels = file_channels or 2
    if caps is None or rate is None:
        return rate, bits, channels, False

    resampled = False
    if rate not in caps.sample_rates:
        lower = [r for r in caps.sample_rates if r <= rate]
        if lower:
            rate = max(lower)
        elif caps.sample_rates:
            rate = min(caps.sample_rates, key=lambda r: abs(r - rate))
        resampled = True

    if bits is not None and bits not in caps.bit_depths:
        lower_bits = [b for b in caps.bit_depths if b <= bits]
        if lower_bits:
            bits = max(lower_bits)
        elif caps.bit_depths:
            bits = max(caps.bit_depths)
        resampled = True

    channels = min(channels, caps.max_channels)
    return rate, bits, channels, resampled


def compute_output_profile(
    *,
    file_meta: FileMetadata | None,
    hw_caps: HwAudioCaps | None,
    endpoint_id: str | None,
    exclusive_enabled: bool,
    device_volume: bool,
    mpv_soft_volume: bool,
) -> tuple[PlaybackOutputProfile, PlaybackPathInfo]:
    """Build mpv profile and UI path info for the active endpoint and file."""
    direct_alsa = is_alsa_endpoint_id(endpoint_id)
    if not direct_alsa:
        profile = PlaybackOutputProfile(
            direct_alsa=False,
            use_exclusive=False,
            allow_resample=True,
        )
        note = "via PipeWire" if endpoint_id and not endpoint_id.startswith("alsa:") else None
        return profile, PlaybackPathInfo(
            bit_perfect_playback=False,
            playback_note=note,
        )

    file_rate = file_meta.sample_rate if file_meta else None
    file_bits = file_meta.bit_depth if file_meta else None
    file_channels = file_meta.channels if file_meta else None

    rate, bits, channels, resampled = choose_output_format(
        file_rate=file_rate,
        file_bits=file_bits,
        file_channels=file_channels,
        caps=hw_caps,
    )

    allow_resample = resampled
    audio_format = bit_depth_to_mpv_format(bits) if bits else None

    profile = PlaybackOutputProfile(
        direct_alsa=True,
        use_exclusive=exclusive_enabled,
        allow_resample=allow_resample,
        target_rate=rate,
        target_bit_depth=bits,
        target_channels=channels,
        audio_format=audio_format,
    )

    playback_note: str | None = None
    if resampled and file_rate and rate and file_rate != rate:
        from tunes_player.core.playback_quality import format_rate_label

        playback_note = f"resampling {format_rate_label(file_rate)} → {format_rate_label(rate)}"

    unity_path = not mpv_soft_volume and device_volume
    bit_perfect = (
        unity_path
        and not resampled
        and file_rate is not None
        and rate == file_rate
        and (file_bits is None or bits == file_bits)
    )

    if bit_perfect:
        playback_note = "bit-perfect playback"
    elif playback_note is None and not exclusive_enabled:
        playback_note = "shared device"

    return profile, PlaybackPathInfo(
        bit_perfect_playback=bit_perfect,
        playback_note=playback_note,
    )
