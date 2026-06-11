"""Per-quality-tier grid expansion (one tile per catalog quality variant)."""

from __future__ import annotations

import logging
import re
from dataclasses import replace

from tunes_player.core.models import Release
from tunes_player.core.release_quality import (
    QUALITY_FILTER_CD,
    QUALITY_FILTER_COMPRESSED,
    QUALITY_FILTER_HI_RES,
    _VALID_QUALITY_FILTERS,
    acoustic_tier_from_lossless,
    is_acoustic_hi_res,
)

_log = logging.getLogger(__name__)

_TIER_SUFFIX_RE = re.compile(r"@(compressed|cd|hi_res)$")
_TIER_ORDER = (
    QUALITY_FILTER_COMPRESSED,
    QUALITY_FILTER_CD,
    QUALITY_FILTER_HI_RES,
)


def parse_quality_tier_suffix(release_id: str) -> str | None:
    match = _TIER_SUFFIX_RE.search(release_id)
    if match is None:
        return None
    tier = match.group(1)
    if tier in _VALID_QUALITY_FILTERS:
        return tier
    return None


def parse_catalog_release_id(release_id: str) -> str:
    """Strip synthetic @tier suffix from a grid tile id."""
    tier = parse_quality_tier_suffix(release_id)
    if tier is None:
        return release_id
    return release_id[: -len(tier) - 1]


def quality_tile_id(catalog_release_id: str, tier: str) -> str:
    if tier not in _VALID_QUALITY_FILTERS:
        return catalog_release_id
    return f"{catalog_release_id}@{tier}"


def _catalog_id_for_release(release: Release) -> str:
    if release.catalog_release_id:
        return release.catalog_release_id
    return parse_catalog_release_id(release.id)


def _tiers_for_expansion(release: Release) -> frozenset[str]:
    if not release.catalog_quality_ready:
        return frozenset()
    tiers = release.available_quality_tiers
    if tiers:
        return frozenset(tier for tier in tiers if tier in _VALID_QUALITY_FILTERS)
    if release.peak_quality_tier in _VALID_QUALITY_FILTERS:
        return frozenset({release.peak_quality_tier})
    return frozenset()


def _effective_bit_depth(depth: int | None, *, rate_hz: int) -> int:
    if depth is not None and depth > 0:
        return depth
    return 24 if is_acoustic_hi_res(rate_hz) else 16


def _bit_depth_sample_rate_for_tier(
    release: Release,
    tier: str,
) -> tuple[int | None, int | None]:
    depth = release.peak_bit_depth
    rate_hz = release.peak_sample_rate_hz
    if rate_hz is not None and rate_hz > 0:
        acoustic = acoustic_tier_from_lossless(
            bit_depth=depth or 16,
            sample_rate_hz=rate_hz,
        )
        if acoustic == tier:
            return _effective_bit_depth(depth, rate_hz=rate_hz), rate_hz
    if tier == QUALITY_FILTER_CD:
        return 16, 44_100
    if tier == QUALITY_FILTER_COMPRESSED:
        return None, None
    if tier == QUALITY_FILTER_HI_RES and rate_hz and is_acoustic_hi_res(rate_hz):
        return _effective_bit_depth(depth, rate_hz=rate_hz), rate_hz
    return None, None


def _tile_for_tier(release: Release, tier: str, *, catalog_id: str) -> Release:
    depth, rate_hz = _bit_depth_sample_rate_for_tier(release, tier)
    tile_id = (
        catalog_id if len(_tiers_for_expansion(release)) == 1 else quality_tile_id(catalog_id, tier)
    )
    return replace(
        release,
        id=tile_id,
        catalog_release_id=catalog_id,
        quality_tier=tier,
        peak_quality_tier=tier,
        available_quality_tiers=frozenset({tier}),
        peak_bit_depth=depth,
        peak_sample_rate_hz=rate_hz,
    )


def expand_releases_by_quality_tier(releases: list[Release]) -> list[Release]:
    """Emit one grid row per quality tier on each catalog release."""
    if not releases:
        return []

    output: list[Release] = []
    for release in releases:
        tiers = _tiers_for_expansion(release)
        if not tiers:
            catalog_id = _catalog_id_for_release(release)
            output.append(
                replace(
                    release,
                    catalog_release_id=catalog_id or release.id,
                ),
            )
            continue
        catalog_id = _catalog_id_for_release(release)
        ordered = [tier for tier in _TIER_ORDER if tier in tiers]
        ordered.extend(sorted(tiers - frozenset(ordered)))
        if len(ordered) == 1:
            output.append(_tile_for_tier(release, ordered[0], catalog_id=catalog_id))
        else:
            for tier in ordered:
                output.append(_tile_for_tier(release, tier, catalog_id=catalog_id))
    return output


def log_grid_quality_tiles(releases: list[Release]) -> None:
    if not releases:
        _log.info("Grid tiles: empty (0)")
        return
    _log.info("Grid tiles: %d", len(releases))
    for index, release in enumerate(releases, start=1):
        _log.info(
            "Grid tile %d/%d: id=%s catalog_release_id=%s quality_tier=%r "
            "peak_quality_tier=%r peak_sample_rate_hz=%s peak_bit_depth=%s",
            index,
            len(releases),
            release.id,
            release.catalog_release_id or release.id,
            release.quality_tier or "",
            release.peak_quality_tier or "",
            release.peak_sample_rate_hz,
            release.peak_bit_depth,
        )


def playback_tier_for_release_id(
    release_id: str,
    *,
    summaries: dict[str, Release],
) -> str:
    cached = summaries.get(release_id)
    if cached is not None and cached.quality_tier in _VALID_QUALITY_FILTERS:
        return cached.quality_tier
    suffix = parse_quality_tier_suffix(release_id)
    if suffix is not None:
        return suffix
    if cached is not None and cached.peak_quality_tier in _VALID_QUALITY_FILTERS:
        return cached.peak_quality_tier
    return QUALITY_FILTER_HI_RES
