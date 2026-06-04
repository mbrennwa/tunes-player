"""Map Qobuz API JSON to Tunes domain models."""

from __future__ import annotations

from typing import Any

from tunes_player.core.backends.qobuz import ids as qobuz_ids
from tunes_player.core.library.release_logic import (
    infer_release_completeness,
    release_type_from_metadata,
)
from tunes_player.core.models import (
    Artist,
    Release,
    Source,
    Track,
)


def cover_url(image: Any) -> str | None:
    """Resolve album art from Qobuz image hash or images dict."""
    if image is None:
        return None
    if isinstance(image, str) and len(image) >= 6:
        return (
            f"https://static.qobuz.com/images/covers/"
            f"{image[0:2]}/{image[2:4]}/{image[4:6]}/{image}_org.jpg"
        )
    if isinstance(image, dict):
        for key in ("large", "extralarge", "mega", "medium", "small", "thumbnail"):
            value = image.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value
        nested = image.get("large") or image.get("small")
        if isinstance(nested, str):
            return cover_url(nested)
    return None


def _artist_name_from_album(album: dict[str, Any]) -> str:
    artist = album.get("artist")
    if isinstance(artist, dict) and artist.get("name"):
        return str(artist["name"])
    artists = album.get("artists")
    if isinstance(artists, list) and artists:
        first = artists[0]
        if isinstance(first, dict) and first.get("name"):
            return str(first["name"])
    return "Unknown Artist"


def _year_from_album(album: dict[str, Any]) -> int | None:
    for key in ("release_date_stream", "released_at", "release_date_original"):
        raw = album.get(key)
        if isinstance(raw, str) and len(raw) >= 4:
            try:
                return int(raw[:4])
            except ValueError:
                continue
    return None


def release_from_qobuz(
    album: dict[str, Any],
    *,
    owned_track_count: int | None = None,
) -> Release:
    album_id = str(album.get("id") or album.get("qobuz_id") or "")
    title = str(album.get("title") or "Unknown")
    artist_name = _artist_name_from_album(album)
    expected = int(album.get("tracks_count") or 0) or None
    if owned_track_count is not None:
        track_count = owned_track_count
    else:
        tracks = album.get("tracks")
        if isinstance(tracks, dict):
            track_count = int(tracks.get("total") or len(tracks.get("items") or []))
        else:
            track_count = expected or 0
    product_type = album.get("product_type")
    type_raw = str(product_type) if product_type is not None else None
    completeness, expected = infer_release_completeness(
        track_count=track_count,
        is_synthetic=False,
        total_tracks_tag=expected,
        max_track_number=None,
    )
    release_type = release_type_from_metadata(type_raw, is_synthetic=False)
    duration = album.get("duration")
    duration_sec = float(duration) if duration is not None else None
    genre = album.get("genre")
    genre_name = genre.get("name") if isinstance(genre, dict) else None
    return Release(
        id=qobuz_ids.album_id(album_id),
        title=title,
        artist_name=artist_name,
        source=Source.QOBUZ,
        year=_year_from_album(album),
        track_count=track_count,
        expected_track_count=expected,
        completeness=completeness,
        release_type=release_type,
        genre=str(genre_name) if genre_name else None,
        art_uri=cover_url(album.get("image")),
        duration_sec=duration_sec,
    )


def artist_from_qobuz(artist: dict[str, Any]) -> Artist:
    return Artist(
        id=qobuz_ids.artist_id(artist.get("id", "")),
        name=str(artist.get("name") or "Unknown Artist"),
        source=Source.QOBUZ,
    )


def track_from_qobuz(
    track: dict[str, Any],
    *,
    album: dict[str, Any] | None = None,
) -> Track:
    track_id = str(track.get("id") or "")
    album_obj = album if album is not None else track.get("album")
    album_title = None
    art_uri = None
    if isinstance(album_obj, dict):
        album_title = str(album_obj.get("title") or "") or None
        art_uri = cover_url(album_obj.get("image"))
    performer = track.get("performer")
    if isinstance(performer, dict) and performer.get("name"):
        artist_name = str(performer["name"])
    elif isinstance(album_obj, dict):
        artist_name = _artist_name_from_album(album_obj)
    else:
        artist_name = "Unknown Artist"
    duration = track.get("duration")
    duration_sec = float(duration) if duration is not None else None
    track_number = track.get("track_number")
    disc_number = track.get("media_number")
    return Track(
        id=qobuz_ids.track_id(track_id),
        title=str(track.get("title") or "Unknown Track"),
        artist_name=artist_name,
        album_title=album_title,
        source=Source.QOBUZ,
        duration_sec=duration_sec,
        art_uri=art_uri,
        track_number=int(track_number) if track_number is not None else None,
        disc_number=int(disc_number) if disc_number is not None else None,
    )
