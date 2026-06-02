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
    queue: Queue[tuple],
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
        result = scanner.scan(progress=progress)
    except Exception as exc:
        queue.put(("error", str(exc)))
        return

    queue.put(
        ("done", result.indexed, result.removed, result.skipped, result.errors, result.art_indexed),
    )


def create_scan_process(
    *,
    db_path: Path,
    music_folders: list[str],
    music_folder_added_at: dict[str, float],
) -> tuple[multiprocessing.Process, multiprocessing.Queue]:
    ctx = multiprocessing.get_context("spawn")
    queue: multiprocessing.Queue = ctx.Queue()
    process = ctx.Process(
        target=run_library_scan,
        args=(str(db_path), list(music_folders), dict(music_folder_added_at), queue),
        name="tunes-library-scan",
        daemon=True,
    )
    return process, queue
