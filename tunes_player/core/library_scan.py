"""Library scan job queue, process lifecycle, and progress/checkpoint SM."""

from __future__ import annotations

import logging
import multiprocessing
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from queue import Empty

from tunes_player.core.config import ConfigManager
from tunes_player.core.folder_scan_status import (
    FOLDER_SCAN_FAILED,
    FOLDER_SCAN_INCOMPLETE,
    log_folder_scan_failure,
)
from tunes_player.core.library import ScanResult
from tunes_player.core.library.db import connect
from tunes_player.core.library.scanner import ScanFileError
from tunes_player.core.library.scan_process import terminate_orphan_library_scans
from tunes_player.core.library.scan_worker import close_scan_queue, create_scan_process
from tunes_player.core.logging_config import diagnostics_log_path

log = logging.getLogger(__name__)

EmitHook = Callable[[str], None]
VoidHook = Callable[[], None]
BoolHook = Callable[[], bool]
CountIndexedHook = Callable[[str], int]


@dataclass(frozen=True, slots=True)
class _ScanJob:
    folder: str
    add_paths: tuple[str, ...] = ()
    remove_paths: tuple[str, ...] = ()

    @property
    def is_incremental(self) -> bool:
        return bool(self.add_paths or self.remove_paths)


class LibraryScanCoordinator:
    """Own scan queue/coalesce/process/poll/cleanup/checkpoint state.

    Shared ``_library_db_write_lock`` and reconcile/art gates stay on
    PlayerService; inject the lock and gate callables. Checkpoints are
    status-only (``checkpoint_path`` is never passed to the worker).
    """

    _SCAN_UI_FLUSH_INTERVAL_SEC = 1.0

    def __init__(
        self,
        *,
        config_manager: ConfigManager,
        library_db_write_lock: threading.Lock,
        is_catalog_reconcile_running: BoolHook,
        is_art_maintenance_running: BoolHook,
        close_store: VoidHook,
        reconnect_store: VoidHook,
        flush_deferred_plays: VoidHook,
        flush_deferred_label_ops: VoidHook,
        emit: EmitHook,
        notify_library_updated: VoidHook,
        notify_art_updated: VoidHook,
        try_start_art_maintenance: VoidHook,
        count_indexed_files: CountIndexedHook,
        try_start_scan: VoidHook,
        start_scan_job: Callable[[_ScanJob], None],
        any_folder_still_needs_scan: BoolHook,
    ) -> None:
        self._config_manager = config_manager
        self._library_db_write_lock = library_db_write_lock
        self._is_catalog_reconcile_running = is_catalog_reconcile_running
        self._is_art_maintenance_running = is_art_maintenance_running
        self._close_store = close_store
        self._reconnect_store = reconnect_store
        self._flush_deferred_plays = flush_deferred_plays
        self._flush_deferred_label_ops = flush_deferred_label_ops
        self._emit = emit
        self._notify_library_updated = notify_library_updated
        self._notify_art_updated = notify_art_updated
        self._try_start_art_maintenance = try_start_art_maintenance
        self._count_indexed_files = count_indexed_files
        # Late-bound façades so tests can patch PlayerService methods.
        self._try_start_scan = try_start_scan
        self._start_scan_job_cb = start_scan_job
        self._any_folder_still_needs_scan = any_folder_still_needs_scan

        self._scan_process: multiprocessing.Process | None = None
        self._scan_queue: multiprocessing.Queue | None = None
        self._scanning_folder: str | None = None
        self._scan_progress: tuple[int, int, str] | None = None
        self._scan_progress_pinned_total: int | None = None
        self._scan_last_error: str | None = None
        self._current_scan_job: _ScanJob | None = None
        self._pending_scan_jobs: list[_ScanJob] = []
        self._scan_catalog_total_persisted = False
        self._scan_last_checkpoint_at = 0
        self._scan_pending_batch: tuple[int, int] | None = None
        self._scan_ui_flush_at = 0.0
        self._incremental_coalesce: dict[str, tuple[set[str], set[str]]] = {}

    @property
    def scanning_folder(self) -> str | None:
        return self._scanning_folder

    @property
    def scan_progress(self) -> tuple[int, int, str] | None:
        return self._scan_progress

    @property
    def scan_last_error(self) -> str | None:
        return self._scan_last_error

    def is_scanning(self) -> bool:
        return self._scan_queue is not None

    def has_pending_jobs(self) -> bool:
        return bool(self._pending_scan_jobs)

    def prepare_shutdown(self) -> None:
        self._pending_scan_jobs.clear()
        self._incremental_coalesce.clear()
        if self.is_scanning():
            self.record_interrupted_scan()
        self.terminate_active_scan()
        self._current_scan_job = None

    def drop_folder_jobs(self, folder: str) -> None:
        resolved = str(Path(folder).expanduser().resolve())
        self._pending_scan_jobs = [
            job for job in self._pending_scan_jobs if job.folder != resolved
        ]
        self._incremental_coalesce.pop(resolved, None)

    def terminate_active_scan(self) -> None:
        process = self._scan_process
        queue = self._scan_queue
        if process is not None and process.is_alive():
            process.terminate()
            process.join(timeout=5.0)
        close_scan_queue(queue)
        self._scan_process = None
        self._scan_queue = None
        self._scanning_folder = None
        self._scan_progress = None
        self._scan_progress_pinned_total = None

    def cancel_scan_for_folder(self, folder: str) -> None:
        resolved = str(Path(folder).expanduser().resolve())
        self._pending_scan_jobs = [
            job for job in self._pending_scan_jobs if job.folder != resolved
        ]
        self._incremental_coalesce.pop(resolved, None)
        if self._scanning_folder != resolved or not self.is_scanning():
            return
        self.record_interrupted_scan()
        self.terminate_active_scan()
        self._emit("scan_finished")
        self._notify_library_updated()
        self._try_start_scan()

    def scan_library(self, *, folder: str) -> None:
        self.enqueue_scan(folder=folder, priority=True)

    def enqueue_scan(self, *, folder: str, priority: bool = False) -> None:
        resolved = str(Path(folder).expanduser().resolve())
        configured = {
            str(Path(item).expanduser().resolve())
            for item in self._config_manager.config.music_folders
        }
        if resolved not in configured:
            return
        job = _ScanJob(folder=resolved)
        if self._scanning_folder == resolved:
            return
        self._pending_scan_jobs = [
            pending
            for pending in self._pending_scan_jobs
            if pending.folder != resolved
        ]
        self._incremental_coalesce.pop(resolved, None)
        if priority:
            self._pending_scan_jobs.insert(0, job)
        else:
            self._pending_scan_jobs.append(job)
        self._try_start_scan()

    def enqueue_incremental_scan(
        self,
        *,
        folder: str,
        add_paths: list[str] | None = None,
        remove_paths: list[str] | None = None,
    ) -> None:
        resolved = str(Path(folder).expanduser().resolve())
        configured = {
            str(Path(item).expanduser().resolve())
            for item in self._config_manager.config.music_folders
        }
        if resolved not in configured:
            return
        adds = [str(Path(path).resolve()) for path in (add_paths or [])]
        removes = [str(Path(path).resolve()) for path in (remove_paths or [])]
        if not adds and not removes:
            return
        if self._scanning_folder == resolved:
            self._accumulate_incremental(resolved, adds, removes)
            return
        if any(
            pending.folder == resolved and not pending.is_incremental
            for pending in self._pending_scan_jobs
        ):
            return
        for index, pending in enumerate(self._pending_scan_jobs):
            if pending.folder == resolved and pending.is_incremental:
                merged_adds = set(pending.add_paths)
                merged_removes = set(pending.remove_paths)
                self._merge_incremental_paths(merged_adds, merged_removes, adds, removes)
                self._pending_scan_jobs[index] = _ScanJob(
                    folder=resolved,
                    add_paths=tuple(sorted(merged_adds)),
                    remove_paths=tuple(sorted(merged_removes)),
                )
                self._try_start_scan()
                return
        self._pending_scan_jobs.append(
            _ScanJob(
                folder=resolved,
                add_paths=tuple(sorted(set(adds))),
                remove_paths=tuple(sorted(set(removes))),
            ),
        )
        self._try_start_scan()

    def _merge_incremental_paths(
        self,
        adds: set[str],
        removes: set[str],
        new_adds: list[str],
        new_removes: list[str],
    ) -> None:
        for path in new_adds:
            removes.discard(path)
            adds.add(path)
        for path in new_removes:
            if path in adds:
                adds.discard(path)
            else:
                removes.add(path)

    def _accumulate_incremental(
        self,
        folder: str,
        add_paths: list[str],
        remove_paths: list[str],
    ) -> None:
        adds, removes = self._incremental_coalesce.get(folder, (set(), set()))
        merged_adds = set(adds)
        merged_removes = set(removes)
        self._merge_incremental_paths(merged_adds, merged_removes, add_paths, remove_paths)
        if merged_adds or merged_removes:
            self._incremental_coalesce[folder] = (merged_adds, merged_removes)
        else:
            self._incremental_coalesce.pop(folder, None)

    def _drain_incremental_coalesce(self, folder: str) -> _ScanJob | None:
        entry = self._incremental_coalesce.pop(folder, None)
        if entry is None:
            return None
        adds, removes = entry
        if not adds and not removes:
            return None
        return _ScanJob(
            folder=folder,
            add_paths=tuple(sorted(adds)),
            remove_paths=tuple(sorted(removes)),
        )

    def any_folder_still_needs_scan(self) -> bool:
        if self._pending_scan_jobs:
            return True
        for folder in self._config_manager.config.music_folders:
            if not self.folder_scan_is_complete(folder):
                return True
            if self.folder_needs_scan_resume(folder):
                return True
        return False

    def try_start_scan(self) -> None:
        if self._is_catalog_reconcile_running() or self._is_art_maintenance_running():
            return
        if self._scan_queue is not None or not self._pending_scan_jobs:
            return
        with self._library_db_write_lock:
            if self._is_catalog_reconcile_running() or self._is_art_maintenance_running():
                return
            if self._scan_queue is not None or not self._pending_scan_jobs:
                return
            job = self._pending_scan_jobs.pop(0)
            self._start_scan_job_cb(job)

    def _count_indexed_files_snapshot(self, folder: str) -> int:
        root = str(Path(folder).expanduser().resolve())
        connection = connect(self._config_manager.database_path)
        try:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM files
                WHERE path = ? OR path LIKE ?
                """,
                (root, root + os.sep + "%"),
            ).fetchone()
            return int(row["count"])
        finally:
            connection.close()

    def folder_scan_is_complete(self, folder: str) -> bool:
        catalog_total = self._config_manager.folder_catalog_total(folder)
        if catalog_total is None or catalog_total <= 0:
            return False
        errors = self._config_manager.folder_last_scan_errors(folder)
        if errors is not None and errors not in (0, FOLDER_SCAN_INCOMPLETE):
            return False
        return self._count_indexed_files(folder) >= catalog_total

    def folder_needs_scan_resume(self, folder: str) -> bool:
        if self.folder_scan_is_complete(folder):
            errors = self._config_manager.folder_last_scan_errors(folder)
            checkpoint = self._config_manager.folder_scan_checkpoint(folder)
            if errors == FOLDER_SCAN_INCOMPLETE or checkpoint:
                catalog_total = self._config_manager.folder_catalog_total(folder)
                self._config_manager.record_folder_scan(
                    folder,
                    errors=0,
                    checkpoint="clear",
                    catalog_total=catalog_total,
                )
            return False
        errors = self._config_manager.folder_last_scan_errors(folder)
        if errors == FOLDER_SCAN_INCOMPLETE:
            return True
        catalog_total = self._config_manager.folder_catalog_total(folder)
        if catalog_total is None or catalog_total <= 0:
            return False
        return self._count_indexed_files(folder) < catalog_total

    def _scan_progress_checkpoint_path(self) -> str | None:
        if self._scan_progress is None:
            return None
        current, total, path = self._scan_progress
        if total <= 0 or current <= 0 or not path:
            return None
        if path.startswith(("Discovering", "Found ", "Finalizing", "Loading library")):
            return None
        if not path:
            return None
        try:
            return str(Path(path).expanduser().resolve())
        except (OSError, ValueError):
            return None

    def record_interrupted_scan(self) -> None:
        folder = self._scanning_folder
        job = self._current_scan_job
        if folder is None or job is None or job.is_incremental:
            return
        checkpoint = self._scan_progress_checkpoint_path()
        catalog_total = None
        if self._scan_progress is not None and self._scan_progress[1] > 0:
            catalog_total = self._scan_progress[1]
        self._config_manager.record_folder_scan(
            folder,
            errors=FOLDER_SCAN_INCOMPLETE,
            scan_kind="full",
            catalog_total=catalog_total,
            checkpoint=checkpoint,
        )

    def _maybe_persist_scan_checkpoint(self) -> None:
        folder = self._scanning_folder
        job = self._current_scan_job
        if folder is None or job is None or job.is_incremental:
            return
        if self._scan_progress is None:
            return
        current, total, _path = self._scan_progress
        if total <= 0:
            return
        if not self._scan_catalog_total_persisted:
            self._scan_catalog_total_persisted = True
            self._maybe_invalidate_scan_checkpoint_for_catalog_change(folder, total)
            stored = self._config_manager.folder_catalog_total(folder)
            if stored != total:
                self._config_manager.record_folder_scan(
                    folder,
                    errors=FOLDER_SCAN_INCOMPLETE,
                    scan_kind="full",
                    catalog_total=total,
                )
        checkpoint = self._scan_progress_checkpoint_path()
        if checkpoint is None:
            return
        if current != total and current - self._scan_last_checkpoint_at < 50:
            return
        self._scan_last_checkpoint_at = current
        self._config_manager.set_folder_scan_checkpoint(folder, checkpoint)

    def _maybe_invalidate_scan_checkpoint_for_catalog_change(
        self,
        folder: str,
        catalog_total: int,
    ) -> None:
        stored = self._config_manager.folder_catalog_total(folder)
        checkpoint = self._config_manager.folder_scan_checkpoint(folder)
        if checkpoint and stored is not None and stored != catalog_total:
            log.info(
                "Catalog size changed for %s (%d -> %d); scan checkpoint cleared",
                folder,
                stored,
                catalog_total,
            )
            self._config_manager.set_folder_scan_checkpoint(folder, None)

    def apply_scan_progress_update(
        self,
        current: int,
        total: int,
        path: str,
    ) -> None:
        """Keep scan progress monotonic across worker lock retries and phase messages."""
        pinned_total = self._scan_progress_pinned_total
        if pinned_total is not None and pinned_total > 0:
            total = max(total, pinned_total)
        elif total > 0:
            self._scan_progress_pinned_total = total
            pinned_total = total

        previous = self._scan_progress
        if previous is not None:
            previous_current, previous_total, previous_path = previous
            if current == 0 and total == 0:
                if previous_current > 0:
                    self._scan_progress = (
                        previous_current,
                        max(previous_total, pinned_total or 0),
                        path or previous_path,
                    )
                    return
            else:
                if previous_current > 0:
                    current = max(current, previous_current)
                if previous_total > 0:
                    total = max(total, previous_total)
                if pinned_total is not None:
                    total = max(total, pinned_total)

        self._scan_progress = (current, total, path)

    def start_scan_job(self, job: _ScanJob) -> None:
        self._current_scan_job = job
        self._scanning_folder = job.folder
        self._scan_progress = None
        self._scan_progress_pinned_total = None
        self._scan_last_error = None
        self._scan_catalog_total_persisted = False
        self._scan_last_checkpoint_at = 0
        self._scan_pending_batch = None
        self._scan_ui_flush_at = 0.0
        checkpoint_path = None
        if not job.is_incremental:
            # Checkpoints are persisted for status only. The scanner always walks
            # the full catalog and fast-skips files already indexed in the DB.
            pass
        terminate_orphan_library_scans(db_path=self._config_manager.database_path)
        expected_total = self._config_manager.folder_catalog_total(job.folder)
        if expected_total is None or expected_total <= 0:
            indexed = self._count_indexed_files_snapshot(job.folder)
            expected_total = indexed if indexed > 0 else None
        if expected_total is not None and expected_total > 0:
            self._scan_progress_pinned_total = expected_total
        self._close_store()
        time.sleep(0.1)
        self._scan_process, self._scan_queue = create_scan_process(
            db_path=self._config_manager.database_path,
            music_folders=self._config_manager.config.music_folders,
            music_folder_added_at=self._config_manager.config.music_folder_added_at,
            scan_folders=[job.folder],
            add_paths=list(job.add_paths) if job.is_incremental else None,
            remove_paths=list(job.remove_paths) if job.is_incremental else None,
            checkpoint_path=checkpoint_path,
            expected_total=expected_total,
        )
        self._scan_process.start()
        self._emit("scan_started")

    def poll_scan(self) -> bool:
        """Drain scan events on the GTK main thread. Returns True while scan runs."""
        if self._scan_queue is None:
            return False

        while True:
            try:
                message = self._scan_queue.get_nowait()
            except Empty:
                break

            kind = message[0]
            if kind == "progress":
                self.apply_scan_progress_update(message[1], message[2], message[3])
                self._maybe_persist_scan_checkpoint()
                self._emit("scan_progress")
            elif kind == "batch":
                self._scan_pending_batch = (int(message[1]), int(message[2]))
                self._maybe_flush_scan_catalog_ui()
            elif kind == "done":
                file_errors = tuple(
                    ScanFileError(path, reason)
                    for path, reason in (message[6] if len(message) > 6 else ())
                )
                result = ScanResult(
                    indexed=message[1],
                    removed=message[2],
                    skipped=message[3],
                    errors=message[4],
                    art_indexed=message[5] if len(message) > 5 else 0,
                    file_errors=file_errors,
                    total_candidates=message[7] if len(message) > 7 else 0,
                )
                finished_folder = self._scanning_folder
                job = self._current_scan_job
                scan_kind = "incremental" if job is not None and job.is_incremental else "full"
                if finished_folder is not None:
                    if result.errors > 0 or file_errors:
                        log_folder_scan_failure(
                            finished_folder,
                            errors=result.errors,
                            log_path=diagnostics_log_path(self._config_manager.state_dir),
                            file_errors=file_errors,
                        )
                    self._config_manager.record_folder_scan(
                        finished_folder,
                        errors=result.errors,
                        scan_kind=scan_kind,
                        catalog_total=result.total_candidates if scan_kind == "full" else None,
                    )
                self.cleanup_scan()
                self._emit("scan_finished")
                self._notify_library_updated()
                if result.art_indexed > 0 or result.indexed > 0:
                    self._notify_art_updated()
                return False
            elif kind == "error":
                finished_folder = self._scanning_folder
                self._scan_last_error = message[1]
                if finished_folder is not None:
                    log_folder_scan_failure(
                        finished_folder,
                        errors=FOLDER_SCAN_FAILED,
                        log_path=diagnostics_log_path(self._config_manager.state_dir),
                        fatal_error=message[1],
                    )
                    self._config_manager.record_folder_scan(
                        finished_folder,
                        errors=FOLDER_SCAN_FAILED,
                        scan_kind=(
                            "incremental"
                            if self._current_scan_job is not None
                            and self._current_scan_job.is_incremental
                            else "full"
                        ),
                    )
                self.cleanup_scan()
                self._emit("scan_error")
                return False

        if self._scan_process is not None and self._scan_process.is_alive():
            self._maybe_flush_scan_catalog_ui()
            return True

        if self._scan_process is not None:
            code = self._scan_process.exitcode
            if code not in (0, None):
                finished_folder = self._scanning_folder
                job = self._current_scan_job
                self._scan_last_error = f"Scan process exited with code {code}"
                partial = False
                if finished_folder is not None:
                    progress = self._scan_progress
                    partial = (
                        job is not None
                        and not job.is_incremental
                        and progress is not None
                        and progress[1] > 0
                        and progress[0] > 0
                    )
                    if partial:
                        self.record_interrupted_scan()
                    else:
                        log_folder_scan_failure(
                            finished_folder,
                            errors=FOLDER_SCAN_FAILED,
                            log_path=diagnostics_log_path(self._config_manager.state_dir),
                            fatal_error=self._scan_last_error,
                        )
                        self._config_manager.record_folder_scan(
                            finished_folder,
                            errors=FOLDER_SCAN_FAILED,
                            scan_kind=(
                                "incremental"
                                if job is not None and job.is_incremental
                                else "full"
                            ),
                        )
                self.cleanup_scan()
                self._emit("scan_error" if not partial else "scan_finished")
        else:
            self.cleanup_scan()
        return False

    def cleanup_scan(self) -> None:
        finished_folder = self._scanning_folder
        if self._scan_process is not None:
            self._scan_process.join(timeout=2.0)
            self._scan_process = None
        close_scan_queue(self._scan_queue)
        self._scan_queue = None
        self._scanning_folder = None
        self._scan_progress = None
        self._scan_progress_pinned_total = None
        self._current_scan_job = None
        self._reconnect_store()
        self._flush_deferred_plays()
        self._flush_deferred_label_ops()
        if finished_folder is not None:
            coalesced = self._drain_incremental_coalesce(finished_folder)
            if coalesced is not None:
                self._pending_scan_jobs.insert(0, coalesced)
        self._try_start_scan()
        if not self._any_folder_still_needs_scan():
            self._try_start_art_maintenance()

    def _maybe_flush_scan_catalog_ui(self) -> None:
        pending = self._scan_pending_batch
        if pending is None or not self.is_scanning():
            return
        now = time.monotonic()
        if now - self._scan_ui_flush_at < self._SCAN_UI_FLUSH_INTERVAL_SEC:
            return
        indexed, art_indexed = pending
        self._scan_ui_flush_at = now
        if indexed > 0:
            self._notify_library_updated()
        if art_indexed > 0:
            self._notify_art_updated()
