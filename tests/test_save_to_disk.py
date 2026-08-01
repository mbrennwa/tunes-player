"""Unit tests for save-to-disk helpers and job behavior."""

from __future__ import annotations

import io
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from tunes_player.core.library import ids as library_ids
from tunes_player.core.library.store import FileMetadata
from tunes_player.core.models import Release, Source, Track
from tunes_player.core.save_to_disk import (
    MAX_SAVE_CONCURRENCY,
    STATUS_INTERRUPTED,
    DownloadJobManifest,
    ExistingLocalMatch,
    SaveCancelled,
    SaveToDiskError,
    album_folder_for_save,
    build_track_path,
    cleanup_download_cache,
    destination_file_exists,
    download_https,
    download_job_label,
    find_existing_local_match,
    infer_extension,
    is_mpd_uri,
    is_writable_dir,
    list_interrupted_jobs,
    music_folder_for_path,
    promote_part_to_destination,
    remux_mpd,
    sanitize_filename,
    save_job_manifest,
    serialize_track,
    staging_part_path,
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


class TestExistingLocalMatch(unittest.TestCase):
    def test_library_exact_release_id(self) -> None:
        track = _track(artist_name="Artist", release_title="Album")
        local = Release(
            id=library_ids.release_id("Artist", "Album"),
            title="Album",
            artist_name="Artist",
            source=Source.LOCAL,
            track_count=1,
        )
        match = find_existing_local_match(
            [track],
            get_release=lambda rid: local if rid == local.id else None,
            search_releases=lambda _q: [],
        )
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.kind, "library")
        self.assertEqual(match.label, "Artist – Album")

    def test_library_search_fallback_casefold(self) -> None:
        track = _track(artist_name="Artist", release_title="Album")
        cand = Release(
            id="local:album:other",
            title="album",
            artist_name="artist",
            source=Source.LOCAL,
            track_count=2,
        )
        match = find_existing_local_match(
            [track],
            get_release=lambda _rid: None,
            search_releases=lambda _q: [cand],
        )
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.kind, "library")

    def test_downloads_path_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            track = _track()
            path = build_track_path(dest, track, ".flac")
            path.parent.mkdir(parents=True)
            path.write_bytes(b"x")
            match = find_existing_local_match(
                [track],
                get_release=lambda _rid: None,
                search_releases=lambda _q: [],
                download_folder=dest,
            )
            self.assertIsNotNone(match)
            assert match is not None
            self.assertEqual(match.kind, "downloads")
            self.assertTrue(destination_file_exists(dest, track))

    def test_no_match(self) -> None:
        match = find_existing_local_match(
            [_track()],
            get_release=lambda _rid: None,
            search_releases=lambda _q: [],
            download_folder=Path("/tmp/nonexistent-tunes-dl"),
        )
        self.assertIsNone(match)

    def test_conflict_dialog_body(self) -> None:
        from tunes_player.ui.gtk.save_to_disk_menu import conflict_dialog_body

        self.assertEqual(
            conflict_dialog_body(ExistingLocalMatch("library", "A – B")),
            "Already in library: A – B",
        )
        self.assertEqual(
            conflict_dialog_body(ExistingLocalMatch("downloads", "A – B")),
            "Already in Downloads: A – B",
        )


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

    def test_cleanup_spares_interrupted_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            job_dir = data / "download-cache" / "abc123"
            job_dir.mkdir(parents=True)
            staged = job_dir / "0001.flac.tunes-partial"
            staged.write_bytes(b"audio")
            save_job_manifest(
                job_dir,
                DownloadJobManifest(
                    version=1,
                    job_id="abc123",
                    dest_dir=str(data / "downloads"),
                    track_ids=["tidal:1"],
                    tracks=[serialize_track(_track())],
                    completed_indices=[1],
                    status=STATUS_INTERRUPTED,
                ),
            )
            removed = cleanup_download_cache(data)
            self.assertEqual(removed, 0)
            self.assertTrue(staged.is_file())
            jobs = list_interrupted_jobs(data)
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0][1].job_id, "abc123")

    def test_staging_part_path_suffix(self) -> None:
        path = staging_part_path(Path("/cache"), "job", 3, ".flac")
        self.assertEqual(path, Path("/cache/job/0003.flac.tunes-partial"))

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

    def test_write_tags_on_tunes_partial_suffix(self) -> None:
        try:
            from mutagen.flac import FLAC
        except ImportError:
            self.skipTest("mutagen unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            import shutil
            import subprocess

            if shutil.which("ffmpeg") is None:
                self.skipTest("ffmpeg unavailable for FLAC fixture")
            flac_path = Path(tmp) / "0001.flac"
            path = Path(tmp) / "0001.flac.tunes-partial"
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
                    str(flac_path),
                ],
                check=True,
            )
            flac_path.rename(path)
            write_tags(
                path,
                _track(title="Partial", artist_name="Artist", release_title="Alb"),
            )
            audio = FLAC(path)
            self.assertEqual(audio["title"], ["Partial"])
            self.assertEqual(audio["artist"], ["Artist"])
            self.assertEqual(audio["album"], ["Alb"])

    def test_media_extension_strips_partial(self) -> None:
        from tunes_player.core.save_to_disk import media_extension

        self.assertEqual(media_extension(Path("a.flac")), ".flac")
        self.assertEqual(media_extension(Path("0001.flac.tunes-partial")), ".flac")
        self.assertEqual(media_extension(Path("0002.mp3.tunes-partial")), ".mp3")
        self.assertEqual(media_extension(Path("x.m4a.tunes-partial")), ".m4a")


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

    def _make_service(self, root: Path, *, dest: Path):
        from tunes_player.core.backends.playable import PlayableSource
        from tunes_player.core.config import ConfigManager
        from tunes_player.core.services import PlayerService

        manager = ConfigManager(root / "config.json")
        manager.load()
        service = PlayerService(config=manager)
        cache = root / "download-cache"

        def fake_resolve(store, track_id, **_kwargs):
            track = _track(
                id=track_id,
                title=f"Song-{track_id.split(':')[-1]}",
                track_number=int(track_id.split(":")[-1]),
            )
            return PlayableSource(
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

        def _cache_dir(_data_dir=None):
            return cache

        patches = [
            mock.patch(
                "tunes_player.core.services.resolve_track",
                side_effect=fake_resolve,
            ),
            mock.patch(
                "tunes_player.core.services.download_https",
                side_effect=lambda url, part, cancel_event=None: part.write_bytes(
                    b"flac"
                ),
            ),
            mock.patch("tunes_player.core.services.write_tags"),
            mock.patch(
                "tunes_player.core.services.fetch_cover_bytes",
                return_value=None,
            ),
            mock.patch(
                "tunes_player.core.services.download_cache_dir",
                side_effect=_cache_dir,
            ),
            mock.patch(
                "tunes_player.core.save_to_disk.download_cache_dir",
                side_effect=_cache_dir,
            ),
            mock.patch(
                "tunes_player.core.services.cleanup_download_cache",
                return_value=0,
            ),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        return service, cache

    def test_single_track_promotes_on_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "downloads"
            dest.mkdir()
            service, _cache = self._make_service(root, dest=dest)
            try:
                service.start_save_to_disk(tracks=[_track()], dest_dir=str(dest))
                thread = service._download_thread
                assert thread is not None
                thread.join(timeout=10)
                self.assertEqual(service.download_saved_count, 1)
                finals = list(dest.rglob("*.flac"))
                self.assertEqual(len(finals), 1)
                self.assertFalse(any(dest.rglob("*.tunes-partial")))
            finally:
                service.shutdown()

    def test_multi_track_cancel_leaves_no_dest_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "downloads"
            dest.mkdir()
            service, cache = self._make_service(root, dest=dest)
            gate = threading.Event()
            started = threading.Event()

            def slow_download(url, part, cancel_event=None):
                started.set()
                while not gate.is_set():
                    if cancel_event is not None and cancel_event.is_set():
                        raise SaveCancelled()
                    gate.wait(0.05)
                if cancel_event is not None and cancel_event.is_set():
                    raise SaveCancelled()
                part.write_bytes(b"flac")

            with mock.patch(
                "tunes_player.core.services.download_https",
                side_effect=slow_download,
            ):
                try:
                    tracks = [
                        _track(id="tidal:1", title="One", track_number=1),
                        _track(id="tidal:2", title="Two", track_number=2),
                    ]
                    service.start_save_to_disk(tracks=tracks, dest_dir=str(dest))
                    self.assertTrue(started.wait(timeout=5))
                    service.pause_save_to_disk_for_quit()
                    self.assertEqual(list(dest.rglob("*.flac")), [])
                    jobs = list_interrupted_jobs(root)
                    self.assertEqual(len(jobs), 1)
                    self.assertEqual(jobs[0][1].status, STATUS_INTERRUPTED)
                    self.assertTrue(cache.is_dir())
                finally:
                    gate.set()
                    service.shutdown()

    def test_multi_track_success_promotes_all(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "downloads"
            dest.mkdir()
            service, _cache = self._make_service(root, dest=dest)
            try:
                tracks = [
                    _track(id="tidal:1", title="One", track_number=1),
                    _track(id="tidal:2", title="Two", track_number=2),
                ]
                service.start_save_to_disk(tracks=tracks, dest_dir=str(dest))
                thread = service._download_thread
                assert thread is not None
                thread.join(timeout=10)
                self.assertEqual(service.download_saved_count, 2)
                finals = sorted(p.name for p in dest.rglob("*.flac"))
                self.assertEqual(finals, ["01 - One.flac", "02 - Two.flac"])
            finally:
                service.shutdown()

    def test_multi_track_failure_does_not_promote(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "downloads"
            dest.mkdir()
            service, _cache = self._make_service(root, dest=dest)

            track1_done = threading.Event()

            def flaky_download(url, part, cancel_event=None):
                # Fail track 2 only after track 1 has staged, so completed=[1].
                if "0002" in part.name:
                    track1_done.wait(timeout=5)
                    raise SaveToDiskError("boom")
                part.write_bytes(b"flac")
                track1_done.set()

            with mock.patch(
                "tunes_player.core.services.download_https",
                side_effect=flaky_download,
            ):
                try:
                    tracks = [
                        _track(id="tidal:1", title="One", track_number=1),
                        _track(id="tidal:2", title="Two", track_number=2),
                    ]
                    service.start_save_to_disk(tracks=tracks, dest_dir=str(dest))
                    thread = service._download_thread
                    assert thread is not None
                    thread.join(timeout=10)
                    self.assertEqual(service.download_saved_count, 0)
                    self.assertEqual(list(dest.rglob("*.flac")), [])
                    jobs = list_interrupted_jobs(root)
                    self.assertEqual(len(jobs), 1)
                    self.assertEqual(jobs[0][1].completed_indices, [1])
                finally:
                    service.shutdown()

    def test_max_two_concurrent_downloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "downloads"
            dest.mkdir()
            service, _cache = self._make_service(root, dest=dest)
            active = 0
            peak = 0
            lock = threading.Lock()
            release = threading.Event()

            def gated_download(url, part, cancel_event=None):
                nonlocal active, peak
                with lock:
                    active += 1
                    peak = max(peak, active)
                # Hold until at least two have entered (or timeout), then finish.
                release.wait(timeout=2)
                with lock:
                    active -= 1
                if cancel_event is not None and cancel_event.is_set():
                    raise SaveCancelled()
                part.write_bytes(b"flac")

            with mock.patch(
                "tunes_player.core.services.download_https",
                side_effect=gated_download,
            ):
                try:
                    tracks = [
                        _track(id="tidal:1", title="One", track_number=1),
                        _track(id="tidal:2", title="Two", track_number=2),
                        _track(id="tidal:3", title="Three", track_number=3),
                    ]
                    service.start_save_to_disk(tracks=tracks, dest_dir=str(dest))
                    # Wait until two workers are in flight.
                    deadline = threading.Event()
                    for _ in range(100):
                        with lock:
                            if peak >= 2 or active >= 2:
                                break
                        deadline.wait(0.05)
                    release.set()
                    thread = service._download_thread
                    assert thread is not None
                    thread.join(timeout=10)
                    self.assertEqual(service.download_saved_count, 3)
                    self.assertLessEqual(peak, MAX_SAVE_CONCURRENCY)
                    self.assertGreaterEqual(peak, 2)
                finally:
                    release.set()
                    service.shutdown()

    def test_resume_skips_completed_indices(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "downloads"
            dest.mkdir()
            service, cache = self._make_service(root, dest=dest)
            job_id = "resumejob1"
            job_dir = cache / job_id
            job_dir.mkdir(parents=True)
            staged = staging_part_path(cache, job_id, 1, ".flac")
            staged.write_bytes(b"flac")
            tracks = [
                _track(id="tidal:1", title="One", track_number=1),
                _track(id="tidal:2", title="Two", track_number=2),
            ]
            save_job_manifest(
                job_dir,
                DownloadJobManifest(
                    version=1,
                    job_id=job_id,
                    dest_dir=str(dest.resolve()),
                    track_ids=[t.id for t in tracks],
                    tracks=[serialize_track(t) for t in tracks],
                    completed_indices=[1],
                    status=STATUS_INTERRUPTED,
                ),
            )
            downloaded: list[str] = []

            def tracking_download(url, part, cancel_event=None):
                downloaded.append(part.name)
                part.write_bytes(b"flac")

            with mock.patch(
                "tunes_player.core.services.download_https",
                side_effect=tracking_download,
            ):
                try:
                    self.assertTrue(service.resume_interrupted_save_to_disk())
                    thread = service._download_thread
                    assert thread is not None
                    thread.join(timeout=10)
                    self.assertEqual(service.download_saved_count, 2)
                    self.assertEqual(downloaded, ["0002.flac.tunes-partial"])
                    finals = sorted(p.name for p in dest.rglob("*.flac"))
                    self.assertEqual(finals, ["01 - One.flac", "02 - Two.flac"])
                    self.assertEqual(list_interrupted_jobs(root), [])
                finally:
                    service.shutdown()

    def test_shutdown_persists_interrupted_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "downloads"
            dest.mkdir()
            service, _cache = self._make_service(root, dest=dest)
            started = threading.Event()

            def slow_download(url, part, cancel_event=None):
                started.set()
                while cancel_event is None or not cancel_event.is_set():
                    started.wait(0.05)
                raise SaveCancelled()

            with mock.patch(
                "tunes_player.core.services.download_https",
                side_effect=slow_download,
            ):
                tracks = [
                    _track(id="tidal:1", title="One", track_number=1),
                    _track(id="tidal:2", title="Two", track_number=2),
                ]
                service.start_save_to_disk(tracks=tracks, dest_dir=str(dest))
                self.assertTrue(started.wait(timeout=5))
                service.shutdown()
                jobs = list_interrupted_jobs(root)
                self.assertEqual(len(jobs), 1)
                self.assertEqual(jobs[0][1].status, STATUS_INTERRUPTED)
                self.assertEqual(list(dest.rglob("*.flac")), [])

    def test_download_job_label(self) -> None:
        self.assertEqual(
            download_job_label(
                [
                    _track(id="tidal:1", title="One", release_title="Album"),
                    _track(id="tidal:2", title="Two", release_title="Album"),
                ]
            ),
            "Artist – Album",
        )
        self.assertEqual(
            download_job_label([_track(title="Solo", release_title=None)]),
            "Solo",
        )

    def test_album_folder_for_save(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            track = _track(artist_name="Artist", release_title="Album")
            file_path = build_track_path(root, track, ".flac")
            file_path.parent.mkdir(parents=True)
            file_path.write_bytes(b"x")
            self.assertEqual(
                album_folder_for_save(root, saved_paths=[file_path]),
                file_path.parent.resolve(),
            )
            self.assertEqual(
                album_folder_for_save(root, tracks=[track]),
                root / "Artist" / "Album",
            )

    def test_second_job_queues_then_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "downloads"
            dest.mkdir()
            service, _cache = self._make_service(root, dest=dest)
            first_started = threading.Event()
            release_first = threading.Event()
            second_started = threading.Event()
            download_count = 0
            count_lock = threading.Lock()

            def gated_download(url, part, cancel_event=None):
                nonlocal download_count
                with count_lock:
                    download_count += 1
                    n = download_count
                if n == 1:
                    first_started.set()
                    while not release_first.is_set():
                        if cancel_event is not None and cancel_event.is_set():
                            raise SaveCancelled()
                        release_first.wait(0.05)
                else:
                    second_started.set()
                if cancel_event is not None and cancel_event.is_set():
                    raise SaveCancelled()
                part.write_bytes(b"flac")

            with mock.patch(
                "tunes_player.core.services.download_https",
                side_effect=gated_download,
            ):
                try:
                    service.start_save_to_disk(
                        tracks=[
                            _track(
                                id="tidal:1",
                                title="A1",
                                release_title="Album A",
                                track_number=1,
                            )
                        ],
                        dest_dir=str(dest),
                    )
                    self.assertTrue(first_started.wait(timeout=5))
                    service.start_save_to_disk(
                        tracks=[
                            _track(
                                id="tidal:2",
                                title="B1",
                                release_title="Album B",
                                track_number=1,
                            )
                        ],
                        dest_dir=str(dest),
                    )
                    snap = service.download_jobs()
                    self.assertIsNotNone(snap.active)
                    self.assertEqual(len(snap.pending), 1)
                    self.assertEqual(snap.pending[0].label, "Artist – Album B")
                    self.assertTrue(service.has_download_activity())
                    release_first.set()
                    self.assertTrue(second_started.wait(timeout=5))
                    deadline = time.monotonic() + 10
                    while service.is_saving_to_disk() and time.monotonic() < deadline:
                        time.sleep(0.05)
                    snap = service.download_jobs()
                    self.assertIsNone(snap.active)
                    self.assertEqual(snap.pending, ())
                    self.assertEqual(len(snap.completed), 2)
                    self.assertEqual(
                        {c.label for c in snap.completed},
                        {"Artist – Album A", "Artist – Album B"},
                    )
                    self.assertTrue(all(c.status == "completed" for c in snap.completed))
                    finals = list(dest.rglob("*.flac"))
                    self.assertEqual(len(finals), 2)
                finally:
                    release_first.set()
                    service.shutdown()

    def test_cancel_pending_does_not_stop_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "downloads"
            dest.mkdir()
            service, _cache = self._make_service(root, dest=dest)
            first_started = threading.Event()
            release_first = threading.Event()

            def gated_download(url, part, cancel_event=None):
                first_started.set()
                while not release_first.is_set():
                    if cancel_event is not None and cancel_event.is_set():
                        raise SaveCancelled()
                    release_first.wait(0.05)
                if cancel_event is not None and cancel_event.is_set():
                    raise SaveCancelled()
                part.write_bytes(b"flac")

            with mock.patch(
                "tunes_player.core.services.download_https",
                side_effect=gated_download,
            ):
                try:
                    service.start_save_to_disk(
                        tracks=[_track(id="tidal:1", title="A1", release_title="A")],
                        dest_dir=str(dest),
                    )
                    self.assertTrue(first_started.wait(timeout=5))
                    service.start_save_to_disk(
                        tracks=[_track(id="tidal:2", title="B1", release_title="B")],
                        dest_dir=str(dest),
                    )
                    pending_id = service.download_jobs().pending[0].job_id
                    service.cancel_save_to_disk(pending_id)
                    snap = service.download_jobs()
                    self.assertEqual(snap.pending, ())
                    self.assertIsNotNone(snap.active)
                    release_first.set()
                    thread = service._download_thread
                    assert thread is not None
                    thread.join(timeout=10)
                    self.assertEqual(service.download_saved_count, 1)
                    self.assertEqual(len(list(dest.rglob("*.flac"))), 1)
                finally:
                    release_first.set()
                    service.shutdown()

    def test_cancel_active_starts_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "downloads"
            dest.mkdir()
            service, _cache = self._make_service(root, dest=dest)
            first_started = threading.Event()
            second_started = threading.Event()
            seen: list[str] = []

            def gated_download(url, part, cancel_event=None):
                seen.append(Path(part).name)
                if not first_started.is_set():
                    first_started.set()
                    while cancel_event is None or not cancel_event.is_set():
                        first_started.wait(0.05)
                    raise SaveCancelled()
                second_started.set()
                part.write_bytes(b"flac")

            with mock.patch(
                "tunes_player.core.services.download_https",
                side_effect=gated_download,
            ):
                try:
                    service.start_save_to_disk(
                        tracks=[_track(id="tidal:1", title="A1", release_title="A")],
                        dest_dir=str(dest),
                    )
                    self.assertTrue(first_started.wait(timeout=5))
                    service.start_save_to_disk(
                        tracks=[_track(id="tidal:2", title="B1", release_title="B")],
                        dest_dir=str(dest),
                    )
                    active_id = service.download_jobs().active.job_id  # type: ignore[union-attr]
                    service.cancel_save_to_disk(active_id)
                    self.assertTrue(second_started.wait(timeout=5))
                    deadline = time.monotonic() + 10
                    while service.is_saving_to_disk() and time.monotonic() < deadline:
                        time.sleep(0.05)
                    snap = service.download_jobs()
                    self.assertIsNone(snap.active)
                    self.assertEqual(len(snap.completed), 1)
                    self.assertEqual(snap.completed[0].label, "Artist – B")
                    self.assertEqual(len(list(dest.rglob("*.flac"))), 1)
                finally:
                    service.shutdown()

    def test_quit_clears_pending_and_persists_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "downloads"
            dest.mkdir()
            service, cache = self._make_service(root, dest=dest)
            first_started = threading.Event()

            def slow_download(url, part, cancel_event=None):
                first_started.set()
                while cancel_event is None or not cancel_event.is_set():
                    first_started.wait(0.05)
                raise SaveCancelled()

            with mock.patch(
                "tunes_player.core.services.download_https",
                side_effect=slow_download,
            ):
                try:
                    service.start_save_to_disk(
                        tracks=[
                            _track(id="tidal:1", title="A1", release_title="A"),
                            _track(id="tidal:3", title="A2", release_title="A", track_number=2),
                        ],
                        dest_dir=str(dest),
                    )
                    self.assertTrue(first_started.wait(timeout=5))
                    service.start_save_to_disk(
                        tracks=[_track(id="tidal:2", title="B1", release_title="B")],
                        dest_dir=str(dest),
                    )
                    self.assertEqual(len(service.download_jobs().pending), 1)
                    service.pause_save_to_disk_for_quit()
                    snap = service.download_jobs()
                    self.assertEqual(snap.pending, ())
                    self.assertFalse(service.is_saving_to_disk())
                    jobs = list_interrupted_jobs(root)
                    self.assertEqual(len(jobs), 1)
                    self.assertEqual(jobs[0][1].status, STATUS_INTERRUPTED)
                    self.assertTrue(cache.is_dir())
                    self.assertEqual(list(dest.rglob("*.flac")), [])
                finally:
                    service.shutdown()


if __name__ == "__main__":
    unittest.main()
