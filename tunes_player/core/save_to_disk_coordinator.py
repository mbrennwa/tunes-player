"""Save-to-disk download job queue and staging state machine."""

from __future__ import annotations

import concurrent.futures
import logging
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import TypeAlias

from tunes_player.core.backends.qobuz import QobuzClient, QobuzUnavailableError
from tunes_player.core.backends.resolve import resolve_track
from tunes_player.core.backends.tidal import TidalClient, TidalUnavailableError
from tunes_player.core.config import ConfigManager
from tunes_player.core.library import LibraryStore
from tunes_player.core.models import Source, Track
from tunes_player.core.release_quality import PlaybackPreference
from tunes_player.core.save_to_disk import (
    MAX_SAVE_CONCURRENCY,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_INTERRUPTED,
    STATUS_RUNNING,
    CompletedDownload,
    DownloadJobInfo,
    DownloadJobManifest,
    DownloadJobsSnapshot,
    ExistingLocalMatch,
    PendingDownloadJob,
    SaveCancelled,
    SaveToDiskError,
    StagedTrack,
    album_folder_for_save,
    build_track_path,
    deserialize_track,
    discard_download_job,
    download_cache_dir,
    download_https,
    download_job_label,
    fetch_cover_bytes,
    find_existing_local_match,
    find_staged_file,
    infer_extension,
    is_mpd_uri,
    is_writable_dir,
    list_interrupted_jobs,
    load_job_manifest,
    music_folder_for_path,
    promote_part_to_destination,
    promote_staged_tracks,
    remux_mpd,
    save_job_manifest,
    serialize_track,
    staging_part_path,
    tracks_need_disc_prefix,
    write_tags,
)

log = logging.getLogger(__name__)

EmitHook: TypeAlias = Callable[..., None]
MainThreadHook: TypeAlias = Callable[[Callable[[], None]], None]
EnqueueIncrementalScanHook: TypeAlias = Callable[..., None]


class SaveToDiskCoordinator:
    """Own download queue/worker/staging/cancel/resume state.

    PlayerService keeps a thin façade for GTK; inject emit, main-thread
    marshaling, resolve clients, and incremental-scan enqueue.
    """

    def __init__(
        self,
        *,
        config_manager: ConfigManager,
        emit: EmitHook,
        run_on_main_thread: MainThreadHook,
        get_store: Callable[[], LibraryStore],
        get_tidal: Callable[[], TidalClient],
        get_qobuz: Callable[[], QobuzClient],
        get_playback_preference: Callable[[], PlaybackPreference],
        enqueue_incremental_scan: EnqueueIncrementalScanHook,
    ) -> None:
        self._config_manager = config_manager
        self._emit = emit
        self._run_on_main_thread = run_on_main_thread
        self._get_store = get_store
        self._get_tidal = get_tidal
        self._get_qobuz = get_qobuz
        self._get_playback_preference = get_playback_preference
        self._enqueue_incremental_scan = enqueue_incremental_scan
        self._download_thread: threading.Thread | None = None
        self._download_cancel = threading.Event()
        self._download_lock = threading.Lock()
        self._download_progress: tuple[int, int, str] | None = None
        self._download_last_error: str | None = None
        self._download_saved_count = 0
        self._download_persist_on_cancel = False
        self._download_active_job_dir: Path | None = None
        self._download_active_manifest: DownloadJobManifest | None = None
        self._download_active_label: str = ""
        self._download_pending: deque[PendingDownloadJob] = deque()
        self._download_completed: deque[CompletedDownload] = deque(maxlen=20)
        self._download_skip_drain = False

    def is_saving_to_disk(self) -> bool:
        thread = self._download_thread
        return thread is not None and thread.is_alive()


    def has_download_activity(self) -> bool:
        """True when a job is running or queued."""
        with self._download_lock:
            return self.is_saving_to_disk() or bool(self._download_pending)

    @property
    def download_progress(self) -> tuple[int, int, str] | None:
        return self._download_progress

    @property
    def download_last_error(self) -> str | None:
        return self._download_last_error

    @property
    def download_saved_count(self) -> int:
        return self._download_saved_count

    def set_download_folder(self, folder: str | None) -> None:
        self._config_manager.set_download_folder(folder)

    def download_jobs(self) -> DownloadJobsSnapshot:
        """Snapshot of active, queued, and in-session completed downloads."""
        with self._download_lock:
            active: DownloadJobInfo | None = None
            manifest = self._download_active_manifest
            if self.is_saving_to_disk() and manifest is not None:
                active = DownloadJobInfo(
                    job_id=manifest.job_id,
                    label=self._download_active_label or download_job_label(
                        [deserialize_track(t) for t in manifest.tracks if isinstance(t, dict)]
                    ),
                    track_count=len(manifest.track_ids),
                    dest_dir=manifest.dest_dir,
                    status="active",
                    progress=self._download_progress,
                )
            pending = tuple(
                DownloadJobInfo(
                    job_id=job.job_id,
                    label=job.label,
                    track_count=len(job.track_ids),
                    dest_dir=job.dest_dir,
                    status="pending",
                )
                for job in self._download_pending
            )
            completed = tuple(
                DownloadJobInfo(
                    job_id=item.job_id,
                    label=item.label,
                    track_count=item.track_count,
                    dest_dir=item.dest_dir,
                    status="completed" if item.finished_ok else "failed",
                    error=item.error,
                )
                for item in self._download_completed
            )
            return DownloadJobsSnapshot(
                active=active,
                pending=pending,
                completed=completed,
            )

    def cancel_save_to_disk(self, job_id: str | None = None) -> None:
        """Cancel the active job (discard staging) or remove a queued job by id."""
        with self._download_lock:
            if job_id is not None:
                for index, job in enumerate(self._download_pending):
                    if job.job_id == job_id:
                        del self._download_pending[index]
                        self._emit("download_queued")
                        return
                manifest = self._download_active_manifest
                if (
                    manifest is None
                    or manifest.job_id != job_id
                    or not self.is_saving_to_disk()
                ):
                    return
            self._download_persist_on_cancel = False
            self._download_cancel.set()
            self._download_progress = None
            self._emit("download_cancelling")

    def pause_save_to_disk_for_quit(self) -> None:
        """Stop the active download and persist it for resume on next start.

        In-memory queued jobs are dropped (not persisted across quit).
        """
        with self._download_lock:
            self._download_pending.clear()
            self._download_skip_drain = True
        if not self.is_saving_to_disk():
            self._mark_active_download_interrupted()
            return
        self._download_persist_on_cancel = True
        self._download_cancel.set()
        thread = self._download_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=30.0)
        self._mark_active_download_interrupted()

    def _mark_active_download_interrupted(self) -> None:
        with self._download_lock:
            manifest = self._download_active_manifest
            job_dir = self._download_active_job_dir
        if manifest is None or job_dir is None:
            return
        if manifest.status == STATUS_COMPLETED:
            return
        manifest.status = STATUS_INTERRUPTED
        try:
            save_job_manifest(job_dir, manifest)
        except OSError:
            log.exception("Failed persisting interrupted download job %s", job_dir)

    def find_save_to_disk_conflict(
        self,
        tracks: list[Track],
        *,
        dest_dir: str | Path,
    ) -> ExistingLocalMatch | None:
        """Return a local/Downloads match for the tracks, if any."""
        return find_existing_local_match(
            tracks,
            get_release=self._get_store().get_release,
            search_releases=self._get_store().search_releases,
            download_folder=Path(dest_dir),
        )

    def start_save_to_disk(
        self,
        *,
        track_ids: list[str] | None = None,
        tracks: list[Track] | None = None,
        dest_dir: str,
    ) -> None:
        """Start or enqueue a save job for streaming tracks into dest_dir."""
        track_list = list(tracks or [])
        ids = [t.id for t in track_list] if track_list else [tid for tid in (track_ids or []) if tid]
        if not ids:
            raise SaveToDiskError("No tracks to save.")
        by_id = {t.id: t for t in track_list}
        ordered_tracks = tuple(
            by_id[tid]
            if tid in by_id
            else Track(
                id=tid,
                title=tid,
                artist_name="Unknown Artist",
                release_title=None,
                source=Source.TIDAL
                if tid.startswith("tidal:")
                else Source.QOBUZ
                if tid.startswith("qobuz:")
                else Source.LOCAL,
            )
            for tid in ids
        )
        dest = Path(dest_dir).expanduser()
        if not is_writable_dir(dest):
            raise SaveToDiskError(f"Folder is not writable: {dest}")
        job = PendingDownloadJob(
            job_id=uuid.uuid4().hex,
            dest_dir=str(dest.resolve()),
            track_ids=tuple(ids),
            tracks=ordered_tracks,
            label=download_job_label(ordered_tracks),
            enqueued_at=time.time(),
        )
        with self._download_lock:
            if self.is_saving_to_disk():
                self._download_pending.append(job)
                self._emit("download_queued")
                return
            self._begin_download_job_locked(
                track_ids=list(job.track_ids),
                dest_dir=job.dest_dir,
                tracks_by_id={t.id: t for t in job.tracks},
                job_id=job.job_id,
                existing_job_dir=None,
                completed_indices=[],
                resumed=False,
                label=job.label,
            )
        self._emit("download_started")

    def resume_interrupted_save_to_disk(self) -> bool:
        """Resume the first interrupted download job, if any. Returns True if started."""
        with self._download_lock:
            if self.is_saving_to_disk():
                return False
        jobs = list_interrupted_jobs(self._config_manager.data_dir)
        if not jobs:
            return False
        job_dir, manifest = jobs[0]
        if not is_writable_dir(Path(manifest.dest_dir)):
            log.warning(
                "Skipping resume of download job %s; dest not writable: %s",
                manifest.job_id,
                manifest.dest_dir,
            )
            return False
        by_id = {
            t["id"]: deserialize_track(t)
            for t in manifest.tracks
            if isinstance(t, dict) and t.get("id")
        }
        for track_id in manifest.track_ids:
            by_id.setdefault(
                track_id,
                Track(
                    id=track_id,
                    title=track_id,
                    artist_name="Unknown Artist",
                    release_title=None,
                    source=Source.TIDAL
                    if track_id.startswith("tidal:")
                    else Source.QOBUZ
                    if track_id.startswith("qobuz:")
                    else Source.LOCAL,
                ),
            )
        completed = sorted({int(i) for i in manifest.completed_indices if int(i) > 0})
        label = download_job_label(
            [by_id[tid] for tid in manifest.track_ids if tid in by_id]
        )
        with self._download_lock:
            if self.is_saving_to_disk():
                return False
            self._begin_download_job_locked(
                track_ids=list(manifest.track_ids),
                dest_dir=str(Path(manifest.dest_dir).resolve()),
                tracks_by_id=by_id,
                job_id=manifest.job_id,
                existing_job_dir=job_dir,
                completed_indices=completed,
                resumed=True,
                label=label,
            )
        self._emit("download_resumed")
        return True

    def _begin_download_job_locked(
        self,
        *,
        track_ids: list[str],
        dest_dir: str,
        tracks_by_id: dict[str, Track],
        job_id: str | None,
        existing_job_dir: Path | None,
        completed_indices: list[int],
        resumed: bool,
        label: str,
    ) -> None:
        """Start the download worker. Caller must hold ``_download_lock``."""
        self._download_cancel = threading.Event()
        self._download_progress = None
        self._download_last_error = None
        self._download_saved_count = 0
        self._download_persist_on_cancel = False
        self._download_skip_drain = False
        self._download_active_label = label
        thread = threading.Thread(
            target=self._run_save_to_disk_job,
            args=(
                list(track_ids),
                dest_dir,
                tracks_by_id,
                job_id,
                existing_job_dir,
                completed_indices,
                resumed,
            ),
            name="tunes-save-to-disk",
            daemon=True,
        )
        self._download_thread = thread
        thread.start()

    def _record_completed_download(
        self,
        *,
        job_id: str,
        label: str,
        track_count: int,
        dest_dir: str,
        finished_ok: bool,
        error: str | None = None,
    ) -> None:
        with self._download_lock:
            self._download_completed.appendleft(
                CompletedDownload(
                    job_id=job_id,
                    label=label,
                    track_count=track_count,
                    dest_dir=dest_dir,
                    finished_ok=finished_ok,
                    error=error,
                )
            )

    def _drain_download_queue(self) -> None:
        """Start the next queued job if idle. Must not be called while holding the lock."""
        with self._download_lock:
            if self._download_skip_drain:
                return
            if self.is_saving_to_disk():
                return
            if not self._download_pending:
                return
            job = self._download_pending.popleft()
            self._begin_download_job_locked(
                track_ids=list(job.track_ids),
                dest_dir=job.dest_dir,
                tracks_by_id={t.id: t for t in job.tracks},
                job_id=job.job_id,
                existing_job_dir=None,
                completed_indices=[],
                resumed=False,
                label=job.label,
            )
        self._emit("download_started")

    def _run_save_to_disk_job(
        self,
        track_ids: list[str],
        dest_dir: str,
        tracks_by_id: dict[str, Track],
        job_id: str | None,
        existing_job_dir: Path | None,
        completed_indices: list[int],
        resumed: bool,
    ) -> None:
        del resumed  # reserved for logging / UI differentiation
        cache_root = download_cache_dir(self._config_manager.data_dir)
        resolved_job_id = job_id or uuid.uuid4().hex
        job_dir = (
            Path(existing_job_dir)
            if existing_job_dir is not None
            else cache_root / resolved_job_id
        )
        album_atomic = len(track_ids) > 1
        staged: list[StagedTrack] = []
        saved_paths: list[Path] = []
        cancelled = False
        persist = False
        errors: list[str] = []
        completed = {int(i) for i in completed_indices}
        known_tracks = [tracks_by_id[tid] for tid in track_ids if tid in tracks_by_id]
        include_disc = tracks_need_disc_prefix(known_tracks)
        manifest = DownloadJobManifest(
            version=1,
            job_id=resolved_job_id,
            dest_dir=dest_dir,
            track_ids=list(track_ids),
            tracks=[
                serialize_track(tracks_by_id[tid])
                for tid in track_ids
                if tid in tracks_by_id
            ],
            completed_indices=sorted(completed),
            status=STATUS_RUNNING,
        )
        with self._download_lock:
            self._download_active_job_dir = job_dir
            self._download_active_manifest = manifest
        try:
            job_dir.mkdir(parents=True, exist_ok=True)
            save_job_manifest(job_dir, manifest)
            for index in sorted(completed):
                if index < 1 or index > len(track_ids):
                    continue
                existing = find_staged_file(job_dir, index)
                if existing is None:
                    # Staged file missing (e.g. single-track already promoted); drop marker.
                    completed.discard(index)
                    continue
                track_id = track_ids[index - 1]
                staged.append(
                    StagedTrack(
                        track_id=track_id,
                        index=index,
                        staged_path=existing,
                        dest_path=self._dest_path_for_staged(
                            existing,
                            dest_root=Path(dest_dir),
                            track=tracks_by_id.get(track_id),
                            track_id=track_id,
                            include_disc=include_disc,
                        ),
                    )
                )
            manifest.completed_indices = sorted(completed)
            save_job_manifest(job_dir, manifest)
            total = len(track_ids)
            pending = [
                index
                for index in range(1, total + 1)
                if index not in completed
            ]
            stage_lock = threading.Lock()
            active_labels: dict[int, str] = {}

            def _should_stop() -> bool:
                if self._download_cancel.is_set():
                    return True
                if album_atomic and errors:
                    return True
                return False

            def _stage_index(index: int) -> None:
                nonlocal cancelled
                track_id = track_ids[index - 1]
                track = tracks_by_id.get(track_id)
                label = track.title if track is not None else track_id
                with stage_lock:
                    if _should_stop():
                        if self._download_cancel.is_set():
                            cancelled = True
                        return
                    active_labels[index] = label
                    if self._download_cancel.is_set():
                        cancelled = True
                        active_labels.pop(index, None)
                        return
                    progress_index = len(completed) + 1
                    self._download_progress = (progress_index, total, label)
                    self._emit("download_progress")
                stale = find_staged_file(job_dir, index)
                if stale is not None:
                    try:
                        stale.unlink(missing_ok=True)
                    except OSError:
                        pass
                try:
                    staged_item = self._stage_one_track(
                        track_id,
                        dest_root=Path(dest_dir),
                        job_dir=job_dir,
                        job_id=resolved_job_id,
                        cache_root=cache_root,
                        index=index,
                        include_disc=include_disc,
                        track=track,
                    )
                except SaveCancelled:
                    with stage_lock:
                        cancelled = True
                        active_labels.pop(index, None)
                    return
                except SaveToDiskError as exc:
                    with stage_lock:
                        errors.append(f"{label}: {exc}")
                        active_labels.pop(index, None)
                        if album_atomic:
                            self._download_cancel.set()
                    log.warning("Save to disk failed for %s: %s", track_id, exc)
                    return
                except Exception as exc:
                    with stage_lock:
                        errors.append(f"{label}: {exc}")
                        active_labels.pop(index, None)
                        if album_atomic:
                            self._download_cancel.set()
                    log.exception("Save to disk failed for %s", track_id)
                    return
                with stage_lock:
                    active_labels.pop(index, None)
                    # Keep successful stages even if a sibling failed/cancelled so
                    # album-atomic resume can skip completed_indices.
                    staged.append(staged_item)
                    completed.add(index)
                    manifest.completed_indices = sorted(completed)
                    manifest.status = STATUS_RUNNING
                    save_job_manifest(job_dir, manifest)
                    if self._download_cancel.is_set():
                        cancelled = True
                    if not album_atomic:
                        final = promote_part_to_destination(
                            staged_item.staged_path,
                            staged_item.dest_path,
                        )
                        saved_paths.append(final)
                    # Do not publish progress after the user cancelled.
                    if self._download_cancel.is_set():
                        return
                    if active_labels:
                        next_label = next(iter(active_labels.values()))
                        self._download_progress = (
                            len(completed) + 1,
                            total,
                            next_label,
                        )
                        self._emit("download_progress")

            if pending:
                if self._download_cancel.is_set():
                    cancelled = True
                else:
                    workers = min(MAX_SAVE_CONCURRENCY, len(pending))
                    with concurrent.futures.ThreadPoolExecutor(
                        max_workers=workers,
                        thread_name_prefix="tunes-save-track",
                    ) as pool:
                        futures = [
                            pool.submit(_stage_index, index) for index in pending
                        ]
                        concurrent.futures.wait(futures)
            if self._download_cancel.is_set():
                cancelled = True
            persist = cancelled and self._download_persist_on_cancel
            # Errors win over sibling-stop cancel (album-atomic failure sets cancel
            # so the other worker aborts). Quit-persist still wins when no errors.
            job_label = self._download_active_label or download_job_label(known_tracks)
            open_folder = str(
                album_folder_for_save(
                    dest_dir,
                    tracks=known_tracks,
                    saved_paths=saved_paths,
                )
            )
            if cancelled and persist and not errors:
                manifest.status = STATUS_INTERRUPTED
                manifest.completed_indices = sorted(completed)
                save_job_manifest(job_dir, manifest)
                self._download_saved_count = len(saved_paths)
                # No toast: quit path persists for resume.
            elif errors:
                self._download_last_error = errors[0]
                if album_atomic:
                    manifest.status = STATUS_FAILED
                    manifest.completed_indices = sorted(completed)
                    save_job_manifest(job_dir, manifest)
                    self._download_saved_count = 0
                else:
                    self._download_saved_count = len(saved_paths)
                    if saved_paths:
                        self._enqueue_saved_paths_scan(saved_paths)
                    discard_download_job(job_dir)
                self._record_completed_download(
                    job_id=resolved_job_id,
                    label=job_label,
                    track_count=len(track_ids),
                    dest_dir=open_folder,
                    finished_ok=False,
                    error=errors[0],
                )
                self._emit("download_error")
            elif cancelled:
                self._download_saved_count = len(saved_paths)
                discard_download_job(job_dir)
                self._emit("download_cancelled")
            else:
                if album_atomic:
                    saved_paths = promote_staged_tracks(staged)
                self._download_saved_count = len(saved_paths)
                if saved_paths:
                    self._enqueue_saved_paths_scan(saved_paths)
                manifest.status = STATUS_COMPLETED
                save_job_manifest(job_dir, manifest)
                discard_download_job(job_dir)
                open_folder = str(
                    album_folder_for_save(
                        dest_dir,
                        tracks=known_tracks,
                        saved_paths=saved_paths,
                    )
                )
                self._record_completed_download(
                    job_id=resolved_job_id,
                    label=job_label,
                    track_count=len(track_ids),
                    dest_dir=open_folder,
                    finished_ok=True,
                )
                self._emit("download_finished")
        finally:
            self._download_progress = None
            with self._download_lock:
                if self._download_thread is threading.current_thread():
                    self._download_thread = None
                remaining = (
                    load_job_manifest(job_dir) if job_dir.is_dir() else None
                )
                if remaining is not None and remaining.status in {
                    STATUS_INTERRUPTED,
                    STATUS_FAILED,
                    STATUS_RUNNING,
                }:
                    self._download_active_job_dir = job_dir
                    self._download_active_manifest = remaining
                else:
                    self._download_active_job_dir = None
                    self._download_active_manifest = None
                self._download_persist_on_cancel = False
            # Drain next queued job unless quitting (skip_drain) or still busy.
            self._drain_download_queue()

    def _dest_path_for_staged(
        self,
        staged_path: Path,
        *,
        dest_root: Path,
        track: Track | None,
        track_id: str,
        include_disc: bool,
    ) -> Path:
        name = staged_path.name
        if name.endswith(".tunes-partial"):
            name = name[: -len(".tunes-partial")]
        # name like 0001.flac
        ext = Path(name).suffix or ".flac"
        meta = track or Track(
            id=track_id,
            title=track_id,
            artist_name="Unknown Artist",
            release_title=None,
            source=Source.TIDAL,
        )
        return build_track_path(dest_root, meta, ext, include_disc=include_disc)

    def _stage_one_track(
        self,
        track_id: str,
        *,
        dest_root: Path,
        job_dir: Path,
        job_id: str,
        cache_root: Path,
        index: int,
        include_disc: bool,
        track: Track | None,
    ) -> StagedTrack:
        if self._download_cancel.is_set():
            raise SaveCancelled()
        if track_id.startswith("local:"):
            raise SaveToDiskError("Local tracks are already on disk.")
        if not (
            track_id.startswith("tidal:") or track_id.startswith("qobuz:")
        ):
            raise SaveToDiskError("Only TIDAL and Qobuz tracks can be saved.")
        try:
            source = resolve_track(
                self._get_store(),
                track_id,
                tidal=self._get_tidal(),
                qobuz=self._get_qobuz(),
                playback_preference=self._get_playback_preference(),
            )
        except (TidalUnavailableError, QobuzUnavailableError) as exc:
            raise SaveToDiskError(str(exc)) from exc
        if source is None:
            raise SaveToDiskError("Could not resolve stream URL.")
        meta = track or source.metadata
        for_mpd = is_mpd_uri(source.uri)
        ext = infer_extension(source.uri, source.stream_metadata, for_mpd=for_mpd)
        part_path = staging_part_path(cache_root, job_id, index, ext)
        # Ensure parent is job_dir (staging_part_path uses cache_root/job_id).
        if part_path.parent != job_dir:
            part_path = job_dir / part_path.name
        if for_mpd:
            remux_mpd(source.uri, part_path, cancel_event=self._download_cancel)
        else:
            if not source.uri.startswith(("http://", "https://")):
                raise SaveToDiskError("Unsupported stream URL for download.")
            download_https(source.uri, part_path, cancel_event=self._download_cancel)
        cover = fetch_cover_bytes(meta.art_uri)
        try:
            write_tags(part_path, meta, cover_bytes=cover)
        except Exception:
            log.exception("Failed writing tags for %s", track_id)
        dest_path = build_track_path(
            dest_root,
            meta,
            ext,
            include_disc=include_disc,
        )
        return StagedTrack(
            track_id=track_id,
            index=index,
            staged_path=part_path,
            dest_path=dest_path,
        )

    def _enqueue_saved_paths_scan(self, paths: list[Path]) -> None:
        by_folder: dict[str, list[str]] = {}
        folders = list(self._config_manager.config.music_folders)
        for path in paths:
            folder = music_folder_for_path(path, folders)
            if folder is None:
                continue
            by_folder.setdefault(folder, []).append(str(path.resolve()))
        for folder, add_paths in by_folder.items():
            self._run_on_main_thread(
                lambda f=folder, p=list(add_paths): self._enqueue_incremental_scan(
                    folder=f,
                    add_paths=p,
                )
            )
