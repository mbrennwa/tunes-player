"""Source-agnostic album art URIs (local cache now, remote URLs later)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import quote, unquote

LOCAL_ART_PREFIX = "tunes://art/local/"


def local_art_uri(album_id: str) -> str:
    """Canonical URI for embedded art cached for a local album."""
    return f"{LOCAL_ART_PREFIX}{quote(album_id, safe='')}"


def parse_art_uri(art_uri: str) -> tuple[str, str]:
    """Return (kind, payload). kind is local, http, file, or unknown."""
    if art_uri.startswith(LOCAL_ART_PREFIX):
        return "local", unquote(art_uri[len(LOCAL_ART_PREFIX) :])
    if art_uri.startswith(("http://", "https://")):
        return "http", art_uri
    if art_uri.startswith("file://"):
        return "file", art_uri[7:]
    return "unknown", art_uri


def art_cache_key(album_id: str) -> str:
    return hashlib.sha256(album_id.encode()).hexdigest()[:24]


def art_cache_path(data_dir: Path, album_id: str, mime_type: str) -> Path:
    ext = _extension_for_mime(mime_type)
    return data_dir / "art" / f"{art_cache_key(album_id)}{ext}"


def find_cached_art_path(data_dir: Path, album_id: str) -> Path | None:
    art_dir = data_dir / "art"
    if not art_dir.is_dir():
        return None
    prefix = art_cache_key(album_id)
    for path in art_dir.glob(f"{prefix}.*"):
        if path.is_file():
            return path
    return None


def resolve_art_url(art_uri: str | None, *, data_dir: Path) -> str | None:
    """Return a URL suitable for MPRIS/GTK (file:// or https://)."""
    if not art_uri:
        return None
    kind, payload = parse_art_uri(art_uri)
    if kind == "http":
        return payload
    if kind == "local":
        path = find_cached_art_path(data_dir, payload)
        return None if path is None else path.resolve().as_uri()
    if kind == "file":
        return Path(payload).resolve().as_uri()
    return None


def _extension_for_mime(mime_type: str) -> str:
    normalized = mime_type.lower().split(";", 1)[0].strip()
    if normalized in {"image/png", "png"}:
        return ".png"
    if normalized in {"image/webp", "webp"}:
        return ".webp"
    if normalized in {"image/gif", "gif"}:
        return ".gif"
    return ".jpg"
