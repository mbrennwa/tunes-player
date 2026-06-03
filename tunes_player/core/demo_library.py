"""Sample library for UI development until scanning lands."""

from __future__ import annotations

from tunes_player.core.models import Album, Artist, Source, Track

_DEMO_ARTISTS = (
    Artist(id="local:artist:coltrane", name="John Coltrane", source=Source.LOCAL),
    Artist(id="local:artist:radiohead", name="Radiohead", source=Source.LOCAL),
    Artist(id="local:artist:daft", name="Daft Punk", source=Source.LOCAL),
)

_DEMO_ALBUMS = (
    Album(
        id="local:album:love-supreme",
        title="A Love Supreme",
        artist_name="John Coltrane",
        source=Source.LOCAL,
        year=1965,
        track_count=4,
    ),
    Album(
        id="local:album:kind-of-blue",
        title="Kind of Blue",
        artist_name="John Coltrane",
        source=Source.LOCAL,
        year=1959,
        track_count=2,
    ),
    Album(
        id="local:album:ok-computer",
        title="OK Computer",
        artist_name="Radiohead",
        source=Source.LOCAL,
        year=1997,
        track_count=3,
    ),
    Album(
        id="local:album:kid-a",
        title="Kid A",
        artist_name="Radiohead",
        source=Source.LOCAL,
        year=2000,
        track_count=2,
    ),
    Album(
        id="local:album:discovery",
        title="Discovery",
        artist_name="Daft Punk",
        source=Source.LOCAL,
        year=2001,
        track_count=3,
    ),
    Album(
        id="local:album:random-access",
        title="Random Access Memories",
        artist_name="Daft Punk",
        source=Source.LOCAL,
        year=2013,
        track_count=2,
    ),
)

_DEMO_TRACKS: dict[str, tuple[Track, ...]] = {
    "local:album:love-supreme": (
        Track(
            id="local:track:als-1",
            title="Acknowledgement",
            artist_name="John Coltrane",
            album_title="A Love Supreme",
            source=Source.LOCAL,
            duration_sec=468.0,
        ),
        Track(
            id="local:track:als-2",
            title="Resolution",
            artist_name="John Coltrane",
            album_title="A Love Supreme",
            source=Source.LOCAL,
            duration_sec=442.0,
        ),
        Track(
            id="local:track:als-3",
            title="Pursuance",
            artist_name="John Coltrane",
            album_title="A Love Supreme",
            source=Source.LOCAL,
            duration_sec=645.0,
        ),
        Track(
            id="local:track:als-4",
            title="Psalm",
            artist_name="John Coltrane",
            album_title="A Love Supreme",
            source=Source.LOCAL,
            duration_sec=420.0,
        ),
    ),
    "local:album:kind-of-blue": (
        Track(
            id="local:track:kob-1",
            title="So What",
            artist_name="John Coltrane",
            album_title="Kind of Blue",
            source=Source.LOCAL,
            duration_sec=562.0,
        ),
        Track(
            id="local:track:kob-2",
            title="Blue in Green",
            artist_name="John Coltrane",
            album_title="Kind of Blue",
            source=Source.LOCAL,
            duration_sec=338.0,
        ),
    ),
    "local:album:ok-computer": (
        Track(
            id="local:track:okc-1",
            title="Airbag",
            artist_name="Radiohead",
            album_title="OK Computer",
            source=Source.LOCAL,
            duration_sec=285.0,
        ),
        Track(
            id="local:track:okc-2",
            title="Paranoid Android",
            artist_name="Radiohead",
            album_title="OK Computer",
            source=Source.LOCAL,
            duration_sec=383.0,
        ),
        Track(
            id="local:track:okc-3",
            title="No Surprises",
            artist_name="Radiohead",
            album_title="OK Computer",
            source=Source.LOCAL,
            duration_sec=228.0,
        ),
    ),
    "local:album:kid-a": (
        Track(
            id="local:track:kida-1",
            title="Everything In Its Right Place",
            artist_name="Radiohead",
            album_title="Kid A",
            source=Source.LOCAL,
            duration_sec=251.0,
        ),
        Track(
            id="local:track:kida-2",
            title="Idioteque",
            artist_name="Radiohead",
            album_title="Kid A",
            source=Source.LOCAL,
            duration_sec=309.0,
        ),
    ),
    "local:album:discovery": (
        Track(
            id="local:track:disc-1",
            title="One More Time",
            artist_name="Daft Punk",
            album_title="Discovery",
            source=Source.LOCAL,
            duration_sec=320.0,
        ),
        Track(
            id="local:track:disc-2",
            title="Digital Love",
            artist_name="Daft Punk",
            album_title="Discovery",
            source=Source.LOCAL,
            duration_sec=301.0,
        ),
        Track(
            id="local:track:disc-3",
            title="Harder, Better, Faster, Stronger",
            artist_name="Daft Punk",
            album_title="Discovery",
            source=Source.LOCAL,
            duration_sec=224.0,
        ),
    ),
    "local:album:random-access": (
        Track(
            id="local:track:ram-1",
            title="Give Life Back to Music",
            artist_name="Daft Punk",
            album_title="Random Access Memories",
            source=Source.LOCAL,
            duration_sec=274.0,
        ),
        Track(
            id="local:track:ram-2",
            title="Instant Crush",
            artist_name="Daft Punk",
            album_title="Random Access Memories",
            source=Source.LOCAL,
            duration_sec=337.0,
        ),
    ),
}

_QUALITY_HINTS: dict[str, str] = {
    "local:album:love-supreme": "24-bit / 96 kHz",
    "local:album:kind-of-blue": "16-bit / 44.1 kHz",
    "local:album:ok-computer": "16-bit / 44.1 kHz",
    "local:album:kid-a": "16-bit / 44.1 kHz",
    "local:album:discovery": "MP3",
    "local:album:random-access": "24-bit / 48 kHz",
}


def demo_artists() -> tuple[Artist, ...]:
    return _DEMO_ARTISTS


def demo_albums() -> tuple[Album, ...]:
    return _DEMO_ALBUMS


def demo_tracks_for_album(album_id: str) -> tuple[Track, ...]:
    return _DEMO_TRACKS.get(album_id, ())


def demo_track_by_id(track_id: str) -> Track | None:
    for tracks in _DEMO_TRACKS.values():
        for track in tracks:
            if track.id == track_id:
                return track
    return None


def demo_album_by_id(album_id: str) -> Album | None:
    for album in _DEMO_ALBUMS:
        if album.id == album_id:
            return album
    return None


def demo_album_id_for_track(track_id: str) -> str | None:
    for album_id, tracks in _DEMO_TRACKS.items():
        if any(track.id == track_id for track in tracks):
            return album_id
    return None


def demo_quality_hint_for_track(track_id: str) -> str:
    album_id = demo_album_id_for_track(track_id)
    if album_id is None:
        return "Local file"
    return _QUALITY_HINTS.get(album_id, "Local file")


def demo_all_tracks() -> tuple[Track, ...]:
    return tuple(track for tracks in _DEMO_TRACKS.values() for track in tracks)
