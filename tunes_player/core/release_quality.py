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

_CD_MAX_BIT_DEPTH = 16
_CD_MAX_SAMPLE_RATE_HZ = 48_000

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


def tier_from_local(
    *,
    max_bit_depth: int | None,
    max_sample_rate: int | None,
    has_lossless: bool,
    has_lossy: bool,
) -> str:
    """Peak catalog tier from per-release file aggregates."""
    if has_lossless:
        bit_depth = max_bit_depth or 0
        sample_rate = max_sample_rate or 0
        if bit_depth > _CD_MAX_BIT_DEPTH or sample_rate > _CD_MAX_SAMPLE_RATE_HZ:
            return QUALITY_FILTER_HI_RES
        return QUALITY_FILTER_CD
    if has_lossy:
        return QUALITY_FILTER_COMPRESSED
    if max_bit_depth is not None and max_sample_rate is not None:
        if max_bit_depth > _CD_MAX_BIT_DEPTH or max_sample_rate > _CD_MAX_SAMPLE_RATE_HZ:
            return QUALITY_FILTER_HI_RES
        return QUALITY_FILTER_CD
    return QUALITY_FILTER_COMPRESSED


def tier_from_tidal_peak(rank: int) -> str:
    """Map ``track_peak_quality()`` rank to a filter bucket."""
    if rank >= _TIDAL_RANK_CD + 1:
        return QUALITY_FILTER_HI_RES
    if rank == _TIDAL_RANK_CD:
        return QUALITY_FILTER_CD
    return QUALITY_FILTER_COMPRESSED


def tier_from_tidal_album(album: object) -> str:
    """Peak catalog tier from TIDAL album metadata (no track list fetch)."""
    from tunes_player.core.backends.tidal.stream_quality import track_peak_quality

    if getattr(album, "audio_quality", None) is None and getattr(
        album,
        "media_metadata_tags",
        None,
    ) is None:
        return QUALITY_FILTER_COMPRESSED
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
    if depth > _CD_MAX_BIT_DEPTH or rate_hz > _CD_MAX_SAMPLE_RATE_HZ:
        return QUALITY_FILTER_HI_RES
    if depth >= _CD_MAX_BIT_DEPTH and rate_hz >= 44_100:
        return QUALITY_FILTER_CD
    return QUALITY_FILTER_COMPRESSED


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
    if album.get("hires") or album.get("hires_streamable"):
        return QUALITY_FILTER_HI_RES
    tiers = [
        _tier_from_qobuz_technical_specifications(
            album.get("maximum_technical_specifications"),
        ),
        _tier_from_qobuz_bit_depth_sample_rate(
            bit_depth=album.get("maximum_bit_depth"),
            sample_rate=album.get("maximum_sampling_rate"),
        ),
    ]
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
    return QUALITY_FILTER_COMPRESSED
