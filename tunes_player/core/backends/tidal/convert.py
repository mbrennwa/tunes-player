"""Map tidalapi objects to Tunes domain models."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tunes_player.core.backends.tidal import ids as tidal_ids
from tunes_player.core.library.release_logic import (
    infer_release_completeness,
    release_type_from_metadata,
)
from tunes_player.core.release_catalog import (
    genre_from_tidal_album,
    media_tags_from_tidal_openapi_payload,
    peak_bit_depth_from_tidal_album,
    peak_sample_rate_from_tidal_album,
    tidal_first_track_openapi_payload,
)
from tunes_player.core.release_quality import (
    classify_tidal_catalog,
    peak_quality_tier_from_tiers,
)
from tunes_player.core.models import (
    Release,
    Source,
    Track,
)

if TYPE_CHECKING:
    from tidalapi.album import Album as TidalAlbum
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

def _release_common_fields(
    session: Session,
    album: TidalAlbum,
    *,
    owned_track_count: int | None = None,
) -> tuple[str, str, int | None, int, object, object, str | None]:
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
    art_uri = _album_art(session, album)
    return artist_name, year, expected, track_count, completeness, release_type, art_uri

def release_stub_from_tidal(
    session: Session,
    album: TidalAlbum,
    *,
    owned_track_count: int | None = None,
) -> Release:
    """Phase-1 browse release without catalog quality classification."""
    artist_name, year, expected, track_count, completeness, release_type, art_uri = (
        _release_common_fields(session, album, owned_track_count=owned_track_count)
    )
    catalog_id = tidal_ids.album_id(album.id)
    return Release(
        id=catalog_id,
        title=album.name or "Unknown",
        artist_name=artist_name,
        source=Source.TIDAL,
        year=year,
        track_count=track_count,
        expected_track_count=expected,
        completeness=completeness,
        release_type=release_type,
        art_uri=art_uri,
        peak_quality_tier="",
        available_quality_tiers=frozenset(),
        catalog_quality_ready=False,
        catalog_release_id=catalog_id,
    )

def release_from_tidal(
    session: Session,
    album: TidalAlbum,
    *,
    owned_track_count: int | None = None,
    tracks: list[object] | None = None,
) -> Release:
    """Classified TIDAL release (album lookup or full get_release)."""
    artist_name, year, expected, track_count, completeness, release_type, art_uri = (
        _release_common_fields(session, album, owned_track_count=owned_track_count)
    )
    first_track_id = getattr(tracks[0], "id", None) if tracks else None
    openapi_track_payload = tidal_first_track_openapi_payload(
        album,
        fetch_tracks=not tracks,
        first_track_id=first_track_id,
    )
    supplemental_media_tags = media_tags_from_tidal_openapi_payload(openapi_track_payload)
    available_quality_tiers = classify_tidal_catalog(
        album,
        tracks=tracks,
        supplemental_media_tags=supplemental_media_tags,
    )
    peak_quality_tier = peak_quality_tier_from_tiers(available_quality_tiers)
    catalog_id = tidal_ids.album_id(album.id)
    return Release(
        id=catalog_id,
        title=album.name or "Unknown",
        artist_name=artist_name,
        source=Source.TIDAL,
        year=year,
        track_count=track_count,
        expected_track_count=expected,
        completeness=completeness,
        release_type=release_type,
        genre=genre_from_tidal_album(
            album,
            fetch_tracks=not tracks,
            first_track_id=first_track_id,
            openapi_track_payload=openapi_track_payload,
        ),
        art_uri=art_uri,
        peak_quality_tier=peak_quality_tier,
        available_quality_tiers=available_quality_tiers,
        catalog_quality_ready=bool(available_quality_tiers),
        catalog_release_id=catalog_id,
        peak_sample_rate_hz=peak_sample_rate_from_tidal_album(album),
        peak_bit_depth=peak_bit_depth_from_tidal_album(album),
    )

def track_from_tidal(session: Session, track: TidalTrack, *, album: TidalAlbum | None = None) -> Track:
    artists = track.artists or []
    artist_name = artists[0].name if artists else "Unknown Artist"
    release_title = None
    art_uri = None
    if album is not None:
        release_title = album.name
        art_uri = _album_art(session, album)
    elif track.album is not None:
        release_title = track.album.name
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
        release_title=release_title,
        source=Source.TIDAL,
        duration_sec=duration,
        art_uri=art_uri,
        track_number=int(track_number) if track_number is not None else None,
        disc_number=int(disc_number) if disc_number is not None else None,
    )
