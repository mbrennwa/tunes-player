"""Map tidalapi objects to Tunes domain models."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tunes_player.core.backends.tidal import ids as tidal_ids
from tunes_player.core.models import Album, Artist, Source, Track

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


def album_from_tidal(session: Session, album: TidalAlbum) -> Album:
    artists = album.artists or []
    artist_name = artists[0].name if artists else "Unknown Artist"
    year = None
    if album.release_date is not None:
        year = album.release_date.year
    return Album(
        id=tidal_ids.album_id(album.id),
        title=album.name or "Unknown Album",
        artist_name=artist_name,
        source=Source.TIDAL,
        year=year,
        track_count=int(album.num_tracks or 0),
        art_uri=_album_art(session, album),
    )


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
    return Track(
        id=tidal_ids.track_id(track.id),
        title=track.name or "Unknown Track",
        artist_name=artist_name,
        album_title=album_title,
        source=Source.TIDAL,
        duration_sec=duration,
        art_uri=art_uri,
    )
