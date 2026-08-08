"""Per-quality-tier grid expansion (one tile per catalog quality variant)."""

from __future__ import annotations

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
    peak_quality_tier_from_tiers,
)

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


def tier_sample_metadata(
    release: Release,
    tier: str,
) -> tuple[int | None, int | None]:
    """Bit depth and sample rate to display for a specific quality tier."""
    return _bit_depth_sample_rate_for_tier(release, tier)


def release_for_quality_tier(release: Release, tier: str) -> Release:
    """Apply grid tile tier metadata (for detail view / playback context)."""
    catalog_id = _catalog_id_for_release(release)
    return _tile_for_tier(release, tier, catalog_id=catalog_id)


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
        tiers = frozenset(tier for tier in tiers if tier in _VALID_QUALITY_FILTERS)
    elif release.peak_quality_tier in _VALID_QUALITY_FILTERS:
        tiers = frozenset({release.peak_quality_tier})
    else:
        return frozenset()
    # Measured non-hi-res peak must not emit a hollow hi_res sibling (#147).
    rate_hz = release.peak_sample_rate_hz
    if (
        QUALITY_FILTER_HI_RES in tiers
        and rate_hz is not None
        and rate_hz > 0
        and not is_acoustic_hi_res(rate_hz)
    ):
        tiers = frozenset(tier for tier in tiers if tier != QUALITY_FILTER_HI_RES)
    return tiers


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
        # Dual-format CD tile under a hi-res peak: synthetic CD display.
        if tier == QUALITY_FILTER_CD:
            return 16, 44_100
        # Claimed hi_res but measured peak is not: keep acoustics for the label
        # (#147). Do not blank rate/depth and leave the tile unlabeled.
        if tier == QUALITY_FILTER_HI_RES and acoustic != QUALITY_FILTER_HI_RES:
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


def collapse_expanded_releases_to_catalog(releases: list[Release]) -> list[Release]:
    """Merge quality-tier tiles back to one catalog row per release (full tiers).

    Expanded tiles store ``available_quality_tiers`` as a singleton. Collapsing by
    first-seen tile alone would drop sibling tiers on re-expand (#147).
    """
    if not releases:
        return []

    groups: dict[str, list[Release]] = {}
    order: list[str] = []
    for release in releases:
        catalog_id = _catalog_id_for_release(release) or release.id
        if catalog_id not in groups:
            groups[catalog_id] = []
            order.append(catalog_id)
        groups[catalog_id].append(release)

    output: list[Release] = []
    for catalog_id in order:
        siblings = groups[catalog_id]
        base = siblings[0]
        for sibling in siblings:
            if len(sibling.available_quality_tiers) > 1:
                base = sibling
                break

        tiers: set[str] = set()
        peak_rate: int | None = None
        peak_depth: int | None = None
        ready = False
        for sibling in siblings:
            ready = ready or sibling.catalog_quality_ready
            tiers.update(
                tier
                for tier in sibling.available_quality_tiers
                if tier in _VALID_QUALITY_FILTERS
            )
            if sibling.quality_tier in _VALID_QUALITY_FILTERS:
                tiers.add(sibling.quality_tier)
            if sibling.peak_quality_tier in _VALID_QUALITY_FILTERS:
                tiers.add(sibling.peak_quality_tier)
            suffix = parse_quality_tier_suffix(sibling.id)
            if suffix is not None:
                tiers.add(suffix)
            rate = sibling.peak_sample_rate_hz
            if rate is not None and rate > 0 and (peak_rate is None or rate > peak_rate):
                peak_rate = rate
                peak_depth = sibling.peak_bit_depth

        available = frozenset(tiers)
        output.append(
            replace(
                base,
                id=catalog_id,
                catalog_release_id=catalog_id,
                quality_tier="",
                available_quality_tiers=available,
                peak_quality_tier=peak_quality_tier_from_tiers(available),
                peak_sample_rate_hz=(
                    peak_rate if peak_rate is not None else base.peak_sample_rate_hz
                ),
                peak_bit_depth=(
                    peak_depth if peak_rate is not None else base.peak_bit_depth
                ),
                catalog_quality_ready=ready or base.catalog_quality_ready,
            ),
        )
    return output


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
