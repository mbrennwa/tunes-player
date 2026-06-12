"""Catalog peak quality tiers for release filtering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tunes_player.core.models import Release, Source

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

ALL_QUALITY_TIERS = frozenset(
    {
        QUALITY_FILTER_COMPRESSED,
        QUALITY_FILTER_CD,
        QUALITY_FILTER_HI_RES,
    }
)

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
    if rate < 1000:
        return int(round(rate * 1000))
    return int(round(rate))


def is_acoustic_hi_res(sample_rate_hz: int) -> bool:
    """True when lossless sample rate is above CD (44.1 kHz)."""
    return sample_rate_hz > _CD_SAMPLE_RATE_HZ


def acoustic_tier_from_lossless(
    *,
    bit_depth: int,
    sample_rate_hz: int,
) -> str:
    """CD is 44.1 kHz lossless (16- or 24-bit); anything above is hi-res."""
    if is_acoustic_hi_res(sample_rate_hz):
        return QUALITY_FILTER_HI_RES
    if (
        bit_depth >= _CD_MIN_BIT_DEPTH
        and sample_rate_hz == _CD_SAMPLE_RATE_HZ
    ):
        return QUALITY_FILTER_CD
    return QUALITY_FILTER_COMPRESSED


def acoustic_tier_from_stream(
    *,
    bit_depth: int | None,
    sample_rate_hz: int,
    lossless: bool = True,
) -> str:
    """Map stream bit depth / sample rate to a filter bucket."""
    if not lossless:
        return QUALITY_FILTER_COMPRESSED
    return acoustic_tier_from_lossless(
        bit_depth=bit_depth or 0,
        sample_rate_hz=sample_rate_hz,
    )


def _tier_from_lossless_bit_depth_sample_rate(
    *,
    bit_depth: int,
    sample_rate_hz: int,
) -> str:
    return acoustic_tier_from_lossless(
        bit_depth=bit_depth,
        sample_rate_hz=sample_rate_hz,
    )


def classify_local_catalog(
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
    return frozenset(tier for tier in tiers if tier in _VALID_QUALITY_FILTERS)


def tiers_from_local(
    *,
    max_bit_depth: int | None,
    max_sample_rate: int | None,
    has_lossless: bool,
    has_lossy: bool,
) -> frozenset[str]:
    return classify_local_catalog(
        max_bit_depth=max_bit_depth,
        max_sample_rate=max_sample_rate,
        has_lossless=has_lossless,
        has_lossy=has_lossy,
    )


def tier_from_local(
    *,
    max_bit_depth: int | None,
    max_sample_rate: int | None,
    has_lossless: bool,
    has_lossy: bool,
) -> str:
    """Peak catalog tier from per-release file aggregates."""
    tiers = classify_local_catalog(
        max_bit_depth=max_bit_depth,
        max_sample_rate=max_sample_rate,
        has_lossless=has_lossless,
        has_lossy=has_lossy,
    )
    return max_quality_tier(*tiers) if tiers else ""


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


def _tidal_album_has_hi_res_mode(album: object) -> bool:
    audio_modes = getattr(album, "audio_modes", None) or []
    for mode in audio_modes:
        key = str(mode).upper().replace(" ", "_")
        if "HI_RES" in key or "HIRES" in key:
            return True
    return False


_HI_RES_MEDIA_TAGS = frozenset({"HIRES_LOSSLESS", "HI_RES_LOSSLESS"})


def _tidal_media_tag_set(
    album: object,
    *,
    supplemental_tags: set[str] | None = None,
) -> set[str]:
    """Normalized TIDAL media tags from album metadata (no stream probes)."""
    from tunes_player.core.backends.tidal.stream_quality import normalize_api_quality

    tags: set[str] = set(supplemental_tags or ())
    for source in (
        getattr(album, "media_metadata_tags", None),
        getattr(album, "mediaTags", None),
    ):
        if not source:
            continue
        try:
            for tag in source:
                normalized = normalize_api_quality(str(tag))
                if normalized:
                    tags.add(normalized)
        except TypeError:
            pass
    return tags


def _catalog_tiers_from_tidal_tags(tags: set[str]) -> set[str]:
    """Map TIDAL mediaTags to browse quality filter buckets."""
    tiers: set[str] = set()
    if tags & _HI_RES_MEDIA_TAGS:
        tiers.add(QUALITY_FILTER_HI_RES)
    elif "LOSSLESS" in tags:
        tiers.add(QUALITY_FILTER_CD)
    return tiers


def _tidal_album_sample_rate_hz(album: object) -> int:
    for attr in ("sample_rate", "sampling_rate", "samplingRate"):
        value = getattr(album, attr, None)
        if value is not None:
            rate_hz = _normalize_sample_rate_hz(value)
            if rate_hz > 0:
                return rate_hz
    tags = getattr(album, "media_metadata_tags", None) or []
    for tag in tags:
        text = str(tag).upper()
        if "KHZ" in text or "HZ" in text:
            import re

            match = re.search(r"(\d+(?:\.\d+)?)\s*KHZ", text)
            if match is not None:
                rate_hz = _normalize_sample_rate_hz(float(match.group(1)))
                if rate_hz > 0:
                    return rate_hz
    from tunes_player.core.backends.tidal.catalog_stream_probe import (
        peak_rate_depth_from_tidal_stream_probe,
    )
    from tunes_player.core.release_catalog import _tidal_album_needs_stream_probe

    if _tidal_album_needs_stream_probe(album):
        _, probed_rate = peak_rate_depth_from_tidal_stream_probe(album)
        if probed_rate is not None and probed_rate > 0:
            return probed_rate
    return 0


def peak_sample_rate_hz_from_tidal_album(album: object) -> int | None:
    rate_hz = _tidal_album_sample_rate_hz(album)
    return rate_hz if rate_hz > 0 else None


def _tidal_acoustic_peak_tier(
    album: object,
    *,
    supplemental_tags: set[str] | None = None,
) -> str:
    """Peak catalog tier from TIDAL album metadata (no stream probes)."""
    from tunes_player.core.backends.tidal.stream_quality import track_peak_quality

    rate_hz = _tidal_album_sample_rate_hz(album)
    if rate_hz > 0:
        if is_acoustic_hi_res(rate_hz):
            return QUALITY_FILTER_HI_RES
        if rate_hz == _CD_SAMPLE_RATE_HZ:
            return QUALITY_FILTER_CD

    tags = _tidal_media_tag_set(album, supplemental_tags=supplemental_tags)
    tag_tiers = _catalog_tiers_from_tidal_tags(tags)
    if QUALITY_FILTER_HI_RES in tag_tiers:
        return QUALITY_FILTER_HI_RES
    if QUALITY_FILTER_CD in tag_tiers:
        return QUALITY_FILTER_CD

    rank = track_peak_quality(album)
    api_tier = tier_from_tidal_peak(rank)
    if api_tier in _VALID_QUALITY_FILTERS:
        return api_tier
    return ""


def _collect_tidal_album_tier_signals(
    album: object,
    *,
    supplemental_tags: set[str] | None = None,
) -> set[str]:
    if not tidal_album_has_quality_metadata(album):
        return set()
    tags = _tidal_media_tag_set(album)
    if not tags and supplemental_tags:
        tags = set(supplemental_tags)
    signals = set(_catalog_tiers_from_tidal_tags(tags))
    peak = _tidal_acoustic_peak_tier(album, supplemental_tags=tags if tags else None)
    if peak in _VALID_QUALITY_FILTERS:
        signals.add(peak)
    if _tidal_album_has_hi_res_mode(album) and QUALITY_FILTER_CD in signals:
        signals.add(QUALITY_FILTER_HI_RES)
    return {tier for tier in signals if tier in _VALID_QUALITY_FILTERS}


def classify_tidal_catalog(
    album: object,
    tracks: list[object] | None = None,
    *,
    supplemental_media_tags: set[str] | None = None,
) -> frozenset[str]:
    """Catalog tiers a TIDAL album supports (facts from album metadata and tracks)."""
    tiers = set(
        _collect_tidal_album_tier_signals(
            album,
            supplemental_tags=supplemental_media_tags,
        ),
    )
    if tracks:
        for track in tracks:
            tier = tier_from_tidal_track(track)
            if tier in _VALID_QUALITY_FILTERS:
                tiers.add(tier)
    return frozenset(tiers)


def tiers_from_tidal_album(album: object) -> frozenset[str]:
    return classify_tidal_catalog(album)


def tier_from_tidal_album(album: object) -> str:
    tiers = classify_tidal_catalog(album)
    return max_quality_tier(*tiers) if tiers else ""


def _tier_from_qobuz_bit_depth_sample_rate(
    *,
    bit_depth: int | float | str | None,
    sample_rate: int | float | str | None,
) -> str | None:
    try:
        depth = int(bit_depth) if bit_depth is not None else 0
    except (TypeError, ValueError):
        depth = 0
    rate_hz = _normalize_sample_rate_hz(sample_rate)
    if depth <= 0 and rate_hz <= 0:
        return None
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


def _qobuz_item_has_hires_above_cd(item: dict[str, Any]) -> bool:
    rate_hz = _normalize_sample_rate_hz(item.get("maximum_sampling_rate"))
    if is_acoustic_hi_res(rate_hz):
        return True
    technical = _tier_from_qobuz_technical_specifications(
        item.get("maximum_technical_specifications"),
    )
    return technical == QUALITY_FILTER_HI_RES


def _qobuz_album_has_hires_above_cd_stream(album: dict[str, Any]) -> bool:
    if _qobuz_item_has_hires_above_cd(album):
        return True
    tracks = album.get("tracks")
    if not isinstance(tracks, dict):
        return False
    for item in tracks.get("items") or []:
        if isinstance(item, dict) and _qobuz_item_has_hires_above_cd(item):
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
    depth_rate_tier = _tier_from_qobuz_bit_depth_sample_rate(
        bit_depth=album.get("maximum_bit_depth"),
        sample_rate=album.get("maximum_sampling_rate"),
    )
    if depth_rate_tier is not None:
        signals.add(depth_rate_tier)
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
    """Catalog signals including streamable hi-res above CD when present."""
    signals = set(_collect_qobuz_peak_signals(album))
    if _qobuz_album_has_hires_above_cd_stream(album):
        signals.add(QUALITY_FILTER_HI_RES)
    return signals


def classify_qobuz_catalog(album: dict[str, Any]) -> frozenset[str]:
    """Catalog tiers a Qobuz album supports (facts from album/get JSON)."""
    return frozenset(
        tier
        for tier in _collect_qobuz_available_signals(album)
        if tier in _VALID_QUALITY_FILTERS
    )


def tiers_from_qobuz_album(album: dict[str, Any]) -> frozenset[str]:
    return classify_qobuz_catalog(album)


def tier_from_qobuz_album(album: dict[str, Any]) -> str:
    tiers = classify_qobuz_catalog(album)
    return max_quality_tier(*tiers) if tiers else ""


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


def min_quality_tier(*tiers: str) -> str:
    """Return the lowest catalog tier among the given bucket names."""
    best = QUALITY_FILTER_HI_RES
    best_rank = len(_TIER_RANK)
    for tier in tiers:
        if tier not in _VALID_QUALITY_FILTERS:
            continue
        rank = _TIER_RANK[tier]
        if rank < best_rank:
            best_rank = rank
            best = tier
    return best


def peak_quality_tier_from_tiers(tiers: frozenset[str]) -> str:
    return max_quality_tier(*tiers) if tiers else ""


@dataclass(frozen=True, slots=True)
class PlaybackPreference:
    """User playback ceiling from shell quality filter (no catalog coupling)."""

    max_tier: str


def playback_preference_from_shell(
    enabled_quality_tiers: frozenset[str],
) -> PlaybackPreference:
    """Derive playback ceiling from enabled shell tiers only."""
    if not enabled_quality_tiers:
        return PlaybackPreference(QUALITY_FILTER_HI_RES)
    return PlaybackPreference(max_quality_tier(*enabled_quality_tiers))


def playback_preference_for_tier(tier: str) -> PlaybackPreference:
    """Fixed playback ceiling for a single quality grid tile."""
    if tier in _VALID_QUALITY_FILTERS:
        return PlaybackPreference(tier)
    return PlaybackPreference(QUALITY_FILTER_HI_RES)


def catalog_quality_label_for_release(release: Release) -> str | None:
    """Human-readable catalog quality for grid tiles and release detail."""
    from tunes_player.core.playback_quality import catalog_tile_quality_label
    from tunes_player.core.release_quality_tiles import (
        parse_quality_tier_suffix,
        tier_sample_metadata,
    )

    tier = (
        release.quality_tier
        or parse_quality_tier_suffix(release.id)
        or release.peak_quality_tier
    )
    if not tier and len(release.available_quality_tiers) == 1:
        tier = next(iter(release.available_quality_tiers))
    if tier:
        depth, rate_hz = tier_sample_metadata(release, tier)
    else:
        depth, rate_hz = release.peak_bit_depth, release.peak_sample_rate_hz
    return catalog_tile_quality_label(
        bit_depth=depth,
        sample_rate_hz=rate_hz,
        quality_tier=tier,
        source=release.source,
    )


def release_available_quality_tiers(release: Release) -> frozenset[str]:
    """Tiers used for shell quality filter matching."""
    return release.available_quality_tiers


def release_quality_filter_bucket(release: Release) -> str:
    if not release.catalog_quality_ready:
        return ""
    tier = release.peak_quality_tier
    if tier in _VALID_QUALITY_FILTERS:
        return tier
    tiers = release.available_quality_tiers
    return max_quality_tier(*tiers) if tiers else ""


def streaming_catalog_quality_needs_enrich(release: Release) -> bool:
    """True when album lookup is still needed (or stale) for streaming quality tiers."""
    if release.source not in (Source.TIDAL, Source.QOBUZ):
        return False
    if not release.catalog_quality_ready:
        return True
    # Rows enriched before acoustic TIDAL resolution kept cd with no sample rate.
    return release.peak_sample_rate_hz is None


def release_matches_quality_filter(
    release: Release,
    enabled_quality_tiers: frozenset[str],
) -> bool:
    """True when the release is available at any enabled quality tier."""
    if not enabled_quality_tiers:
        return True
    if not release.catalog_quality_ready:
        return False
    available = release.available_quality_tiers
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


def _tier_to_qobuz_max_rank(tier: str) -> int:
    if tier == QUALITY_FILTER_HI_RES:
        return 3
    if tier == QUALITY_FILTER_CD:
        return 1
    if tier == QUALITY_FILTER_COMPRESSED:
        return 0
    return 3


def qobuz_format_candidates_for_preference(
    *,
    config_format_id: int,
    preference: PlaybackPreference,
) -> list[int]:
    """Ordered Qobuz format_id values to try, highest quality first."""
    config_rank = _QOBUZ_FORMAT_ID_RANK.get(config_format_id, 3)
    max_target = _tier_to_qobuz_max_rank(preference.max_tier)
    effective_max = min(config_rank, max_target)
    return [
        _QOBUZ_RANK_TO_FORMAT_ID[rank]
        for rank in range(effective_max, -1, -1)
        if rank in _QOBUZ_RANK_TO_FORMAT_ID
    ]


def qobuz_format_id_for_preference(
    *,
    config_format_id: int,
    preference: PlaybackPreference,
) -> int:
    candidates = qobuz_format_candidates_for_preference(
        config_format_id=config_format_id,
        preference=preference,
    )
    if candidates:
        return candidates[0]
    return _QOBUZ_RANK_TO_FORMAT_ID[3]
