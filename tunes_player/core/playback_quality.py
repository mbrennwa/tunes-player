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


def _compact_rate_khz(hz: int) -> str:
    """Sample rate for grid tiles (e.g. 44.1, 96, 192)."""
    khz = hz / 1000
    if abs(khz - round(khz)) < 0.01:
        return str(int(round(khz)))
    return f"{khz:g}"


def format_rate_bit_depth_compact(
    *,
    bit_depth: int | float | str | None,
    sample_rate_hz: int | float | str | None,
    quality_tier: str = "",
) -> str | None:
    """Compact catalog tile label: rate kHz / bit depth (e.g. 44.1/16, 192/24)."""
    from tunes_player.core.release_quality import (
        QUALITY_FILTER_CD,
        QUALITY_FILTER_HI_RES,
        is_acoustic_hi_res,
    )

    try:
        depth = int(bit_depth) if bit_depth is not None else None
    except (TypeError, ValueError):
        depth = None
    if depth is not None and depth <= 0:
        depth = None
    rate_hz = _normalize_qobuz_sample_rate_hz(sample_rate_hz)
    if rate_hz is not None and rate_hz <= 0:
        rate_hz = None

    if rate_hz and depth is None:
        if quality_tier == QUALITY_FILTER_CD:
            depth = 16
        elif quality_tier == QUALITY_FILTER_HI_RES or is_acoustic_hi_res(rate_hz):
            depth = 24
        else:
            depth = 16
    if depth and rate_hz is None:
        if quality_tier == QUALITY_FILTER_CD:
            rate_hz = 44_100
        elif quality_tier == QUALITY_FILTER_HI_RES:
            rate_hz = 96_000

    if depth is None or rate_hz is None:
        return None
    return f"{_compact_rate_khz(rate_hz)}/{depth}"


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


def stream_file_metadata(
    *,
    bit_depth: int | float | str | None = None,
    sample_rate_hz: int | float | str | None = None,
    channels: int | None = 2,
) -> FileMetadata | None:
    """Build output-profile metadata from a streaming API payload."""
    try:
        depth = int(bit_depth) if bit_depth is not None else None
    except (TypeError, ValueError):
        depth = None
    rate_hz = _normalize_qobuz_sample_rate_hz(sample_rate_hz)
    if depth is None and rate_hz is None:
        return None
    return FileMetadata(
        path="",
        codec="flac",
        duration_sec=None,
        sample_rate=rate_hz,
        bit_depth=depth,
        channels=channels or 2,
    )


def qobuz_stream_file_metadata(stream: dict) -> FileMetadata | None:
    return stream_file_metadata(
        bit_depth=stream.get("bit_depth"),
        sample_rate_hz=stream.get("sampling_rate"),
    )


def tidal_stream_file_metadata(payload: dict) -> FileMetadata | None:
    return stream_file_metadata(
        bit_depth=payload.get("bitDepth"),
        sample_rate_hz=payload.get("sampleRate"),
    )


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


def lossy_codec_label(codec: str) -> str:
    normalized = codec.casefold()
    if normalized in _LOSSY_CODEC_LABELS:
        return _LOSSY_CODEC_LABELS[normalized]
    if codec:
        return codec.upper()
    return "Lossy"


def _lossy_codec_label(codec: str) -> str:
    return lossy_codec_label(codec)


def catalog_tile_quality_label(
    *,
    bit_depth: int | None,
    sample_rate_hz: int | None,
    quality_tier: str = "",
    source: object | None = None,
    lossy_codec: str | None = None,
) -> str | None:
    """Grid tile quality: rate/depth for lossless, codec name for compressed."""
    from tunes_player.core.models import Source
    from tunes_player.core.release_quality import (
        QUALITY_FILTER_CD,
        QUALITY_FILTER_COMPRESSED,
        _VALID_QUALITY_FILTERS,
    )

    tier = quality_tier if quality_tier in _VALID_QUALITY_FILTERS else ""
    if tier == QUALITY_FILTER_COMPRESSED:
        if lossy_codec:
            return lossy_codec_label(lossy_codec)
        if source == Source.TIDAL:
            return "AAC"
        return "MP3"

    label = format_rate_bit_depth_compact(
        bit_depth=bit_depth,
        sample_rate_hz=sample_rate_hz,
        quality_tier=tier,
    )
    if label:
        return label
    if tier == QUALITY_FILTER_CD:
        return "44.1/16"
    return None
