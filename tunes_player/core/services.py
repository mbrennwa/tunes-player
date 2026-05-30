"""Facade between UI and backends — expand as features land."""

from __future__ import annotations

import multiprocessing
from dataclasses import dataclass
from queue import Empty, Queue
from typing import Callable

from tunes_player.core.backends.local import resolve_local_track
from tunes_player.core.config import ConfigManager
from tunes_player.core.library import LibraryStore, ScanResult
from tunes_player.core.library.scan_worker import create_scan_process
from tunes_player.core.models import Album, Artist, Track
from tunes_player.core.playback.engine import EngineEvent, PlaybackEngine

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
    position_sec: float
    duration_sec: float | None


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
        self._position_sec = 0.0
        self._duration_sec: float | None = None
        self._engine: PlaybackEngine | None = None
        self._engine_error: str | None = None
        self._engine_events: Queue[EngineEvent] = Queue()
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
        engine = self._engine
        if engine is not None and self._current_track is not None:
            self._position_sec = engine.get_position()
            duration = engine.get_duration()
            if duration is not None:
                self._duration_sec = duration
            self._is_playing = engine.is_playing()
        return PlaybackState(
            current_track=self._current_track,
            is_playing=self._is_playing,
            volume=self._volume,
            queue=tuple(self._queue),
            queue_index=self._queue_index,
            quality_hint=self._quality_hint,
            bit_perfect=self._bit_perfect,
            device_volume=self._device_volume,
            position_sec=self._position_sec,
            duration_sec=self._duration_sec,
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
        self._start_queue_track(track)

    def play_album(self, album_id: str, *, start_index: int = 0) -> None:
        tracks = self.get_album_tracks(album_id)
        if not tracks:
            return
        start_index = max(0, min(start_index, len(tracks) - 1))
        self._queue = tracks
        self._queue_index = start_index
        self._start_queue_track(tracks[start_index])

    def toggle_play_pause(self) -> None:
        if self._current_track is None and self._queue:
            self._queue_index = max(self._queue_index, 0)
            self._start_queue_track(self._queue[self._queue_index], resume=False)
            return
        if self._current_track is None:
            return
        engine = self._ensure_engine()
        if engine is None:
            return
        if self._is_playing:
            engine.pause()
        else:
            engine.play()
        self._sync_from_engine()
        self._emit("playback_changed")

    def pause(self) -> None:
        if not self._is_playing:
            return
        engine = self._engine
        if engine is not None:
            engine.pause()
            self._sync_from_engine()
        else:
            self._is_playing = False
        self._emit("playback_changed")

    def play(self) -> None:
        if self._current_track is None and self._queue:
            self._queue_index = max(self._queue_index, 0)
            self._start_queue_track(self._queue[self._queue_index], resume=False)
            return
        if self._current_track is None:
            return
        if self._is_playing:
            return
        engine = self._ensure_engine()
        if engine is None:
            return
        engine.play()
        self._sync_from_engine()
        self._emit("playback_changed")

    def skip_next(self) -> None:
        if not self._queue:
            return
        if self._queue_index + 1 >= len(self._queue):
            engine = self._engine
            if engine is not None:
                engine.pause()
                self._sync_from_engine()
            else:
                self._is_playing = False
            self._emit("playback_changed")
            return
        self._queue_index += 1
        self._start_queue_track(self._queue[self._queue_index])

    def skip_previous(self) -> None:
        if not self._queue:
            return
        engine = self._engine
        if engine is not None and self._position_sec > 3.0:
            engine.seek(0.0)
            self._sync_from_engine()
            self._emit("playback_changed", "position_changed")
            return
        if self._queue_index > 0:
            self._queue_index -= 1
            self._start_queue_track(self._queue[self._queue_index])

    def seek(self, position_sec: float) -> None:
        engine = self._engine
        if engine is None or self._current_track is None:
            return
        engine.seek(position_sec)
        self._sync_from_engine()
        self._emit("position_changed", "playback_changed")

    def set_volume(self, level: float) -> None:
        self._volume = max(0.0, min(1.0, level))
        engine = self._engine
        if engine is not None:
            engine.set_volume(self._volume)
        self._emit("volume_changed")

    def adjust_volume(self, delta: float) -> None:
        self.set_volume(self._volume + delta)

    def set_bit_perfect(self, enabled: bool) -> None:
        self._bit_perfect = enabled
        self._config_manager.config.bit_perfect = enabled
        self._config_manager.save()
        engine = self._engine
        if engine is not None:
            engine.set_bit_perfect(enabled)
        self._emit("playback_changed")

    def poll_playback(self) -> None:
        """Drain mpv events on the GTK main thread."""
        while True:
            try:
                event = self._engine_events.get_nowait()
            except Empty:
                break
            self._handle_engine_event(event)

    def shutdown(self) -> None:
        if self._engine is not None:
            self._engine.quit()
            self._engine = None

    def subscribe(self, callback: EventCallback) -> Unsubscribe:
        self._listeners.append(callback)

        def unsubscribe() -> None:
            if callback in self._listeners:
                self._listeners.remove(callback)

        return unsubscribe

    def _ensure_engine(self) -> PlaybackEngine | None:
        if self._engine is not None:
            return self._engine
        if self._engine_error is not None:
            return None
        try:
            from tunes_player.engines.mpv import create_mpv_engine

            self._engine = create_mpv_engine(
                bit_perfect=self._bit_perfect,
                volume=self._volume,
                on_event=self._on_engine_event,
            )
        except RuntimeError as exc:
            self._engine_error = str(exc)
            self._emit("playback_error")
            return None
        return self._engine

    def _start_queue_track(self, track: Track, *, resume: bool = True) -> None:
        source = resolve_local_track(self._store, track.id)
        if source is None:
            self._engine_error = "Track file is missing from disk."
            self._emit("playback_error")
            return
        engine = self._ensure_engine()
        if engine is None:
            return
        self._engine_error = None
        self._set_current_track(track)
        engine.load(source.path, start_sec=source.start_sec)
        if not resume:
            engine.pause()
        self._sync_from_engine()
        self._emit("playback_changed", "queue_changed", "position_changed")

    def _set_current_track(self, track: Track) -> None:
        self._current_track = track
        metadata = self._store.get_file_metadata(track.id)
        self._quality_hint = LibraryStore.quality_hint(metadata)
        if metadata is not None and metadata.duration_sec is not None:
            self._duration_sec = metadata.duration_sec
        else:
            self._duration_sec = track.duration_sec
        self._position_sec = 0.0

    def _sync_from_engine(self) -> None:
        engine = self._engine
        if engine is None:
            return
        self._position_sec = engine.get_position()
        duration = engine.get_duration()
        if duration is not None:
            self._duration_sec = duration
        self._is_playing = engine.is_playing()

    def _on_engine_event(self, event: EngineEvent) -> None:
        self._engine_events.put(event)

    def _handle_engine_event(self, event: EngineEvent) -> None:
        if event == "track_finished":
            self.skip_next()
            return
        if event == "playback_error":
            self._sync_from_engine()
            self._emit("playback_error", "playback_changed")
            return
        self._sync_from_engine()
        if event == "position_changed":
            self._emit("position_changed")
        elif event == "duration_changed":
            self._emit("position_changed", "playback_changed")
        elif event == "playing_changed":
            self._emit("playback_changed")

    def _emit(self, *events: str) -> None:
        for event in events:
            for listener in list(self._listeners):
                listener(event)
