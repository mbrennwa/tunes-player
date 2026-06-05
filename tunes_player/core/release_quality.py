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


def tier_from_qobuz_album(album: dict[str, Any]) -> str:
    """Peak catalog tier from Qobuz album JSON."""
    if album.get("hires") or album.get("hires_streamable"):
        return QUALITY_FILTER_HI_RES
    max_bit_depth = album.get("maximum_bit_depth")
    max_sample_rate = album.get("maximum_sampling_rate")
    try:
        bit_depth = int(max_bit_depth) if max_bit_depth is not None else 0
    except (TypeError, ValueError):
        bit_depth = 0
    sample_rate = _normalize_sample_rate_hz(max_sample_rate)
    if bit_depth > _CD_MAX_BIT_DEPTH or sample_rate > _CD_MAX_SAMPLE_RATE_HZ:
        return QUALITY_FILTER_HI_RES
    if bit_depth >= _CD_MAX_BIT_DEPTH and sample_rate >= 44_100:
        return QUALITY_FILTER_CD
    return QUALITY_FILTER_COMPRESSED


def release_quality_filter_bucket(release: Release) -> str:
    return release.peak_quality_tier
