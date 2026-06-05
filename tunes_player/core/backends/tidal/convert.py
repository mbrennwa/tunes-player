"""Map tidalapi objects to Tunes domain models."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tunes_player.core.backends.tidal import ids as tidal_ids
from tunes_player.core.backends.tidal.stream_quality import track_peak_quality
from tunes_player.core.library.release_logic import (
    infer_release_completeness,
    release_type_from_metadata,
)
from tunes_player.core.release_quality import (
    QUALITY_FILTER_COMPRESSED,
    max_quality_tier,
    tier_from_tidal_album,
    tier_from_tidal_peak,
    tidal_album_has_quality_metadata,
)
from tunes_player.core.models import (
    Artist,
    Release,
    Source,
    Track,
)

if TYPE_CHECKING:
    from tidalapi.album import Album as TidalAlbum
    from tidalapi.artist import Artist as TidalArtist
    from tidalapi.media import Track as TidalTrack
    from tidalapi.session import Session


def _album_art(session: Session, album: TidalAlbum, *, size: int = 320) -> str | None:
    try:
        return album.image(size)
    except Exception:
        return None


def _tidal_album_type_raw(album: TidalAlbum) -> str | None:
    raw = getattr(album, "type", None)
    if raw is None:
        return None
    if hasattr(raw, "value") and raw.value is not None:
        text = str(raw.value).strip()
        if text:
            return text
    if hasattr(raw, "name") and raw.name is not None:
        text = str(raw.name).strip()
        if text:
            return text
    text = str(raw).strip()
    if "." in text:
        text = text.rsplit(".", 1)[-1].strip()
    return text or None


def _resolve_tidal_release_type(session: Session, album: TidalAlbum) -> str | None:
    """Read album.type; fetch full album metadata when search gave a sparse album."""
    type_raw = _tidal_album_type_raw(album)
    if type_raw is not None:
        return type_raw
    album_id = getattr(album, "id", None)
    if album_id is None or album_id == -1:
        return None
    try:
        full = session.album(album_id)
    except Exception:
        return None
    return _tidal_album_type_raw(full)


def _tidal_album_for_quality(session: Session, album: TidalAlbum) -> TidalAlbum:
    album_id = getattr(album, "id", None)
    if album_id is None or album_id == -1:
        return album
    try:
        return session.album(album_id)
    except Exception:
        return album


def _tidal_album_tracks(session: Session, album: TidalAlbum) -> list[object]:
    try:
        tracks = list(album.tracks())
        if tracks:
            return tracks
    except Exception:
        pass
    resolved = _tidal_album_for_quality(session, album)
    try:
        return list(resolved.tracks())
    except Exception:
        return []


def peak_quality_tier_from_tidal_tracks(tracks: list[object]) -> str:
    if not tracks:
        return QUALITY_FILTER_COMPRESSED
    peak_rank = max(track_peak_quality(track) for track in tracks)
    return tier_from_tidal_peak(peak_rank)


def _resolve_tidal_peak_quality_tier(session: Session, album: TidalAlbum) -> str:
    """Peak catalog tier from album tracks (issues network calls)."""
    return peak_quality_tier_from_tidal_tracks(_tidal_album_tracks(session, album))


def resolve_tidal_grid_peak_quality_tier(
    session: Session,
    album: TidalAlbum,
    *,
    quality_hint_tier: str = "",
) -> str:
    """Best-effort catalog tier for grids without loading every track."""
    tiers: list[str] = []
    if quality_hint_tier:
        tiers.append(quality_hint_tier)
    album_tier = tier_from_tidal_album(album)
    if album_tier:
        tiers.append(album_tier)
    elif not tidal_album_has_quality_metadata(album):
        album_id = getattr(album, "id", None)
        if album_id is not None and album_id != -1:
            try:
                resolved = session.album(album_id)
                fetched = tier_from_tidal_album(resolved)
                if fetched:
                    tiers.append(fetched)
            except Exception:
                pass
    if tiers:
        return max_quality_tier(*tiers)
    return ""


def release_from_tidal(
    session: Session,
    album: TidalAlbum,
    *,
    owned_track_count: int | None = None,
    resolve_peak_quality: bool = False,
    quality_hint_tier: str = "",
) -> Release:
    artists = album.artists or []
    artist_name = artists[0].name if artists else "Unknown Artist"
    year = None
    if album.release_date is not None:
        year = album.release_date.year
    expected = int(album.num_tracks or 0) or None
    track_count = owned_track_count if owned_track_count is not None else (expected or 0)
    completeness, expected = infer_release_completeness(
        track_count=track_count,
        is_synthetic=False,
        total_tracks_tag=expected,
        max_track_number=None,
    )
    release_type = release_type_from_metadata(
        _resolve_tidal_release_type(session, album),
        is_synthetic=False,
    )
    return Release(
        id=tidal_ids.album_id(album.id),
        title=album.name or "Unknown",
        artist_name=artist_name,
        source=Source.TIDAL,
        year=year,
        track_count=track_count,
        expected_track_count=expected,
        completeness=completeness,
        release_type=release_type,
        art_uri=_album_art(session, album),
        peak_quality_tier=(
            _resolve_tidal_peak_quality_tier(session, album)
            if resolve_peak_quality
            else resolve_tidal_grid_peak_quality_tier(
                session,
                album,
                quality_hint_tier=quality_hint_tier,
            )
        ),
    )


def album_from_tidal(session: Session, album: TidalAlbum) -> Release:
    return release_from_tidal(session, album)


def artist_from_tidal(artist: TidalArtist) -> Artist:
    return Artist(
        id=tidal_ids.artist_id(artist.id),
        name=artist.name or "Unknown Artist",
        source=Source.TIDAL,
    )


def track_from_tidal(session: Session, track: TidalTrack, *, album: TidalAlbum | None = None) -> Track:
    artists = track.artists or []
    artist_name = artists[0].name if artists else "Unknown Artist"
    album_title = None
    art_uri = None
    if album is not None:
        album_title = album.name
        art_uri = _album_art(session, album)
    elif track.album is not None:
        album_title = track.album.name
        art_uri = _album_art(session, track.album)
    duration = None
    if track.duration is not None:
        duration = float(track.duration)
    track_number = getattr(track, "track_num", None)
    disc_number = getattr(track, "volume_num", None)
    return Track(
        id=tidal_ids.track_id(track.id),
        title=track.name or "Unknown Track",
        artist_name=artist_name,
        album_title=album_title,
        source=Source.TIDAL,
        duration_sec=duration,
        art_uri=art_uri,
        track_number=int(track_number) if track_number is not None else None,
        disc_number=int(disc_number) if disc_number is not None else None,
    )
