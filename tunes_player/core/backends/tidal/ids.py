"""Opaque Tidal entity IDs used in core models."""

from __future__ import annotations


def track_id(tidal_track_id: int | str) -> str:
    return f"tidal:track:{tidal_track_id}"


def album_id(tidal_album_id: int | str) -> str:
    return f"tidal:album:{tidal_album_id}"


def artist_id(tidal_artist_id: int | str) -> str:
    return f"tidal:artist:{tidal_artist_id}"


def parse_prefixed_id(value: str, kind: str) -> int | None:
    prefix = f"tidal:{kind}:"
    if not value.startswith(prefix):
        return None
    suffix = value[len(prefix) :]
    try:
        return int(suffix)
    except ValueError:
        return None
