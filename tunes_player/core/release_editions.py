"""UPC-based collapse of same-source streaming quality editions."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING

from tunes_player.core.models import Release, Source
from tunes_player.core.release_quality import (
    QUALITY_FILTER_COMPRESSED,
    PlaybackPreference,
    _TIER_RANK,
    _VALID_QUALITY_FILTERS,
    _normalize_sample_rate_hz,
    peak_quality_tier_from_tiers,
    release_available_quality_tiers,
)

if TYPE_CHECKING:
    pass

_log = logging.getLogger(__name__)
_MIN_UPC_DIGITS = 8


def normalize_upc(raw: object) -> str | None:
    """Return canonical digits-only UPC/EAN (leading zeros stripped for matching)."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        return None
    canonical = digits.lstrip("0")
    if len(canonical) < _MIN_UPC_DIGITS:
        return None
    return canonical


def peak_sample_rate_from_qobuz_album(album: dict) -> int | None:
    rate_hz = _normalize_sample_rate_hz(album.get("maximum_sampling_rate"))
    return rate_hz if rate_hz > 0 else None


def peak_sample_rate_from_tidal_album(album: object) -> int | None:
    from tunes_player.core.release_quality import peak_sample_rate_hz_from_tidal_album

    return peak_sample_rate_hz_from_tidal_album(album)


def upc_from_qobuz_album(album: dict) -> str | None:
    for key in ("upc", "barcode", "ean"):
        normalized = normalize_upc(album.get(key))
        if normalized is not None:
            return normalized
    return None


def upc_from_tidal_album(album: object) -> str | None:
    for attr in (
        "upc",
        "universal_product_number",
        "barcodeId",
        "barcode",
        "barcode_id",
    ):
        normalized = normalize_upc(getattr(album, attr, None))
        if normalized is not None:
            return normalized
    return None


def _edition_sort_key(release: Release) -> tuple:
    tier = release.peak_quality_tier
    tier_rank = _TIER_RANK.get(tier, -1) if tier in _VALID_QUALITY_FILTERS else -1
    rate = release.peak_sample_rate_hz or 0
    return (-tier_rank, -rate, release.id)


def _pick_canonical_member(members: list[Release]) -> Release:
    return sorted(members, key=_edition_sort_key)[0]


def _merge_upc_group(members: list[Release]) -> Release:
    canonical = _pick_canonical_member(members)
    merged_upc = normalize_upc(canonical.upc)
    if merged_upc is None:
        for release in members:
            merged_upc = normalize_upc(release.upc)
            if merged_upc is not None:
                break
    edition_ids = frozenset(release.id for release in members)
    available: set[str] = set()
    for release in members:
        if release.catalog_quality_ready:
            available.update(release_available_quality_tiers(release))
    available_tiers = frozenset(available)
    peak_tier = peak_quality_tier_from_tiers(available_tiers) if available_tiers else ""
    if not peak_tier:
        peak_tier = canonical.peak_quality_tier
    return replace(
        canonical,
        upc=merged_upc,
        peak_quality_tier=peak_tier,
        available_quality_tiers=available_tiers,
        edition_release_ids=edition_ids,
        catalog_quality_ready=all(release.catalog_quality_ready for release in members),
    )


def log_grid_release_upcs(releases: list[Release]) -> None:
    """Log UPC metadata for each release row shown in the album grid."""
    if not releases:
        _log.info("Grid UPC: empty (0 tiles)")
        return
    _log.info("Grid UPC: %d tile(s)", len(releases))
    for index, release in enumerate(releases, start=1):
        normalized = normalize_upc(release.upc) if release.upc else None
        edition_ids = (
            sorted(release.edition_release_ids)
            if release.edition_release_ids
            else [release.id]
        )
        _log.info(
            "Grid UPC tile %d/%d: id=%s source=%s title=%r artist=%r "
            "upc=%r normalized_upc=%r edition_release_ids=%s "
            "catalog_quality_ready=%s peak_quality_tier=%r peak_sample_rate_hz=%s",
            index,
            len(releases),
            release.id,
            release.source.value,
            release.title,
            release.artist_name,
            release.upc,
            normalized,
            edition_ids,
            release.catalog_quality_ready,
            release.peak_quality_tier or "",
            release.peak_sample_rate_hz,
        )


def collapse_releases_by_upc(releases: list[Release]) -> list[Release]:
    """Merge streaming rows sharing (source, upc). Local rows are unchanged."""
    if not releases:
        return []

    output: list[Release] = []
    streaming_by_upc: dict[tuple[Source, str], list[Release]] = {}
    streaming_order: list[tuple[Source, str]] = []
    streaming_positions: dict[tuple[Source, str], int] = {}

    for index, release in enumerate(releases):
        if release.source == Source.LOCAL:
            output.append((index, release))
            continue
        upc = normalize_upc(release.upc) if release.upc else None
        if not upc:
            output.append((index, release))
            continue
        key = (release.source, upc)
        if key not in streaming_by_upc:
            streaming_by_upc[key] = []
            streaming_order.append(key)
            streaming_positions[key] = index
        streaming_by_upc[key].append(release)

    collapsed: dict[tuple[Source, str], Release] = {}
    for key in streaming_order:
        members = streaming_by_upc[key]
        if len(members) == 1:
            collapsed[key] = members[0]
        else:
            collapsed[key] = _merge_upc_group(members)

    for key in streaming_order:
        output.append((streaming_positions[key], collapsed[key]))

    output.sort(key=lambda item: item[0])
    return [release for _, release in output]


def _tier_rank(tier: str) -> int:
    if tier in _VALID_QUALITY_FILTERS:
        return _TIER_RANK[tier]
    return -1


def resolve_edition_release_id(
    canonical: Release,
    *,
    preference: PlaybackPreference,
    summaries: dict[str, Release],
) -> str:
    """Pick the provider album id to play for a collapsed UPC group."""
    edition_ids = canonical.edition_release_ids
    if not edition_ids:
        return canonical.id

    max_allowed = _tier_rank(preference.max_tier)
    if max_allowed < 0:
        max_allowed = _TIER_RANK[QUALITY_FILTER_COMPRESSED]

    candidates: list[Release] = []
    for release_id in edition_ids:
        release = summaries.get(release_id)
        if release is None and release_id == canonical.id:
            release = canonical
        if release is not None:
            candidates.append(release)
    if not candidates:
        return canonical.id

    eligible = [
        release
        for release in candidates
        if _tier_rank(release.peak_quality_tier) <= max_allowed
    ]
    if not eligible:
        return sorted(candidates, key=_edition_sort_key)[0].id
    return sorted(eligible, key=_edition_sort_key)[0].id
