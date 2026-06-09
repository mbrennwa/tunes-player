"""Tier 1 local audio formats (v0.1)."""

from __future__ import annotations

from pathlib import Path

TIER1_EXTENSIONS = frozenset({".flac", ".wav", ".aiff", ".aif", ".mp3", ".aac", ".ogg", ".m4a"})

_SKIP_SCAN_DIR_NAMES = frozenset({
    ".git",
    ".Trash",
    ".Trash-1000",
    ".AppleDouble",
    ".AppleDB",
    ".DS_Store",
    "__MACOSX",
    "@eaDir",
    "#recycle",
    "$RECYCLE.BIN",
    "System Volume Information",
    "lost+found",
    ".snapshot",
    ".zfs",
    "PhotoRec",
    ".cache",
    ".local",
    "node_modules",
})


def should_skip_scan_dir(name: str) -> bool:
    """Skip known junk/system directories during library walks."""
    if name in _SKIP_SCAN_DIR_NAMES:
        return True
    return name.startswith("@")


def has_tier1_extension(path: Path) -> bool:
    """Fast extension check for directory walks — no file I/O."""
    return has_tier1_extension_name(path.name)


def has_tier1_extension_name(name: str) -> bool:
    """Fast extension check on a filename — no file I/O."""
    dot = name.rfind(".")
    if dot < 0:
        return False
    return name[dot:].casefold() in TIER1_EXTENSIONS


def is_tier1_path(path: Path) -> bool:
    """Full check before indexing — probes .m4a codec."""
    if not has_tier1_extension(path):
        return False
    if path.suffix.casefold() == ".m4a":
        return mp4_codec_for_path(path) in {"alac", "aac"}
    return True


def codec_for_extension(ext: str) -> str | None:
    normalized = ext.casefold()
    if not normalized.startswith("."):
        normalized = f".{normalized}"
    if normalized == ".flac":
        return "flac"
    if normalized in {".wav", ".aiff", ".aif"}:
        return normalized.lstrip(".")
    if normalized == ".mp3":
        return "mp3"
    if normalized == ".ogg":
        return "vorbis"
    if normalized == ".aac":
        return "aac"
    return None


def codec_for_path(path: Path) -> str | None:
    ext = path.suffix.casefold()
    if ext == ".m4a":
        return mp4_codec_for_path(path)
    return codec_for_extension(ext)


def mp4_codec_from_info(info) -> str | None:
    codec = info.codec if info else None
    if codec == "alac":
        return "alac"
    if codec in {"mp4a", "aac"}:
        return "aac"
    return None


def mp4_codec_for_path(path: Path) -> str | None:
    try:
        from mutagen.mp4 import MP4
    except ImportError:
        return None

    try:
        audio = MP4(path)
    except Exception:
        return None

    return mp4_codec_from_info(audio.info)


def _m4a_codec(path: Path) -> str | None:
    return mp4_codec_for_path(path)
