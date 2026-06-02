"""Facade between UI and backends — expand as features land."""

from __future__ import annotations

import logging
import multiprocessing
import time
from dataclasses import dataclass
from queue import Empty, Queue
from typing import Callable

from tunes_player.core.backends.resolve import resolve_track
from tunes_player.core.backends.tidal import TidalClient, TidalUnavailableError
from tunes_player.core.config import ConfigManager
from tunes_player.core.home import RecentlyAddedItem
from tunes_player.core.library import LibraryStore, ScanResult
from tunes_player.core.library.scan_worker import create_scan_process
from tunes_player.core.models import Album, Artist, Track
from tunes_player.core.playback.engine import EngineEvent, PlaybackEngine
from tunes_player.core.volume import VolumeController, VolumeEndpoint

EventCallback = Callable[[str], None]
Unsubscribe = Callable[[], None]

log = logging.getLogger(__name__)


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

    def __init__(
        self,
        *,
        config: ConfigManager | None = None,
        volume_controller: VolumeController | None = None,
    ) -> None:
        self._config_manager = config or ConfigManager()
        self._config_manager.load()
        self._store = LibraryStore(self._config_manager.database_path)
        self._volume_controller = volume_controller
        self._listeners: list[EventCallback] = []
        self._volume = 0.72
        self._bit_perfect = self._config_manager.config.bit_perfect
        self._device_volume = self._has_device_volume()
        if self._device_volume and self._volume_controller is not None:
            try:
                self._volume = self._volume_controller.get_level()
            except OSError:
                self._device_volume = False
        self._device_output_fallback = False
        self._is_playing = False
        self._queue: list[Track] = []
        self._queue_index = -1
        self._current_track: Track | None = None
        self._quality_hint = ""
        self._position_sec = 0.0
        self._position_synced_at: float | None = None
        self._duration_sec: float | None = None
        self._engine: PlaybackEngine | None = None
        self._engine_error: str | None = None
        self._engine_events: Queue[EngineEvent] = Queue()
        self._scan_process: multiprocessing.Process | None = None
        self._scan_queue: multiprocessing.Queue | None = None
        self._scan_on_progress: Callable[[int, int, str], None] | None = None
        self._scan_on_finished: Callable[[ScanResult], None] | None = None
        self._scan_on_error: Callable[[Exception], None] | None = None
        data_dir = self._config_manager.data_dir
        self._tidal = TidalClient(
            data_dir / "tidal-session.json",
            cache_dir=data_dir / "tidal-cache",
        )

    @property
    def config(self) -> ConfigManager:
        return self._config_manager

    @property
    def store(self) -> LibraryStore:
        return self._store

    @property
    def tidal(self) -> TidalClient:
        return self._tidal

    def last_error(self) -> str | None:
        """Last user-facing playback error, if any."""
        return self._engine_error

    def playback_available(self) -> str | None:
        """Return an error message when the playback engine cannot load, else None."""
        from tunes_player.engines.mpv import probe_playback_engine

        return probe_playback_engine()

    def list_albums(self) -> list[Album]:
        return self._store.list_albums()

    def list_artists(self) -> list[Artist]:
        return self._store.list_artists()

    def list_recently_added_items(self, *, within_days: int = 30) -> list[RecentlyAddedItem]:
        items = self._store.list_recently_added_items(within_days=within_days)
        if self._tidal.is_logged_in():
            try:
                tidal_items = self._tidal.list_new_release_items(within_days=within_days)
                items = [*items, *tidal_items]
            except TidalUnavailableError:
                pass
        items.sort(key=lambda item: item.added_ns, reverse=True)
        return items[:120]

    def get_album(self, album_id: str) -> Album | None:
        if album_id.startswith("tidal:"):
            if not self._tidal.is_logged_in():
                return None
            try:
                return self._tidal.get_album(album_id)
            except TidalUnavailableError:
                return None
        return self._store.get_album(album_id)

    def get_album_tracks(self, album_id: str) -> list[Track]:
        if album_id.startswith("tidal:"):
            if not self._tidal.is_logged_in():
                return []
            try:
                return self._tidal.get_album_tracks(album_id)
            except TidalUnavailableError:
                return []
        return self._store.get_album_tracks(album_id)

    def get_artist_albums(self, artist_id: str) -> list[Album]:
        return self._store.get_artist_albums(artist_id)

    def search(self, query: str) -> SearchResults:
        needle = query.strip()
        if not needle:
            return SearchResults(albums=[], tracks=[])
        albums, tracks = self._store.search(needle)
        if self._tidal.is_logged_in():
            try:
                tidal_albums, tidal_tracks = self._tidal.search(needle)
                albums = [*albums, *tidal_albums]
                tracks = [*tracks, *tidal_tracks]
            except TidalUnavailableError:
                pass
        return SearchResults(albums=albums, tracks=tracks)

    def tidal_available(self) -> bool:
        return self._tidal.is_available()

    def tidal_is_logged_in(self) -> bool:
        return self._tidal.is_logged_in()

    def tidal_account_label(self) -> str | None:
        return self._tidal.account_label()

    def tidal_begin_login(self) -> tuple[str, float]:
        return self._tidal.begin_oauth()

    def tidal_poll_login(self) -> str:
        return self._tidal.poll_oauth()

    def tidal_cancel_login(self) -> None:
        self._tidal.cancel_oauth()

    def tidal_oauth_error(self) -> str | None:
        return self._tidal.oauth_error_message()

    def tidal_logout(self) -> None:
        self._tidal.logout()
        self._emit("sources_changed")

    def notify_sources_changed(self) -> None:
        self._emit("sources_changed")

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
            music_folder_added_at=self._config_manager.config.music_folder_added_at,
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
                    art_indexed=message[5] if len(message) > 5 else 0,
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
            bit_perfect=self._effective_bit_perfect(),
            device_volume=self._device_volume,
            position_sec=self._playback_position(),
            duration_sec=self._duration_sec,
        )

    def play_track(self, track_id: str) -> None:
        if track_id.startswith("tidal:"):
            self._play_tidal_track(track_id)
            return
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

    def _play_tidal_track(self, track_id: str) -> None:
        if not self._tidal.is_logged_in():
            self._report_error("Sign in to TIDAL in Settings → Sources.")
            return
        try:
            self._queue, self._queue_index = self._tidal.queue_for_track(track_id)
        except TidalUnavailableError as exc:
            self._report_error(str(exc), exc=exc)
            return
        if not self._queue:
            self._report_error("TIDAL track not found.")
            return
        self._start_queue_track(self._queue[self._queue_index])

    def play_album(self, album_id: str, *, start_index: int = 0) -> None:
        tracks = self.get_album_tracks(album_id)
        if not tracks:
            if album_id.startswith("tidal:"):
                if not self._tidal.is_logged_in():
                    self._report_error("Sign in to TIDAL in Settings → Sources.")
                else:
                    self._report_error("Could not load tracks for this album.")
            else:
                self._report_error("No tracks in this album.")
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
            self._notify_playback_unavailable()
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
            self._notify_playback_unavailable()
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
        target = max(0.0, position_sec)
        engine.seek(target)
        self._reset_playback_position(target)
        self._emit("position_changed")

    def set_volume(self, level: float, *, notify: bool = True) -> None:
        self._volume = max(0.0, min(1.0, level))
        if self._device_volume and self._volume_controller is not None:
            self._volume_controller.set_level(self._volume)
        else:
            engine = self._engine
            if engine is not None:
                engine.set_volume(self._volume)
        if notify:
            self._emit("volume_changed")

    def adjust_volume(self, delta: float) -> None:
        if self._device_volume and self._volume_controller is not None:
            self._volume_controller.adjust_level(delta)
            try:
                self._volume = self._volume_controller.get_level()
            except OSError:
                self._volume = max(0.0, min(1.0, self._volume + delta))
        else:
            self.set_volume(self._volume + delta)
            return
        self._emit("volume_changed")

    def set_output_sink(self, endpoint_id: str) -> None:
        if self._volume_controller is None:
            return
        self._volume_controller.set_active_endpoint(endpoint_id)
        self._config_manager.save()
        engine = self._engine
        if engine is not None:
            device = self._mpv_audio_device()
            if device and hasattr(engine, "set_audio_device"):
                engine.set_audio_device(device)
            track = self._current_track
            if track is not None:
                source = resolve_track(self._store, track.id, tidal=self._tidal)
                if source is not None:
                    pos = engine.get_position()
                    playing = engine.is_playing()
                    engine.load(source.playback_target, start_sec=pos)
                    if not playing:
                        engine.pause()
        self._emit("playback_changed")

    def list_output_sinks(self) -> list[VolumeEndpoint]:
        if self._volume_controller is None:
            return []
        return self._volume_controller.list_endpoints()

    def set_bit_perfect(self, enabled: bool) -> None:
        self._bit_perfect = enabled
        self._config_manager.config.bit_perfect = enabled
        self._config_manager.save()
        engine = self._engine
        if engine is not None:
            engine.set_bit_perfect(self._effective_bit_perfect())
            if self._effective_bit_perfect() or self._device_volume:
                engine.set_volume(1.0)
            else:
                engine.set_volume(self._volume)
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

    def _has_device_volume(self) -> bool:
        controller = self._volume_controller
        return controller is not None and controller.available() and controller.uses_device_volume

    def _reset_engine(self) -> None:
        engine = self._engine
        if engine is not None:
            engine.quit()
        self._engine = None
        self._engine_error = None

    def _fallback_to_software_volume(self) -> bool:
        """Retry playback through mpv soft volume when PipeWire routing fails."""
        if not self._device_volume or self._device_output_fallback:
            return False
        self._device_output_fallback = True
        self._device_volume = False
        track = self._current_track
        pos = self._position_sec
        playing = self._is_playing
        self._reset_engine()
        if track is None:
            return True
        try:
            source = resolve_track(self._store, track.id, tidal=self._tidal)
        except Exception as exc:
            self._report_error(str(exc), exc=exc)
            return False
        if source is None:
            if track.id.startswith("tidal:"):
                self._report_error(
                    "Could not play TIDAL track. Check your subscription and sign-in."
                )
            else:
                self._report_error("Track file is missing from disk.")
            return False
        engine = self._ensure_engine()
        if engine is None:
            self._notify_playback_unavailable()
            return False
        engine.load(source.playback_target, start_sec=pos)
        if not playing:
            engine.pause()
        engine.set_volume(self._volume)
        self._sync_from_engine()
        return True

    def _effective_bit_perfect(self) -> bool:
        return self._bit_perfect and self._device_volume

    def _mpv_volume_level(self) -> float:
        if self._effective_bit_perfect() or self._device_volume:
            return 1.0
        return self._volume

    def _mpv_audio_device(self) -> str | None:
        if self._volume_controller is None:
            return None
        try:
            return self._volume_controller.mpv_audio_device()
        except OSError:
            return None

    def _ensure_engine(self) -> PlaybackEngine | None:
        if self._engine is not None:
            return self._engine
        if self._engine_error is not None:
            return None
        try:
            from tunes_player.engines.mpv import create_mpv_engine

            self._engine = create_mpv_engine(
                bit_perfect=self._effective_bit_perfect(),
                volume=self._mpv_volume_level(),
                audio_device=self._mpv_audio_device(),
                use_device_output=self._device_volume,
                on_event=self._on_engine_event,
            )
        except RuntimeError as exc:
            self._report_error(str(exc), exc=exc)
            return None
        return self._engine

    def _start_queue_track(self, track: Track, *, resume: bool = True) -> None:
        try:
            source = resolve_track(self._store, track.id, tidal=self._tidal)
        except Exception as exc:
            self._report_error(str(exc), exc=exc)
            return
        if source is None:
            if track.id.startswith("tidal:"):
                self._report_error(
                    "Could not play TIDAL track. Check your subscription and sign-in."
                )
            else:
                self._report_error("Track file is missing from disk.")
            return
        engine = self._ensure_engine()
        if engine is None:
            self._notify_playback_unavailable()
            return
        self._engine_error = None
        self._set_current_track(track)
        engine.load(source.playback_target, start_sec=source.start_sec)
        if not resume:
            engine.pause()
        self._sync_from_engine()
        self._emit("playback_changed", "queue_changed")

    def _set_current_track(self, track: Track) -> None:
        self._current_track = track
        if track.source.value == "tidal":
            self._quality_hint = "TIDAL"
        else:
            metadata = self._store.get_file_metadata(track.id)
            self._quality_hint = LibraryStore.quality_hint(metadata)
        self._duration_sec = track.duration_sec
        self._reset_playback_position(0.0)

    def _reset_playback_position(self, position_sec: float) -> None:
        self._position_sec = max(0.0, position_sec)
        self._position_synced_at = time.monotonic()

    def _playback_position(self) -> float:
        return self._position_sec

    def _apply_engine_position(self, position_sec: float, *, allow_backward: bool = False) -> None:
        position = max(0.0, position_sec)
        if not allow_backward:
            if self._is_playing:
                # mpv time-pos flickers at track start; never snap backward while playing.
                if position < self._position_sec:
                    return
            elif position < self._position_sec:
                return
        self._position_sec = position
        self._position_synced_at = time.monotonic()

    def _sync_playback_position_from_engine(self) -> None:
        engine = self._engine
        if engine is None:
            return
        self._apply_engine_position(engine.get_position())

    def _sync_duration_from_engine(self) -> None:
        engine = self._engine
        if engine is None:
            return
        duration = engine.get_duration()
        if duration is not None:
            track = self._current_track
            catalog_duration = track.duration_sec if track is not None else None
            if (
                catalog_duration
                and catalog_duration > 0
                and duration < catalog_duration * 0.5
            ):
                # Manifest still looks like a short preview — keep catalog length for UI.
                self._duration_sec = catalog_duration
            else:
                self._duration_sec = duration
        self._is_playing = engine.is_playing()

    def _sync_from_engine(self) -> None:
        self._sync_playback_position_from_engine()
        self._sync_duration_from_engine()

    def _on_engine_event(self, event: EngineEvent) -> None:
        self._engine_events.put(event)

    def _handle_engine_event(self, event: EngineEvent) -> None:
        if event == "track_finished":
            self.skip_next()
            return
        if event == "playback_error":
            if self._fallback_to_software_volume():
                self._emit("playback_changed", "volume_changed")
                return
            self._sync_duration_from_engine()
            self._sync_playback_position_from_engine()
            if self._engine_error is None:
                self._report_error("Playback failed.")
            else:
                self._emit("playback_error", "playback_changed")
            return
        if event == "position_changed":
            self._sync_playback_position_from_engine()
            self._sync_duration_from_engine()
            self._emit("position_changed")
        elif event == "duration_changed":
            self._sync_duration_from_engine()
            self._emit("playback_changed")
        elif event == "playing_changed":
            self._sync_duration_from_engine()
            self._sync_playback_position_from_engine()
            self._emit("playback_changed")

    def _report_error(self, message: str, *, exc: BaseException | None = None) -> None:
        self._engine_error = message
        if exc is not None:
            log.error("%s", message, exc_info=exc)
        else:
            log.warning("%s", message)
        self._emit("playback_error")

    def _notify_playback_unavailable(self) -> None:
        if self._engine_error is not None:
            self._emit("playback_error")

    def _emit(self, *events: str) -> None:
        for event in events:
            for listener in list(self._listeners):
                listener(event)
