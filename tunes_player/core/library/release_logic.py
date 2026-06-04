"""Release completeness and metadata-driven release type for library groups."""

from __future__ import annotations

from tunes_player.core.models import ReleaseCompleteness, ReleaseType

# Normalized keys from Vorbis RELEASETYPE, MusicBrainz album type, TIDAL, Qobuz, etc.
_TYPE_ALIASES: dict[str, ReleaseType] = {
    "album": ReleaseType.ALBUM,
    "lp": ReleaseType.ALBUM,
    "single": ReleaseType.SINGLE,
    "ep": ReleaseType.EP,
    "extended play": ReleaseType.EP,
    "compilation": ReleaseType.COMPILATION,
    "anthology": ReleaseType.COMPILATION,
    "live": ReleaseType.LIVE_ALBUM,
    "live album": ReleaseType.LIVE_ALBUM,
    "live_album": ReleaseType.LIVE_ALBUM,
    "soundtrack": ReleaseType.ALBUM,
    "other": ReleaseType.ALBUM,
}


def _normalize_type_key(raw: str) -> str:
    return " ".join(raw.strip().casefold().replace("_", " ").replace("-", " ").split())


def release_type_from_metadata(
    raw: str | None,
    *,
    is_synthetic: bool,
) -> ReleaseType:
    """Map provider or file tag strings to ReleaseType (no track-count heuristics)."""
    if is_synthetic:
        return ReleaseType.SYNTHETIC
    if not raw or not str(raw).strip():
        return ReleaseType.ALBUM
    key = _normalize_type_key(str(raw))
    if key in _TYPE_ALIASES:
        return _TYPE_ALIASES[key]
    if key.startswith("ep"):
        return ReleaseType.EP
    if "compilation" in key:
        return ReleaseType.COMPILATION
    if "live" in key:
        return ReleaseType.LIVE_ALBUM
    if "single" in key:
        return ReleaseType.SINGLE
    return ReleaseType.ALBUM


def infer_release_completeness(
    *,
    track_count: int,
    is_synthetic: bool,
    total_tracks_tag: int | None,
    max_track_number: int | None,
) -> tuple[ReleaseCompleteness, int | None]:
    if is_synthetic:
        return ReleaseCompleteness.SYNTHETIC, 1

    expected = total_tracks_tag
    if expected is None and max_track_number is not None and max_track_number > track_count:
        expected = max_track_number

    if expected is not None and track_count < expected:
        return ReleaseCompleteness.PARTIAL, expected

    return ReleaseCompleteness.COMPLETE, expected


def infer_release_metadata(
    *,
    track_count: int,
    is_synthetic: bool,
    total_tracks_tag: int | None,
    max_track_number: int | None,
    release_type_tag: str | None = None,
) -> tuple[ReleaseCompleteness, ReleaseType, int | None]:
    completeness, expected = infer_release_completeness(
        track_count=track_count,
        is_synthetic=is_synthetic,
        total_tracks_tag=total_tracks_tag,
        max_track_number=max_track_number,
    )
    release_type = release_type_from_metadata(
        release_type_tag,
        is_synthetic=is_synthetic,
    )
    return completeness, release_type, expected
