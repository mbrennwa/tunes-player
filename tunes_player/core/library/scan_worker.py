"""Run library scan in a separate process (avoids GIL / GTK fork issues)."""

from __future__ import annotations

import multiprocessing
from pathlib import Path
from typing import TYPE_CHECKING

from tunes_player.core.config import AppConfig
from tunes_player.core.library.scanner import LibraryScanner

if TYPE_CHECKING:
    from multiprocessing.queues import Queue


def run_library_scan(
    db_path: str,
    music_folders: list[str],
    music_folder_added_at: dict[str, float],
    scan_folders: list[str],
    queue: Queue[tuple],
    *,
    add_paths: list[str] | None = None,
    remove_paths: list[str] | None = None,
) -> None:
    """Worker entry point — must remain a top-level function for spawn."""

    def progress(current: int, total: int, path: str) -> None:
        queue.put(("progress", current, total, path))

    try:
        config = AppConfig(
            music_folders=list(music_folders),
            music_folder_added_at=dict(music_folder_added_at),
        )
        scanner = LibraryScanner(db_path=Path(db_path), config=config)
        if add_paths or remove_paths:
            result = scanner.scan_changes(
                folder=scan_folders[0],
                add_paths=list(add_paths or []),
                remove_paths=list(remove_paths or []),
                progress=progress,
            )
        else:
            result = scanner.scan(scan_folders=list(scan_folders), progress=progress)
    except Exception as exc:
        queue.put(("error", str(exc)))
        return

    file_errors = tuple((item.path, item.reason) for item in result.file_errors)
    queue.put(
        (
            "done",
            result.indexed,
            result.removed,
            result.skipped,
            result.errors,
            result.art_indexed,
            file_errors,
            result.total_candidates,
        ),
    )


def create_scan_process(
    *,
    db_path: Path,
    music_folders: list[str],
    music_folder_added_at: dict[str, float],
    scan_folders: list[str],
    add_paths: list[str] | None = None,
    remove_paths: list[str] | None = None,
) -> tuple[multiprocessing.Process, multiprocessing.Queue]:
    ctx = multiprocessing.get_context("spawn")
    queue: multiprocessing.Queue = ctx.Queue()
    process = ctx.Process(
        target=run_library_scan,
        args=(
            str(db_path),
            list(music_folders),
            dict(music_folder_added_at),
            list(scan_folders),
            queue,
        ),
        kwargs={
            "add_paths": list(add_paths) if add_paths else None,
            "remove_paths": list(remove_paths) if remove_paths else None,
        },
        name="tunes-library-scan",
        daemon=True,
    )
    return process, queue


def close_scan_queue(queue: multiprocessing.Queue | None) -> None:
    """Release Queue feeder thread and semaphores (avoids resource_tracker warnings)."""
    if queue is None:
        return
    try:
        while True:
            queue.get_nowait()
    except Exception:
        pass
    try:
        queue.close()
    except Exception:
        pass
    try:
        queue.join_thread()
    except Exception:
        pass
