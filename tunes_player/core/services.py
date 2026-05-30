"""Facade between UI and backends — expand as features land."""

from __future__ import annotations

import multiprocessing
from dataclasses import dataclass
from queue import Empty
from typing import Callable

from tunes_player.core.config import ConfigManager
from tunes_player.core.library import LibraryStore, ScanResult
from tunes_player.core.library.scan_worker import create_scan_process
from tunes_player.core.models import Album, Artist, Track

EventCallback = Callable[[str], None]
Unsubscribe = Callable[[], None]


@dataclass(frozen=True, slots=True)
class SearchResults:
    albums: list[Album]
    tracks: list[Track]


@dataclass(frozen=True, slots=True)
class PlaybackState:
    current_track: Track | None
    is_playing: bool
    volume: float
    queue: tuple[Track, ...]
    queue_index: int
    quality_hint: str
    bit_perfect: bool
    device_volume: bool


class PlayerService:
    """Stable API for GTK (and future) frontends."""

    def __init__(self, *, config: ConfigManager | None = None) -> None:
        self._config_manager = config or ConfigManager()
        self._config_manager.load()
        self._store = LibraryStore(self._config_manager.database_path)
        self._listeners: list[EventCallback] = []
        self._volume = 0.72
        self._bit_perfect = self._config_manager.config.bit_perfect
        self._device_volume = True
        self._is_playing = False
        self._queue: list[Track] = []
        self._queue_index = -1
        self._current_track: Track | None = None
        self._quality_hint = ""
        self._scan_process: multiprocessing.Process | None = None
        self._scan_queue: multiprocessing.Queue | None = None
        self._scan_on_progress: Callable[[int, int, str], None] | None = None
        self._scan_on_finished: Callable[[ScanResult], None] | None = None
        self._scan_on_error: Callable[[Exception], None] | None = None

    @property
    def config(self) -> ConfigManager:
        return self._config_manager

    @property
    def store(self) -> LibraryStore:
        return self._store

    def list_albums(self) -> list[Album]:
        return self._store.list_albums()

    def list_artists(self) -> list[Artist]:
        return self._store.list_artists()

    def get_album(self, album_id: str) -> Album | None:
        return self._store.get_album(album_id)

    def get_album_tracks(self, album_id: str) -> list[Track]:
        return self._store.get_album_tracks(album_id)

    def get_artist_albums(self, artist_id: str) -> list[Album]:
        return self._store.get_artist_albums(artist_id)

    def search(self, query: str) -> SearchResults:
        needle = query.strip()
        if not needle:
            return SearchResults(albums=[], tracks=[])
        albums, tracks = self._store.search(needle)
        return SearchResults(albums=albums, tracks=tracks)

    def scan_library(
        self,
        *,
        on_progress: Callable[[int, int, str], None] | None = None,
        on_finished: Callable[[ScanResult], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        if self.is_scanning():
            return

        self._scan_on_progress = on_progress
        self._scan_on_finished = on_finished
        self._scan_on_error = on_error
        self._scan_process, self._scan_queue = create_scan_process(
            db_path=self._config_manager.database_path,
            music_folders=self._config_manager.config.music_folders,
        )
        self._scan_process.start()

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
            if kind == "progress" and self._scan_on_progress is not None:
                self._scan_on_progress(message[1], message[2], message[3])
            elif kind == "done":
                result = ScanResult(
                    indexed=message[1],
                    removed=message[2],
                    skipped=message[3],
                    errors=message[4],
                )
                if self._scan_on_finished is not None:
                    self._scan_on_finished(result)
                self._cleanup_scan()
                return False
            elif kind == "error":
                if self._scan_on_error is not None:
                    self._scan_on_error(RuntimeError(message[1]))
                self._cleanup_scan()
                return False

        if self._scan_process is not None and self._scan_process.is_alive():
            return True

        if self._scan_process is not None and self._scan_on_error is not None:
            code = self._scan_process.exitcode
            if code not in (0, None):
                self._scan_on_error(RuntimeError(f"Scan process exited with code {code}"))
        self._cleanup_scan()
        return False

    def _cleanup_scan(self) -> None:
        if self._scan_process is not None:
            self._scan_process.join(timeout=2.0)
            self._scan_process = None
        self._scan_queue = None
        self._scan_on_progress = None
        self._scan_on_finished = None
        self._scan_on_error = None

    def is_scanning(self) -> bool:
        return self._scan_process is not None and self._scan_process.is_alive()

    def notify_library_updated(self) -> None:
        """Call from the GTK main thread after a scan completes."""
        self._store.reconnect()
        self._emit("library_updated")

    def get_playback_state(self) -> PlaybackState:
        return PlaybackState(
            current_track=self._current_track,
            is_playing=self._is_playing,
            volume=self._volume,
            queue=tuple(self._queue),
            queue_index=self._queue_index,
            quality_hint=self._quality_hint,
            bit_perfect=self._bit_perfect,
            device_volume=self._device_volume,
        )

    def play_track(self, track_id: str) -> None:
        track = self._store.get_track(track_id)
        if track is None:
            return
        album_id = self._store.album_id_for_track(track_id)
        if album_id is not None:
            self._queue = self.get_album_tracks(album_id)
            self._queue_index = next(
                (index for index, item in enumerate(self._queue) if item.id == track_id),
                0,
            )
        else:
            self._queue = [track]
            self._queue_index = 0
        self._set_current_track(track)
        self._is_playing = True
        self._emit("playback_changed", "queue_changed")

    def play_album(self, album_id: str, *, start_index: int = 0) -> None:
        tracks = self.get_album_tracks(album_id)
        if not tracks:
            return
        start_index = max(0, min(start_index, len(tracks) - 1))
        self._queue = tracks
        self._queue_index = start_index
        self._set_current_track(tracks[start_index])
        self._is_playing = True
        self._emit("playback_changed", "queue_changed")

    def toggle_play_pause(self) -> None:
        if self._current_track is None and self._queue:
            self._queue_index = max(self._queue_index, 0)
            self._set_current_track(self._queue[self._queue_index])
        if self._current_track is None:
            return
        self._is_playing = not self._is_playing
        self._emit("playback_changed")

    def pause(self) -> None:
        if not self._is_playing:
            return
        self._is_playing = False
        self._emit("playback_changed")

    def play(self) -> None:
        if self._current_track is None and self._queue:
            self._queue_index = max(self._queue_index, 0)
            self._set_current_track(self._queue[self._queue_index])
        if self._current_track is None:
            return
        if self._is_playing:
            return
        self._is_playing = True
        self._emit("playback_changed")

    def skip_next(self) -> None:
        if not self._queue:
            return
        if self._queue_index + 1 >= len(self._queue):
            self._is_playing = False
            self._emit("playback_changed")
            return
        self._queue_index += 1
        self._set_current_track(self._queue[self._queue_index])
        self._is_playing = True
        self._emit("playback_changed", "queue_changed")

    def skip_previous(self) -> None:
        if not self._queue:
            return
        if self._queue_index > 0:
            self._queue_index -= 1
            self._set_current_track(self._queue[self._queue_index])
            self._is_playing = True
            self._emit("playback_changed", "queue_changed")

    def set_volume(self, level: float) -> None:
        self._volume = max(0.0, min(1.0, level))
        self._emit("volume_changed")

    def adjust_volume(self, delta: float) -> None:
        self.set_volume(self._volume + delta)

    def set_bit_perfect(self, enabled: bool) -> None:
        self._bit_perfect = enabled
        self._config_manager.config.bit_perfect = enabled
        self._config_manager.save()
        self._emit("playback_changed")

    def subscribe(self, callback: EventCallback) -> Unsubscribe:
        self._listeners.append(callback)

        def unsubscribe() -> None:
            if callback in self._listeners:
                self._listeners.remove(callback)

        return unsubscribe

    def _set_current_track(self, track: Track) -> None:
        self._current_track = track
        metadata = self._store.get_file_metadata(track.id)
        self._quality_hint = LibraryStore.quality_hint(metadata)

    def _emit(self, *events: str) -> None:
        for event in events:
            for listener in list(self._listeners):
                listener(event)
