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


def tier_from_local(
    *,
    max_bit_depth: int | None,
    max_sample_rate: int | None,
    has_lossless: bool,
    has_lossy: bool,
) -> str:
    """Peak catalog tier from per-release file aggregates."""
    if has_lossless:
        return _tier_from_lossless_bit_depth_sample_rate(
            bit_depth=max_bit_depth or 0,
            sample_rate_hz=max_sample_rate or 0,
        )
    if has_lossy:
        return QUALITY_FILTER_COMPRESSED
    if max_bit_depth is not None and max_sample_rate is not None:
        return _tier_from_lossless_bit_depth_sample_rate(
            bit_depth=max_bit_depth,
            sample_rate_hz=max_sample_rate,
        )
    return QUALITY_FILTER_COMPRESSED


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


def tier_from_tidal_album(album: object) -> str:
    """Peak catalog tier from TIDAL album metadata (no track list fetch)."""
    from tunes_player.core.backends.tidal.stream_quality import track_peak_quality

    if not tidal_album_has_quality_metadata(album):
        return ""
    rank = track_peak_quality(album)
    audio_modes = getattr(album, "audio_modes", None) or []
    for mode in audio_modes:
        key = str(mode).upper().replace(" ", "_")
        if "HI_RES" in key or "HIRES" in key:
            rank = max(rank, _TIDAL_RANK_CD + 1)
    return tier_from_tidal_peak(rank)


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


def tier_from_qobuz_album(album: dict[str, Any]) -> str:
    """Peak catalog tier from Qobuz album JSON."""
    tiers: list[str] = []
    rate_hz = _normalize_sample_rate_hz(album.get("maximum_sampling_rate"))
    if (
        (album.get("hires") or album.get("hires_streamable"))
        and rate_hz > _CD_SAMPLE_RATE_HZ
    ):
        tiers.append(QUALITY_FILTER_HI_RES)
    tiers.extend(
        [
        _tier_from_qobuz_technical_specifications(
            album.get("maximum_technical_specifications"),
        ),
        _tier_from_qobuz_bit_depth_sample_rate(
            bit_depth=album.get("maximum_bit_depth"),
            sample_rate=album.get("maximum_sampling_rate"),
        ),
        ]
    )
    tracks = album.get("tracks")
    if isinstance(tracks, dict):
        for item in tracks.get("items") or []:
            if not isinstance(item, dict):
                continue
            tiers.append(
                tier_from_qobuz_album(
                    {
                        "hires": item.get("hires"),
                        "hires_streamable": item.get("hires_streamable"),
                        "maximum_bit_depth": item.get("maximum_bit_depth"),
                        "maximum_sampling_rate": item.get("maximum_sampling_rate"),
                    },
                ),
            )
    valid = [tier for tier in tiers if tier is not None]
    if not valid:
        return QUALITY_FILTER_COMPRESSED
    return max_quality_tier(*valid)


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


def release_quality_filter_bucket(release: Release) -> str:
    tier = release.peak_quality_tier
    if tier in _VALID_QUALITY_FILTERS:
        return tier
    return ""
