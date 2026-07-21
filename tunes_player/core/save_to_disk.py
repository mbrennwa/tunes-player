"""Download streaming tracks to local music files."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

from tunes_player.core.library.store import FileMetadata
from tunes_player.core.models import Track

log = logging.getLogger(__name__)

_USER_AGENT = "Tunes/0.1"
_CHUNK_SIZE = 256 * 1024
_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_PART_SUFFIX = ".tunes-partial"


class SaveToDiskError(Exception):
    """User-visible failure while saving a track or destination."""


class SaveCancelled(Exception):
    """Download job was cancelled."""


@dataclass(frozen=True, slots=True)
class SavedTrackResult:
    track_id: str
    path: Path


def download_cache_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "download-cache"


def cleanup_download_cache(data_dir: Path) -> int:
    """Remove stale staging files under download-cache. Returns files removed."""
    root = download_cache_dir(data_dir)
    if not root.is_dir():
        return 0
    removed = 0
    try:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            name = path.name
            if name.endswith(_PART_SUFFIX) or name.endswith(".part") or name.endswith(".mpd"):
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    log.debug("Could not remove stale download cache file %s", path)
        for path in sorted(root.rglob("*"), reverse=True):
            if path.is_dir():
                try:
                    path.rmdir()
                except OSError:
                    pass
    except OSError:
        log.exception("Failed cleaning download cache under %s", root)
    return removed


def sanitize_filename(name: str, *, fallback: str = "Unknown") -> str:
    cleaned = _INVALID_FILENAME_CHARS.sub("_", (name or "").strip())
    cleaned = cleaned.strip(" .")
    return cleaned or fallback


def build_track_path(
    dest_root: Path,
    track: Track,
    ext: str,
    *,
    include_disc: bool = False,
) -> Path:
    """Build ``Artist/Album/[d-]NN - Title.ext`` under dest_root."""
    artist = sanitize_filename(track.artist_name, fallback="Unknown Artist")
    album = sanitize_filename(track.release_title or "Unknown Album", fallback="Unknown Album")
    title = sanitize_filename(track.title, fallback="Unknown Title")
    number = track.track_number if track.track_number and track.track_number > 0 else 1
    suffix = ext if ext.startswith(".") else f".{ext}"
    if include_disc and track.disc_number and track.disc_number > 1:
        filename = f"{track.disc_number}-{number:02d} - {title}{suffix}"
    else:
        filename = f"{number:02d} - {title}{suffix}"
    return Path(dest_root) / artist / album / filename


def is_writable_dir(path: Path) -> bool:
    target = Path(path)
    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / f".tunes-write-probe-{os.getpid()}"
        probe.write_bytes(b"")
        probe.unlink()
        return True
    except OSError:
        return False


def is_mpd_uri(uri: str) -> bool:
    text = (uri or "").casefold()
    if text.endswith(".mpd"):
        return True
    if text.startswith("file://") and ".mpd" in text:
        return True
    return False


def mpd_path_from_uri(uri: str) -> Path:
    if uri.startswith("file://"):
        parsed = urlparse(uri)
        return Path(unquote(parsed.path))
    return Path(uri)


def infer_extension(
    uri: str,
    stream_metadata: FileMetadata | None,
    *,
    for_mpd: bool = False,
) -> str:
    codec = (stream_metadata.codec if stream_metadata is not None else None) or ""
    codec_key = codec.casefold()
    if "flac" in codec_key:
        return ".flac"
    if codec_key in {"mp3", "mpeg"}:
        return ".mp3"
    if codec_key in {"aac", "mp4", "m4a", "alac"}:
        return ".m4a"
    if for_mpd:
        # TIDAL DASH without codec hint: prefer FLAC (common for hi-res MPD).
        return ".flac"
    path = urlparse(uri).path.casefold()
    for candidate in (".flac", ".mp3", ".m4a", ".aac", ".ogg"):
        if path.endswith(candidate):
            return ".m4a" if candidate == ".aac" else candidate
    return ".flac"


def download_https(
    url: str,
    part_path: Path,
    *,
    cancel_event: threading.Event | None = None,
    timeout: float = 60.0,
) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise SaveCancelled()
    part_path.parent.mkdir(parents=True, exist_ok=True)
    request = Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urlopen(request, timeout=timeout) as response:
            with part_path.open("wb") as handle:
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        raise SaveCancelled()
                    chunk = response.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    handle.write(chunk)
    except SaveCancelled:
        _unlink_quiet(part_path)
        raise
    except HTTPError as exc:
        _unlink_quiet(part_path)
        raise SaveToDiskError(f"Download failed (HTTP {exc.code})") from exc
    except URLError as exc:
        _unlink_quiet(part_path)
        raise SaveToDiskError(f"Download failed: {exc.reason}") from exc
    except OSError as exc:
        _unlink_quiet(part_path)
        raise SaveToDiskError(f"Download failed: {exc}") from exc


def remux_mpd(
    mpd_uri_or_path: str,
    part_path: Path,
    *,
    cancel_event: threading.Event | None = None,
) -> None:
    """Remux a DASH/MPD stream to a single file via ffmpeg.

    TIDAL hi-res is often FLAC-in-DASH (``audio/mp4`` segments). That cannot be
    ``-c copy``'d into ``.m4a``/MP4, so FLAC targets are decoded to native
    ``.flac``. AAC/DASH uses ``-c copy`` into ``.m4a``.
    """
    if cancel_event is not None and cancel_event.is_set():
        raise SaveCancelled()
    mpd_path = mpd_path_from_uri(mpd_uri_or_path)
    if not mpd_path.is_file():
        raise SaveToDiskError(f"MPD manifest not found: {mpd_path}")
    part_path.parent.mkdir(parents=True, exist_ok=True)
    suffix = part_path.suffix.casefold()
    if suffix == ".flac":
        # Native FLAC muxer (lossless decode from DASH FLAC).
        codec_args = ["-c:a", "flac"]
    else:
        codec_args = ["-c", "copy"]
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        # Local .mpd references https:// segment URLs.
        "-protocol_whitelist",
        "file,http,https,tcp,tls,crypto",
        "-i",
        str(mpd_path),
        *codec_args,
        str(part_path),
    ]
    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise SaveToDiskError(
            "ffmpeg is required to save this TIDAL track (DASH/MPD stream)."
        ) from exc
    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                process.kill()
                process.wait(timeout=5)
                _unlink_quiet(part_path)
                raise SaveCancelled()
            try:
                code = process.wait(timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                continue
        stderr = b""
        if process.stderr is not None:
            stderr = process.stderr.read() or b""
        if code != 0:
            _unlink_quiet(part_path)
            detail = stderr.decode("utf-8", errors="replace").strip()
            message = "ffmpeg remux failed"
            if detail:
                message = f"{message}: {detail.splitlines()[-1]}"
            raise SaveToDiskError(message)
    finally:
        if process.poll() is None:
            process.kill()


def fetch_cover_bytes(art_uri: str | None, *, timeout: float = 15.0) -> bytes | None:
    if not art_uri:
        return None
    if not art_uri.startswith(("http://", "https://")):
        return None
    try:
        request = Request(art_uri, headers={"User-Agent": _USER_AGENT})
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except (HTTPError, URLError, OSError, ValueError):
        log.debug("Could not fetch cover art from %s", art_uri, exc_info=True)
        return None


def write_tags(
    path: Path,
    track: Track,
    *,
    cover_bytes: bytes | None = None,
) -> None:
    suffix = path.suffix.casefold()
    if suffix == ".flac":
        _write_flac_tags(path, track, cover_bytes=cover_bytes)
    elif suffix == ".mp3":
        _write_mp3_tags(path, track, cover_bytes=cover_bytes)
    elif suffix in {".m4a", ".aac", ".mp4"}:
        _write_mp4_tags(path, track, cover_bytes=cover_bytes)
    else:
        log.debug("No tag writer for %s; leaving untagged", path)


def unique_destination(path: Path) -> Path:
    """Avoid overwriting: ``file.flac``, ``file (1).flac``, …"""
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    index = 1
    while True:
        candidate = parent / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def promote_part_to_destination(part_path: Path, dest_path: Path) -> Path:
    final_path = unique_destination(dest_path)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    os.replace(part_path, final_path)
    return final_path


def music_folder_for_path(path: Path, music_folders: list[str]) -> str | None:
    resolved = path.resolve()
    best: str | None = None
    best_len = -1
    for folder in music_folders:
        try:
            root = Path(folder).expanduser().resolve()
        except OSError:
            continue
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        root_len = len(str(root))
        if root_len > best_len:
            best = str(root)
            best_len = root_len
    return best


def tracks_need_disc_prefix(tracks: list[Track]) -> bool:
    discs = {t.disc_number for t in tracks if t.disc_number is not None}
    return any(d > 1 for d in discs if d is not None)


def _unlink_quiet(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _write_flac_tags(
    path: Path,
    track: Track,
    *,
    cover_bytes: bytes | None,
) -> None:
    from mutagen.flac import FLAC, Picture

    audio = FLAC(path)
    audio["title"] = [track.title]
    audio["artist"] = [track.artist_name]
    if track.release_title:
        audio["album"] = [track.release_title]
    if track.track_number:
        audio["tracknumber"] = [str(track.track_number)]
    if track.disc_number:
        audio["discnumber"] = [str(track.disc_number)]
    if cover_bytes:
        picture = Picture()
        picture.type = 3
        picture.mime = _image_mime(cover_bytes)
        picture.desc = "Cover"
        picture.data = cover_bytes
        audio.clear_pictures()
        audio.add_picture(picture)
    audio.save()


def _write_mp3_tags(
    path: Path,
    track: Track,
    *,
    cover_bytes: bytes | None,
) -> None:
    from mutagen.easyid3 import EasyID3
    from mutagen.id3 import APIC, ID3
    from mutagen.mp3 import MP3

    try:
        tags = EasyID3(path)
    except Exception:
        audio = MP3(path)
        audio.add_tags()
        audio.save()
        tags = EasyID3(path)
    tags["title"] = track.title
    tags["artist"] = track.artist_name
    if track.release_title:
        tags["album"] = track.release_title
    if track.track_number:
        tags["tracknumber"] = str(track.track_number)
    if track.disc_number:
        tags["discnumber"] = str(track.disc_number)
    tags.save()
    if cover_bytes:
        id3 = ID3(path)
        id3.delall("APIC")
        id3.add(
            APIC(
                encoding=3,
                mime=_image_mime(cover_bytes),
                type=3,
                desc="Cover",
                data=cover_bytes,
            )
        )
        id3.save()


def _write_mp4_tags(
    path: Path,
    track: Track,
    *,
    cover_bytes: bytes | None,
) -> None:
    from mutagen.mp4 import MP4, MP4Cover

    audio = MP4(path)
    if audio.tags is None:
        audio.add_tags()
    assert audio.tags is not None
    audio.tags["\xa9nam"] = [track.title]
    audio.tags["\xa9ART"] = [track.artist_name]
    if track.release_title:
        audio.tags["\xa9alb"] = [track.release_title]
    if track.track_number:
        audio.tags["trkn"] = [(int(track.track_number), 0)]
    if track.disc_number:
        audio.tags["disk"] = [(int(track.disc_number), 0)]
    if cover_bytes:
        fmt = (
            MP4Cover.FORMAT_PNG
            if _image_mime(cover_bytes) == "image/png"
            else MP4Cover.FORMAT_JPEG
        )
        audio.tags["covr"] = [MP4Cover(cover_bytes, imageformat=fmt)]
    audio.save()


def _image_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    return "image/jpeg"


def staging_part_path(cache_root: Path, job_id: str, index: int, ext: str) -> Path:
    suffix = ext if ext.startswith(".") else f".{ext}"
    return cache_root / job_id / f"{index:04d}{suffix}{_PART_SUFFIX}"


def rmtree_quiet(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
