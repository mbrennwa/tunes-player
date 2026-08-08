"""Facade between UI and backends — expand as features land."""

from __future__ import annotations

import concurrent.futures
import logging
import multiprocessing
import os
import socket
import sqlite3
import threading
import time
import uuid
from collections import deque
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
from tunes_player.core.labels_sync import LabelSyncService, LabelSyncStatus
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
from tunes_player.core.library.db import connect, is_locked_error, with_db_retry
from tunes_player.core.library.store import FileMetadata
from tunes_player.core.library.scanner import ScanFileError
from tunes_player.core.library.scan_process import terminate_orphan_library_scans
from tunes_player.core.library.scan_worker import close_scan_queue, create_scan_process
from tunes_player.core.models import Release, Source, Track
from tunes_player.core.search_query import parse_search_query, release_matches_query
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
    build_track_path,
    cleanup_download_cache,
    deserialize_track,
    discard_download_job,
    download_cache_dir,
    download_https,
    download_job_label,
    album_folder_for_save,
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
from tunes_player.core.shell_state import refresh_local_release_art_uris
from tunes_player.core.playback.engine import EngineEvent, PlaybackEngine
from tunes_player.core.playback.health_monitor import (
    PlaybackHealthMonitor,
    create_playback_health_monitor,
    sample_from_mpv_properties,
)
from tunes_player.core.playback.output_profile import (
    PlaybackOutputProfile,
    PlaybackPathInfo,
    compute_output_profile,
)
from tunes_player.core.playback_quality import format_playback_status
from tunes_player.core.release_quality import (
    PlaybackPreference,
    playback_preference_for_tier,
    playback_preference_from_shell,
)
from tunes_player.core.release_quality_tiles import (
    expand_releases_by_quality_tier,
    parse_catalog_release_id,
    parse_quality_tier_suffix,
    playback_tier_for_release_id,
    release_for_quality_tier,
)
from tunes_player.core.volume import (
    Unsubscribe as VolumeUnsubscribe,
    VolumeController,
    VolumeEndpoint,
    VolumeMode,
    derive_volume_mode,
    is_alsa_endpoint_id,
)
from tunes_player.core.volume_apply import VolumeApplyCoordinator

EventCallback = Callable[[str], None]
Unsubscribe = Callable[[], None]

log = logging.getLogger(__name__)
_QUEUE_END_MARGIN_SEC = 1.0
_UNSET_PLAYBACK_PREFERENCE = object()

MainThreadHook: TypeAlias = Callable[[Callable[[], None]], None]


@dataclass(frozen=True, slots=True)
class SearchResults:
    releases: list[Release]


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
    position_stalled: bool = False


class PlayerService:
    """Stable API for GTK (and future) frontends."""

    def __init__(
        self,
        *,
        config: ConfigManager | None = None,
        volume_controller: VolumeController | None = None,
        main_thread_hook: MainThreadHook | None = None,
    ) -> None:
        self._config_manager = config or ConfigManager()
        self._config_manager.load()
        self._store = LibraryStore(self._config_manager.database_path)
        self._store.set_preserve_synced_label_orphans(
            self._config_manager.config.labels_sync_enabled,
        )
        self._volume_controller = volume_controller
        self._main_thread_hook = main_thread_hook
        self._label_sync = LabelSyncService(
            library_store=self._store,
            get_enabled=lambda: self._config_manager.config.labels_sync_enabled,
            get_folder=lambda: self._config_manager.config.labels_sync_folder,
            set_status=self._config_manager.set_labels_sync_status,
            device_id=self._config_manager.ensure_labels_sync_device_id(),
            by_name=socket.gethostname() or "unknown",
            writes_available=self._store.writes_available,
            on_applied=lambda: self._run_on_main_thread(self.notify_flags_changed),
        )
        self._label_sync.seed_status(
            self._config_manager.config.labels_sync_last_success_at,
            self._config_manager.config.labels_sync_last_error,
        )
        self._listeners: list[EventCallback] = []
        self._volume = 0.72
        self._muted = False
        self._allow_software_volume_fallback = (
            self._config_manager.config.allow_software_volume_fallback
        )
        self._normalize_volume_control_config()
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
        self._volume_apply = VolumeApplyCoordinator(
            get_controller=lambda: self._volume_controller,
            run_on_main_thread=self._run_on_main_thread,
            apply_inbound_level=self._apply_inbound_device_volume,
        )
        self._volume_controller_unsubscribe: VolumeUnsubscribe | None = None
        if self._volume_controller is not None:
            self._volume_controller_unsubscribe = self._volume_controller.subscribe(
                self._volume_apply.on_device_volume_level
            )
        self._device_output_fallback = False
        self._is_playing = False
        self._playlist_meta: list[Track] = []
        self._playlist_build_generation = 0
        self._playlist_prepared: dict[str, _PreparedTrackLoad] = {}
        self._playlist_playback_preference: PlaybackPreference | None = None
        self._current_track: Track | None = None
        self._current_release_id: str | None = None
        self._release_summaries: dict[str, Release] = {}
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
        self._duration_sec: float | None = None
        self._engine: PlaybackEngine | None = None
        self._engine_error: str | None = None
        self._engine_events: Queue[EngineEvent] = Queue()
        self._playback_intended = False
        self._direct_alsa_recovery_at = 0.0
        self._direct_alsa_recovery_attempts = 0
        self._queue_index = -1
        self._auto_advanced_from_index: int | None = None
        self._scan_process: multiprocessing.Process | None = None
        self._scan_queue: multiprocessing.Queue | None = None
        self._scanning_folder: str | None = None
        self._scan_progress: tuple[int, int, str] | None = None
        self._scan_progress_pinned_total: int | None = None
        self._scan_finished_folder: str | None = None
        self._scan_last_result: ScanResult | None = None
        self._scan_last_error: str | None = None
        self._current_scan_job: _ScanJob | None = None
        self._pending_scan_jobs: list[_ScanJob] = []
        self._scan_catalog_total_persisted = False
        self._scan_last_checkpoint_at = 0
        self._scan_pending_batch: tuple[int, int] | None = None
        self._scan_ui_flush_at = 0.0
        self._pending_startup_art_maintenance = False
        self._art_maintenance_running = False
        self._catalog_reconcile_running = False
        self._library_db_write_lock = threading.Lock()
        self._incremental_coalesce: dict[str, tuple[set[str], set[str]]] = {}
        self._last_recorded_track_id: str | None = None
        self._last_recorded_at_ns = 0
        self._deferred_plays: list[_DeferredPlay] = []
        self._deferred_label_lock = threading.Lock()
        self._deferred_label_toggles: dict[tuple[str, str], bool] = {}
        self._deferred_label_sets: dict[str, frozenset[str]] = {}
        self._load_generation = 0
        self._play_release_generation = 0
        self._playback_load_active = False
        self._pending_track_loads: Queue[_PreparedTrackLoad] = Queue()
        self._discover_fetch_lock = threading.Lock()
        self._engine_init_lock = threading.Lock()
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
        self._playback_position_stalled = False
        self._soft_stall_message: str | None = None
        self._direct_alsa_soft_stall_attempts = 0
        self._playback_health_monitor: PlaybackHealthMonitor | None = (
            create_playback_health_monitor()
        )
        if self._playback_health_monitor is not None:
            self._playback_health_monitor.set_sustained_issues_handler(
                self._on_playback_health_issues
            )
        data_dir = self._config_manager.data_dir
        cleanup_download_cache(data_dir)
        self._tidal = TidalClient(
            data_dir / "tidal-session.json",
            cache_dir=data_dir / "tidal-cache",
        )
        self._qobuz = self._make_qobuz_client(data_dir)
        self._release_external_playback_contention()
        if self._volume_mode() == "fixed":
            self._apply_fixed_mode_hardware_output(reset_app_volume=True)

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

    def list_releases(self) -> list[Release]:
        return self._store.list_releases()

    def list_recently_added_items(self) -> list[RecentlyAddedItem]:
        with self._discover_fetch_lock:
            return self._list_recently_added_items_locked()

    def _dedupe_expand_truncate_discover(
        self,
        items: list[RecentlyAddedItem],
        limit: int,
    ) -> list[RecentlyAddedItem]:
        by_release_id: dict[str, RecentlyAddedItem] = {}
        for item in items:
            existing = by_release_id.get(item.release.id)
            if existing is None or item.added_ns > existing.added_ns:
                by_release_id[item.release.id] = item
        deduped = sorted(
            by_release_id.values(),
            key=lambda item: (-item.added_ns, item.release.title.casefold()),
        )
        expanded = self._expand_discover_items(deduped)
        return sorted(
            expanded,
            key=lambda item: (-item.added_ns, item.release.title.casefold()),
        )[:limit]

    def _list_recently_added_items_locked(self) -> list[RecentlyAddedItem]:
        within_days = self._config_manager.config.new_music_within_days
        items = self._store.list_recently_added_items(
            within_days=within_days,
            limit=NEW_MUSIC_LOCAL_LIMIT,
        )
        streaming_futures: dict[str, concurrent.futures.Future[list[RecentlyAddedItem]]] = {}
        from tunes_player.core.concurrency import DaemonThreadPoolExecutor

        with DaemonThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="tunes-new-music",
        ) as executor:
            if self._tidal.is_logged_in():
                streaming_futures["tidal"] = executor.submit(
                    lambda: self._tidal.list_new_release_items(
                        limit=NEW_MUSIC_STREAMING_PER_SOURCE_LIMIT,
                        within_days=within_days,
                    ),
                )
            if self._qobuz.is_logged_in():
                streaming_futures["qobuz"] = executor.submit(
                    lambda: self._qobuz.list_new_release_items(
                        limit=NEW_MUSIC_STREAMING_PER_SOURCE_LIMIT,
                        within_days=within_days,
                    ),
                )
            for name, future in streaming_futures.items():
                try:
                    items.extend(future.result())
                except (TidalUnavailableError, QobuzUnavailableError):
                    pass
                except Exception:
                    log.exception("Failed to load %s new releases", name)
        return self._dedupe_expand_truncate_discover(items, NEW_MUSIC_MERGE_LIMIT)

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
        from tunes_player.core.concurrency import DaemonThreadPoolExecutor

        with DaemonThreadPoolExecutor(
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
        return self._dedupe_expand_truncate_discover(items, SUGGESTIONS_MERGE_LIMIT)

    def cache_release_summary(self, release: Release) -> None:
        self._release_summaries[release.id] = release

    def expand_releases_with_cache(self, releases: list[Release]) -> list[Release]:
        for release in releases:
            self.cache_release_summary(release)
        expanded = expand_releases_by_quality_tier(releases)
        for release in expanded:
            self.cache_release_summary(release)
        return expanded

    def _expand_discover_items(
        self,
        items: list[RecentlyAddedItem],
    ) -> list[RecentlyAddedItem]:
        if not items:
            return []
        by_id = {item.release.id: item for item in items}
        expanded = self.expand_releases_with_cache(
            [item.release for item in items],
        )
        result: list[RecentlyAddedItem] = []
        seen_tiles: set[str] = set()
        for release in expanded:
            if release.id in seen_tiles:
                continue
            seen_tiles.add(release.id)
            catalog_id = release.catalog_release_id or parse_catalog_release_id(release.id)
            candidate_ids = {release.id, catalog_id}
            best: RecentlyAddedItem | None = None
            for release_id in candidate_ids:
                item = by_id.get(release_id)
                if item is None:
                    continue
                if best is None or item.added_ns > best.added_ns:
                    best = item
            if best is None:
                result.append(RecentlyAddedItem(added_ns=0, release=release))
            else:
                result.append(RecentlyAddedItem(added_ns=best.added_ns, release=release))
        return result

    def get_release_summary(self, release_id: str) -> Release | None:
        """Lightweight release lookup for grids (no full track list)."""
        catalog_id = parse_catalog_release_id(release_id)
        if catalog_id.startswith("tidal:"):
            if not self._tidal.is_logged_in():
                return None
            try:
                return self._tidal.get_release_summary(catalog_id)
            except TidalUnavailableError:
                return None
        if catalog_id.startswith("qobuz:"):
            if not self._qobuz.is_logged_in():
                return None
            try:
                return self._qobuz.get_release_summary(catalog_id)
            except QobuzUnavailableError:
                return None
        return self._store.get_release(catalog_id)

    def get_release(self, release_id: str) -> Release | None:
        cached = self._release_summaries.get(release_id)
        if cached is not None and cached.duration_sec is not None:
            return cached
        tier = parse_quality_tier_suffix(release_id)
        catalog_id = parse_catalog_release_id(release_id)
        release: Release | None
        if catalog_id.startswith("tidal:"):
            if not self._tidal.is_logged_in():
                return None
            try:
                release = self._tidal.get_release(catalog_id)
            except TidalUnavailableError:
                return None
        elif catalog_id.startswith("qobuz:"):
            if not self._qobuz.is_logged_in():
                return None
            try:
                release = self._qobuz.get_release(catalog_id)
            except QobuzUnavailableError:
                return None
        else:
            release = self._store.get_release(catalog_id)
        if release is not None and tier is not None:
            release = release_for_quality_tier(release, tier)
        if release is not None:
            self.cache_release_summary(release)
        return release

    def get_release_tracks(
        self,
        release_id: str,
        *,
        playback_preference: PlaybackPreference | None = None,
    ) -> list[Track]:
        tier = playback_tier_for_release_id(
            release_id,
            summaries=self._release_summaries,
        )
        preference = (
            playback_preference
            if playback_preference is not None
            else playback_preference_for_tier(tier)
        )
        cached = self._release_summaries.get(release_id)
        catalog_id = (
            cached.catalog_release_id
            if cached is not None and cached.catalog_release_id
            else parse_catalog_release_id(release_id)
        )
        resolved_id = catalog_id
        if resolved_id.startswith("tidal:"):
            if not self._tidal.is_logged_in():
                return []
            try:
                return self._tidal.get_release_tracks(resolved_id)
            except TidalUnavailableError:
                return []
        if resolved_id.startswith("qobuz:"):
            if not self._qobuz.is_logged_in():
                return []
            try:
                return self._qobuz.get_release_tracks(resolved_id)
            except QobuzUnavailableError:
                return []
        return self._store.get_release_tracks(resolved_id)

    def search(self, query: str, *, artists_only: bool = False) -> SearchResults:
        parsed = parse_search_query(query)
        if not parsed.terms:
            return SearchResults(releases=[])
        # Pass original stripped query so quoted phrases survive local parse.
        local_query = query.strip()
        plain = parsed.plain_query
        releases = self._store.search_releases(local_query, artists_only=artists_only)
        seen = {release.id for release in releases}
        if self._tidal.is_logged_in():
            try:
                for release in self._tidal.search_releases(plain):
                    if not release_matches_query(
                        release, parsed, artists_only=artists_only
                    ):
                        continue
                    if release.id not in seen:
                        seen.add(release.id)
                        releases.append(release)
            except TidalUnavailableError:
                pass
        if self._qobuz.is_logged_in():
            try:
                for release in self._qobuz.search_releases(plain):
                    if not release_matches_query(
                        release, parsed, artists_only=artists_only
                    ):
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
        finally:
            self._run_on_main_thread(self._after_startup_reconcile)

    def _after_startup_reconcile(self) -> None:
        self._try_start_scan()
        self._try_start_art_maintenance()

    def reconcile_library_catalog(self) -> int:
        """Drop indexed tracks whose files are outside configured music folders."""
        self._catalog_reconcile_running = True
        removed = 0
        try:
            with self._library_db_write_lock:
                terminate_orphan_library_scans(db_path=self._config_manager.database_path)
                scanner = LibraryScanner(
                    db_path=self._config_manager.database_path,
                    config=self._config_manager.config,
                )
                self._store.close()
                try:
                    try:
                        removed = scanner.purge_unconfigured_folders()
                    except sqlite3.OperationalError as exc:
                        if is_locked_error(exc):
                            log.warning(
                                "Library catalog reconciliation skipped: %s",
                                exc,
                            )
                            return 0
                        raise
                finally:
                    self._store.reconnect()
                    self._flush_deferred_label_ops()
        finally:
            self._catalog_reconcile_running = False
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
            or self._catalog_reconcile_running
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
                log.exception("Release art maintenance failed")
                return
            if added or repaired:
                log.info(
                    "Release art maintenance indexed %d and repaired %d covers",
                    added,
                    repaired,
                )
                self.notify_art_updated()
        finally:
            self._art_maintenance_running = False
            self._run_on_main_thread(self._try_start_scan)

    def _maintain_library_art_blocking(self) -> tuple[int, int]:
        from tunes_player.core.library.art_cache import maintain_album_art
        from tunes_player.core.library.db import connect

        db_path = self._config_manager.database_path
        data_dir = self._config_manager.data_dir
        self._store.close()
        try:
            with self._library_db_write_lock:
                def attempt() -> tuple[int, int]:
                    connection = connect(db_path)
                    try:
                        result = maintain_album_art(connection, data_dir=data_dir)
                        connection.commit()
                        return result
                    except Exception:
                        connection.rollback()
                        raise
                    finally:
                        connection.close()

                return with_db_retry(attempt)
        finally:
            self._store.reconnect()
            self._flush_deferred_label_ops()

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
        with self._library_db_write_lock:
            self._store.close()
            try:
                removed = scanner.purge_folder(resolved)
            finally:
                self._store.reconnect()
                self._flush_deferred_label_ops()

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
        self._scan_progress_pinned_total = None

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

    def _any_folder_still_needs_scan(self) -> bool:
        if self._pending_scan_jobs:
            return True
        for folder in self._config_manager.config.music_folders:
            if not self._folder_scan_is_complete(folder):
                return True
            if self._folder_needs_scan_resume(folder):
                return True
        return False

    def _try_start_scan(self) -> None:
        if self._catalog_reconcile_running or self._art_maintenance_running:
            return
        if self._scan_queue is not None or not self._pending_scan_jobs:
            return
        with self._library_db_write_lock:
            if self._catalog_reconcile_running or self._art_maintenance_running:
                return
            if self._scan_queue is not None or not self._pending_scan_jobs:
                return
            job = self._pending_scan_jobs.pop(0)
            self._start_scan_job(job)

    def count_indexed_files(self, folder: str) -> int:
        return self._store.count_files_under_folder(folder)

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
        if path.startswith(("Discovering", "Found ", "Finalizing", "Loading library")):
            return None
        if not path:
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

    def _apply_scan_progress_update(
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

    def _start_scan_job(self, job: _ScanJob) -> None:
        self._current_scan_job = job
        self._scanning_folder = job.folder
        self._scan_progress = None
        self._scan_progress_pinned_total = None
        self._scan_finished_folder = None
        self._scan_last_result = None
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
                self._apply_scan_progress_update(message[1], message[2], message[3])
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
                self._scan_last_result = result
                self._scan_finished_folder = finished_folder
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
                self._cleanup_scan()
                self._emit("scan_finished")
                self.notify_library_updated()
                if result.art_indexed > 0 or result.indexed > 0:
                    self.notify_art_updated()
                return False
            elif kind == "error":
                finished_folder = self._scanning_folder
                self._scan_last_error = message[1]
                self._scan_finished_folder = finished_folder
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
                self._cleanup_scan()
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
        self._scan_progress_pinned_total = None
        self._current_scan_job = None
        self._store.reconnect()
        self._flush_deferred_plays()
        self._flush_deferred_label_ops()
        if finished_folder is not None:
            coalesced = self._drain_incremental_coalesce(finished_folder)
            if coalesced is not None:
                self._pending_scan_jobs.insert(0, coalesced)
        self._try_start_scan()
        if not self._any_folder_still_needs_scan():
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

    def _queue_deferred_label_toggle(self, release_id: str, label: str, *, on: bool) -> None:
        with self._deferred_label_lock:
            pending_set = self._deferred_label_sets.get(release_id)
            if pending_set is not None:
                if on:
                    self._deferred_label_sets[release_id] = pending_set | {label}
                else:
                    self._deferred_label_sets[release_id] = pending_set - {label}
                return
            self._deferred_label_toggles[(release_id, label)] = on

    def _queue_deferred_label_set(self, release_id: str, labels: frozenset[str]) -> None:
        with self._deferred_label_lock:
            for key in list(self._deferred_label_toggles):
                if key[0] == release_id:
                    del self._deferred_label_toggles[key]
            self._deferred_label_sets[release_id] = labels

    def _flush_deferred_label_ops(self) -> None:
        """Apply label mutations queued while the store write connection was closed."""
        if not self._store.writes_available():
            return
        with self._deferred_label_lock:
            sets = self._deferred_label_sets
            toggles = self._deferred_label_toggles
            if not sets and not toggles:
                pass
            else:
                self._deferred_label_sets = {}
                self._deferred_label_toggles = {}
        if not sets and not toggles:
            self._label_sync.schedule_sync()
            return
        device_id = self._label_sync.by_name
        applied = False
        remaining_sets: dict[str, frozenset[str]] = {}
        remaining_toggles: dict[tuple[str, str], bool] = {}
        for release_id, labels in sets.items():
            try:
                self._store.set_release_labels(
                    release_id,
                    labels,
                    by_device=device_id,
                    mark_dirty=True,
                )
                applied = True
            except sqlite3.OperationalError as exc:
                if is_locked_error(exc):
                    remaining_sets[release_id] = labels
                    continue
                log.warning(
                    "Could not flush deferred label set for %s",
                    release_id,
                    exc_info=True,
                )
            except Exception:
                log.warning(
                    "Could not flush deferred label set for %s",
                    release_id,
                    exc_info=True,
                )
        for (release_id, label), on in toggles.items():
            try:
                self._store.toggle_release_label(
                    release_id,
                    label,
                    on=on,
                    by_device=device_id,
                    mark_dirty=True,
                )
                applied = True
            except sqlite3.OperationalError as exc:
                if is_locked_error(exc):
                    remaining_toggles[(release_id, label)] = on
                    continue
                log.warning(
                    "Could not flush deferred label toggle %s/%s",
                    release_id,
                    label,
                    exc_info=True,
                )
            except Exception:
                log.warning(
                    "Could not flush deferred label toggle %s/%s",
                    release_id,
                    label,
                    exc_info=True,
                )
        if remaining_sets or remaining_toggles:
            with self._deferred_label_lock:
                self._deferred_label_sets = {
                    **remaining_sets,
                    **self._deferred_label_sets,
                }
                self._deferred_label_toggles = {
                    **remaining_toggles,
                    **self._deferred_label_toggles,
                }
        if applied:
            self.notify_flags_changed()
        self._label_sync.schedule_sync()

    def is_scanning(self) -> bool:
        return self._scan_queue is not None

    def notify_library_updated(self) -> None:
        """Call from the GTK main thread after a scan completes."""
        if not self.is_scanning():
            self._store.reconnect()
            self._flush_deferred_label_ops()
        self._emit("library_updated")

    def list_user_labels(self) -> tuple[str, ...]:
        # Do not prune here — menus call this often and must stay snappy.
        # Stale rows are cleaned on reconnect / explicit prune.
        names = set(self._store.list_user_label_names())
        with self._deferred_label_lock:
            for labels in self._deferred_label_sets.values():
                names.update(labels)
            for (_release_id, label), on in self._deferred_label_toggles.items():
                if on:
                    names.add(label)
        return tuple(sorted(names, key=lambda item: item.casefold()))

    def get_release_labels(self, release_id: str) -> frozenset[str]:
        catalog_id = parse_catalog_release_id(release_id)
        labels = self._store.get_release_label_names(catalog_id)
        with self._deferred_label_lock:
            pending_set = self._deferred_label_sets.get(catalog_id)
            if pending_set is not None:
                labels = pending_set
            for (rid, name), on in self._deferred_label_toggles.items():
                if rid != catalog_id:
                    continue
                if on:
                    labels = labels | {name}
                else:
                    labels = labels - {name}
        return labels

    def labels_for_releases(self, release_ids: list[str]) -> dict[str, frozenset[str]]:
        catalog_ids = [parse_catalog_release_id(release_id) for release_id in release_ids]
        by_catalog = self._store.labels_for_release_ids(catalog_ids)
        # Preserve caller keys (may include @tier suffixes) mapped to catalog labels.
        result: dict[str, frozenset[str]] = {}
        with self._deferred_label_lock:
            deferred_sets = dict(self._deferred_label_sets)
            deferred_toggles = dict(self._deferred_label_toggles)
        for release_id in release_ids:
            catalog_id = parse_catalog_release_id(release_id)
            labels = by_catalog.get(catalog_id, frozenset())
            pending_set = deferred_sets.get(catalog_id)
            if pending_set is not None:
                labels = pending_set
            for (rid, name), on in deferred_toggles.items():
                if rid != catalog_id:
                    continue
                if on:
                    labels = labels | {name}
                else:
                    labels = labels - {name}
            result[release_id] = labels
        return result

    def set_release_labels(self, release_id: str, labels: frozenset[str]) -> None:
        catalog_id = parse_catalog_release_id(release_id)
        if not self._store.writes_available():
            self._queue_deferred_label_set(catalog_id, labels)
            self.notify_flags_changed()
            return
        self._store.set_release_labels(
            catalog_id,
            labels,
            by_device=self._label_sync.by_name,
            mark_dirty=True,
        )
        self.notify_flags_changed()
        self._label_sync.schedule_sync()

    def toggle_release_label(self, release_id: str, label: str, *, on: bool) -> None:
        catalog_id = parse_catalog_release_id(release_id)
        if not self._store.writes_available():
            self._queue_deferred_label_toggle(catalog_id, label, on=on)
            self.notify_flags_changed()
            return
        self._store.toggle_release_label(
            catalog_id,
            label,
            on=on,
            by_device=self._label_sync.by_name,
            mark_dirty=True,
        )
        self.notify_flags_changed()
        self._label_sync.schedule_sync()

    def toggle_release_label_async(
        self,
        release_id: str,
        label: str,
        *,
        on: bool,
        emit_changed: bool = True,
        on_done: Callable[[], None] | None = None,
    ) -> None:
        """Persist a label toggle off the GTK thread (avoids UI stalls during sync/scan).

        Pass emit_changed=False while a label editor is open so flags_changed /
        grid refresh do not run until the popover closes (keeps dismiss responsive).
        Folder sync is still scheduled as soon as the DB write lands.
        """
        catalog_id = parse_catalog_release_id(release_id)
        device_id = self._label_sync.by_name

        def work() -> None:
            try:
                wrote = False
                if not self._store.writes_available():
                    self._queue_deferred_label_toggle(catalog_id, label, on=on)
                else:
                    self._store.toggle_release_label(
                        catalog_id,
                        label,
                        on=on,
                        by_device=device_id,
                        mark_dirty=True,
                    )
                    wrote = True
            except Exception:
                log.exception(
                    "async label toggle failed release=%s label=%s on=%s",
                    catalog_id,
                    label,
                    on,
                )
                if on_done is not None:
                    self._run_on_main_thread(on_done)
                return
            if emit_changed:
                self._run_on_main_thread(self.notify_flags_changed)
            # Push to the sync folder soon after the DB write, even while the
            # label editor is still open (UI refresh stays gated on emit_changed).
            if wrote:
                self._label_sync.schedule_sync()
            if on_done is not None:
                self._run_on_main_thread(on_done)

        threading.Thread(target=work, name="label-toggle", daemon=True).start()

    def schedule_labels_sync(self) -> None:
        """Debounced push/pull after local label edits."""
        self._label_sync.schedule_sync()

    def list_labelled_releases(self) -> list[Release]:
        return list(self.list_labelled_browse()[0])

    def list_labelled_browse(self) -> tuple[list[Release], tuple[str, ...]]:
        """Return (resolved releases, labelled ids that could not be loaded)."""
        store_ids = self._store.list_labelled_release_ids()
        with self._deferred_label_lock:
            deferred_ids = set(self._deferred_label_sets)
            deferred_ids.update(release_id for release_id, _label in self._deferred_label_toggles)
        ordered_ids: list[str] = []
        seen: set[str] = set()
        for release_id in (*store_ids, *sorted(deferred_ids)):
            if release_id in seen:
                continue
            seen.add(release_id)
            # Include deferred toggles/sets so Labelled matches the label editor.
            if not self.get_release_labels(release_id):
                continue
            ordered_ids.append(release_id)

        releases: list[Release] = []
        unavailable: list[str] = []
        for release_id in ordered_ids:
            try:
                # Grid browse only needs summaries; full track lists are loaded on play.
                release = self.get_release_summary(release_id)
            except Exception:
                log.exception("skip labelled release that failed to load: %s", release_id)
                unavailable.append(release_id)
                continue
            if release is None:
                unavailable.append(release_id)
            else:
                releases.append(release)
        return releases, tuple(unavailable)

    def labels_sync_status(self) -> LabelSyncStatus:
        return self._label_sync.status()

    def labels_sync_ignore_watch_events(self) -> bool:
        return self._label_sync.ignore_watch_events()

    def labels_sync_remote_unchanged(self) -> bool:
        return self._label_sync.remote_digest_unchanged()

    def set_labels_sync_enabled(self, enabled: bool) -> None:
        self._config_manager.set_labels_sync_enabled(enabled)
        self._store.set_preserve_synced_label_orphans(bool(enabled))
        self._emit("labels_sync_changed")
        if enabled:
            self._label_sync.schedule_sync()

    def set_labels_sync_folder(self, folder: str | None) -> None:
        self._config_manager.set_labels_sync_folder(folder)
        self._emit("labels_sync_changed")
        if self._config_manager.config.labels_sync_enabled:
            self._label_sync.schedule_sync()

    def sync_labels_now(self) -> bool:
        return self._label_sync.sync_now()

    def flush_labels_sync(self) -> None:
        self._label_sync.flush()

    def export_labels(self, path: str | Path) -> None:
        self._label_sync.export_to(path)

    def import_labels(self, path: str | Path) -> None:
        self._label_sync.import_from(path)
        self.notify_flags_changed()

    def notify_flags_changed(self) -> None:
        """Call after user label associations change."""
        self._emit("flags_changed")

    def notify_labels_sync_changed(self) -> None:
        self._emit("labels_sync_changed")

    def refresh_local_release_art_uris(self, releases: list[Release]) -> list[Release]:
        """Refresh art_uri on local releases from the library store."""
        local_ids = [release.id for release in releases if release.source == Source.LOCAL]
        if not local_ids:
            return releases
        return refresh_local_release_art_uris(
            releases,
            local_art_by_id=self._store.art_uri_map(local_ids),
        )

    def notify_art_updated(self) -> None:
        """Call after album-art cache repair; refreshes in-place UI cover art."""
        if not self.is_scanning():
            self._store.reconnect()
            self._flush_deferred_label_ops()
        track = self._current_track
        if track is not None and track.id.startswith("local:"):
            refreshed = self._store.get_track(track.id)
            if refreshed is not None:
                self._current_track = refreshed
        self._emit("art_updated")

    _SCAN_UI_FLUSH_INTERVAL_SEC = 1.0

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
            self.notify_library_updated()
        if art_indexed > 0:
            self.notify_art_updated()

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
            position_sec=self._position_sec,
            duration_sec=self._duration_sec,
            position_stalled=self._playback_position_stalled,
        )

    def volume_mode(self) -> VolumeMode:
        return self._volume_mode()

    def volume_control_enabled(self) -> bool:
        return self._volume_mode() != "fixed"

    def volume_adjustable(self) -> bool:
        """Alias for volume_control_enabled (transport / MPRIS call sites)."""
        return self.volume_control_enabled()

    def refresh_output_volume_detection(self) -> None:
        """Re-probe whether the active output supports hardware volume."""
        try:
            from tunes_player.platform.linux.alsa_mixer import clear_alsa_mixer_cache

            clear_alsa_mixer_cache()
        except ImportError:
            pass
        was_device_volume = self._device_volume
        detected = self._has_device_volume(verify_alsa=True)
        self._device_volume = detected
        if was_device_volume != detected:
            self._log_hardware_volume_discovery()
        if was_device_volume and not detected:
            self._apply_engine_volume_policy()
            self._emit("playback_changed")

    def _log_hardware_volume_discovery(self) -> None:
        try:
            from tunes_player.platform.linux.alsa_mixer import (
                alsa_card_from_endpoint_id,
                alsa_device_from_endpoint_id,
            )
            from tunes_player.platform.linux.volume_discovery import discover_hardware_volume
        except ImportError:
            return
        active = self._active_endpoint_id()
        if not is_alsa_endpoint_id(active):
            controller = self._volume_controller
            if controller is not None:
                resolved = getattr(controller, "_resolved_alsa_hw_endpoint_id", None)
                if callable(resolved):
                    active = resolved()
        if not active or not is_alsa_endpoint_id(active):
            return
        card = alsa_card_from_endpoint_id(active)
        device = alsa_device_from_endpoint_id(active)
        if card is None:
            return
        result = discover_hardware_volume(card, device=device, verify=True)
        log.info(
            "output hardware volume detection: available=%s source=%s reason=%s",
            result.control is not None,
            result.source,
            result.reason,
        )

    def _playlist_position(self) -> int:
        if not self._playlist_meta:
            return -1
        if self._queue_index < 0:
            return 0
        return min(self._queue_index, len(self._playlist_meta) - 1)

    def play_playlist_index(self, index: int) -> None:
        if index < 0 or index >= len(self._playlist_meta):
            return
        if self._playback_transport_blocked():
            return
        self._auto_advanced_from_index = None
        self._play_queue_index(index)

    def playback_preference_for_release_id(self, release_id: str) -> PlaybackPreference:
        tier = playback_tier_for_release_id(
            release_id,
            summaries=self._release_summaries,
        )
        return playback_preference_for_tier(tier)

    def playback_preference_for_shell(
        self,
        *,
        enabled_quality_tiers: frozenset[str] | None = None,
    ) -> PlaybackPreference:
        enabled = (
            enabled_quality_tiers
            if enabled_quality_tiers is not None
            else self.config.config.shell_state.enabled_quality_tiers
        )
        return playback_preference_from_shell(enabled or frozenset())

    def _playback_preference_for_shell(
        self,
        *,
        enabled_quality_tiers: frozenset[str] | None = None,
    ) -> PlaybackPreference:
        return self.playback_preference_for_shell(
            enabled_quality_tiers=enabled_quality_tiers,
        )

    def enrich_catalog_quality(self, release_id: str) -> Release | None:
        """Classify streaming catalog quality via album lookup; local is already ready."""
        if release_id.startswith("local:"):
            release = self.get_release(release_id)
        else:
            release = self.get_release_summary(release_id)
        if release is not None:
            self.cache_release_summary(release)
        return release

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
                self._start_playlist(
                    tracks,
                    start_index=start_index,
                    playback_preference=self._playback_preference_for_shell(),
                )

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
        release_id = self.release_id_for_track(tracks[start_index])
        self._start_playlist(
            tracks,
            start_index=start_index,
            playback_preference=self._playback_preference_for_shell(),
        )

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
        release_id = self.release_id_for_track(tracks[start_index])
        self._start_playlist(
            tracks,
            start_index=start_index,
            playback_preference=self._playback_preference_for_shell(),
        )

    def play_release(
        self,
        release_id: str,
        *,
        start_index: int = 0,
    ) -> None:
        self._play_release_generation += 1
        generation = self._play_release_generation
        def worker() -> None:
            tier = playback_tier_for_release_id(
                release_id,
                summaries=self._release_summaries,
            )
            preference = playback_preference_for_tier(tier)
            tracks = self.get_release_tracks(
                release_id,
                playback_preference=preference,
            )
            if not tracks:

                def apply_empty() -> None:
                    if generation != self._play_release_generation:
                        return
                    self._start_play_release(
                        release_id,
                        tracks,
                        start_index=start_index,
                        catalog_release_id=release_id,
                    )

                self._run_on_main_thread(apply_empty)
                return

            start_index_clamped = max(0, min(start_index, len(tracks) - 1))

            def apply() -> None:
                if generation != self._play_release_generation:
                    return
                self._start_playlist(
                    tracks,
                    start_index=start_index_clamped,
                    playback_preference=preference,
                    catalog_release_id=release_id,
                )

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
        catalog_release_id: str | None = None,
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
        self._start_playlist(
            tracks,
            start_index=start_index,
            playback_preference=self._playback_preference_for_shell(),
            catalog_release_id=catalog_release_id or release_id,
        )

    def current_release_id(self) -> str | None:
        """Release id for the current track, resolved once when playback changes."""
        return self._current_release_id

    def is_release_playing(self, release_id: str) -> bool:
        if self._current_release_id != release_id:
            return False
        return self._is_playing or self._playback_load_active

    def play_or_toggle_release(
        self,
        release_id: str,
        *,
        start_index: int = 0,
    ) -> None:
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
        pos = self._playlist_position()
        if pos + 1 >= len(self._playlist_meta):
            engine = self._engine
            if engine is not None:
                engine.pause()
            self._playback_intended = False
            self._is_playing = False
            self._sync_from_engine()
            self._emit("playback_changed")
            return
        self._auto_advanced_from_index = None
        self._play_queue_index(pos + 1)

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
            self._auto_advanced_from_index = None
            self._play_queue_index(pos - 1)

    def seek(self, position_sec: float) -> None:
        engine = self._engine
        if engine is None or self._current_track is None:
            return
        if not self._engine_is_available(engine):
            return
        target = max(0.0, position_sec)
        seek_cap_fn = getattr(engine, "max_seek_position_sec", None)
        if callable(seek_cap_fn):
            seek_cap = seek_cap_fn()
            if seek_cap is not None:
                target = min(target, seek_cap)
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

    def _routes_volume_to_sink(self) -> bool:
        """True when the OS sink/mixer should carry attenuation (instant on PipeWire/ALSA)."""
        if not self._device_volume:
            return False
        return self._volume_mode() in ("hardware", "software")

    def begin_volume_gesture(self) -> None:
        """Ignore inbound stack volume while the UI slider is being dragged."""
        self._volume_apply.begin_gesture()

    def end_volume_gesture(self) -> None:
        self._volume_apply.end_gesture()

    def _apply_inbound_device_volume(self, level: float) -> None:
        """Main-thread update after coordinator filters suppress/gesture."""
        if abs(self._volume - level) < 1e-4 and not (self._muted and level > 0):
            return
        self._volume = level
        if self._muted and level > 0:
            self._muted = False
        self._emit("volume_changed")

    def _set_device_volume_sync(self, level: float) -> None:
        """Blocking device-volume write (mode transitions); echo-suppressed."""
        self._volume_apply.set_level_sync(level)

    def flush_pending_volume_apply(self, *, timeout: float = 2.0) -> None:
        """Block until coalesced device-volume applies finish (tests / shutdown)."""
        self._volume_apply.flush(timeout=timeout)

    def _push_volume_to_output(self, *, notify: bool = True) -> None:
        if not self.volume_adjustable():
            return
        level = self._output_volume_level()
        if self._routes_volume_to_sink() and self._volume_controller is not None:
            self._volume_apply.schedule_apply(level)
        engine = self._engine
        if engine is not None:
            if hasattr(engine, "set_bit_perfect"):
                engine.set_bit_perfect(self._unity_gain_profile())
            engine.set_volume(self._mpv_volume_level())
        if notify:
            self._emit("volume_changed")

    def adjust_volume(self, delta: float) -> None:
        if not self.volume_adjustable():
            return
        self.set_volume(self._volume + delta)

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
        self._apply_engine_volume_policy()
        self._emit("playback_changed")

    def list_output_sinks(self) -> list[VolumeEndpoint]:
        if self._volume_controller is None:
            return []
        normalize = getattr(
            self._volume_controller, "normalize_output_sink_config", None
        )
        before = self._config_manager.config.output_sink_id
        if callable(normalize) and normalize():
            self._config_manager.save()
            after = self._config_manager.config.output_sink_id
            if before and after is None:
                from tunes_player.core.audio_device_messages import OUTPUT_UNAVAILABLE

                self._report_error(OUTPUT_UNAVAILABLE)
        return self._volume_controller.list_endpoints()

    def get_linux_audio_stack_info(self) -> object:
        """Return LinuxAudioStackInfo on Linux, else None."""
        try:
            from tunes_player.platform.linux.audio_probe import (
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

    def set_volume_control_enabled(self, enabled: bool) -> None:
        if enabled == self.volume_control_enabled():
            return
        prev_mode = self._volume_mode()
        cfg = self._config_manager.config
        if enabled:
            cfg.volume_control_mode = None
            cfg.allow_software_volume_fallback = True
            self._allow_software_volume_fallback = True
            self._muted = False
            if self._device_volume and self._volume_controller is not None:
                try:
                    self._volume = self._volume_controller.get_level()
                except OSError:
                    pass
        else:
            self._apply_unity_gain_output()
            cfg.volume_control_mode = "fixed"
            cfg.allow_software_volume_fallback = False
            self._allow_software_volume_fallback = False
        self._config_manager.save()
        self._apply_engine_volume_policy()
        volume_changed = self._sync_device_volume_after_mode_change(prev_mode)
        self._emit("playback_changed")
        if volume_changed:
            self._emit("volume_changed")

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
        if (
            self._playback_intended
            and self._playlist_meta
            and not self._playback_load_active
            and self._engine is not None
        ):
            self._sync_duration_from_engine()
            self.refresh_playback_position_for_ui()
            self._maybe_auto_advance_queue()
        self._publish_playback_health_sample()

    def _publish_playback_health_sample(self) -> None:
        monitor = self._playback_health_monitor
        if monitor is None:
            return
        engine = self._engine
        if engine is None or not self._playback_intended:
            monitor.publish_sample(
                sample_from_mpv_properties(
                    lambda _name: None,
                    intended_playing=False,
                    engine_playing=False,
                    time_pos_sec=self._position_sec,
                    endpoint_id=self._active_endpoint_id(),
                    mpv_audio_device=self._mpv_audio_device(),
                )
            )
            return

        props: dict[str, object] = {}
        snapshot = getattr(engine, "snapshot_health_properties", None)
        if callable(snapshot):
            try:
                props = dict(snapshot())
            except Exception:
                log.debug("Playback health property snapshot failed", exc_info=True)

        def _get(name: str) -> object:
            return props.get(name)

        try:
            engine_playing = bool(engine.is_playing())
            time_pos = float(engine.query_time_pos())
        except Exception:
            log.debug("Playback health engine sample failed", exc_info=True)
            engine_playing = self._is_playing
            time_pos = self._position_sec
        monitor.publish_sample(
            sample_from_mpv_properties(
                _get,
                intended_playing=True,
                engine_playing=engine_playing,
                time_pos_sec=time_pos,
                endpoint_id=self._active_endpoint_id(),
                mpv_audio_device=self._mpv_audio_device(),
            )
        )

    def shutdown(self) -> None:
        unsub = self._volume_controller_unsubscribe
        self._volume_controller_unsubscribe = None
        if unsub is not None:
            unsub()
        self._volume_apply.prepare_shutdown()
        controller = self._volume_controller
        if controller is not None:
            close = getattr(controller, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    log.debug("Volume controller close failed", exc_info=True)
        try:
            self.flush_pending_volume_apply(timeout=1.0)
        except TimeoutError:
            log.debug("Timed out waiting for volume apply on shutdown")
        self.flush_labels_sync()
        self.pause_save_to_disk_for_quit()
        monitor = self._playback_health_monitor
        self._playback_health_monitor = None
        if monitor is not None:
            monitor.stop()
        self._tidal.save_session()
        self._pending_scan_jobs.clear()
        self._incremental_coalesce.clear()
        if self.is_scanning():
            self._record_interrupted_scan()
        self._terminate_active_scan()
        self._current_scan_job = None
        self._release_exclusive_session()
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
        try:
            if not self._store.writes_available():
                self._store.reconnect()
            self._flush_deferred_label_ops()
        except Exception:
            log.exception("Failed to flush deferred labels on shutdown")
        self._store.close()

    def is_saving_to_disk(self) -> bool:
        thread = self._download_thread
        return thread is not None and thread.is_alive()

    def is_save_to_disk_cancel_requested(self) -> bool:
        """True after the user cancels the active job, until the worker exits."""
        return self._download_cancel.is_set()

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
            get_release=self._store.get_release,
            search_releases=self._store.search_releases,
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
                self._store,
                track_id,
                tidal=self._tidal,
                qobuz=self._qobuz,
                playback_preference=self._playback_preference_for_shell(),
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
                lambda f=folder, p=list(add_paths): self.enqueue_incremental_scan(
                    folder=f,
                    add_paths=p,
                )
            )
    def subscribe(self, callback: EventCallback) -> Unsubscribe:
        self._listeners.append(callback)

        def unsubscribe() -> None:
            if callback in self._listeners:
                self._listeners.remove(callback)

        return unsubscribe

    def _has_device_volume(self, *, verify_alsa: bool = False) -> bool:
        controller = self._volume_controller
        if controller is None or not controller.available():
            return False
        if verify_alsa:
            active = self._active_endpoint_id()
            if is_alsa_endpoint_id(active):
                from tunes_player.platform.linux.alsa_mixer import (
                    alsa_mixer_adjustable_for_endpoint,
                )

                return alsa_mixer_adjustable_for_endpoint(active)
        return controller.uses_device_volume

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

    def _raw_mpv_audio_device(self) -> str | None:
        return self._mpv_audio_device()

    def _playback_target_for_engine(self, source: PlayableSource) -> str:
        return source.playback_target

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

    def _try_recover_direct_alsa_on_error(self) -> bool:
        """One ao-reload then full-reload retry after a direct ALSA playback error."""
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
        if self._direct_alsa_recovery_attempts >= 1:
            return False
        now = time.monotonic()
        if now - self._direct_alsa_recovery_at < 5.0:
            return False

        pos_before = self._engine_time_pos_sec(engine)
        recovered = engine.recover_direct_alsa_output(ao_reload_only=True)
        if not recovered:
            recovered = engine.recover_direct_alsa_output(full_reload=True)
        if not recovered:
            return False

        self._direct_alsa_recovery_at = now
        self._direct_alsa_recovery_attempts += 1
        self._sync_from_engine()
        self._emit("playback_changed")
        log.warning(
            "Recovered direct ALSA playback after error at %.2fs",
            pos_before,
        )
        return True

    def soft_stall_message(self) -> str | None:
        return self._soft_stall_message

    def _on_playback_health_issues(self, issues: list) -> None:
        codes = {getattr(issue, "code", "") for issue in issues}
        soft = codes & {"alsa_feed_stalled", "alsa_not_running", "time_pos_stalled"}
        if not soft:
            return
        self._run_on_main_thread(lambda: self._handle_soft_stall(soft))

    def _soft_stall_near_track_end(self, *, margin_sec: float = 5.0) -> bool:
        """True when stalled playback is within ``margin_sec`` of known duration (#66)."""
        duration = self._effective_playback_duration_sec()
        if duration is None or duration <= 0:
            return False
        engine = self._engine
        time_pos = (
            self._engine_time_pos_sec(engine)
            if engine is not None
            else self._position_sec
        )
        return time_pos >= duration - margin_sec

    def _advance_after_near_end_soft_stall(self, codes: set[str]) -> None:
        """Treat near-EOF soft stall as finished and advance — do not ao-reload (#66)."""
        log.warning(
            "Soft stall near track end (%s at %.2fs) — advancing queue instead of AO recovery",
            ",".join(sorted(codes)) if codes else "-",
            self._engine_time_pos_sec(self._engine)
            if self._engine is not None
            else self._position_sec,
        )
        self._playback_position_stalled = False
        self._soft_stall_message = None
        monitor = self._playback_health_monitor
        if monitor is not None:
            monitor.clear_issues()
        self._is_playing = False
        self._advance_queue_after_eof()
        self._emit("playback_changed")

    def _handle_soft_stall(self, codes: set[str]) -> None:
        """Honest UI + mid-track direct-ALSA recovery for soft stalls (#67).

        Near track end (#66): do not ao-reload — synthesize EOF and advance the queue.
        """
        if self._soft_stall_near_track_end():
            self._advance_after_near_end_soft_stall(codes)
            return

        self._playback_position_stalled = True
        self._emit("playback_changed")
        from tunes_player.core.audio_device_messages import (
            AUDIO_OUTPUT_STALLED,
            DIRECT_ALSA_STALLED,
        )

        profile = self._output_profile
        if profile is None or not profile.direct_alsa:
            self._soft_stall_message = AUDIO_OUTPUT_STALLED
            self._emit("playback_stalled")
            return
        if "alsa_feed_stalled" not in codes and "alsa_not_running" not in codes:
            # time_pos only — freeze UI but don't ao-reload unless ALSA feed is stuck
            if "time_pos_stalled" in codes:
                self._soft_stall_message = "Playback position stalled."
                self._emit("playback_stalled")
            return
        now = time.monotonic()
        if now - self._direct_alsa_recovery_at < 8.0:
            return
        if self._direct_alsa_soft_stall_attempts >= 3:
            self._soft_stall_message = DIRECT_ALSA_STALLED
            self._emit("playback_stalled")
            return
        self._soft_stall_message = "Audio output stalled; recovering…"
        self._emit("playback_stalled")
        recovered = self._try_recover_direct_alsa_soft_stall()
        if recovered:
            self._playback_position_stalled = False
            self._soft_stall_message = None
            monitor = self._playback_health_monitor
            if monitor is not None:
                monitor.clear_issues()
            self._emit("playback_changed")
            log.warning("Recovered direct ALSA playback after soft stall")
        else:
            self._soft_stall_message = DIRECT_ALSA_STALLED
            self._emit("playback_stalled")

    def _try_recover_direct_alsa_soft_stall(self) -> bool:
        """ao-reload then full-reload for soft ALSA stalls (pointers frozen, no end-file)."""
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
        now = time.monotonic()
        pos_before = self._engine_time_pos_sec(engine)
        recovered = engine.recover_direct_alsa_output(ao_reload_only=True)
        if not recovered:
            recovered = engine.recover_direct_alsa_output(full_reload=True)
        if not recovered:
            return False
        self._direct_alsa_recovery_at = now
        self._direct_alsa_soft_stall_attempts += 1
        self._sync_from_engine()
        self._emit("playback_changed")
        log.warning(
            "Recovered direct ALSA after soft stall at %.2fs (attempt %d)",
            pos_before,
            self._direct_alsa_soft_stall_attempts,
        )
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

    def _resolve_quality_hint(
        self,
        track: Track,
        *,
        format_label: str | None = None,
        playback_note: str | None = None,
    ) -> str:
        """Resolve tidal/qobuz/local format text and attach playback-note suffix."""
        if format_label is not None:
            base_hint = format_label
        elif track.id.startswith("tidal:") or track.source.value == "tidal":
            base_hint = self._tidal_quality_hint_for_track(track.id)
        elif track.id.startswith("qobuz:") or track.source.value == "qobuz":
            base_hint = self._qobuz_quality_hint_for_track(track.id)
        else:
            metadata = self._store.get_file_metadata(track.id)
            base_hint = LibraryStore.quality_hint(metadata)
        note = self._playback_note if playback_note is None else playback_note
        return format_playback_status(base_hint, playback_note=note)

    def _refresh_quality_hint(self) -> None:
        """Rebuild now-playing format line including the active audio layer."""
        track = self._current_track
        if track is None:
            return
        self._quality_hint = self._resolve_quality_hint(track)

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

    def _apply_fixed_mode_hardware_output(self, *, reset_app_volume: bool) -> bool:
        """Restore sink to 100% for fixed mode; optionally sync in-app volume."""
        if not self._device_volume or self._volume_controller is None:
            return False
        self._set_device_volume_sync(1.0)
        if not reset_app_volume:
            return False
        self._volume = 1.0
        self._muted = False
        return True

    def _sync_device_volume_after_mode_change(self, prev_mode: VolumeMode) -> bool:
        """Align hardware sink with the new volume mode; return True if in-app volume changed."""
        new_mode = self._volume_mode()
        if new_mode == "fixed":
            return self._apply_fixed_mode_hardware_output(
                reset_app_volume=prev_mode == "hardware",
            )
        if (
            new_mode == "software"
            and prev_mode == "hardware"
            and self._device_volume
            and self._volume_controller is not None
        ):
            self._set_device_volume_sync(1.0)
            return False
        if new_mode == "hardware" and prev_mode == "software":
            self._push_volume_to_output(notify=False)
            return False
        return False

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
            self._store,
            track.id,
            tidal=self._tidal,
            qobuz=self._qobuz,
            playback_preference=self._playlist_playback_preference,
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
        self._reset_engine_unlocked()

    def _reset_engine_unlocked(self) -> None:
        engine = self._engine
        self._release_exclusive_session()
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
                self._store,
                track.id,
                tidal=self._tidal,
                qobuz=self._qobuz,
                playback_preference=self._playlist_playback_preference,
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

    def _normalize_volume_control_config(self) -> None:
        cfg = self._config_manager.config
        if cfg.volume_control_mode in (None, "fixed"):
            return
        # Legacy "software" / "hardware" override -> auto (device volume when available).
        cfg.volume_control_mode = None
        cfg.allow_software_volume_fallback = True
        self._allow_software_volume_fallback = True
        self._config_manager.save()

    def _apply_unity_gain_output(self) -> None:
        self._volume = 1.0
        self._muted = False
        if self._device_volume and self._volume_controller is not None:
            self._set_device_volume_sync(1.0)

    def _auto_volume_mode(self) -> VolumeMode:
        return derive_volume_mode(
            device_volume=self._device_volume,
            mpv_soft_volume=(
                not self._device_volume and self._allow_software_volume_fallback
            ),
        )

    def _volume_mode(self) -> VolumeMode:
        if self._config_manager.config.volume_control_mode == "fixed":
            return "fixed"
        return self._auto_volume_mode()

    def _mpv_soft_volume(self) -> bool:
        return self._volume_mode() == "software"

    def _unity_gain_profile(self) -> bool:
        """mpv unity gain — attenuation on the sink or via mpv soft volume only."""
        return not (
            self._volume_mode() == "software" and not self._device_volume
        )

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
        return self._output_volume_level()

    def _mpv_audio_device(self) -> str | None:
        if self._volume_controller is None:
            return None
        try:
            return self._volume_controller.mpv_audio_device()
        except OSError:
            return None

    def _ensure_engine(self) -> PlaybackEngine | None:
        return self._ensure_engine_locked()

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
                self._store,
                track.id,
                tidal=self._tidal,
                qobuz=self._qobuz,
                playback_preference=self._playlist_playback_preference,
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
        quality_hint = self._resolve_quality_hint(
            track,
            format_label=source.format_label,
            playback_note=playback_note,
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

    def _play_queue_index(self, index: int, *, resume: bool = True) -> None:
        if not self._playlist_meta or index < 0 or index >= len(self._playlist_meta):
            return
        self._queue_index = index
        track = self._playlist_meta[index]
        prepared = self._playlist_prepared.get(track.id)
        if (
            prepared is not None
            and prepared.error is None
            and prepared.playback_target is not None
            and prepared.profile is not None
            and prepared.path_info is not None
            and prepared.source is not None
        ):
            self._load_generation += 1
            prepared = _PreparedTrackLoad(
                generation=self._load_generation,
                track=prepared.track,
                resume=resume,
                source=prepared.source,
                profile=prepared.profile,
                path_info=prepared.path_info,
                playback_target=prepared.playback_target,
                playback_note=prepared.playback_note,
                release_id=prepared.release_id,
                quality_hint=prepared.quality_hint,
            )
            self._load_prepared_queue_track(prepared, resume=resume)
            return

        self._playback_load_active = True
        self._load_generation += 1
        generation = self._load_generation

        def worker() -> None:
            built = self._build_prepared_track_load(
                track,
                resume=resume,
                generation=generation,
            )
            if built.error is not None:
                self._run_on_main_thread(lambda: self._fail_track_load(built))
                return
            self._playlist_prepared[track.id] = built
            self._run_on_main_thread(
                lambda: self._load_prepared_queue_track(built, resume=resume)
            )

        threading.Thread(
            target=worker,
            name="tunes-queue-load",
            daemon=True,
        ).start()

    def _load_prepared_queue_track(
        self,
        prepared: _PreparedTrackLoad,
        *,
        resume: bool = True,
    ) -> None:
        if prepared.generation != self._load_generation:
            return
        if (
            prepared.error is not None
            or prepared.source is None
            or prepared.profile is None
            or prepared.path_info is None
            or prepared.playback_target is None
        ):
            self._fail_track_load(prepared)
            return

        engine = self._ensure_engine()
        if engine is None:
            self._abort_prepared_track_load()
            return

        previous = self._current_track
        if previous is not None and previous.id != prepared.track.id:
            try:
                self._record_playback(previous)
            except Exception:
                log.warning(
                    "Could not record play history for %s",
                    previous.id,
                    exc_info=True,
                )

        self._playback_load_active = True
        try:
            self._sync_exclusive_session_for_profile(prepared.profile)
            self._configure_engine_playback_path(
                engine, prepared.track, source=prepared.source
            )
            engine.load(
                prepared.playback_target,
                start_sec=0.0,
                output_profile=prepared.profile,
                mode="replace",
            )
        except (BrokenPipeError, ConnectionError, OSError) as exc:
            log.warning("Playback engine disconnected during queue load", exc_info=True)
            self._reset_engine()
            self._abort_prepared_track_load(str(exc), exc=exc)
            return
        except Exception as exc:
            self._abort_prepared_track_load(str(exc), exc=exc)
            return

        self._playlist_prepared[prepared.track.id] = prepared
        self._apply_prepared_track_state(prepared, reset_position=True)
        self._playback_load_active = False
        self._playback_intended = resume
        self._direct_alsa_recovery_attempts = 0
        self._direct_alsa_soft_stall_attempts = 0
        self._playback_position_stalled = False
        self._soft_stall_message = None
        self._is_playing = resume
        self._auto_advanced_from_index = None
        self._reset_playback_position(0.0)
        self._duration_sec = None
        self._emit("playback_changed", "queue_changed")

    def _effective_playback_duration_sec(self) -> float | None:
        duration = self._duration_sec
        engine = self._engine
        if (duration is None or duration <= 0) and engine is not None:
            duration = engine.get_duration()
            if duration is not None and duration > 0:
                self._duration_sec = duration
        if duration is None or duration <= 0:
            return None
        return duration

    def _maybe_auto_advance_queue(self) -> None:
        """Poll-based advance when time-pos reaches near track end (end-file fallback)."""
        if (
            not self._playback_intended
            or self._playback_load_active
            or not self._playlist_meta
        ):
            return
        duration = self._effective_playback_duration_sec()
        if duration is None:
            return
        engine = self._engine
        time_pos = self._engine_time_pos_sec(engine) if engine is not None else 0.0
        end_threshold = duration - _QUEUE_END_MARGIN_SEC
        if time_pos < end_threshold:
            return
        self._advance_queue_from_current()

    def _advance_queue_after_eof(self) -> None:
        """Advance on mpv end-file EOF without requiring time-pos near duration (#66)."""
        if (
            not self._playback_intended
            or self._playback_load_active
            or not self._playlist_meta
        ):
            return
        self._advance_queue_from_current()

    def _advance_queue_from_current(self) -> None:
        if not self._playlist_meta:
            return
        index = self._playlist_position()
        if self._auto_advanced_from_index == index:
            return
        self._auto_advanced_from_index = index

        if index + 1 >= len(self._playlist_meta):
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
            engine = self._engine
            if engine is not None:
                engine.pause()
            self._playback_intended = False
            self._is_playing = False
            self._emit("playback_changed")
            return

        self._play_queue_index(index + 1)

    def _start_playlist(
        self,
        tracks: list[Track],
        *,
        start_index: int = 0,
        playback_preference: PlaybackPreference | object = _UNSET_PLAYBACK_PREFERENCE,
        catalog_release_id: str | None = None,
    ) -> None:
        if not tracks:
            return
        if playback_preference is not _UNSET_PLAYBACK_PREFERENCE:
            self._playlist_playback_preference = playback_preference  # type: ignore[assignment]
        start_index = max(0, min(start_index, len(tracks) - 1))
        self._playlist_build_generation += 1
        build_generation = self._playlist_build_generation
        self._load_generation += 1
        load_generation = self._load_generation
        self._playlist_meta = tracks
        self._playlist_prepared = {}
        self._queue_index = start_index
        self._auto_advanced_from_index = None
        track = tracks[start_index]
        self._playback_load_active = True
        self._playback_input_class = None
        self._engine_error = None
        self._current_track = track
        self._current_release_id = (
            catalog_release_id or self._release_id_for_playback(track)
        )
        self._duration_sec = None
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
                            str(err), exc=err
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

            for index in range(start_index + 1, len(tracks)):
                if build_generation != self._playlist_build_generation:
                    return
                track = tracks[index]
                if track.id in self._playlist_prepared:
                    continue
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
                        "Skipping playlist prepare for %s: %s",
                        track.id,
                        next_prepared.error or "missing playback target",
                    )
                    continue
                self._playlist_prepared[track.id] = next_prepared
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
        self._direct_alsa_soft_stall_attempts = 0
        self._playback_position_stalled = False
        self._soft_stall_message = None
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
        index = self._queue_index
        if index < 0 or index >= len(self._playlist_meta):
            return
        track = self._playlist_meta[index]
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
        if previous is None or previous.id != track.id:
            self._reset_playback_position(0.0)
            self._duration_sec = None
            self.refresh_playback_position_for_ui()
        self._playback_load_active = False
        self._direct_alsa_recovery_attempts = 0
        self._direct_alsa_soft_stall_attempts = 0
        self._playback_position_stalled = False
        self._soft_stall_message = None
        self._auto_advanced_from_index = None
        self._sync_duration_from_engine()
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
        # Format-cache side effects stay here; hint text resolves below.
        if format_label is not None:
            if track.source.value == "tidal":
                self._tidal_playback_format_track_id = track.id
                self._tidal_playback_format_label = format_label
            elif track.source.value == "qobuz":
                self._qobuz_playback_format_track_id = track.id
                self._qobuz_playback_format_label = format_label
        elif track.source.value == "tidal":
            self._tidal_playback_format_track_id = None
            self._tidal_playback_format_label = None
        elif track.source.value == "qobuz":
            self._qobuz_playback_format_track_id = None
            self._qobuz_playback_format_label = None
        if playback_note is not None:
            self._playback_note = playback_note
        if quality_hint is not None:
            self._quality_hint = quality_hint
        else:
            self._quality_hint = self._resolve_quality_hint(
                track,
                format_label=format_label,
                playback_note=self._playback_note,
            )
        self._duration_sec = None
        if reset_position:
            self._reset_playback_position(0.0)

    def _reset_playback_position(self, position_sec: float) -> None:
        self._position_sec = max(0.0, position_sec)

    def max_seek_position_sec(self) -> float | None:
        engine = self._engine
        if engine is None:
            return None
        cap_fn = getattr(engine, "max_seek_position_sec", None)
        if not callable(cap_fn):
            return None
        return cap_fn()

    @staticmethod
    def _engine_time_pos_sec(engine: PlaybackEngine) -> float:
        query_fn = getattr(engine, "query_time_pos", None)
        if callable(query_fn):
            return max(0.0, query_fn())
        get_fn = getattr(engine, "get_time_pos", None)
        if callable(get_fn):
            return max(0.0, get_fn())
        return max(0.0, engine.get_position())

    def refresh_playback_position_for_ui(self) -> None:
        """Pull live mpv time-pos for the seek bar."""
        engine = self._engine
        if engine is None:
            return
        previous = self._position_sec
        query_fn = getattr(engine, "query_time_pos", None)
        if callable(query_fn):
            self._position_sec = max(0.0, query_fn())
        else:
            self._position_sec = max(0.0, engine.get_position())
        if (
            self._playback_position_stalled
            and self._position_sec > previous + 0.2
        ):
            self._playback_position_stalled = False
            self._soft_stall_message = None
            monitor = self._playback_health_monitor
            if monitor is not None:
                monitor.clear_issues()
            self._emit("playback_changed")

    def _sync_playback_position_from_engine(self) -> None:
        self.refresh_playback_position_for_ui()
        self._maybe_auto_advance_queue()

    def _sync_duration_from_engine(self) -> None:
        engine = self._engine
        if engine is None:
            return
        duration = engine.get_duration()
        if duration is not None and duration > 0:
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
        if event == "track_eof":
            self._is_playing = False
            self._sync_duration_from_engine()
            self.refresh_playback_position_for_ui()
            self._advance_queue_after_eof()
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
            if self._try_recover_direct_alsa_on_error():
                return
            self._sync_duration_from_engine()
            self._sync_playback_position_from_engine()
            if profile is not None and profile.direct_alsa:
                from tunes_player.core.audio_device_messages import DIRECT_ALSA_UNAVAILABLE

                self._report_error(DIRECT_ALSA_UNAVAILABLE)
            else:
                self._report_error("Playback failed.")
            return
        if event == "position_changed":
            self.refresh_playback_position_for_ui()
            self._maybe_auto_advance_queue()
        elif event == "duration_changed":
            self._sync_duration_from_engine()
            self._emit("playback_changed")
        elif event == "playing_changed":
            engine = self._engine
            if engine is not None:
                self._is_playing = engine.is_playing()
            self._sync_duration_from_engine()
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
