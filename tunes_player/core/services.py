"""Facade between UI and backends — expand as features land."""

from __future__ import annotations

import concurrent.futures
import logging
import multiprocessing
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from typing import Callable, TypeAlias

from tunes_player.core.backends.playable import PlayableSource
from tunes_player.core.backends.qobuz import QobuzClient, QobuzUnavailableError
from tunes_player.core.backends.resolve import resolve_track
from tunes_player.core.backends.tidal import TidalClient, TidalUnavailableError
from tunes_player.core.config import ConfigManager
from tunes_player.core.folder_scan_status import (
    FOLDER_SCAN_FAILED,
    FOLDER_SCAN_INCOMPLETE,
    log_folder_scan_failure,
)
from tunes_player.core.logging_config import diagnostics_log_path
from tunes_player.core.home import (
    NEW_MUSIC_LOCAL_LIMIT,
    NEW_MUSIC_MERGE_LIMIT,
    NEW_MUSIC_STREAMING_PER_SOURCE_LIMIT,
    SUGGESTIONS_MERGE_LIMIT,
    RecentlyAddedItem,
    suggestion_added_ns,
)
from tunes_player.core.library import LibraryScanner, LibraryStore, ScanResult
from tunes_player.core.library.db import is_locked_error
from tunes_player.core.library.store import FileMetadata
from tunes_player.core.library.scanner import ScanFileError
from tunes_player.core.library.scan_process import terminate_orphan_library_scans
from tunes_player.core.library.scan_worker import close_scan_queue, create_scan_process
from tunes_player.core.models import Album, Release, Source, Track
from tunes_player.core.playback.engine import EngineEvent, PlaybackEngine
from tunes_player.core.playback.output_profile import (
    PlaybackOutputProfile,
    PlaybackPathInfo,
    compute_output_profile,
)
from tunes_player.core.playback_quality import format_playback_status
from tunes_player.core.volume import (
    VolumeController,
    VolumeEndpoint,
    VolumeMode,
    derive_volume_mode,
    is_alsa_endpoint_id,
)

EventCallback = Callable[[str], None]
Unsubscribe = Callable[[], None]

log = logging.getLogger(__name__)

MainThreadHook: TypeAlias = Callable[[Callable[[], None]], None]


def _artist_name_matches_query(query: str, artist_name: str) -> bool:
    needle = query.strip().casefold()
    if not needle:
        return False
    return needle in artist_name.casefold()


@dataclass(frozen=True, slots=True)
class SearchResults:
    releases: list[Release]

    @property
    def albums(self) -> list[Release]:
        return self.releases


@dataclass(frozen=True, slots=True)
class _DeferredPlay:
    track_id: str
    release_id: str
    source: str
    played_at_ns: int


@dataclass(frozen=True, slots=True)
class _PreparedTrackLoad:
    generation: int
    track: Track
    resume: bool
    source: PlayableSource | None = None
    profile: PlaybackOutputProfile | None = None
    path_info: PlaybackPathInfo | None = None
    playback_target: str | None = None
    playback_note: str | None = None
    release_id: str | None = None
    quality_hint: str | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class _ScanJob:
    folder: str
    add_paths: tuple[str, ...] = ()
    remove_paths: tuple[str, ...] = ()

    @property
    def is_incremental(self) -> bool:
        return bool(self.add_paths or self.remove_paths)


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
    volume_mode: VolumeMode
    output_using_fallback: bool
    position_sec: float
    duration_sec: float | None


_ENGINE_CREATE_TIMEOUT_SEC = 15.0


class PlayerService:
    """Stable API for GTK (and future) frontends."""

    def __init__(
        self,
        *,
        config: ConfigManager | None = None,
        volume_controller: VolumeController | None = None,
        main_thread_hook: MainThreadHook | None = None,
        prewarm_engine: bool = True,
    ) -> None:
        self._config_manager = config or ConfigManager()
        self._config_manager.load()
        self._store = LibraryStore(self._config_manager.database_path)
        self._volume_controller = volume_controller
        self._main_thread_hook = main_thread_hook
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
        self._playlist_meta: list[Track] = []
        self._playlist_build_generation = 0
        self._playlist_prepared: dict[str, _PreparedTrackLoad] = {}
        self._current_track: Track | None = None
        self._current_release_id: str | None = None
        self._quality_hint = ""
        self._tidal_playback_format_label: str | None = None
        self._tidal_playback_format_track_id: str | None = None
        self._qobuz_playback_format_label: str | None = None
        self._qobuz_playback_format_track_id: str | None = None
        self._current_stream_metadata = None
        self._playback_note: str | None = None
        self._playback_input_class: object | None = None
        self._bit_perfect_playback = False
        self._output_profile: PlaybackOutputProfile | None = None
        self._exclusive_session: object | None = None
        self._position_sec = 0.0
        self._position_synced_at: float | None = None
        self._duration_sec: float | None = None
        self._engine: PlaybackEngine | None = None
        self._engine_error: str | None = None
        self._engine_events: Queue[EngineEvent] = Queue()
        self._alsa_xrun_monitor = self._create_alsa_xrun_monitor()
        self._playback_intended = False
        self._direct_alsa_recovery_at = 0.0
        self._direct_alsa_recovery_attempts = 0
        self._direct_alsa_light_recovery_failures = 0
        self._direct_alsa_watchdog_pos = 0.0
        self._direct_alsa_watchdog_at = 0.0
        self._scan_process: multiprocessing.Process | None = None
        self._scan_queue: multiprocessing.Queue | None = None
        self._scanning_folder: str | None = None
        self._scan_progress: tuple[int, int, str] | None = None
        self._scan_finished_folder: str | None = None
        self._scan_last_result: ScanResult | None = None
        self._scan_last_error: str | None = None
        self._current_scan_job: _ScanJob | None = None
        self._pending_scan_jobs: list[_ScanJob] = []
        self._scan_catalog_total_persisted = False
        self._scan_last_checkpoint_at = 0
        self._pending_startup_art_maintenance = False
        self._art_maintenance_running = False
        self._incremental_coalesce: dict[str, tuple[set[str], set[str]]] = {}
        self._last_recorded_track_id: str | None = None
        self._last_recorded_at_ns = 0
        self._deferred_plays: list[_DeferredPlay] = []
        self._load_generation = 0
        self._play_release_generation = 0
        self._playback_load_active = False
        self._pending_track_loads: Queue[_PreparedTrackLoad] = Queue()
        self._discover_fetch_lock = threading.Lock()
        self._engine_init_lock = threading.Lock()
        self._engine_prewarm_started = False
        self._engine_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="tunes-mpv-owner",
        )
        data_dir = self._config_manager.data_dir
        self._tidal = TidalClient(
            data_dir / "tidal-session.json",
            cache_dir=data_dir / "tidal-cache",
        )
        self._qobuz = self._make_qobuz_client(data_dir)
        self._release_external_playback_contention()
        if prewarm_engine:
            self.schedule_prewarm()

    def schedule_prewarm(self) -> None:
        """Start subprocess mpv on the dedicated owner thread."""
        if (
            self._engine is not None
            or self.playback_available() is not None
            or self._engine_prewarm_started
        ):
            return
        self._engine_prewarm_started = True
        self._engine_executor.submit(self._prewarm_playback_engine_worker)

    def _release_external_playback_contention(self) -> None:
        device = self._mpv_audio_device()
        if not device:
            return
        try:
            from tunes_player.platform.linux.mpv_cleanup import terminate_mpv_using_audio_device

            terminate_mpv_using_audio_device(device)
        except ImportError:
            pass

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
        from tunes_player.engines.factory import probe_playback_engine

        return probe_playback_engine()

    def prewarm_playback_engine(self) -> None:
        """Start subprocess mpv while idle so the first play click skips process startup."""
        self.schedule_prewarm()

    def _prewarm_playback_engine_worker(self) -> None:
        for attempt in range(3):
            try:
                engine = self._ensure_engine_locked()
                if engine is None:
                    return
                ping = getattr(engine, "ping", None)
                if callable(ping):
                    ping()
                if self._engine_is_available(engine):
                    return
            except Exception:
                log.warning(
                    "Playback engine prewarm attempt %s failed",
                    attempt + 1,
                    exc_info=True,
                )
            self._reset_engine_unlocked()
            time.sleep(0.25 * (attempt + 1))
        log.warning("Playback engine prewarm failed after retries")

    def list_releases(self) -> list[Release]:
        return self._store.list_releases()

    def list_albums(self) -> list[Album]:
        return self.list_releases()

    def list_recently_added_items(self) -> list[RecentlyAddedItem]:
        with self._discover_fetch_lock:
            return self._list_recently_added_items_locked()

    def _tidal_new_release_items(self, within_days: int) -> list[RecentlyAddedItem]:
        return self._tidal.list_new_release_items(
            limit=NEW_MUSIC_STREAMING_PER_SOURCE_LIMIT,
            within_days=within_days,
        )

    def _qobuz_new_release_items(self, within_days: int) -> list[RecentlyAddedItem]:
        return self._qobuz.list_new_release_items(
            limit=NEW_MUSIC_STREAMING_PER_SOURCE_LIMIT,
            within_days=within_days,
        )

    def _list_recently_added_items_locked(self) -> list[RecentlyAddedItem]:
        within_days = self._config_manager.config.new_music_within_days
        items = self._store.list_recently_added_items(
            within_days=within_days,
            limit=NEW_MUSIC_LOCAL_LIMIT,
        )
        streaming_futures: dict[str, concurrent.futures.Future[list[RecentlyAddedItem]]] = {}
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="tunes-new-music",
        ) as executor:
            if self._tidal.is_logged_in():
                streaming_futures["tidal"] = executor.submit(
                    self._tidal_new_release_items,
                    within_days,
                )
            if self._qobuz.is_logged_in():
                streaming_futures["qobuz"] = executor.submit(
                    self._qobuz_new_release_items,
                    within_days,
                )
            for name, future in streaming_futures.items():
                try:
                    items.extend(future.result())
                except (TidalUnavailableError, QobuzUnavailableError):
                    pass
                except Exception:
                    log.exception("Failed to load %s new releases", name)
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
        for release_id, played_ns in self._store.list_continue_listening_entries():
            release = self.get_release_summary(release_id)
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
        streaming_futures: dict[str, concurrent.futures.Future[list[RecentlyAddedItem]]] = {}
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="tunes-suggestions",
        ) as executor:
            if self._tidal.is_logged_in():
                streaming_futures["tidal"] = executor.submit(
                    self._tidal.list_suggestion_items,
                )
            if self._qobuz.is_logged_in():
                streaming_futures["qobuz"] = executor.submit(
                    self._qobuz.list_suggestion_items,
                )
            for name, future in streaming_futures.items():
                try:
                    source = Source.TIDAL if name == "tidal" else Source.QOBUZ
                    for index, item in enumerate(future.result()):
                        items.append(
                            RecentlyAddedItem(
                                added_ns=suggestion_added_ns(source, index=index),
                                release=item.release,
                            ),
                        )
                except (TidalUnavailableError, QobuzUnavailableError):
                    pass
                except Exception:
                    log.exception("Failed to load %s suggestions", name)
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

    def get_release_summary(self, release_id: str) -> Release | None:
        """Lightweight release lookup for grids (no full track list)."""
        if release_id.startswith("tidal:"):
            if not self._tidal.is_logged_in():
                return None
            try:
                return self._tidal.get_release_summary(release_id)
            except TidalUnavailableError:
                return None
        if release_id.startswith("qobuz:"):
            if not self._qobuz.is_logged_in():
                return None
            try:
                return self._qobuz.get_release_summary(release_id)
            except QobuzUnavailableError:
                return None
        return self._store.get_release(release_id)

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

    def search(self, query: str, *, artists_only: bool = False) -> SearchResults:
        needle = query.strip()
        if not needle:
            return SearchResults(releases=[])
        releases = self._store.search_releases(needle, artists_only=artists_only)
        seen = {release.id for release in releases}
        if self._tidal.is_logged_in():
            try:
                for release in self._tidal.search_releases(needle):
                    if artists_only and not _artist_name_matches_query(needle, release.artist_name):
                        continue
                    if release.id not in seen:
                        seen.add(release.id)
                        releases.append(release)
            except TidalUnavailableError:
                pass
        if self._qobuz.is_logged_in():
            try:
                for release in self._qobuz.search_releases(needle):
                    if artists_only and not _artist_name_matches_query(needle, release.artist_name):
                        continue
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

    def folder_auto_monitor_enabled(self, folder: str) -> bool:
        return self._config_manager.folder_auto_monitor_enabled(folder)

    def set_folder_auto_monitor(self, folder: str, enabled: bool) -> None:
        self._config_manager.set_folder_auto_monitor(folder, enabled)
        if enabled:
            self.enqueue_scan(folder=folder, priority=True)
        else:
            self._cancel_scan_for_folder(folder)
        self.notify_sources_changed()

    def add_music_folder(self, folder: str, *, auto_monitor: bool = False) -> None:
        self._config_manager.add_music_folder(folder, auto_monitor=auto_monitor)
        if auto_monitor:
            self.enqueue_scan(folder=folder, priority=True)
        self.notify_sources_changed()

    def enqueue_startup_scans(self) -> None:
        threading.Thread(
            target=self._run_startup_reconcile,
            name="tunes-library-reconcile",
            daemon=True,
        ).start()
        enqueued: set[str] = set()
        for folder in self._config_manager.config.music_folders:
            if self.folder_auto_monitor_enabled(folder):
                if not self._folder_scan_is_complete(folder):
                    self.enqueue_scan(folder=folder)
                enqueued.add(str(Path(folder).expanduser().resolve()))
        for folder in self._config_manager.config.music_folders:
            resolved = str(Path(folder).expanduser().resolve())
            if resolved in enqueued:
                continue
            if self._folder_needs_scan_resume(folder):
                self.enqueue_scan(folder=folder)

    def _run_startup_reconcile(self) -> None:
        try:
            self.reconcile_library_catalog()
        except Exception:
            log.exception("Startup library catalog reconciliation failed")

    def reconcile_library_catalog(self) -> int:
        """Drop indexed tracks whose files are outside configured music folders."""
        terminate_orphan_library_scans(db_path=self._config_manager.database_path)
        scanner = LibraryScanner(
            db_path=self._config_manager.database_path,
            config=self._config_manager.config,
        )
        self._store.close()
        try:
            removed = scanner.purge_unconfigured_folders()
        except sqlite3.OperationalError as exc:
            self._store.reconnect()
            if is_locked_error(exc):
                log.warning(
                    "Library catalog reconciliation skipped: %s",
                    exc,
                )
                return 0
            raise
        else:
            self._store.reconnect()
        if removed:
            log.info(
                "Removed %d indexed tracks outside configured music folders",
                removed,
            )
            self._run_on_main_thread(self.notify_library_updated)
        return removed

    def enqueue_startup_art_maintenance(self) -> None:
        """Repair/backfill album art without walking the library tree."""
        if not self._config_manager.config.music_folders:
            return
        self._pending_startup_art_maintenance = True
        self._try_start_art_maintenance()

    def _try_start_art_maintenance(self) -> None:
        if not self._pending_startup_art_maintenance:
            return
        if (
            self._art_maintenance_running
            or self.is_scanning()
            or self._pending_scan_jobs
        ):
            return
        self._pending_startup_art_maintenance = False
        self._art_maintenance_running = True
        threading.Thread(target=self._run_art_maintenance_worker, daemon=True).start()

    def _run_art_maintenance_worker(self) -> None:
        try:
            try:
                added, repaired = self._maintain_library_art_blocking()
            except Exception:
                log.exception("Album art maintenance failed")
                return
            if added or repaired:
                log.info(
                    "Album art maintenance indexed %d and repaired %d covers",
                    added,
                    repaired,
                )
                self.notify_art_updated()
        finally:
            self._art_maintenance_running = False

    def _maintain_library_art_blocking(self) -> tuple[int, int]:
        from tunes_player.core.library.art_cache import maintain_album_art
        from tunes_player.core.library.db import (
            LOCK_RETRY_ATTEMPTS,
            LOCK_RETRY_BASE_DELAY_SEC,
            connect,
            is_locked_error,
        )

        db_path = self._config_manager.database_path
        data_dir = self._config_manager.data_dir
        self._store.close()
        try:
            last_error: sqlite3.OperationalError | None = None
            for attempt in range(LOCK_RETRY_ATTEMPTS):
                connection = connect(db_path)
                try:
                    result = maintain_album_art(connection, data_dir=data_dir)
                    connection.commit()
                    return result
                except sqlite3.OperationalError as exc:
                    connection.rollback()
                    if not is_locked_error(exc) or attempt == LOCK_RETRY_ATTEMPTS - 1:
                        raise
                    last_error = exc
                    time.sleep(LOCK_RETRY_BASE_DELAY_SEC * (attempt + 1))
                except Exception:
                    connection.rollback()
                    raise
                finally:
                    connection.close()
            if last_error is not None:
                raise last_error
            raise RuntimeError("album art maintenance retry loop exited without result")
        finally:
            self._store.reconnect()

    def remove_music_folder(self, folder: str) -> int:
        """Drop a configured folder and purge its indexed tracks from the catalog."""
        resolved = str(Path(folder).expanduser().resolve())
        self._pending_scan_jobs = [
            job
            for job in self._pending_scan_jobs
            if job.folder != resolved
        ]
        self._incremental_coalesce.pop(resolved, None)
        if self.is_scanning():
            self._terminate_active_scan()
        terminate_orphan_library_scans(db_path=self._config_manager.database_path)
        time.sleep(0.1)

        scanner = LibraryScanner(
            db_path=self._config_manager.database_path,
            config=self._config_manager.config,
        )
        self._store.close()
        try:
            removed = scanner.purge_folder(resolved)
        finally:
            self._store.reconnect()

        self._config_manager.remove_music_folder(folder)
        self.notify_library_updated()
        self.notify_sources_changed()
        self._try_start_scan()
        return removed

    def _terminate_active_scan(self) -> None:
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

    def _cancel_scan_for_folder(self, folder: str) -> None:
        resolved = str(Path(folder).expanduser().resolve())
        self._pending_scan_jobs = [
            job
            for job in self._pending_scan_jobs
            if job.folder != resolved
        ]
        self._incremental_coalesce.pop(resolved, None)
        if self._scanning_folder != resolved or not self.is_scanning():
            return
        self._record_interrupted_scan()
        self._terminate_active_scan()
        self._emit("scan_finished")
        self.notify_library_updated()
        self._try_start_scan()

    @property
    def scanning_folder(self) -> str | None:
        return self._scanning_folder

    @property
    def scan_progress(self) -> tuple[int, int, str] | None:
        return self._scan_progress

    @property
    def scan_finished_folder(self) -> str | None:
        return self._scan_finished_folder

    @property
    def scan_last_result(self) -> ScanResult | None:
        return self._scan_last_result

    @property
    def scan_last_error(self) -> str | None:
        return self._scan_last_error

    def scan_library(self, *, folder: str) -> None:
        """Queue a priority scan for one configured folder."""
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

    def _try_start_scan(self) -> None:
        if self._scan_queue is not None or not self._pending_scan_jobs:
            return
        job = self._pending_scan_jobs.pop(0)
        self._start_scan_job(job)

    def count_indexed_files(self, folder: str) -> int:
        return self._store.count_files_under_folder(folder)

    def _folder_scan_is_complete(self, folder: str) -> bool:
        catalog_total = self._config_manager.folder_catalog_total(folder)
        if catalog_total is None or catalog_total <= 0:
            return False
        errors = self._config_manager.folder_last_scan_errors(folder)
        if errors is not None and errors not in (0, FOLDER_SCAN_INCOMPLETE):
            return False
        return self.count_indexed_files(folder) >= catalog_total

    def _folder_needs_scan_resume(self, folder: str) -> bool:
        if self._folder_scan_is_complete(folder):
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
        return self.count_indexed_files(folder) < catalog_total

    def _scan_progress_checkpoint_path(self) -> str | None:
        if self._scan_progress is None:
            return None
        current, total, path = self._scan_progress
        if total <= 0 or current <= 0 or not path:
            return None
        if path.startswith(("Discovering", "Found ", "Finalizing")):
            return None
        try:
            return str(Path(path).expanduser().resolve())
        except (OSError, ValueError):
            return None

    def _record_interrupted_scan(self) -> None:
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

    def _start_scan_job(self, job: _ScanJob) -> None:
        self._current_scan_job = job
        self._scanning_folder = job.folder
        self._scan_progress = None
        self._scan_finished_folder = None
        self._scan_last_result = None
        self._scan_last_error = None
        self._scan_catalog_total_persisted = False
        self._scan_last_checkpoint_at = 0
        checkpoint_path = None
        if not job.is_incremental:
            # Checkpoints are persisted for status only. The scanner always walks
            # the full catalog and fast-skips files already indexed in the DB.
            pass
        terminate_orphan_library_scans(db_path=self._config_manager.database_path)
        self._store.close()
        time.sleep(0.1)
        self._scan_process, self._scan_queue = create_scan_process(
            db_path=self._config_manager.database_path,
            music_folders=self._config_manager.config.music_folders,
            music_folder_added_at=self._config_manager.config.music_folder_added_at,
            scan_folders=[job.folder],
            add_paths=list(job.add_paths) if job.is_incremental else None,
            remove_paths=list(job.remove_paths) if job.is_incremental else None,
            checkpoint_path=checkpoint_path,
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
                self._scan_progress = (message[1], message[2], message[3])
                self._maybe_persist_scan_checkpoint()
                self._emit("scan_progress")
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
                self._scan_last_result = result
                self._scan_finished_folder = finished_folder
                if finished_folder is not None:
                    if result.errors > 0 or file_errors:
                        log_folder_scan_failure(
                            finished_folder,
                            errors=result.errors,
                            log_path=diagnostics_log_path(self._config_manager.data_dir),
                            file_errors=file_errors,
                        )
                    self._config_manager.record_folder_scan(
                        finished_folder,
                        errors=result.errors,
                        scan_kind=scan_kind,
                        catalog_total=result.total_candidates if scan_kind == "full" else None,
                    )
                self._cleanup_scan()
                self._emit("scan_finished")
                self.notify_library_updated()
                return False
            elif kind == "error":
                finished_folder = self._scanning_folder
                self._scan_last_error = message[1]
                self._scan_finished_folder = finished_folder
                if finished_folder is not None:
                    log_folder_scan_failure(
                        finished_folder,
                        errors=FOLDER_SCAN_FAILED,
                        log_path=diagnostics_log_path(self._config_manager.data_dir),
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
                self._cleanup_scan()
                self._emit("scan_error")
                return False

        if self._scan_process is not None and self._scan_process.is_alive():
            return True

        if self._scan_process is not None:
            code = self._scan_process.exitcode
            if code not in (0, None):
                finished_folder = self._scanning_folder
                job = self._current_scan_job
                self._scan_last_error = f"Scan process exited with code {code}"
                self._scan_finished_folder = finished_folder
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
                        self._record_interrupted_scan()
                    else:
                        log_folder_scan_failure(
                            finished_folder,
                            errors=FOLDER_SCAN_FAILED,
                            log_path=diagnostics_log_path(self._config_manager.data_dir),
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
                self._cleanup_scan()
                self._emit("scan_error" if not partial else "scan_finished")
        else:
            self._cleanup_scan()
        return False

    def _cleanup_scan(self) -> None:
        finished_folder = self._scanning_folder
        if self._scan_process is not None:
            self._scan_process.join(timeout=2.0)
            self._scan_process = None
        close_scan_queue(self._scan_queue)
        self._scan_queue = None
        self._scanning_folder = None
        self._scan_progress = None
        self._current_scan_job = None
        self._store.reconnect()
        self._flush_deferred_plays()
        if finished_folder is not None:
            coalesced = self._drain_incremental_coalesce(finished_folder)
            if coalesced is not None:
                self._pending_scan_jobs.insert(0, coalesced)
        self._try_start_scan()
        self._try_start_art_maintenance()

    def _flush_deferred_plays(self) -> None:
        pending = self._deferred_plays
        self._deferred_plays = []
        remaining: list[_DeferredPlay] = []
        for item in pending:
            try:
                self._store.record_play(
                    track_id=item.track_id,
                    release_id=item.release_id,
                    source=item.source,
                    played_at_ns=item.played_at_ns,
                )
            except sqlite3.OperationalError as exc:
                if is_locked_error(exc):
                    remaining.append(item)
                    continue
                log.warning(
                    "Could not flush deferred play for %s",
                    item.track_id,
                    exc_info=True,
                )
            except Exception:
                log.warning(
                    "Could not flush deferred play for %s",
                    item.track_id,
                    exc_info=True,
                )
        if remaining:
            self._deferred_plays = remaining + self._deferred_plays

    def is_scanning(self) -> bool:
        return self._scan_queue is not None

    def notify_library_updated(self) -> None:
        """Call from the GTK main thread after a scan completes."""
        self._store.reconnect()
        self._emit("library_updated")

    def notify_art_updated(self) -> None:
        """Call after album-art cache repair; refreshes in-place UI cover art."""
        self._store.reconnect()
        track = self._current_track
        if track is not None and track.id.startswith("local:"):
            refreshed = self._store.get_track(track.id)
            if refreshed is not None:
                self._current_track = refreshed
        self._emit("art_updated")

    def get_playback_state(self) -> PlaybackState:
        volume_mode = self._volume_mode()
        mpv_soft_volume = volume_mode == "software"
        return PlaybackState(
            current_track=self._current_track,
            is_playing=self._is_playing,
            volume=self._volume,
            muted=self._muted,
            queue=tuple(self._playlist_meta),
            queue_index=self._playlist_position(),
            quality_hint=self._quality_hint,
            bit_perfect_playback=self._bit_perfect_playback,
            playback_note=self._playback_note,
            device_volume=self._device_volume,
            mpv_soft_volume=mpv_soft_volume,
            no_volume_control=volume_mode == "fixed",
            volume_mode=volume_mode,
            output_using_fallback=self._output_using_fallback(),
            position_sec=self._playback_position(),
            duration_sec=self._duration_sec,
        )

    def volume_mode(self) -> VolumeMode:
        return self._volume_mode()

    def volume_adjustable(self) -> bool:
        return self._volume_mode() != "fixed"

    def refresh_output_volume_detection(self) -> None:
        """Re-probe whether the active output supports hardware volume."""
        try:
            from tunes_player.platform.linux.alsa_mixer import clear_alsa_mixer_cache

            clear_alsa_mixer_cache()
        except ImportError:
            pass
        was_device_volume = self._device_volume
        detected = self._has_device_volume()
        self._device_volume = detected
        if (
            was_device_volume
            and not detected
            and self._config_manager.config.volume_control_mode is None
        ):
            self.set_volume_mode("fixed")

    def _playlist_position(self) -> int:
        if not self._playlist_meta:
            return -1
        engine = self._engine
        if engine is None:
            return 0
        pos_fn = getattr(engine, "get_playlist_pos", None)
        if not callable(pos_fn):
            return 0
        pos = pos_fn()
        return pos if pos >= 0 else 0

    def play_playlist_index(self, index: int) -> None:
        if index < 0 or index >= len(self._playlist_meta):
            return
        engine = self._engine
        if engine is None or self._playback_transport_blocked():
            return
        play_index = getattr(engine, "playlist_play_index", None)
        if not callable(play_index):
            return
        play_index(index)
        self._playback_intended = True
        self._is_playing = True
        self._emit("playback_changed")

    def play_track(self, track_id: str) -> None:
        if track_id.startswith("tidal:"):
            self._play_tidal_track(track_id)
            return
        if track_id.startswith("qobuz:"):
            self._play_qobuz_track(track_id)
            return

        self._play_release_generation += 1
        generation = self._play_release_generation

        def worker() -> None:
            track = self._store.get_track(track_id)
            if track is None:
                return
            release_id = self._store.release_id_for_track(track_id)
            if release_id is not None:
                tracks = self.get_release_tracks(release_id)
                start_index = next(
                    (index for index, item in enumerate(tracks) if item.id == track_id),
                    0,
                )
            else:
                tracks = [track]
                start_index = 0

            def apply() -> None:
                if generation != self._play_release_generation:
                    return
                self._start_playlist(tracks, start_index=start_index)

            self._run_on_main_thread(apply)

        threading.Thread(
            target=worker,
            name="tunes-play-track",
            daemon=True,
        ).start()

    def _play_tidal_track(self, track_id: str) -> None:
        if not self._tidal.is_logged_in():
            self._report_error("Sign in to TIDAL in Settings → Sources.")
            return
        try:
            tracks, start_index = self._tidal.queue_for_track(track_id)
        except TidalUnavailableError as exc:
            self._report_error(str(exc), exc=exc)
            return
        if not tracks:
            self._report_error("TIDAL track not found.")
            return
        self._start_playlist(tracks, start_index=start_index)

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
            tracks, start_index = self._qobuz.queue_for_track(track_id)
        except QobuzUnavailableError as exc:
            self._report_error(str(exc), exc=exc)
            return
        if not tracks:
            self._report_error("Qobuz track not found.")
            return
        self._start_playlist(tracks, start_index=start_index)

    def play_release(self, release_id: str, *, start_index: int = 0) -> None:
        self._play_release_generation += 1
        generation = self._play_release_generation

        def worker() -> None:
            tracks = self.get_release_tracks(release_id)
            if not tracks:

                def apply_empty() -> None:
                    if generation != self._play_release_generation:
                        return
                    self._start_play_release(release_id, tracks, start_index=start_index)

                self._run_on_main_thread(apply_empty)
                return

            start_index_clamped = max(0, min(start_index, len(tracks) - 1))

            def apply() -> None:
                if generation != self._play_release_generation:
                    return
                self._start_playlist(tracks, start_index=start_index_clamped)

            self._run_on_main_thread(apply)

        threading.Thread(
            target=worker,
            name="tunes-play-release",
            daemon=True,
        ).start()

    def _start_play_release(
        self,
        release_id: str,
        tracks: list[Track],
        *,
        start_index: int = 0,
    ) -> None:
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
        self._start_playlist(tracks, start_index=start_index)

    def play_album(self, album_id: str, *, start_index: int = 0) -> None:
        self.play_release(album_id, start_index=start_index)

    def current_release_id(self) -> str | None:
        """Release id for the current track, resolved once when playback changes."""
        return self._current_release_id

    def is_release_playing(self, release_id: str) -> bool:
        if self._current_release_id != release_id:
            return False
        return self._is_playing or self._playback_load_active

    def play_or_toggle_release(self, release_id: str, *, start_index: int = 0) -> None:
        if self._current_track is not None and self._current_release_id == release_id:
            self.toggle_play_pause()
            return
        self.play_release(release_id, start_index=start_index)

    def toggle_play_pause(self) -> None:
        if self._current_track is None and self._playlist_meta:
            self._start_playlist(self._playlist_meta, start_index=max(self._playlist_position(), 0))
            return
        if self._current_track is None:
            return
        engine = self._engine
        if engine is None or self._playback_transport_blocked():
            return
        if self._is_playing:
            engine.pause()
        else:
            engine.play()
        self._emit("playback_changed")

    def pause(self) -> None:
        if not self._is_playing:
            return
        self._playback_intended = False
        engine = self._engine
        if engine is not None:
            engine.pause()
        else:
            self._is_playing = False
        self._emit("playback_changed")

    def play(self) -> None:
        if self._current_track is None and self._playlist_meta:
            self._start_playlist(self._playlist_meta, start_index=max(self._playlist_position(), 0))
            return
        if self._current_track is None:
            return
        if self._is_playing:
            return
        self._playback_intended = True
        engine = self._engine
        if engine is None or self._playback_transport_blocked():
            return
        engine.play()
        self._emit("playback_changed")

    def skip_next(self) -> None:
        if not self._playlist_meta:
            return
        engine = self._engine
        if engine is None:
            return
        pos = self._playlist_position()
        count_fn = getattr(engine, "get_playlist_count", None)
        count = count_fn() if callable(count_fn) else len(self._playlist_meta)
        if pos + 1 >= count:
            self._playback_intended = False
            engine.pause()
            self._sync_from_engine()
            self._emit("playback_changed")
            return
        next_fn = getattr(engine, "playlist_next", None)
        if callable(next_fn):
            next_fn()
        self._playback_intended = True
        self._sync_from_engine()
        self._emit("playback_changed")

    def skip_previous(self) -> None:
        if not self._playlist_meta:
            return
        engine = self._engine
        if engine is None:
            return
        if self._position_sec > 3.0:
            engine.seek(0.0)
            self._sync_from_engine()
            self._emit("playback_changed", "position_changed")
            return
        pos = self._playlist_position()
        if pos > 0:
            prev_fn = getattr(engine, "playlist_prev", None)
            if callable(prev_fn):
                prev_fn()
            self._playback_intended = True
            self._sync_from_engine()
            self._emit("playback_changed")

    def seek(self, position_sec: float) -> None:
        engine = self._engine
        if engine is None or self._current_track is None:
            return
        if not self._engine_is_available(engine):
            return
        target = max(0.0, position_sec)
        was_playing = self._is_playing
        try:
            engine.seek(target, resume=was_playing)
        except (TimeoutError, OSError):
            log.debug("Seek ignored while playback engine is unavailable", exc_info=True)
            return
        self._reset_playback_position(target)
        if was_playing:
            self._is_playing = True
        self._emit("position_changed")

    def set_volume(self, level: float, *, notify: bool = True) -> None:
        if not self.volume_adjustable():
            return
        self._volume = max(0.0, min(1.0, level))
        if self._muted and self._volume > 0:
            self._muted = False
        self._push_volume_to_output(notify=notify)

    def toggle_mute(self) -> None:
        if not self.volume_adjustable():
            return
        self._muted = not self._muted
        self._push_volume_to_output(notify=True)

    def _output_volume_level(self) -> float:
        return 0.0 if self._muted else self._volume

    def _push_volume_to_output(self, *, notify: bool = True) -> None:
        if not self.volume_adjustable():
            return
        level = self._output_volume_level()
        mode = self._volume_mode()
        if mode == "hardware" and self._device_volume and self._volume_controller is not None:
            self._volume_controller.set_level(level)
        elif mode == "software":
            engine = self._engine
            if engine is not None:
                engine.set_volume(level)
        if notify:
            self._emit("volume_changed")

    def adjust_volume(self, delta: float) -> None:
        if not self.volume_adjustable():
            return
        mode = self._volume_mode()
        if mode == "hardware" and self._device_volume and self._volume_controller is not None:
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
        self._config_manager.config.volume_control_mode = None
        self._config_manager.save()
        self._rebuild_engine_for_output_change()
        if not self._device_volume:
            self.set_volume_mode("fixed")
        else:
            self._apply_engine_volume_policy()
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

    def set_volume_mode(self, mode: VolumeMode) -> None:
        if mode == "hardware" and not self._device_volume:
            return
        cfg = self._config_manager.config
        cfg.volume_control_mode = None if mode == "hardware" else mode
        cfg.allow_software_volume_fallback = mode == "software"
        self._allow_software_volume_fallback = cfg.allow_software_volume_fallback
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

    def poll_playback_health(self) -> None:
        """Direct ALSA watchdog and xrun checks — keep off the hot event path."""
        self._poll_alsa_xrun_monitor()
        self._poll_direct_alsa_recovery()

    @staticmethod
    def _create_alsa_xrun_monitor():
        try:
            from tunes_player.platform.linux.alsa_xrun_monitor import AlsaXrunMonitor
        except ImportError:
            return None
        return AlsaXrunMonitor()

    def _poll_alsa_xrun_monitor(self) -> None:
        monitor = self._alsa_xrun_monitor
        if monitor is None or not self._is_playing or self._engine is None:
            return
        endpoint_id = self._active_endpoint_id()
        if not is_alsa_endpoint_id(endpoint_id):
            return
        try:
            monitor.poll(
                mpv_audio_device=self._mpv_audio_device(),
                endpoint_id=endpoint_id,
            )
        except OSError:
            log.debug("ALSA xrun monitor poll failed", exc_info=True)

    def _poll_direct_alsa_recovery(self) -> None:
        if not self._playback_intended or not self._is_playing:
            return
        profile = self._output_profile
        engine = self._engine
        if (
            profile is None
            or not profile.direct_alsa
            or engine is None
            or not hasattr(engine, "recover_direct_alsa_output")
        ):
            return

        stall_age_fn = getattr(engine, "playback_stall_age_sec", None)
        pos = engine.get_position()
        now = time.monotonic()
        if abs(pos - self._direct_alsa_watchdog_pos) >= 0.05:
            self._direct_alsa_watchdog_pos = pos
            self._direct_alsa_watchdog_at = now
            self._direct_alsa_light_recovery_failures = 0
            touch = getattr(engine, "_touch_position_clock", None)
            if callable(touch):
                touch()
            return
        if now - self._direct_alsa_watchdog_at < 12.0:
            return
        if callable(stall_age_fn):
            stall_age = stall_age_fn()
            if stall_age is None or stall_age < 8.0:
                return
        self._try_recover_direct_alsa(reason="stall")

    def shutdown(self) -> None:
        self._pending_scan_jobs.clear()
        self._incremental_coalesce.clear()
        if self.is_scanning():
            self._record_interrupted_scan()
        self._terminate_active_scan()
        self._current_scan_job = None
        self._release_exclusive_session()
        if self._alsa_xrun_monitor is not None:
            self._alsa_xrun_monitor.reset()
        engine = self._engine
        self._engine = None
        while True:
            try:
                self._engine_events.get_nowait()
            except Empty:
                break
        while True:
            try:
                self._pending_track_loads.get_nowait()
            except Empty:
                break
        if engine is not None:
            engine.quit()
        self._engine_executor.shutdown(wait=False, cancel_futures=True)
        self._store.close()

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

    def _file_meta_for_playback_profile(
        self, track: Track, *, source: PlayableSource | None = None
    ) -> FileMetadata | None:
        if track.id.startswith("local:"):
            return self._store.get_file_metadata(track.id)
        if source is not None and source.stream_metadata is not None:
            return source.stream_metadata
        return self._current_stream_metadata

    def _remember_stream_metadata(self, source: PlayableSource | None) -> None:
        self._current_stream_metadata = (
            None if source is None else source.stream_metadata
        )

    def _compute_playback_profile_for_track(
        self, track: Track, *, source: PlayableSource | None = None
    ) -> tuple[PlaybackOutputProfile, PlaybackPathInfo]:
        file_meta = self._file_meta_for_playback_profile(track, source=source)
        profile, path_info = compute_output_profile(
            file_meta=file_meta,
            hw_caps=self._hw_caps_for_endpoint(self._active_endpoint_id()),
            endpoint_id=self._active_endpoint_id(),
            exclusive_enabled=self._direct_alsa_exclusive_enabled(),
            device_volume=self._device_volume,
            mpv_soft_volume=self._mpv_soft_volume(),
        )
        return self._apply_portable_usb_playback_path(profile, path_info)

    def _compute_playback_profile_for_current(
        self,
    ) -> tuple[PlaybackOutputProfile, PlaybackPathInfo]:
        track = self._current_track
        if track is None:
            profile, path_info = compute_output_profile(
                file_meta=None,
                hw_caps=self._hw_caps_for_endpoint(self._active_endpoint_id()),
                endpoint_id=self._active_endpoint_id(),
                exclusive_enabled=self._direct_alsa_exclusive_enabled(),
                device_volume=self._device_volume,
                mpv_soft_volume=self._mpv_soft_volume(),
            )
            return self._apply_portable_usb_playback_path(profile, path_info)
        return self._compute_playback_profile_for_track(track)

    def _apply_portable_usb_playback_path(
        self,
        profile: PlaybackOutputProfile,
        path_info: PlaybackPathInfo,
    ) -> tuple[PlaybackOutputProfile, PlaybackPathInfo]:
        try:
            from dataclasses import replace

            from tunes_player.platform.linux.alsa_playback import (
                direct_alsa_use_exclusive,
                portable_usb_playback_note,
            )
        except ImportError:
            return profile, path_info

        endpoint_id = self._active_endpoint_id()
        raw_device = self._raw_mpv_audio_device()
        exclusive_active = direct_alsa_use_exclusive(
            self._config_manager.config.exclusive_device_access,
            endpoint_id,
            raw_device,
        )
        if profile.direct_alsa and not exclusive_active:
            if profile.use_exclusive:
                profile = replace(profile, use_exclusive=False)
        extra = portable_usb_playback_note(
            endpoint_id,
            raw_device,
            exclusive_active=exclusive_active,
        )
        if extra is None:
            return profile, path_info
        base = path_info.playback_note
        if not base:
            note = extra
        elif extra in base:
            note = base
        else:
            note = f"{base} · {extra}"
        if note == path_info.playback_note:
            return profile, path_info
        return profile, replace(path_info, playback_note=note)

    def _direct_alsa_exclusive_enabled(self) -> bool:
        if not self._config_manager.config.exclusive_device_access:
            return False
        try:
            from tunes_player.platform.linux.alsa_playback import direct_alsa_use_exclusive
        except ImportError:
            return True
        return direct_alsa_use_exclusive(
            True,
            self._active_endpoint_id(),
            self._raw_mpv_audio_device(),
        )

    def _usb_direct_alsa_active(self) -> bool:
        profile = self._output_profile
        if profile is None or not profile.direct_alsa:
            return False
        try:
            from tunes_player.platform.linux.alsa_playback import is_usb_alsa_playback
        except ImportError:
            return False
        return is_usb_alsa_playback(
            self._active_endpoint_id(),
            self._raw_mpv_audio_device(),
        )

    def _raw_mpv_audio_device(self) -> str | None:
        return self._mpv_audio_device()

    def _playback_target_for_engine(self, source: PlayableSource) -> str:
        from tunes_player.core.playback.network_playback_cache import (
            resolve_playback_target,
            schedule_playback_cache_warmup,
        )

        cache_dir = self._config_manager.data_dir / "playback-cache"
        path = source.playback_target
        target = resolve_playback_target(path, cache_dir=cache_dir)
        if target == path:
            schedule_playback_cache_warmup(path, cache_dir=cache_dir)
        return target

    def _set_playback_input_class_for_source(self, source: PlayableSource | None) -> None:
        from tunes_player.core.playback.buffer_policy import InputClass, classify_playback_uri

        if source is None:
            self._playback_input_class = None
            return
        self._playback_input_class = classify_playback_uri(source.playback_target)

    def _merge_path_info_playback_note(
        self,
        path_info: PlaybackPathInfo,
    ) -> PlaybackPathInfo:
        from dataclasses import replace

        from tunes_player.core.playback.buffer_policy import (
            InputClass,
            merge_playback_note,
        )

        input_class = self._playback_input_class
        if not isinstance(input_class, InputClass):
            return path_info
        note = merge_playback_note(path_info.playback_note, input_class)
        if note == path_info.playback_note:
            return path_info
        return replace(path_info, playback_note=note)

    def _playback_note_for_source(
        self,
        path_info: PlaybackPathInfo,
        source: PlayableSource,
    ) -> str | None:
        from tunes_player.core.playback.buffer_policy import (
            classify_playback_uri,
            merge_playback_note,
        )

        return merge_playback_note(
            path_info.playback_note,
            classify_playback_uri(source.playback_target),
        )

    def _refresh_usb_playback_isolation(self) -> None:
        if not self._usb_direct_alsa_active():
            return
        engine = self._engine
        if engine is not None and hasattr(engine, "refresh_usb_playback_isolation"):
            engine.refresh_usb_playback_isolation()

    def _try_recover_direct_alsa(self, *, reason: str = "stall") -> bool:
        if not self._playback_intended:
            return False
        profile = self._output_profile
        engine = self._engine
        if (
            profile is None
            or not profile.direct_alsa
            or engine is None
            or not hasattr(engine, "recover_direct_alsa_output")
        ):
            return False
        if self._direct_alsa_recovery_attempts >= 5:
            return False
        now = time.monotonic()
        if now - self._direct_alsa_recovery_at < 5.0:
            return False

        pos_before = engine.get_position()
        usb = self._usb_direct_alsa_active()
        if usb:
            if reason == "error":
                if pos_before <= 0.5:
                    recovered = engine.recover_direct_alsa_output(ao_reload_only=True)
                    if not recovered:
                        recovered = engine.recover_direct_alsa_output(full_reload=True)
                else:
                    recovered = engine.recover_direct_alsa_output(full_reload=True)
            else:
                ao_reload_only = self._direct_alsa_light_recovery_failures < 2
                full_reload = not ao_reload_only
                recovered = engine.recover_direct_alsa_output(
                    full_reload=full_reload,
                    ao_reload_only=ao_reload_only,
                )
            if not recovered:
                self._direct_alsa_light_recovery_failures += 1
                return False
            self._direct_alsa_light_recovery_failures = 0
            self._direct_alsa_recovery_at = now
            self._direct_alsa_recovery_attempts += 1
            self._direct_alsa_watchdog_at = now
            self._direct_alsa_watchdog_pos = engine.get_position()
            self._sync_from_engine()
            self._emit("playback_changed")
            log.warning(
                "Recovered USB direct ALSA playback after %s at %.2fs",
                reason,
                pos_before,
            )
            return True

        if reason == "error":
            if pos_before <= 0.5:
                if engine.recover_direct_alsa_output(ao_reload_only=True):
                    self._direct_alsa_recovery_at = now
                    self._direct_alsa_recovery_attempts += 1
                    self._direct_alsa_watchdog_at = now
                    self._direct_alsa_watchdog_pos = engine.get_position()
                    self._sync_from_engine()
                    self._emit("playback_changed")
                    return True
            if engine.recover_direct_alsa_output(full_reload=True):
                self._direct_alsa_recovery_at = now
                self._direct_alsa_recovery_attempts += 1
                self._direct_alsa_watchdog_at = now
                self._direct_alsa_watchdog_pos = engine.get_position()
                self._sync_from_engine()
                self._emit("playback_changed")
                return True
            self._direct_alsa_light_recovery_failures += 1
            return False

        if (
            self._direct_alsa_light_recovery_failures >= 2
            and hasattr(engine, "switch_to_stable_alsa_output")
            and not getattr(engine, "_stable_output_active", False)
        ):
            if engine.switch_to_stable_alsa_output():
                self._direct_alsa_recovery_at = now
                self._direct_alsa_recovery_attempts += 1
                self._direct_alsa_light_recovery_failures = 0
                self._direct_alsa_watchdog_at = now
                self._direct_alsa_watchdog_pos = engine.get_position()
                self._sync_from_engine()
                self._emit("playback_changed")
                return True

        full_reload = self._direct_alsa_light_recovery_failures >= 3
        if not engine.recover_direct_alsa_output(full_reload=full_reload):
            self._direct_alsa_light_recovery_failures += 1
            return False

        pos_after = engine.get_position()
        if abs(pos_after - pos_before) < 0.25:
            self._direct_alsa_light_recovery_failures += 1
        elif full_reload:
            self._direct_alsa_light_recovery_failures = 0

        self._direct_alsa_recovery_at = now
        self._direct_alsa_recovery_attempts += 1
        self._direct_alsa_watchdog_at = now
        self._direct_alsa_watchdog_pos = pos_after
        self._sync_from_engine()
        self._emit("playback_changed")
        return True

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
        path_info = self._merge_path_info_playback_note(path_info)
        self._bit_perfect_playback = path_info.bit_perfect_playback
        self._playback_note = path_info.playback_note
        self._refresh_quality_hint()

    def _finalize_playback_path_info(self, path_info: PlaybackPathInfo) -> PlaybackPathInfo:
        """Merge engine path info with USB playback notes."""
        profile = self._output_profile
        if profile is not None:
            _, path_info = self._apply_portable_usb_playback_path(profile, path_info)
        return path_info

    def _configure_engine_playback_path(
        self,
        engine: PlaybackEngine,
        track: Track,
        *,
        source: PlayableSource | None = None,
    ) -> None:
        setter = getattr(engine, "set_playback_path_context", None)
        if not callable(setter):
            return
        from tunes_player.core.playback.playback_path import PlaybackPathContext

        setter(
            PlaybackPathContext(
                endpoint_id=self._active_endpoint_id(),
                device_volume=self._device_volume,
                mpv_soft_volume=self._mpv_soft_volume(),
                file_meta=self._file_meta_for_playback_profile(track, source=source),
            )
        )

    def _sync_playback_path_from_engine(self) -> None:
        engine = self._engine
        if engine is None:
            return
        refresher = getattr(engine, "refresh_playback_path_info", None)
        if callable(refresher):
            refresher()
        getter = getattr(engine, "get_playback_path_info", None)
        if not callable(getter):
            return
        path_info = getter()
        if path_info is not None:
            self._apply_path_info(self._finalize_playback_path_info(path_info))

    def _refresh_quality_hint(self) -> None:
        """Rebuild now-playing format line including the active audio layer."""
        track = self._current_track
        if track is None:
            return
        if track.id.startswith("tidal:"):
            base_hint = self._tidal_quality_hint_for_track(track.id)
        elif track.id.startswith("qobuz:"):
            base_hint = self._qobuz_quality_hint_for_track(track.id)
        else:
            metadata = self._store.get_file_metadata(track.id)
            base_hint = LibraryStore.quality_hint(metadata)
        self._quality_hint = format_playback_status(
            base_hint, playback_note=self._playback_note
        )

    def _qobuz_quality_hint_for_track(self, track_id: str) -> str:
        if (
            self._qobuz_playback_format_track_id == track_id
            and self._qobuz_playback_format_label
        ):
            return self._qobuz_playback_format_label
        from tunes_player.core.playback_quality import qobuz_stream_format_label

        return qobuz_stream_format_label(
            self._config_manager.config.qobuz_stream_format_id,
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
        self._remember_stream_metadata(source)
        profile, path_info = self._compute_playback_profile_for_track(
            track, source=source
        )
        self._output_profile = profile
        self._apply_path_info(path_info)
        self._acquire_exclusive_session_if_needed(profile)
        self._set_current_track(
            track,
            format_label=source.format_label,
            playback_note=path_info.playback_note,
        )
        self._configure_engine_playback_path(engine, track, source=source)
        playback_target = self._playback_target_for_engine(source)
        engine.load(
            playback_target,
            start_sec=pos,
            output_profile=profile,
        )
        if not playing:
            engine.pause()

    def _reset_engine(self) -> None:
        if threading.current_thread().name.startswith("tunes-mpv-owner"):
            self._reset_engine_unlocked()
            return
        try:
            self._engine_executor.submit(self._reset_engine_unlocked).result(timeout=5.0)
        except Exception:
            log.debug("Playback engine reset failed", exc_info=True)

    def _reset_engine_unlocked(self) -> None:
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
        self._remember_stream_metadata(source)
        profile, path_info = self._compute_playback_profile_for_track(
            track, source=source
        )
        self._output_profile = profile
        self._apply_path_info(path_info)
        self._set_current_track(
            track,
            format_label=source.format_label,
            playback_note=path_info.playback_note,
        )
        self._configure_engine_playback_path(engine, track, source=source)
        playback_target = self._playback_target_for_engine(source)
        engine.load(playback_target, start_sec=pos, output_profile=profile)
        if not playing:
            engine.pause()
        engine.set_volume(self._volume)
        self._sync_from_engine()
        return True

    def _auto_volume_mode(self) -> VolumeMode:
        return derive_volume_mode(
            device_volume=self._device_volume,
            mpv_soft_volume=(
                not self._device_volume and self._allow_software_volume_fallback
            ),
        )

    def _volume_mode(self) -> VolumeMode:
        override = self._config_manager.config.volume_control_mode
        if override == "software":
            return "software"
        if override == "fixed":
            return "fixed"
        if override == "hardware" and self._device_volume:
            return "hardware"
        return self._auto_volume_mode()

    def _mpv_soft_volume(self) -> bool:
        return self._volume_mode() == "software"

    def _unity_gain_profile(self) -> bool:
        """mpv unity gain — no in-player attenuation (derived bit-perfect active)."""
        return self._volume_mode() != "software"

    def _no_volume_control(self) -> bool:
        return self._volume_mode() == "fixed"

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
        if threading.current_thread().name.startswith("tunes-mpv-owner"):
            return self._ensure_engine_locked()
        try:
            return self._engine_executor.submit(self._ensure_engine_locked).result(
                timeout=_ENGINE_CREATE_TIMEOUT_SEC,
            )
        except concurrent.futures.TimeoutError:
            log.warning("Timed out waiting for playback engine creation")
            return None
        except Exception:
            log.warning("Playback engine creation failed", exc_info=True)
            return None

    def _ensure_engine_locked(self) -> PlaybackEngine | None:
        with self._engine_init_lock:
            engine = self._engine
            if engine is not None and not self._engine_is_available(engine):
                self._engine = None
                try:
                    engine.quit()
                except Exception:
                    log.debug("Could not shut down stale playback engine", exc_info=True)
            if self._engine is not None:
                return self._engine
            return self._create_playback_engine()

    @staticmethod
    def _engine_is_available(engine: PlaybackEngine) -> bool:
        is_available = getattr(engine, "is_available", None)
        if callable(is_available):
            return bool(is_available())
        return True

    def _playback_transport_blocked(self) -> bool:
        if self._playback_load_active:
            return True
        engine = self._engine
        return bool(
            engine is not None and getattr(engine, "load_in_progress", False)
        )

    def _create_playback_engine(self) -> PlaybackEngine | None:
        try:
            from tunes_player.engines.factory import create_playback_engine

            profile, path_info = self._compute_playback_profile_for_current()
            self._output_profile = profile
            self._apply_path_info(path_info)
            self._engine = create_playback_engine(
                unity_gain=self._unity_gain_profile(),
                volume=self._mpv_volume_level(),
                audio_device=self._mpv_audio_device(),
                use_device_output=self._device_volume and not profile.direct_alsa,
                output_profile=profile,
                on_event=self._on_engine_event,
                ipc_socket_path=self._config_manager.data_dir / "mpv-playback.sock",
                endpoint_id=self._active_endpoint_id(),
            )
        except RuntimeError as exc:
            self._report_error(str(exc), exc=exc)
            return None
        return self._engine

    def _sync_exclusive_session_for_profile(self, profile: PlaybackOutputProfile) -> None:
        if profile.direct_alsa and profile.use_exclusive:
            if self._exclusive_session is None:
                self._acquire_exclusive_session_if_needed(profile)
            return
        self._release_exclusive_session()

    def _run_on_main_thread(self, callback: Callable[[], None]) -> None:
        hook = self._main_thread_hook
        if hook is not None:
            hook(callback)
        else:
            callback()

    def _build_prepared_track_load(
        self,
        track: Track,
        *,
        resume: bool,
        generation: int,
    ) -> _PreparedTrackLoad:
        try:
            source = resolve_track(
                self._store, track.id, tidal=self._tidal, qobuz=self._qobuz
            )
        except Exception as exc:
            return _PreparedTrackLoad(
                generation=generation,
                track=track,
                resume=resume,
                error=str(exc),
            )
        if source is None:
            if track.id.startswith("tidal:"):
                error = "Could not play TIDAL track. Check your subscription and sign-in."
            elif track.id.startswith("qobuz:"):
                error = "Could not play Qobuz track. Check your subscription and sign-in."
            else:
                error = "Track file is missing from disk."
            return _PreparedTrackLoad(
                generation=generation,
                track=track,
                resume=resume,
                error=error,
            )
        playback_target = self._playback_target_for_engine(source)

        self._remember_stream_metadata(source)
        profile, path_info = self._compute_playback_profile_for_track(
            track, source=source
        )
        playback_note = self._playback_note_for_source(path_info, source)
        release_id = self._release_id_for_playback(track)
        format_label = source.format_label
        if format_label is not None:
            base_hint = format_label
        elif track.source.value == "tidal":
            base_hint = self._tidal_quality_hint_for_track(track.id)
        elif track.source.value == "qobuz":
            base_hint = self._qobuz_quality_hint_for_track(track.id)
        else:
            metadata = self._store.get_file_metadata(track.id)
            base_hint = LibraryStore.quality_hint(metadata)
        quality_hint = format_playback_status(
            base_hint, playback_note=playback_note
        )
        return _PreparedTrackLoad(
            generation=generation,
            track=track,
            resume=resume,
            source=source,
            profile=profile,
            path_info=path_info,
            playback_target=playback_target,
            playback_note=playback_note,
            release_id=release_id,
            quality_hint=quality_hint,
        )

    def _start_playlist(self, tracks: list[Track], *, start_index: int = 0) -> None:
        if not tracks:
            return
        start_index = max(0, min(start_index, len(tracks) - 1))
        self._playlist_build_generation += 1
        build_generation = self._playlist_build_generation
        self._load_generation += 1
        load_generation = self._load_generation
        self._playlist_meta = tracks
        self._playlist_prepared = {}
        track = tracks[start_index]
        self._playback_load_active = True
        self._playback_input_class = None
        self._engine_error = None
        self._current_track = track
        self._current_release_id = self._release_id_for_playback(track)
        self._duration_sec = track.duration_sec
        self._reset_playback_position(0.0)
        self._is_playing = False
        self._emit("playback_changed", "queue_changed")
        threading.Thread(
            target=self._build_playlist_worker,
            args=(tracks, start_index, build_generation, load_generation),
            name="tunes-playlist-build",
            daemon=True,
        ).start()

    def _build_playlist_worker(
        self,
        tracks: list[Track],
        start_index: int,
        build_generation: int,
        load_generation: int,
    ) -> None:
        try:
            if build_generation != self._playlist_build_generation:
                return
            first = tracks[start_index]
            prepared = self._build_prepared_track_load(
                first, resume=True, generation=load_generation
            )
            if build_generation != self._playlist_build_generation:
                return
            if prepared.error is not None:
                self._run_on_main_thread(lambda: self._fail_track_load(prepared))
                return
            if (
                prepared.source is None
                or prepared.profile is None
                or prepared.path_info is None
                or prepared.playback_target is None
            ):
                self._run_on_main_thread(
                    lambda: self._fail_track_load(
                        _PreparedTrackLoad(
                            generation=load_generation,
                            track=first,
                            resume=True,
                            error="Could not prepare track for playback.",
                        )
                    )
                )
                return

            engine = self._ensure_engine()
            if engine is None:
                self._run_on_main_thread(self._abort_prepared_track_load)
                return

            load_error: BaseException | None = None
            for attempt in range(2):
                if build_generation != self._playlist_build_generation:
                    return
                engine = self._ensure_engine()
                if engine is None:
                    self._run_on_main_thread(self._abort_prepared_track_load)
                    return
                try:
                    profile = prepared.profile
                    source = prepared.source
                    assert profile is not None and source is not None
                    self._sync_exclusive_session_for_profile(profile)
                    self._refresh_usb_playback_isolation()
                    self._configure_engine_playback_path(engine, first, source=source)
                    engine.load(
                        prepared.playback_target,
                        start_sec=source.start_sec,
                        output_profile=profile,
                        mode="replace",
                    )
                    load_error = None
                    break
                except (BrokenPipeError, ConnectionError, OSError) as exc:
                    load_error = exc
                    log.warning(
                        "Playback engine disconnected during playlist load (attempt %s); recreating",
                        attempt + 1,
                        exc_info=True,
                    )
                    self._reset_engine()
                    continue
                except Exception as exc:
                    self._run_on_main_thread(
                        lambda err=exc: self._abort_prepared_track_load(
                            str(err), exc=exc
                        ),
                    )
                    return
            if load_error is not None:
                self._run_on_main_thread(
                    lambda err=load_error: self._abort_prepared_track_load(
                        str(err), exc=err
                    ),
                )
                return
            if build_generation != self._playlist_build_generation:
                return

            self._playlist_prepared[first.id] = prepared
            self._run_on_main_thread(lambda: self._commit_first_playlist_track(prepared))
            self._wait_for_playback_start(engine, build_generation)
            if build_generation != self._playlist_build_generation:
                return

            append_fn = getattr(engine, "append", None)
            if not callable(append_fn):
                return
            for index in range(start_index + 1, len(tracks)):
                if build_generation != self._playlist_build_generation:
                    return
                track = tracks[index]
                next_prepared = self._build_prepared_track_load(
                    track, resume=False, generation=load_generation
                )
                if build_generation != self._playlist_build_generation:
                    return
                if (
                    next_prepared.error is not None
                    or next_prepared.playback_target is None
                ):
                    log.warning(
                        "Skipping playlist append for %s: %s",
                        track.id,
                        next_prepared.error or "missing playback target",
                    )
                    continue
                self._playlist_prepared[track.id] = next_prepared
                try:
                    append_fn(next_prepared.playback_target)
                except Exception:
                    log.warning(
                        "Could not append %s to mpv playlist",
                        track.id,
                        exc_info=True,
                    )
        except Exception:
            log.exception("Background playlist build failed")
            if build_generation == self._playlist_build_generation:
                self._run_on_main_thread(self._abort_prepared_track_load)

    def _wait_for_playback_start(
        self,
        engine: PlaybackEngine,
        build_generation: int,
        *,
        timeout_sec: float = 30.0,
    ) -> bool:
        """Wait until mpv confirms playback before appending more playlist entries."""
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            if build_generation != self._playlist_build_generation:
                return False
            if self._engine_error is not None:
                return False
            if engine.get_position() > 0.05:
                return True
            time.sleep(0.05)
        return False

    def _fail_track_load(self, prepared: _PreparedTrackLoad) -> None:
        if prepared.generation != self._load_generation:
            return
        self._playback_load_active = False
        if prepared.error is not None:
            self._report_error(prepared.error)

    def _commit_first_playlist_track(self, prepared: _PreparedTrackLoad) -> None:
        if prepared.generation != self._load_generation:
            return
        self._apply_prepared_track_state(prepared)
        self._playback_load_active = False
        self._playback_intended = prepared.resume
        self._direct_alsa_recovery_attempts = 0
        self._direct_alsa_light_recovery_failures = 0
        self._direct_alsa_watchdog_at = time.monotonic()
        self._direct_alsa_watchdog_pos = 0.0
        self._is_playing = prepared.resume
        source = prepared.source
        if source is not None:
            self._reset_playback_position(source.start_sec)
        self._emit("playback_changed", "queue_changed")

    def _apply_prepared_track_state(
        self,
        prepared: _PreparedTrackLoad,
        *,
        reset_position: bool = True,
    ) -> None:
        source = prepared.source
        profile = prepared.profile
        path_info = prepared.path_info
        if source is None or profile is None or path_info is None:
            return
        track = prepared.track
        self._engine_error = None
        self._output_profile = profile
        self._set_playback_input_class_for_source(source)
        self._apply_path_info(path_info)
        self._set_current_track(
            track,
            format_label=source.format_label,
            playback_note=prepared.playback_note,
            release_id=prepared.release_id,
            quality_hint=prepared.quality_hint,
            reset_position=reset_position,
        )

    def _on_engine_track_started(self) -> None:
        engine = self._engine
        if engine is None or not self._playlist_meta:
            return
        pos = self._playlist_position()
        if pos < 0 or pos >= len(self._playlist_meta):
            return
        track = self._playlist_meta[pos]
        previous = self._current_track
        if previous is not None and previous.id != track.id:
            try:
                self._record_playback(previous)
            except Exception:
                log.warning(
                    "Could not record play history for %s",
                    previous.id,
                    exc_info=True,
                )
        prepared = self._playlist_prepared.get(track.id)
        if prepared is not None:
            self._apply_prepared_track_state(prepared, reset_position=False)
        elif self._current_track is None or self._current_track.id != track.id:
            self._set_current_track(track, reset_position=False)
        self._playback_load_active = False
        self._direct_alsa_recovery_attempts = 0
        self._direct_alsa_light_recovery_failures = 0
        self._direct_alsa_watchdog_at = time.monotonic()
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
        deferred = _DeferredPlay(
            track_id=track.id,
            release_id=release_id,
            source=track.source.value,
            played_at_ns=now_ns,
        )
        if self.is_scanning():
            self._deferred_plays.append(deferred)
            return
        try:
            self._store.record_play(
                track_id=track.id,
                release_id=release_id,
                source=track.source.value,
                played_at_ns=now_ns,
            )
        except sqlite3.OperationalError as exc:
            if is_locked_error(exc):
                self._deferred_plays.append(deferred)
                log.warning(
                    "Deferred play history for %s (database busy)",
                    track.id,
                )
                return
            raise
        self._last_recorded_track_id = track.id
        self._last_recorded_at_ns = now_ns

    def release_id_for_track(self, track: Track) -> str | None:
        """Resolve the catalog release id for a track (local, TIDAL, or Qobuz)."""
        return self._release_id_for_playback(track)

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
        release_id: str | None = None,
        quality_hint: str | None = None,
        reset_position: bool = True,
    ) -> None:
        self._current_track = track
        if track.id.startswith("local:"):
            self._current_stream_metadata = None
        if release_id is not None:
            self._current_release_id = release_id
        else:
            self._current_release_id = self._release_id_for_playback(track)
        if track.source.value != "tidal":
            self._tidal_playback_format_track_id = None
            self._tidal_playback_format_label = None
        if track.source.value != "qobuz":
            self._qobuz_playback_format_track_id = None
            self._qobuz_playback_format_label = None
        if format_label is not None:
            base_hint = format_label
            if track.source.value == "tidal":
                self._tidal_playback_format_track_id = track.id
                self._tidal_playback_format_label = format_label
            elif track.source.value == "qobuz":
                self._qobuz_playback_format_track_id = track.id
                self._qobuz_playback_format_label = format_label
        elif track.source.value == "tidal":
            self._tidal_playback_format_track_id = None
            self._tidal_playback_format_label = None
            base_hint = self._tidal_quality_hint_for_track(track.id)
        elif track.source.value == "qobuz":
            self._qobuz_playback_format_track_id = None
            self._qobuz_playback_format_label = None
            base_hint = self._qobuz_quality_hint_for_track(track.id)
        else:
            metadata = self._store.get_file_metadata(track.id)
            base_hint = LibraryStore.quality_hint(metadata)
        if playback_note is not None:
            self._playback_note = playback_note
        if quality_hint is not None:
            self._quality_hint = quality_hint
        else:
            self._quality_hint = format_playback_status(
                base_hint, playback_note=self._playback_note
            )
        self._duration_sec = track.duration_sec
        if reset_position:
            self._reset_playback_position(0.0)

    def _reset_playback_position(self, position_sec: float) -> None:
        self._position_sec = max(0.0, position_sec)
        self._position_synced_at = time.monotonic()

    def _playback_position(self) -> float:
        engine = self._engine
        if engine is not None and self._is_playing:
            pos = engine.get_position()
            if abs(pos - self._position_sec) > 0.01:
                self._apply_engine_position(pos)
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

    def _abort_prepared_track_load(
        self,
        message: str | None = None,
        *,
        exc: BaseException | None = None,
    ) -> None:
        self._playback_load_active = False
        if message is not None:
            self._report_error(message, exc=exc)
        else:
            self._notify_playback_unavailable()

    def _handle_engine_event(self, event: EngineEvent) -> None:
        if event == "track_started":
            self._on_engine_track_started()
            return
        if event == "track_finished":
            track = self._current_track
            if track is not None:
                try:
                    self._record_playback(track)
                except Exception:
                    log.warning(
                        "Could not record play history for %s",
                        track.id,
                        exc_info=True,
                    )
            self._playback_intended = False
            self._is_playing = False
            self._sync_from_engine()
            self._emit("playback_changed")
            return
        if self._playback_load_active and event != "playback_error":
            return
        if event == "playback_error":
            if self._fallback_to_software_volume():
                self._emit("playback_changed", "volume_changed")
                return
            profile = self._output_profile
            if profile is not None and profile.direct_alsa:
                self._release_exclusive_session()
                self._acquire_exclusive_session_if_needed(profile)
            engine = self._engine
            release = getattr(engine, "release_alsa_device_contention", None)
            if callable(release):
                release()
            if self._try_recover_direct_alsa(reason="error"):
                return
            self._sync_duration_from_engine()
            self._sync_playback_position_from_engine()
            self._report_error("Playback failed.")
            return
        if event == "position_changed":
            self._sync_playback_position_from_engine()
        elif event == "duration_changed":
            self._sync_duration_from_engine()
            self._emit("playback_changed")
        elif event == "playing_changed":
            engine = self._engine
            if engine is not None:
                self._is_playing = engine.is_playing()
            self._sync_playback_position_from_engine()
            self._emit("playback_changed")
        elif event == "playback_path_changed":
            self._sync_playback_path_from_engine()
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
        if (
            self._main_thread_hook is not None
            and threading.current_thread() is not threading.main_thread()
        ):
            batch = tuple(events)
            self._run_on_main_thread(lambda: self._dispatch_events(batch))
            return
        self._dispatch_events(events)

    def _dispatch_events(self, events: tuple[str, ...]) -> None:
        for event in events:
            for listener in list(self._listeners):
                listener(event)
