"""Opaque Qobuz entity IDs used in core models."""

from __future__ import annotations


def track_id(qobuz_track_id: int | str) -> str:
    return f"qobuz:track:{qobuz_track_id}"


def album_id(qobuz_album_id: int | str) -> str:
    return f"qobuz:album:{qobuz_album_id}"


def artist_id(qobuz_artist_id: int | str) -> str:
    return f"qobuz:artist:{qobuz_artist_id}"


def parse_prefixed_id(value: str, kind: str) -> str | None:
    prefix = f"qobuz:{kind}:"
    if not value.startswith(prefix):
        return None
    suffix = value[len(prefix) :]
    return suffix if suffix else None
