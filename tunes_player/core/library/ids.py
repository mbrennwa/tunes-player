"""Stable opaque IDs for local library entities."""

from __future__ import annotations

import hashlib


def make_id(prefix: str, *parts: str) -> str:
    payload = "|".join(part.casefold().strip() for part in parts)
    digest = hashlib.sha256(payload.encode()).hexdigest()[:16]
    return f"{prefix}:{digest}"


def release_id(album_artist: str, album: str) -> str:
    return make_id("local:album", album_artist, album)


def synthetic_release_id(track_id: str) -> str:
    return f"local:release:synthetic:{track_id}"


def album_id(album_artist: str, album: str) -> str:
    return release_id(album_artist, album)


def artist_id(name: str) -> str:
    return make_id("local:artist", name)


def track_id(path: str) -> str:
    return make_id("local:track", path)
