"""Catalog peak quality tiers for release filtering."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tunes_player.core.models import Release

QUALITY_FILTER_COMPRESSED = "compressed"
QUALITY_FILTER_CD = "cd"
QUALITY_FILTER_HI_RES = "hi_res"

_VALID_QUALITY_FILTERS = frozenset(
    {
        QUALITY_FILTER_COMPRESSED,
        QUALITY_FILTER_CD,
        QUALITY_FILTER_HI_RES,
    }
)

_TIER_RANK = {
    QUALITY_FILTER_COMPRESSED: 0,
    QUALITY_FILTER_CD: 1,
    QUALITY_FILTER_HI_RES: 2,
}

_CD_MIN_BIT_DEPTH = 16
_CD_SAMPLE_RATE_HZ = 44_100

_TIDAL_RANK_COMPRESSED_MAX = 1
_TIDAL_RANK_CD = 2


def _normalize_sample_rate_hz(value: int | float | None) -> int:
    """Normalize Hz or kHz sample-rate values to integer Hz."""
    if value is None:
        return 0
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return 0
    if rate <= 0:
        return 0
    # Qobuz API reports kHz (44.1, 96, 192); local library index uses Hz.
    if rate < 1000:
        return int(round(rate * 1000))
    return int(round(rate))


def _tier_from_lossless_bit_depth_sample_rate(
    *,
    bit_depth: int,
    sample_rate_hz: int,
) -> str:
    """CD is 44.1 kHz lossless (16- or 24-bit); anything above is hi-res."""
    if sample_rate_hz > _CD_SAMPLE_RATE_HZ:
        return QUALITY_FILTER_HI_RES
    if (
        bit_depth >= _CD_MIN_BIT_DEPTH
        and sample_rate_hz == _CD_SAMPLE_RATE_HZ
    ):
        return QUALITY_FILTER_CD
    return QUALITY_FILTER_COMPRESSED


def tiers_from_local(
    *,
    max_bit_depth: int | None,
    max_sample_rate: int | None,
    has_lossless: bool,
    has_lossy: bool,
) -> frozenset[str]:
    """Catalog quality tiers present in a local release (from file aggregates)."""
    tiers: set[str] = set()
    if has_lossy:
        tiers.add(QUALITY_FILTER_COMPRESSED)
    if has_lossless:
        tiers.add(
            _tier_from_lossless_bit_depth_sample_rate(
                bit_depth=max_bit_depth or 0,
                sample_rate_hz=max_sample_rate or 0,
            ),
        )
    elif max_bit_depth is not None and max_sample_rate is not None:
        tiers.add(
            _tier_from_lossless_bit_depth_sample_rate(
                bit_depth=max_bit_depth,
                sample_rate_hz=max_sample_rate,
            ),
        )
    if not tiers:
        tiers.add(QUALITY_FILTER_COMPRESSED)
    return frozenset(tier for tier in tiers if tier in _VALID_QUALITY_FILTERS)


def tier_from_local(
    *,
    max_bit_depth: int | None,
    max_sample_rate: int | None,
    has_lossless: bool,
    has_lossy: bool,
) -> str:
    """Peak catalog tier from per-release file aggregates."""
    tiers = tiers_from_local(
        max_bit_depth=max_bit_depth,
        max_sample_rate=max_sample_rate,
        has_lossless=has_lossless,
        has_lossy=has_lossy,
    )
    return max_quality_tier(*tiers)


def tier_from_tidal_peak(rank: int) -> str:
    """Map ``track_peak_quality()`` rank to a filter bucket."""
    if rank >= _TIDAL_RANK_CD + 1:
        return QUALITY_FILTER_HI_RES
    if rank == _TIDAL_RANK_CD:
        return QUALITY_FILTER_CD
    return QUALITY_FILTER_COMPRESSED


def tidal_album_has_quality_metadata(album: object) -> bool:
    audio_quality = getattr(album, "audio_quality", None)
    if audio_quality is not None and str(audio_quality).strip():
        return True
    tags = getattr(album, "media_metadata_tags", None)
    if tags:
        return True
    modes = getattr(album, "audio_modes", None) or []
    return bool(modes)


def tier_from_tidal_track(track: object) -> str:
    """Peak catalog tier from a TIDAL track object (no album fetch)."""
    from tunes_player.core.backends.tidal.stream_quality import track_peak_quality

    audio_quality = getattr(track, "audio_quality", None)
    if audio_quality is None or not str(audio_quality).strip():
        return ""
    return tier_from_tidal_peak(track_peak_quality(track))


def _collect_tidal_album_tier_signals(album: object) -> set[str]:
    from tunes_player.core.backends.tidal.stream_quality import track_peak_quality

    if not tidal_album_has_quality_metadata(album):
        return set()
    signals: set[str] = set()
    rank = track_peak_quality(album)
    signals.add(tier_from_tidal_peak(rank))
    audio_modes = getattr(album, "audio_modes", None) or []
    for mode in audio_modes:
        key = str(mode).upper().replace(" ", "_")
        if "HI_RES" in key or "HIRES" in key:
            signals.add(QUALITY_FILTER_HI_RES)
    return {tier for tier in signals if tier in _VALID_QUALITY_FILTERS}


def available_tiers_from_signals(signals: set[str]) -> frozenset[str]:
    """Expand raw catalog signals into available filter tiers."""
    valid = {tier for tier in signals if tier in _VALID_QUALITY_FILTERS}
    if not valid:
        return frozenset({QUALITY_FILTER_COMPRESSED})
    peak = max_quality_tier(*valid)
    return _streaming_available_tiers(peak, valid)


def tiers_from_tidal_album(album: object) -> frozenset[str]:
    """All catalog quality tiers a TIDAL album is available at."""
    signals = _collect_tidal_album_tier_signals(album)
    if not signals:
        return frozenset()
    return available_tiers_from_signals(signals)


def tier_from_tidal_album(album: object) -> str:
    """Peak catalog tier from TIDAL album metadata (no track list fetch)."""
    tiers = tiers_from_tidal_album(album)
    if not tiers:
        return ""
    return max_quality_tier(*tiers)


def _tier_from_qobuz_bit_depth_sample_rate(
    *,
    bit_depth: int | float | str | None,
    sample_rate: int | float | str | None,
) -> str:
    try:
        depth = int(bit_depth) if bit_depth is not None else 0
    except (TypeError, ValueError):
        depth = 0
    rate_hz = _normalize_sample_rate_hz(sample_rate)
    return _tier_from_lossless_bit_depth_sample_rate(
        bit_depth=depth,
        sample_rate_hz=rate_hz,
    )


def _tier_from_qobuz_technical_specifications(spec: object) -> str | None:
    if not isinstance(spec, str) or not spec.strip():
        return None
    import re

    match = re.search(
        r"(\d+)\s*(?:bit|-bit).*?(\d+(?:\.\d+)?)\s*khz",
        spec,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    return _tier_from_qobuz_bit_depth_sample_rate(
        bit_depth=match.group(1),
        sample_rate=match.group(2),
    )


def _qobuz_album_has_hires_stream(album: dict[str, Any]) -> bool:
    if album.get("hires") or album.get("hires_streamable"):
        return True
    tracks = album.get("tracks")
    if not isinstance(tracks, dict):
        return False
    for item in tracks.get("items") or []:
        if not isinstance(item, dict):
            continue
        if item.get("hires") or item.get("hires_streamable"):
            return True
    return False


def _collect_qobuz_peak_signals(album: dict[str, Any]) -> set[str]:
    """Peak catalog quality signals from Qobuz album JSON."""
    signals: set[str] = set()
    rate_hz = _normalize_sample_rate_hz(album.get("maximum_sampling_rate"))
    if (
        (album.get("hires") or album.get("hires_streamable"))
        and rate_hz > _CD_SAMPLE_RATE_HZ
    ):
        signals.add(QUALITY_FILTER_HI_RES)
    technical = _tier_from_qobuz_technical_specifications(
        album.get("maximum_technical_specifications"),
    )
    if technical is not None:
        signals.add(technical)
    signals.add(
        _tier_from_qobuz_bit_depth_sample_rate(
            bit_depth=album.get("maximum_bit_depth"),
            sample_rate=album.get("maximum_sampling_rate"),
        ),
    )
    tracks = album.get("tracks")
    if isinstance(tracks, dict):
        for item in tracks.get("items") or []:
            if not isinstance(item, dict):
                continue
            signals.update(
                _collect_qobuz_peak_signals(
                    {
                        "hires": item.get("hires"),
                        "hires_streamable": item.get("hires_streamable"),
                        "maximum_bit_depth": item.get("maximum_bit_depth"),
                        "maximum_sampling_rate": item.get("maximum_sampling_rate"),
                    },
                ),
            )
    return {tier for tier in signals if tier in _VALID_QUALITY_FILTERS}


def _collect_qobuz_available_signals(album: dict[str, Any]) -> set[str]:
    """Catalog signals including streamable hi-res below the peak sample rate."""
    signals = set(_collect_qobuz_peak_signals(album))
    if _qobuz_album_has_hires_stream(album):
        signals.add(QUALITY_FILTER_HI_RES)
    return signals


def _streaming_available_tiers(
    peak: str,
    signals: set[str],
) -> frozenset[str]:
    """Expand peak/signals into streamable tiers (lower tiers are usually available too)."""
    tiers = set(signals)
    tiers.add(peak)
    peak_rank = _TIER_RANK.get(peak, 0)
    for tier, rank in _TIER_RANK.items():
        if rank <= peak_rank:
            tiers.add(tier)
    return frozenset(
        tier for tier in tiers if tier in _VALID_QUALITY_FILTERS
    )


def tiers_from_qobuz_album(album: dict[str, Any]) -> frozenset[str]:
    """All catalog quality tiers a Qobuz album is available at."""
    return available_tiers_from_signals(_collect_qobuz_available_signals(album))


def tier_from_qobuz_album(album: dict[str, Any]) -> str:
    """Peak catalog tier from Qobuz album JSON."""
    signals = _collect_qobuz_peak_signals(album)
    if not signals:
        return QUALITY_FILTER_COMPRESSED
    return max_quality_tier(*signals)


def max_quality_tier(*tiers: str) -> str:
    """Return the highest catalog tier among the given bucket names."""
    best = QUALITY_FILTER_COMPRESSED
    best_rank = -1
    for tier in tiers:
        if tier not in _VALID_QUALITY_FILTERS:
            continue
        rank = _TIER_RANK[tier]
        if rank > best_rank:
            best_rank = rank
            best = tier
    return best


def release_available_quality_tiers(release: Release) -> frozenset[str]:
    """Tiers used for shell quality filter matching."""
    if release.available_quality_tiers:
        return release.available_quality_tiers
    tier = release.peak_quality_tier
    if tier in _VALID_QUALITY_FILTERS:
        return _streaming_available_tiers(tier, {tier})
    return frozenset()


def release_quality_filter_bucket(release: Release) -> str:
    tier = release.peak_quality_tier
    if tier in _VALID_QUALITY_FILTERS:
        return tier
    return ""


def release_matches_quality_filter(
    release: Release,
    enabled_quality_tiers: frozenset[str],
) -> bool:
    """True when the release is available at any enabled quality tier."""
    if not enabled_quality_tiers:
        return True
    available = release_available_quality_tiers(release)
    if not available:
        return False
    return bool(available & enabled_quality_tiers)


_QOBUZ_FORMAT_ID_RANK: dict[int, int] = {
    5: 0,  # MP3 320
    6: 1,  # 16/44 FLAC
    7: 2,  # 24/96
    27: 3,  # 24/192
}
_QOBUZ_RANK_TO_FORMAT_ID: dict[int, int] = {
    0: 5,
    1: 6,
    2: 7,
    3: 27,
}


def playback_ceiling_tier(enabled_quality_tiers: frozenset[str]) -> str | None:
    """Highest enabled shell filter tier, or None when all tiers are enabled (no cap)."""
    if not enabled_quality_tiers:
        return None
    return max_quality_tier(*enabled_quality_tiers)


def qobuz_format_id_for_playback(
    *,
    config_format_id: int,
    ceiling_tier: str | None,
) -> int:
    """Apply shell playback ceiling to the configured Qobuz stream format."""
    config_rank = _QOBUZ_FORMAT_ID_RANK.get(config_format_id, 3)
    if ceiling_tier is None or ceiling_tier == QUALITY_FILTER_HI_RES:
        cap_rank = 3
    elif ceiling_tier == QUALITY_FILTER_CD:
        cap_rank = 1
    elif ceiling_tier == QUALITY_FILTER_COMPRESSED:
        cap_rank = 0
    else:
        cap_rank = 3
    return _QOBUZ_RANK_TO_FORMAT_ID[min(config_rank, cap_rank)]
