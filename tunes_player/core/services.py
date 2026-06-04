"""Facade between UI and backends — expand as features land."""

from __future__ import annotations

import logging
import multiprocessing
import threading
import time
from dataclasses import dataclass
from queue import Empty, Queue
from typing import Callable

from tunes_player.core.backends.qobuz import QobuzClient, QobuzUnavailableError
from tunes_player.core.backends.resolve import resolve_track
from tunes_player.core.backends.tidal import TidalClient, TidalUnavailableError
from tunes_player.core.config import ConfigManager
from tunes_player.core.home import (
    NEW_MUSIC_LOCAL_LIMIT,
    NEW_MUSIC_MERGE_LIMIT,
    NEW_MUSIC_STREAMING_PER_SOURCE_LIMIT,
    SUGGESTIONS_MERGE_LIMIT,
    RecentlyAddedItem,
    suggestion_added_ns,
)
from tunes_player.core.library import LibraryStore, ScanResult
from tunes_player.core.library.scan_worker import create_scan_process
from tunes_player.core.models import Album, Release, Source, Track
from tunes_player.core.playback.engine import EngineEvent, PlaybackEngine
from tunes_player.core.playback.output_profile import (
    PlaybackOutputProfile,
    PlaybackPathInfo,
    compute_output_profile,
)
from tunes_player.core.playback_quality import format_playback_status
from tunes_player.core.volume import VolumeController, VolumeEndpoint, is_alsa_endpoint_id

EventCallback = Callable[[str], None]
Unsubscribe = Callable[[], None]

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SearchResults:
    releases: list[Release]

    @property
    def albums(self) -> list[Release]:
        return self.releases


@dataclass(frozen=True, slots=True)
class PlaybackState:
    current_track: Track | None
    is_playing: bool
    volume: float
    muted: bool
    queue: tuple[Track, ...]
    queue_index: int
    quality_hint: str
    bit_perfect_playback: bool
    playback_note: str | None
    device_volume: bool
    mpv_soft_volume: bool
    no_volume_control: bool
    output_using_fallback: bool
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
        self._muted = False
        self._allow_software_volume_fallback = (
            self._config_manager.config.allow_software_volume_fallback
        )
        self._device_volume = self._has_device_volume()
        if self._device_volume and self._volume_controller is not None:
            try:
                self._volume = self._volume_controller.get_level()
            except OSError:
                self._device_volume = False
        normalize = getattr(
            self._volume_controller, "normalize_output_sink_config", None
        )
        if callable(normalize) and normalize():
            self._config_manager.save()
        self._device_output_fallback = False
        self._is_playing = False
        self._queue: list[Track] = []
        self._queue_index = -1
        self._current_track: Track | None = None
        self._quality_hint = ""
        self._tidal_playback_format_label: str | None = None
        self._tidal_playback_format_track_id: str | None = None
        self._playback_note: str | None = None
        self._bit_perfect_playback = False
        self._output_profile: PlaybackOutputProfile | None = None
        self._exclusive_session: object | None = None
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
        self._last_recorded_track_id: str | None = None
        self._last_recorded_at_ns = 0
        self._discover_fetch_lock = threading.Lock()
        data_dir = self._config_manager.data_dir
        self._tidal = TidalClient(
            data_dir / "tidal-session.json",
            cache_dir=data_dir / "tidal-cache",
        )
        self._qobuz = self._make_qobuz_client(data_dir)

    @property
    def config(self) -> ConfigManager:
        return self._config_manager

    @property
    def store(self) -> LibraryStore:
        return self._store

    @property
    def tidal(self) -> TidalClient:
        return self._tidal

    @property
    def qobuz(self) -> QobuzClient:
        return self._qobuz

    def _make_qobuz_client(self, data_dir) -> QobuzClient:
        cfg = self._config_manager.config
        return QobuzClient(
            data_dir / "qobuz-session.json",
            app_id=cfg.qobuz_app_id,
            app_secret=cfg.qobuz_app_secret,
            format_id=cfg.qobuz_stream_format_id,
        )

    def last_error(self) -> str | None:
        """Last user-facing playback error, if any."""
        return self._engine_error

    def playback_available(self) -> str | None:
        """Return an error message when the playback engine cannot load, else None."""
        from tunes_player.engines.mpv import probe_playback_engine

        return probe_playback_engine()

    def list_releases(self) -> list[Release]:
        return self._store.list_releases()

    def list_albums(self) -> list[Album]:
        return self.list_releases()

    def list_recently_added_items(self) -> list[RecentlyAddedItem]:
        with self._discover_fetch_lock:
            return self._list_recently_added_items_locked()

    def _list_recently_added_items_locked(self) -> list[RecentlyAddedItem]:
        within_days = self._config_manager.config.new_music_within_days
        items = self._store.list_recently_added_items(
            within_days=within_days,
            limit=NEW_MUSIC_LOCAL_LIMIT,
        )
        if self._tidal.is_logged_in():
            try:
                tidal_items = self._tidal.list_new_release_items(
                    limit=NEW_MUSIC_STREAMING_PER_SOURCE_LIMIT,
                    within_days=within_days,
                )
                items = [*items, *tidal_items]
            except TidalUnavailableError:
                pass
        if self._qobuz.is_logged_in():
            try:
                qobuz_items = self._qobuz.list_new_release_items(
                    limit=NEW_MUSIC_STREAMING_PER_SOURCE_LIMIT,
                    within_days=within_days,
                )
                items = [*items, *qobuz_items]
            except QobuzUnavailableError:
                pass
        by_release_id: dict[str, RecentlyAddedItem] = {}
        for item in items:
            existing = by_release_id.get(item.release.id)
            if existing is None or item.added_ns > existing.added_ns:
                by_release_id[item.release.id] = item
        deduped = sorted(
            by_release_id.values(),
            key=lambda item: (-item.added_ns, item.release.title.casefold()),
        )
        return deduped[:NEW_MUSIC_MERGE_LIMIT]

    def list_suggestion_items(self) -> list[RecentlyAddedItem]:
        with self._discover_fetch_lock:
            return self._list_suggestion_items_locked()

    def _list_suggestion_items_locked(self) -> list[RecentlyAddedItem]:
        items: list[RecentlyAddedItem] = []
        track = self._current_track
        if track is not None and track.id.startswith("tidal:") and self._tidal.is_logged_in():
            try:
                for index, item in enumerate(self._tidal.list_similar_items(track.id)):
                    items.append(
                        RecentlyAddedItem(
                            added_ns=suggestion_added_ns(
                                Source.TIDAL,
                                index=index,
                            ),
                            release=item.release,
                        ),
                    )
            except TidalUnavailableError:
                pass
        for release_id, played_ns in self._store.list_continue_listening_entries():
            release = self.get_release(release_id)
            if release is None:
                continue
            items.append(
                RecentlyAddedItem(
                    added_ns=suggestion_added_ns(
                        release.source,
                        played_at_ns=played_ns,
                    ),
                    release=release,
                ),
            )
        if self._tidal.is_logged_in():
            try:
                for index, item in enumerate(self._tidal.list_suggestion_items()):
                    items.append(
                        RecentlyAddedItem(
                            added_ns=suggestion_added_ns(
                                Source.TIDAL,
                                index=index,
                            ),
                            release=item.release,
                        ),
                    )
            except TidalUnavailableError:
                pass
        if self._qobuz.is_logged_in():
            try:
                for index, item in enumerate(self._qobuz.list_suggestion_items()):
                    items.append(
                        RecentlyAddedItem(
                            added_ns=suggestion_added_ns(
                                Source.QOBUZ,
                                index=index,
                            ),
                            release=item.release,
                        ),
                    )
            except QobuzUnavailableError:
                pass
        for index, item in enumerate(self._store.list_rediscover_items()):
            items.append(
                RecentlyAddedItem(
                    added_ns=suggestion_added_ns(
                        Source.LOCAL,
                        index=index,
                    ),
                    release=item.release,
                ),
            )
        by_release_id: dict[str, RecentlyAddedItem] = {}
        for item in items:
            existing = by_release_id.get(item.release.id)
            if existing is None or item.added_ns > existing.added_ns:
                by_release_id[item.release.id] = item
        deduped = sorted(
            by_release_id.values(),
            key=lambda item: (-item.added_ns, item.release.title.casefold()),
        )
        return deduped[:SUGGESTIONS_MERGE_LIMIT]

    def get_release(self, release_id: str) -> Release | None:
        if release_id.startswith("tidal:"):
            if not self._tidal.is_logged_in():
                return None
            try:
                return self._tidal.get_release(release_id)
            except TidalUnavailableError:
                return None
        if release_id.startswith("qobuz:"):
            if not self._qobuz.is_logged_in():
                return None
            try:
                return self._qobuz.get_release(release_id)
            except QobuzUnavailableError:
                return None
        return self._store.get_release(release_id)

    def get_album(self, album_id: str) -> Album | None:
        return self.get_release(album_id)

    def get_release_tracks(self, release_id: str) -> list[Track]:
        if release_id.startswith("tidal:"):
            if not self._tidal.is_logged_in():
                return []
            try:
                return self._tidal.get_release_tracks(release_id)
            except TidalUnavailableError:
                return []
        if release_id.startswith("qobuz:"):
            if not self._qobuz.is_logged_in():
                return []
            try:
                return self._qobuz.get_release_tracks(release_id)
            except QobuzUnavailableError:
                return []
        return self._store.get_release_tracks(release_id)

    def get_album_tracks(self, album_id: str) -> list[Track]:
        return self.get_release_tracks(album_id)

    def search(self, query: str) -> SearchResults:
        needle = query.strip()
        if not needle:
            return SearchResults(releases=[])
        releases = self._store.search_releases(needle)
        seen = {release.id for release in releases}
        if self._tidal.is_logged_in():
            try:
                for release in self._tidal.search_releases(needle):
                    if release.id not in seen:
                        seen.add(release.id)
                        releases.append(release)
            except TidalUnavailableError:
                pass
        if self._qobuz.is_logged_in():
            try:
                for release in self._qobuz.search_releases(needle):
                    if release.id not in seen:
                        seen.add(release.id)
                        releases.append(release)
            except QobuzUnavailableError:
                pass
        return SearchResults(releases=releases)

    def tidal_available(self) -> bool:
        return self._tidal.is_available()

    def tidal_is_logged_in(self) -> bool:
        return self._tidal.is_logged_in()

    def tidal_account_label(self) -> str | None:
        return self._tidal.account_label()

    def tidal_begin_pkce_login(self) -> str:
        """Start PKCE sign-in (required for lossless streams)."""
        return self._tidal.begin_pkce_login()

    def tidal_complete_pkce_login(self, redirect_url: str) -> None:
        self._tidal.complete_pkce_login(redirect_url)
        self._emit("sources_changed")

    def tidal_needs_lossless_relogin(self) -> bool:
        return self._tidal.needs_lossless_relogin()

    def tidal_logout(self) -> None:
        self._tidal.logout()
        self._emit("sources_changed")

    def qobuz_configured(self) -> bool:
        return self._qobuz.is_configured()

    def qobuz_is_logged_in(self) -> bool:
        return self._qobuz.is_logged_in()

    def qobuz_account_label(self) -> str | None:
        return self._qobuz.account_label()

    def qobuz_login(self, email: str, password: str) -> None:
        self._qobuz.login(email, password)
        self._emit("sources_changed")

    def qobuz_logout(self) -> None:
        self._qobuz.logout()
        self._emit("sources_changed")

    def qobuz_set_credentials(
        self,
        app_id: str,
        app_secret: str,
        *,
        format_id: int | None = None,
    ) -> None:
        cfg = self._config_manager.config
        cfg.qobuz_app_id = app_id.strip() or None
        cfg.qobuz_app_secret = app_secret.strip() or None
        if format_id is not None:
            cfg.qobuz_stream_format_id = format_id
        self._config_manager.save()
        self._qobuz = self._make_qobuz_client(self._config_manager.data_dir)
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
            muted=self._muted,
            queue=tuple(self._queue),
            queue_index=self._queue_index,
            quality_hint=self._quality_hint,
            bit_perfect_playback=self._bit_perfect_playback,
            playback_note=self._playback_note,
            device_volume=self._device_volume,
            mpv_soft_volume=self._mpv_soft_volume(),
            no_volume_control=self._no_volume_control(),
            output_using_fallback=self._output_using_fallback(),
            position_sec=self._playback_position(),
            duration_sec=self._duration_sec,
        )

    def play_track(self, track_id: str) -> None:
        if track_id.startswith("tidal:"):
            self._play_tidal_track(track_id)
            return
        if track_id.startswith("qobuz:"):
            self._play_qobuz_track(track_id)
            return
        track = self._store.get_track(track_id)
        if track is None:
            return
        release_id = self._store.release_id_for_track(track_id)
        if release_id is not None:
            self._queue = self.get_release_tracks(release_id)
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

    def _play_qobuz_track(self, track_id: str) -> None:
        if not self._qobuz.is_configured():
            self._report_error(
                "Qobuz App ID and App Secret are required. Add them in Settings → Sources."
            )
            return
        if not self._qobuz.is_logged_in():
            self._report_error("Sign in to Qobuz in Settings → Sources.")
            return
        try:
            self._queue, self._queue_index = self._qobuz.queue_for_track(track_id)
        except QobuzUnavailableError as exc:
            self._report_error(str(exc), exc=exc)
            return
        if not self._queue:
            self._report_error("Qobuz track not found.")
            return
        self._start_queue_track(self._queue[self._queue_index])

    def play_release(self, release_id: str, *, start_index: int = 0) -> None:
        tracks = self.get_release_tracks(release_id)
        if not tracks:
            if release_id.startswith("tidal:"):
                if not self._tidal.is_logged_in():
                    self._report_error("Sign in to TIDAL in Settings → Sources.")
                else:
                    self._report_error("Could not load tracks for this release.")
            elif release_id.startswith("qobuz:"):
                if not self._qobuz.is_configured():
                    self._report_error(
                        "Qobuz App ID and App Secret are required. Add them in Settings → Sources."
                    )
                elif not self._qobuz.is_logged_in():
                    self._report_error("Sign in to Qobuz in Settings → Sources.")
                else:
                    self._report_error("Could not load tracks for this release.")
            else:
                self._report_error("No tracks in this release.")
            return
        start_index = max(0, min(start_index, len(tracks) - 1))
        self._queue = tracks
        self._queue_index = start_index
        self._start_queue_track(tracks[start_index])

    def play_album(self, album_id: str, *, start_index: int = 0) -> None:
        self.play_release(album_id, start_index=start_index)

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
        if self._muted and self._volume > 0:
            self._muted = False
        self._push_volume_to_output(notify=notify)

    def toggle_mute(self) -> None:
        self._muted = not self._muted
        self._push_volume_to_output(notify=True)

    def _output_volume_level(self) -> float:
        return 0.0 if self._muted else self._volume

    def _push_volume_to_output(self, *, notify: bool = True) -> None:
        level = self._output_volume_level()
        if self._device_volume and self._volume_controller is not None:
            self._volume_controller.set_level(level)
        else:
            engine = self._engine
            if engine is not None:
                engine.set_volume(level)
        if notify:
            self._emit("volume_changed")

    def adjust_volume(self, delta: float) -> None:
        if self._device_volume and self._volume_controller is not None:
            if self._muted:
                self._volume = max(0.0, min(1.0, self._volume + delta))
                if self._volume > 0:
                    self._muted = False
                self._push_volume_to_output(notify=True)
                return
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
        self._release_exclusive_session()
        self._volume_controller.set_active_endpoint(endpoint_id)
        self._config_manager.config.output_sink_id = endpoint_id
        self._config_manager.save()
        self._rebuild_engine_for_output_change()
        self._emit("playback_changed")

    def list_output_sinks(self) -> list[VolumeEndpoint]:
        if self._volume_controller is None:
            return []
        normalize = getattr(
            self._volume_controller, "normalize_output_sink_config", None
        )
        if callable(normalize) and normalize():
            self._config_manager.save()
        return self._volume_controller.list_endpoints()

    def get_linux_audio_stack_info(self) -> object:
        """Return LinuxAudioStackInfo on Linux, else None."""
        try:
            from tunes_player.platform.linux.audio_probe import (
                LinuxAudioStackInfo,
                probe_linux_audio_stack,
            )
        except ImportError:
            return None
        return probe_linux_audio_stack()

    def set_allow_software_volume_fallback(self, enabled: bool) -> None:
        if enabled == self._allow_software_volume_fallback:
            return
        self._allow_software_volume_fallback = enabled
        self._config_manager.config.allow_software_volume_fallback = enabled
        self._config_manager.save()
        self._apply_engine_volume_policy()
        self._emit("playback_changed")

    def exclusive_access_supported(self) -> bool:
        controller = self._volume_controller
        if controller is None:
            return False
        supported = getattr(controller, "exclusive_access_supported", None)
        if callable(supported):
            return bool(supported())
        return False

    def set_exclusive_device_access(self, enabled: bool) -> None:
        if enabled == self._config_manager.config.exclusive_device_access:
            return
        self._config_manager.config.exclusive_device_access = enabled
        self._config_manager.save()
        self._release_exclusive_session()
        self._rebuild_engine_for_output_change()
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
        self._release_exclusive_session()
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

    def _active_endpoint_id(self) -> str | None:
        if self._volume_controller is None:
            return None
        return self._volume_controller.get_active_endpoint_id()

    def _hw_caps_for_endpoint(self, endpoint_id: str | None):
        if not is_alsa_endpoint_id(endpoint_id):
            return None
        try:
            from tunes_player.platform.linux.alsa_caps import caps_for_endpoint

            return caps_for_endpoint(
                endpoint_id,
                data_dir=self._config_manager.data_dir,
            )
        except ImportError:
            return None

    def _compute_playback_profile_for_track(
        self, track: Track
    ) -> tuple[PlaybackOutputProfile, PlaybackPathInfo]:
        file_meta = None
        if track.id.startswith("local:"):
            file_meta = self._store.get_file_metadata(track.id)
        return compute_output_profile(
            file_meta=file_meta,
            hw_caps=self._hw_caps_for_endpoint(self._active_endpoint_id()),
            endpoint_id=self._active_endpoint_id(),
            exclusive_enabled=self._config_manager.config.exclusive_device_access,
            device_volume=self._device_volume,
            mpv_soft_volume=self._mpv_soft_volume(),
        )

    def _compute_playback_profile_for_current(
        self,
    ) -> tuple[PlaybackOutputProfile, PlaybackPathInfo]:
        track = self._current_track
        if track is None:
            return compute_output_profile(
                file_meta=None,
                hw_caps=self._hw_caps_for_endpoint(self._active_endpoint_id()),
                endpoint_id=self._active_endpoint_id(),
                exclusive_enabled=self._config_manager.config.exclusive_device_access,
                device_volume=self._device_volume,
                mpv_soft_volume=self._mpv_soft_volume(),
            )
        return self._compute_playback_profile_for_track(track)

    def _tidal_quality_hint_for_track(self, track_id: str) -> str:
        if (
            self._tidal_playback_format_track_id == track_id
            and self._tidal_playback_format_label
        ):
            return self._tidal_playback_format_label
        if self._tidal is not None:
            return self._tidal.stream_format_label(track_id)
        return "Unknown format"

    def _apply_path_info(self, path_info: PlaybackPathInfo) -> None:
        self._bit_perfect_playback = path_info.bit_perfect_playback
        self._playback_note = path_info.playback_note
        self._refresh_quality_hint()

    def _refresh_quality_hint(self) -> None:
        """Rebuild now-playing format line including the active audio layer."""
        track = self._current_track
        if track is None:
            return
        if track.id.startswith("tidal:"):
            base_hint = self._tidal_quality_hint_for_track(track.id)
        elif track.id.startswith("qobuz:"):
            from tunes_player.core.playback_quality import qobuz_stream_format_label

            base_hint = qobuz_stream_format_label(
                self._config_manager.config.qobuz_stream_format_id
            )
        else:
            metadata = self._store.get_file_metadata(track.id)
            base_hint = LibraryStore.quality_hint(metadata)
        self._quality_hint = format_playback_status(
            base_hint, playback_note=self._playback_note
        )

    def _acquire_exclusive_session_if_needed(self, profile: PlaybackOutputProfile) -> None:
        if not profile.direct_alsa or not profile.use_exclusive:
            return
        controller = self._volume_controller
        if controller is None:
            return
        card_getter = getattr(controller, "active_alsa_card", None)
        if not callable(card_getter):
            return
        card = card_getter()
        if card is None:
            return
        try:
            from tunes_player.platform.linux.exclusive_session import (
                acquire_exclusive_session,
            )

            self._exclusive_session = acquire_exclusive_session(card)
        except ImportError:
            pass

    def _release_exclusive_session(self) -> None:
        if self._exclusive_session is None:
            return
        try:
            from tunes_player.platform.linux.exclusive_session import (
                release_exclusive_session,
            )

            release_exclusive_session(self._exclusive_session)
        except ImportError:
            pass
        self._exclusive_session = None

    def _apply_engine_volume_policy(self) -> None:
        engine = self._engine
        if engine is None:
            return
        if hasattr(engine, "set_bit_perfect"):
            engine.set_bit_perfect(self._unity_gain_profile())
        engine.set_volume(self._mpv_volume_level())

    def _rebuild_engine_for_output_change(self) -> None:
        self._device_volume = self._has_device_volume()
        track = self._current_track
        pos = 0.0
        playing = False
        engine = self._engine
        if engine is not None:
            pos = engine.get_position()
            playing = engine.is_playing()
        self._reset_engine()
        if track is None:
            return
        source = resolve_track(
            self._store, track.id, tidal=self._tidal, qobuz=self._qobuz
        )
        if source is None:
            return
        engine = self._ensure_engine()
        if engine is None:
            return
        profile, path_info = self._compute_playback_profile_for_track(track)
        self._output_profile = profile
        self._apply_path_info(path_info)
        self._acquire_exclusive_session_if_needed(profile)
        self._set_current_track(
            track,
            format_label=source.format_label,
            playback_note=path_info.playback_note,
        )
        if hasattr(engine, "set_output_profile"):
            engine.set_output_profile(profile)
        engine.load(
            source.playback_target,
            start_sec=pos,
            output_profile=profile,
        )
        if not playing:
            engine.pause()

    def _reset_engine(self) -> None:
        self._release_exclusive_session()
        engine = self._engine
        if engine is not None:
            engine.quit()
        self._engine = None
        self._engine_error = None

    def _fallback_to_software_volume(self) -> bool:
        """Retry playback through mpv soft volume when PipeWire routing fails."""
        if (
            not self._allow_software_volume_fallback
            or not self._device_volume
            or self._device_output_fallback
        ):
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
            source = resolve_track(
                self._store, track.id, tidal=self._tidal, qobuz=self._qobuz
            )
        except Exception as exc:
            self._report_error(str(exc), exc=exc)
            return False
        if source is None:
            if track.id.startswith("tidal:"):
                self._report_error(
                    "Could not play TIDAL track. Check your subscription and sign-in."
                )
            elif track.id.startswith("qobuz:"):
                self._report_error(
                    "Could not play Qobuz track. Check your subscription and sign-in."
                )
            else:
                self._report_error("Track file is missing from disk.")
            return False
        engine = self._ensure_engine()
        if engine is None:
            self._notify_playback_unavailable()
            return False
        profile, path_info = self._compute_playback_profile_for_track(track)
        self._output_profile = profile
        self._apply_path_info(path_info)
        self._set_current_track(
            track,
            format_label=source.format_label,
            playback_note=path_info.playback_note,
        )
        engine.load(source.playback_target, start_sec=pos, output_profile=profile)
        if not playing:
            engine.pause()
        engine.set_volume(self._volume)
        self._sync_from_engine()
        return True

    def _mpv_soft_volume(self) -> bool:
        return not self._device_volume and self._allow_software_volume_fallback

    def _unity_gain_profile(self) -> bool:
        """mpv unity gain — no in-player attenuation (derived bit-perfect active)."""
        return not self._mpv_soft_volume()

    def _no_volume_control(self) -> bool:
        return not self._device_volume and not self._allow_software_volume_fallback

    def _output_using_fallback(self) -> bool:
        configured = self._config_manager.config.output_sink_id
        if not configured or self._volume_controller is None:
            return False
        ids = {item.id for item in self._volume_controller.list_endpoints()}
        return configured not in ids

    def _mpv_volume_level(self) -> float:
        if self._unity_gain_profile():
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

            profile, path_info = self._compute_playback_profile_for_current()
            self._output_profile = profile
            self._apply_path_info(path_info)
            self._engine = create_mpv_engine(
                unity_gain=self._unity_gain_profile(),
                volume=self._mpv_volume_level(),
                audio_device=self._mpv_audio_device(),
                use_device_output=self._device_volume and not profile.direct_alsa,
                output_profile=profile,
                on_event=self._on_engine_event,
            )
        except RuntimeError as exc:
            self._report_error(str(exc), exc=exc)
            return None
        return self._engine

    def _start_queue_track(self, track: Track, *, resume: bool = True) -> None:
        try:
            source = resolve_track(
                self._store, track.id, tidal=self._tidal, qobuz=self._qobuz
            )
        except Exception as exc:
            self._report_error(str(exc), exc=exc)
            return
        if source is None:
            if track.id.startswith("tidal:"):
                self._report_error(
                    "Could not play TIDAL track. Check your subscription and sign-in."
                )
            elif track.id.startswith("qobuz:"):
                self._report_error(
                    "Could not play Qobuz track. Check your subscription and sign-in."
                )
            else:
                self._report_error("Track file is missing from disk.")
            return
        engine = self._ensure_engine()
        if engine is None:
            self._notify_playback_unavailable()
            return
        self._engine_error = None
        profile, path_info = self._compute_playback_profile_for_track(track)
        self._output_profile = profile
        self._apply_path_info(path_info)
        self._set_current_track(
            track,
            format_label=source.format_label,
            playback_note=path_info.playback_note,
        )
        self._record_playback(track)
        self._release_exclusive_session()
        self._acquire_exclusive_session_if_needed(profile)
        if self._engine is not None and hasattr(self._engine, "set_output_profile"):
            self._engine.set_output_profile(profile)
        engine.load(
            source.playback_target,
            start_sec=source.start_sec,
            output_profile=profile,
        )
        if not resume:
            engine.pause()
        self._sync_from_engine()
        self._emit("playback_changed", "queue_changed")

    def _record_playback(self, track: Track) -> None:
        now_ns = time.time_ns()
        if (
            track.id == self._last_recorded_track_id
            and (now_ns - self._last_recorded_at_ns) < 30_000_000_000
        ):
            return
        release_id = self._release_id_for_playback(track)
        if release_id is None:
            return
        self._store.record_play(
            track_id=track.id,
            release_id=release_id,
            source=track.source.value,
            played_at_ns=now_ns,
        )
        self._last_recorded_track_id = track.id
        self._last_recorded_at_ns = now_ns

    def _release_id_for_playback(self, track: Track) -> str | None:
        if track.id.startswith("local:"):
            return self._store.release_id_for_track(track.id)
        if track.id.startswith("tidal:"):
            if not self._tidal.is_logged_in():
                return None
            try:
                return self._tidal.release_id_for_track(track.id)
            except TidalUnavailableError:
                return None
        if track.id.startswith("qobuz:"):
            if not self._qobuz.is_logged_in():
                return None
            try:
                return self._qobuz.release_id_for_track(track.id)
            except QobuzUnavailableError:
                return None
        return None

    def _set_current_track(
        self,
        track: Track,
        *,
        format_label: str | None = None,
        playback_note: str | None = None,
    ) -> None:
        self._current_track = track
        if track.source.value != "tidal":
            self._tidal_playback_format_track_id = None
            self._tidal_playback_format_label = None
        if format_label is not None:
            base_hint = format_label
            if track.source.value == "tidal":
                self._tidal_playback_format_track_id = track.id
                self._tidal_playback_format_label = format_label
        elif track.source.value == "tidal":
            self._tidal_playback_format_track_id = None
            self._tidal_playback_format_label = None
            base_hint = self._tidal_quality_hint_for_track(track.id)
        elif track.source.value == "qobuz":
            from tunes_player.core.playback_quality import qobuz_stream_format_label

            base_hint = qobuz_stream_format_label(
                self._config_manager.config.qobuz_stream_format_id
            )
        else:
            metadata = self._store.get_file_metadata(track.id)
            base_hint = LibraryStore.quality_hint(metadata)
        if playback_note is not None:
            self._playback_note = playback_note
        self._quality_hint = format_playback_status(
            base_hint, playback_note=self._playback_note
        )
        self._duration_sec = track.duration_sec
        self._reset_playback_position(0.0)

    def _reset_playback_position(self, position_sec: float) -> None:
        self._position_sec = max(0.0, position_sec)
        self._position_synced_at = time.monotonic()

    def _playback_position(self) -> float:
        return self._position_sec

    def _apply_engine_position(self, position_sec: float) -> None:
        self._position_sec = max(0.0, position_sec)
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
