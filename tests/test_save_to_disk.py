"""Unit tests for save-to-disk helpers and job behavior."""

from __future__ import annotations

import io
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from tunes_player.core.library.store import FileMetadata
from tunes_player.core.models import Source, Track
from tunes_player.core.save_to_disk import (
    SaveCancelled,
    SaveToDiskError,
    build_track_path,
    cleanup_download_cache,
    download_https,
    infer_extension,
    is_mpd_uri,
    is_writable_dir,
    music_folder_for_path,
    promote_part_to_destination,
    remux_mpd,
    sanitize_filename,
    unique_destination,
    write_tags,
)


def _track(**kwargs: object) -> Track:
    values = {
        "id": "tidal:1",
        "title": "Song",
        "artist_name": "Artist",
        "release_title": "Album",
        "source": Source.TIDAL,
        "track_number": 1,
        "disc_number": 1,
    }
    values.update(kwargs)
    return Track(**values)  # type: ignore[arg-type]


class TestSaveToDiskHelpers(unittest.TestCase):
    def test_sanitize_filename(self) -> None:
        self.assertEqual(sanitize_filename('a/b:c*?"'), "a_b_c___")
        self.assertEqual(sanitize_filename("   "), "Unknown")

    def test_build_track_path(self) -> None:
        path = build_track_path(Path("/music"), _track(), ".flac")
        self.assertEqual(path, Path("/music/Artist/Album/01 - Song.flac"))
        path = build_track_path(
            Path("/music"),
            _track(disc_number=2, track_number=3),
            ".flac",
            include_disc=True,
        )
        self.assertEqual(path, Path("/music/Artist/Album/2-03 - Song.flac"))

    def test_infer_extension(self) -> None:
        meta = FileMetadata(
            path="",
            codec="flac",
            duration_sec=1.0,
            sample_rate=44100,
            bit_depth=16,
            channels=2,
        )
        self.assertEqual(infer_extension("https://x/a", meta), ".flac")
        self.assertEqual(
            infer_extension("file:///tmp/x.mpd", None, for_mpd=True),
            ".flac",
        )
        self.assertEqual(infer_extension("https://x/a.mp3", None), ".mp3")
        mp3_meta = FileMetadata(
            path="",
            codec="mp3",
            duration_sec=1.0,
            sample_rate=None,
            bit_depth=None,
            channels=2,
        )
        aac_meta = FileMetadata(
            path="",
            codec="aac",
            duration_sec=1.0,
            sample_rate=None,
            bit_depth=None,
            channels=2,
        )
        self.assertEqual(infer_extension("https://cdn.example/opaque", mp3_meta), ".mp3")
        self.assertEqual(infer_extension("https://cdn.example/opaque", aac_meta), ".m4a")
        self.assertEqual(
            infer_extension("file:///tmp/x.mpd", aac_meta, for_mpd=True),
            ".m4a",
        )

    def test_is_mpd_uri(self) -> None:
        self.assertTrue(is_mpd_uri("file:///tmp/tidal_1.mpd"))
        self.assertFalse(is_mpd_uri("https://cdn.example/a.flac"))

    def test_is_writable_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(is_writable_dir(Path(tmp)))

    def test_unique_destination_and_promote(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            part = root / "a.part"
            part.write_bytes(b"abc")
            dest = root / "out" / "track.flac"
            final = promote_part_to_destination(part, dest)
            self.assertTrue(final.is_file())
            self.assertFalse(part.exists())
            part2 = root / "b.part"
            part2.write_bytes(b"def")
            final2 = promote_part_to_destination(part2, dest)
            self.assertEqual(final2.name, "track (1).flac")
            self.assertEqual(unique_destination(final).name, "track (2).flac")

    def test_music_folder_for_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            music = root / "music"
            nested = music / "a" / "b"
            nested.mkdir(parents=True)
            other = root / "other"
            other.mkdir()
            track = nested / "t.flac"
            track.write_bytes(b"x")
            self.assertEqual(
                music_folder_for_path(track, [str(music), str(other)]),
                str(music.resolve()),
            )
            self.assertIsNone(music_folder_for_path(track, [str(other)]))

    def test_cleanup_download_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            cache = data / "download-cache" / "job"
            cache.mkdir(parents=True)
            stale = cache / "1.flac.tunes-partial"
            stale.write_bytes(b"x")
            removed = cleanup_download_cache(data)
            self.assertGreaterEqual(removed, 1)
            self.assertFalse(stale.exists())

    def test_download_https_writes_and_cancel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            part = Path(tmp) / "x.part"
            payload = b"hello-audio"

            class _Resp(io.BytesIO):
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

            with mock.patch(
                "tunes_player.core.save_to_disk.urlopen",
                return_value=_Resp(payload),
            ):
                download_https("https://example/a", part)
            self.assertEqual(part.read_bytes(), payload)

            cancel = threading.Event()
            cancel.set()
            with self.assertRaises(SaveCancelled):
                download_https("https://example/a", part, cancel_event=cancel)

    def test_remux_mpd_missing_ffmpeg(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mpd = Path(tmp) / "a.mpd"
            mpd.write_text("<MPD/>", encoding="utf-8")
            out = Path(tmp) / "out.m4a"
            with mock.patch(
                "tunes_player.core.save_to_disk.subprocess.Popen",
                side_effect=FileNotFoundError,
            ):
                with self.assertRaises(SaveToDiskError) as ctx:
                    remux_mpd(str(mpd), out)
            self.assertIn("ffmpeg", str(ctx.exception).casefold())

    def test_remux_mpd_uses_flac_encode_for_flac_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mpd = Path(tmp) / "a.mpd"
            mpd.write_text("<MPD/>", encoding="utf-8")
            out = Path(tmp) / "out.flac"
            seen: list[list[str]] = []

            class _Proc:
                def __init__(self) -> None:
                    self.stderr = io.BytesIO(b"")
                    self._done = False

                def wait(self, timeout=None):
                    self._done = True
                    return 0

                def poll(self):
                    return 0 if self._done else None

                def kill(self) -> None:
                    return None

            def _popen(cmd, **_kwargs):
                seen.append(list(cmd))
                return _Proc()

            with mock.patch(
                "tunes_player.core.save_to_disk.subprocess.Popen",
                side_effect=_popen,
            ):
                remux_mpd(str(mpd), out)
            self.assertTrue(seen)
            self.assertIn("-c:a", seen[0])
            self.assertIn("flac", seen[0])
            self.assertIn("-protocol_whitelist", seen[0])

    def test_remux_mpd_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mpd = Path(tmp) / "a.mpd"
            mpd.write_text("<MPD/>", encoding="utf-8")
            out = Path(tmp) / "out.m4a"

            class _Proc:
                def __init__(self) -> None:
                    self.stderr = io.BytesIO(b"")
                    self._done = False

                def wait(self, timeout=None):
                    self._done = True
                    return 0

                def poll(self):
                    return 0 if self._done else None

                def kill(self) -> None:
                    return None

            with mock.patch(
                "tunes_player.core.save_to_disk.subprocess.Popen",
                return_value=_Proc(),
            ):
                remux_mpd(mpd.as_uri(), out)
            # ffmpeg mock does not create file; ensure no exception path
            self.assertTrue(True)

    def test_write_tags_flac_roundtrip(self) -> None:
        try:
            from mutagen.flac import FLAC
        except ImportError:
            self.skipTest("mutagen unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.flac"
            # Minimal writable FLAC via mutagen needs an existing file; create with ffmpeg if present.
            import shutil
            import subprocess

            if shutil.which("ffmpeg") is None:
                self.skipTest("ffmpeg unavailable for FLAC fixture")
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "anullsrc=r=44100:cl=mono",
                    "-t",
                    "0.05",
                    "-c:a",
                    "flac",
                    str(path),
                ],
                check=True,
            )
            write_tags(
                path,
                _track(title="Hello", artist_name="World", release_title="LP", track_number=2),
            )
            audio = FLAC(path)
            self.assertEqual(audio["title"], ["Hello"])
            self.assertEqual(audio["artist"], ["World"])
            self.assertEqual(audio["album"], ["LP"])
            self.assertEqual(audio["tracknumber"], ["2"])


class TestSaveFolderConfig(unittest.TestCase):
    def test_download_folder_roundtrip(self) -> None:
        from tunes_player.core.config import ConfigManager

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            manager = ConfigManager(path)
            manager.load()
            manager.set_download_folder(str(Path(tmp) / "Tunes Downloads"))
            other = ConfigManager(path)
            other.load()
            self.assertIsNotNone(other.config.download_folder)
            self.assertTrue(other.config.download_folder.endswith("Tunes Downloads"))
            # Legacy last_save_folder is ignored (not migrated into download_folder).
            raw = path.read_text(encoding="utf-8")
            self.assertNotIn("last_save_folder", raw)


class TestPlayerServiceSaveHook(unittest.TestCase):
    def test_unwritable_dest_rejected(self) -> None:
        from tunes_player.core.config import ConfigManager
        from tunes_player.core.services import PlayerService

        with tempfile.TemporaryDirectory() as tmp:
            cfg = ConfigManager(Path(tmp) / "config.json")
            cfg.load()
            service = PlayerService(config=cfg)
            try:
                blocker = Path(tmp) / "not-a-dir"
                blocker.write_text("x", encoding="utf-8")
                with self.assertRaises(SaveToDiskError):
                    service.start_save_to_disk(
                        tracks=[_track()],
                        dest_dir=str(blocker),
                    )
            finally:
                service.shutdown()

    def _run_fake_save(
        self,
        *,
        music_folder: Path | None,
        dest: Path,
    ) -> list[tuple[str, list[str]]]:
        from tunes_player.core.backends.playable import PlayableSource
        from tunes_player.core.config import ConfigManager
        from tunes_player.core.services import PlayerService

        root = dest.parent if music_folder is None else music_folder.parent
        cfg_path = root / "config.json"
        manager = ConfigManager(cfg_path)
        manager.load()
        if music_folder is not None:
            manager.add_music_folder(str(music_folder))
        service = PlayerService(config=manager)
        scanned: list[tuple[str, list[str]]] = []

        def _record_scan(*, folder: str, add_paths=None, remove_paths=None):
            scanned.append((folder, list(add_paths or [])))

        service.enqueue_incremental_scan = _record_scan  # type: ignore[method-assign]
        track = _track()
        source = PlayableSource(
            uri="https://example.com/a.flac",
            metadata=track,
            stream_metadata=FileMetadata(
                path="",
                codec="flac",
                duration_sec=1.0,
                sample_rate=44100,
                bit_depth=16,
                channels=2,
            ),
        )

        def fake_resolve(*_args, **_kwargs):
            return source

        with (
            mock.patch(
                "tunes_player.core.services.resolve_track",
                side_effect=fake_resolve,
            ),
            mock.patch(
                "tunes_player.core.services.download_https",
                side_effect=lambda url, part, cancel_event=None: part.write_bytes(b"flac"),
            ),
            mock.patch(
                "tunes_player.core.services.write_tags",
            ),
            mock.patch(
                "tunes_player.core.services.fetch_cover_bytes",
                return_value=None,
            ),
            mock.patch(
                "tunes_player.core.services.download_cache_dir",
                return_value=root / "download-cache",
            ),
            mock.patch(
                "tunes_player.core.services.cleanup_download_cache",
                return_value=0,
            ),
        ):
            service.start_save_to_disk(tracks=[track], dest_dir=str(dest))
            thread = service._download_thread
            assert thread is not None
            thread.join(timeout=10)
        self.assertEqual(service.download_saved_count, 1)
        service.shutdown()
        return scanned

    def test_incremental_scan_when_dest_is_music_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            music = root / "music"
            music.mkdir()
            scanned = self._run_fake_save(music_folder=music, dest=music)
            self.assertEqual(len(scanned), 1)
            self.assertEqual(scanned[0][0], str(music.resolve()))
            self.assertEqual(len(scanned[0][1]), 1)

    def test_no_incremental_scan_outside_music_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            music = root / "music"
            downloads = root / "downloads"
            music.mkdir()
            downloads.mkdir()
            scanned = self._run_fake_save(music_folder=music, dest=downloads)
            self.assertEqual(scanned, [])


if __name__ == "__main__":
    unittest.main()
