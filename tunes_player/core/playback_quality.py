"""Human-readable playback format labels for the Now Playing bar."""

from __future__ import annotations

from tunes_player.core.library.store import FileMetadata

_LOSSLESS_CODECS = frozenset({"flac", "alac", "wav", "aiff", "aif"})

_QOBUZ_FORMAT_LABELS: dict[int, str] = {
    5: "MP3 320",
    6: "16-bit / 44.1 kHz",
    7: "24-bit / 96 kHz",
    27: "24-bit / 192 kHz",
}

_TIDAL_QUALITY_LABELS: dict[str, str] = {
    "LOW": "96 kbps",
    "HIGH": "320 kbps",
    "LOSSLESS": "16-bit / 44.1 kHz lossless",
    "HI_RES": "24-bit / 96 kHz lossless",
    "HI_RES_LOSSLESS": "24-bit / 96 kHz lossless",
}

_LOSSLESS_TIDAL_QUALITIES = frozenset({"LOSSLESS", "HI_RES", "HI_RES_LOSSLESS"})

_LOSSY_CODEC_LABELS: dict[str, str] = {
    "mp3": "MP3",
    "aac": "AAC",
    "vorbis": "Vorbis",
    "ogg": "Vorbis",
}


def _format_sample_rate_hz(hz: int) -> str:
    khz = hz / 1000
    if abs(khz - round(khz)) < 0.01:
        return f"{int(round(khz))} kHz"
    return f"{khz:g} kHz"


def format_rate_label(hz: int) -> str:
    """Short rate label for resample notes (e.g. 192 kHz)."""
    return _format_sample_rate_hz(hz)


def format_playback_status(
    format_label: str,
    *,
    playback_note: str | None = None,
) -> str:
    """Combine file format line with honest playback path note."""
    if not playback_note:
        return format_label
    return f"{format_label} · {playback_note}"


def local_file_format_label(metadata: FileMetadata | None) -> str:
    """Lossless: bit-depth / sample-rate; lossy: compression codec (e.g. MP3)."""
    if metadata is None:
        return "Unknown format"
    codec = (metadata.codec or "").casefold()
    if _is_lossless_local(codec, metadata):
        parts: list[str] = []
        if metadata.bit_depth:
            parts.append(f"{metadata.bit_depth}-bit")
        if metadata.sample_rate:
            parts.append(_format_sample_rate_hz(metadata.sample_rate))
        if parts:
            return " / ".join(parts)
        return (metadata.codec or "Lossless").upper()
    return _lossy_codec_label(codec)


def qobuz_stream_format_label(format_id: int) -> str:
    return _QOBUZ_FORMAT_LABELS.get(format_id, f"Qobuz format {format_id}")


def _normalize_qobuz_sample_rate_hz(value: int | float | str | None) -> int | None:
    if value is None:
        return None
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return None
    if rate <= 0:
        return None
    if rate < 1000:
        return int(round(rate * 1000))
    return int(round(rate))


def qobuz_format_label_from_bit_depth_sample_rate(
    *,
    bit_depth: int | float | str | None,
    sample_rate_hz: int | float | str | None,
) -> str | None:
    """Human-readable label from Qobuz bit depth and sample rate (Hz or kHz)."""
    try:
        depth = int(bit_depth) if bit_depth is not None else None
    except (TypeError, ValueError):
        depth = None
    rate_hz = _normalize_qobuz_sample_rate_hz(sample_rate_hz)
    if depth is None or rate_hz is None:
        return None
    return f"{depth}-bit / {_format_sample_rate_hz(rate_hz)}"


def qobuz_format_label_from_stream(
    stream: dict,
    *,
    fallback_format_id: int | None = None,
) -> str:
    """Label from track/getFileUrl response (actual stream), not the format ceiling."""
    label = qobuz_format_label_from_bit_depth_sample_rate(
        bit_depth=stream.get("bit_depth"),
        sample_rate_hz=stream.get("sampling_rate"),
    )
    if label is not None:
        return label
    if fallback_format_id is not None:
        return qobuz_stream_format_label(fallback_format_id)
    return "Unknown format"


def _normalize_tidal_quality_key(quality: str | None) -> str:
    if not quality:
        return ""
    key = str(quality).strip().upper()
    if "." in key:
        key = key.split(".")[-1]
    return key.replace("AUDIOQUALITY.", "")


def tidal_format_label(
    *,
    audio_quality: str | None = None,
    bit_depth: int | None = None,
    sample_rate: int | None = None,
) -> str:
    """Label from TIDAL stream or track metadata (preferred over session default)."""
    key = _normalize_tidal_quality_key(audio_quality)
    if key in _LOSSLESS_TIDAL_QUALITIES and bit_depth and sample_rate:
        return (
            f"{bit_depth}-bit / {_format_sample_rate_hz(sample_rate)} lossless"
        )
    if key:
        return _TIDAL_QUALITY_LABELS.get(key, key)
    return "Unknown format"


def tidal_format_label_from_stream_payload(payload: dict) -> str:
    bit_depth = payload.get("bitDepth")
    sample_rate = payload.get("sampleRate")
    return tidal_format_label(
        audio_quality=payload.get("audioQuality"),
        bit_depth=int(bit_depth) if bit_depth is not None else None,
        sample_rate=int(sample_rate) if sample_rate is not None else None,
    )


def tidal_stream_format_label(quality: str) -> str:
    return tidal_format_label(audio_quality=quality)


def _is_lossless_local(codec: str, metadata: FileMetadata) -> bool:
    if codec in _LOSSLESS_CODECS:
        return True
    if codec in _LOSSY_CODEC_LABELS:
        return False
    return metadata.bit_depth is not None and metadata.sample_rate is not None


def _lossy_codec_label(codec: str) -> str:
    if codec in _LOSSY_CODEC_LABELS:
        return _LOSSY_CODEC_LABELS[codec]
    if codec:
        return codec.upper()
    return "Lossy"
