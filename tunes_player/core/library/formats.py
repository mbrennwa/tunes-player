"""Tier 1 local audio formats (v0.1)."""

from __future__ import annotations

from pathlib import Path

TIER1_EXTENSIONS = frozenset({".flac", ".wav", ".aiff", ".aif", ".mp3", ".aac", ".ogg", ".m4a"})


def has_tier1_extension(path: Path) -> bool:
    """Fast extension check for directory walks — no file I/O."""
    return path.suffix.casefold() in TIER1_EXTENSIONS


def is_tier1_path(path: Path) -> bool:
    """Full check before indexing — probes .m4a codec."""
    if not has_tier1_extension(path):
        return False
    if path.suffix.casefold() == ".m4a":
        return _m4a_codec(path) in {"alac", "aac"}
    return True


def codec_for_path(path: Path) -> str | None:
    ext = path.suffix.casefold()
    if ext == ".flac":
        return "flac"
    if ext in {".wav", ".aiff", ".aif"}:
        return ext.lstrip(".")
    if ext == ".mp3":
        return "mp3"
    if ext == ".ogg":
        return "vorbis"
    if ext == ".aac":
        return "aac"
    if ext == ".m4a":
        return _m4a_codec(path)
    return None


def _m4a_codec(path: Path) -> str | None:
    try:
        from mutagen.mp4 import MP4
    except ImportError:
        return None

    try:
        audio = MP4(path)
    except Exception:
        return None

    codec = audio.info.codec if audio.info else None
    if codec == "alac":
        return "alac"
    if codec in {"mp4a", "aac"}:
        return "aac"
    return None
