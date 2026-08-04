"""Application configuration persisted on disk."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

from platformdirs import user_config_dir, user_data_dir, user_state_dir

from tunes_player.core.home import (
    NEW_MUSIC_LOCAL_WITHIN_DAYS_DEFAULT,
    NEW_MUSIC_LOCAL_WITHIN_DAYS_MAX,
    NEW_MUSIC_LOCAL_WITHIN_DAYS_MIN,
)
from tunes_player.core.shell_state import ShellState, parse_shell_state

APP_NAME = "tunes-player"

# Keep in sync with tunes_player.core.folder_scan_status.
_SCAN_STATUS_FAILED = -1
_SCAN_STATUS_INCOMPLETE = -2


def _normalize_folder_path(key: object) -> str | None:
    if not key:
        return None
    try:
        return str(Path(str(key)).expanduser().resolve())
    except (TypeError, ValueError, OSError):
        return None


def _load_folder_float_map(raw: object, folder_set: set[str]) -> dict[str, float]:
    values: dict[str, float] = {}
    if not isinstance(raw, dict):
        return values
    for key, value in raw.items():
        path = _normalize_folder_path(key)
        if path is None or path not in folder_set:
            continue
        try:
            values[path] = float(value)
        except (TypeError, ValueError):
            continue
    return values


def _load_music_folders(raw: object) -> list[str]:
    folders: list[str] = []
    seen: set[str] = set()
    if not isinstance(raw, list):
        return folders
    for item in raw:
        path = _normalize_folder_path(item)
        if path is None or path in seen:
            continue
        seen.add(path)
        folders.append(path)
    return folders


def _load_folder_str_map(
    raw: object,
    folder_set: set[str],
    *,
    allowed: set[str] | None = None,
) -> dict[str, str]:
    values: dict[str, str] = {}
    if not isinstance(raw, dict):
        return values
    for key, value in raw.items():
        path = _normalize_folder_path(key)
        if path is None or path not in folder_set:
            continue
        text = str(value).strip()
        if not text or (allowed is not None and text not in allowed):
            continue
        values[path] = text
    return values


def _load_folder_int_map(raw: object, folder_set: set[str]) -> dict[str, int]:
    values: dict[str, int] = {}
    if not isinstance(raw, dict):
        return values
    for key, value in raw.items():
        path = _normalize_folder_path(key)
        if path is None or path not in folder_set:
            continue
        try:
            values[path] = int(value)
        except (TypeError, ValueError):
            continue
    return values


def normalize_new_music_within_days(value: object) -> int:
    try:
        days = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return NEW_MUSIC_LOCAL_WITHIN_DAYS_DEFAULT
    return max(NEW_MUSIC_LOCAL_WITHIN_DAYS_MIN, min(NEW_MUSIC_LOCAL_WITHIN_DAYS_MAX, days))


@dataclass
class AppConfig:
    music_folders: list[str] = field(default_factory=list)
    # Unix seconds when each folder was added (or re-added) in Settings.
    music_folder_added_at: dict[str, float] = field(default_factory=dict)
    # When true, Tunes scans on add/startup and watches for filesystem changes.
    music_folder_auto_monitor: dict[str, bool] = field(default_factory=dict)
    # Unix seconds and error count from the most recent completed scan attempt.
    music_folder_last_scan_at: dict[str, float] = field(default_factory=dict)
    music_folder_last_scan_errors: dict[str, int] = field(default_factory=dict)
    # Tier-1 file count from the most recent completed full scan.
    music_folder_catalog_total: dict[str, int] = field(default_factory=dict)
    music_folder_last_scan_kind: dict[str, str] = field(default_factory=dict)
    # Last fully processed file path from an interrupted full scan (sorted order).
    music_folder_scan_checkpoint: dict[str, str] = field(default_factory=dict)
    output_sink_id: str | None = None
    allow_software_volume_fallback: bool = True
    # None = volume control on (hardware when available, else software); fixed = off.
    volume_control_mode: str | None = None
    exclusive_device_access: bool = False
    qobuz_app_id: str | None = None
    qobuz_app_secret: str | None = None
    qobuz_stream_format_id: int = 27
    new_music_within_days: int = NEW_MUSIC_LOCAL_WITHIN_DAYS_DEFAULT
    # Destination for Save to disk (not a music Source unless the user adds it).
    download_folder: str | None = None
    shell_state: ShellState = field(default_factory=ShellState)
    labels_sync_enabled: bool = False
    labels_sync_folder: str | None = None
    labels_sync_last_success_at: float | None = None
    labels_sync_last_error: str | None = None


class ConfigManager:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or Path(user_config_dir(APP_NAME)) / "config.json"
        self._config = AppConfig()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def data_dir(self) -> Path:
        return Path(user_data_dir(APP_NAME))

    @property
    def state_dir(self) -> Path:
        """XDG state directory for runtime logs and similar non-portable state (#76)."""
        return Path(user_state_dir(APP_NAME))

    @property
    def database_path(self) -> Path:
        return self.data_dir / "library.db"

    def load(self) -> AppConfig:
        if not self._path.is_file():
            self._config = AppConfig()
            return self._config

        raw = json.loads(self._path.read_text(encoding="utf-8"))
        folders = _load_music_folders(raw.get("music_folders", []))
        added_raw = raw.get("music_folder_added_at", {})
        added_at: dict[str, float] = {}
        if isinstance(added_raw, dict):
            for key, value in added_raw.items():
                if not key:
                    continue
                try:
                    added_at[str(Path(key).expanduser().resolve())] = float(value)
                except (TypeError, ValueError):
                    continue
        format_id = raw.get("qobuz_stream_format_id", 27)
        try:
            format_id = int(format_id)
        except (TypeError, ValueError):
            format_id = 27
        if format_id not in (5, 6, 7, 27):
            format_id = 27
        monitor_raw = raw.get("music_folder_auto_monitor", {})
        auto_monitor: dict[str, bool] = {}
        if isinstance(monitor_raw, dict):
            for key, value in monitor_raw.items():
                if not key:
                    continue
                try:
                    auto_monitor[str(Path(key).expanduser().resolve())] = bool(value)
                except (TypeError, ValueError, OSError):
                    continue
        folder_set = set(folders)
        auto_monitor = {
            path: enabled
            for path, enabled in auto_monitor.items()
            if path in folder_set
        }
        last_scan_at = _load_folder_float_map(raw.get("music_folder_last_scan_at", {}), folder_set)
        last_scan_errors = _load_folder_int_map(
            raw.get("music_folder_last_scan_errors", {}),
            folder_set,
        )
        catalog_total = _load_folder_int_map(
            raw.get("music_folder_catalog_total", {}),
            folder_set,
        )
        last_scan_kind = _load_folder_str_map(
            raw.get("music_folder_last_scan_kind", {}),
            folder_set,
            allowed={"full", "incremental"},
        )
        scan_checkpoint = _load_folder_str_map(
            raw.get("music_folder_scan_checkpoint", {}),
            folder_set,
        )
        app_id = raw.get("qobuz_app_id")
        app_secret = raw.get("qobuz_app_secret")
        volume_control_mode = raw.get("volume_control_mode")
        if volume_control_mode not in {None, "hardware", "software", "fixed"}:
            volume_control_mode = None
        download_raw = raw.get("download_folder")
        download_folder = None
        if download_raw:
            download_folder = (
                _normalize_folder_path(download_raw) or str(download_raw).strip() or None
            )
        labels_folder_raw = raw.get("labels_sync_folder")
        labels_sync_folder = None
        if labels_folder_raw:
            labels_sync_folder = (
                _normalize_folder_path(labels_folder_raw)
                or str(labels_folder_raw).strip()
                or None
            )
        last_success_raw = raw.get("labels_sync_last_success_at")
        labels_sync_last_success_at: float | None
        try:
            labels_sync_last_success_at = (
                float(last_success_raw) if last_success_raw is not None else None
            )
        except (TypeError, ValueError):
            labels_sync_last_success_at = None
        last_error_raw = raw.get("labels_sync_last_error")
        labels_sync_last_error = (
            str(last_error_raw).strip() if last_error_raw else None
        ) or None
        self._config = AppConfig(
            music_folders=folders,
            music_folder_added_at=added_at,
            music_folder_auto_monitor=auto_monitor,
            music_folder_last_scan_at=last_scan_at,
            music_folder_last_scan_errors=last_scan_errors,
            music_folder_catalog_total=catalog_total,
            music_folder_last_scan_kind=last_scan_kind,
            music_folder_scan_checkpoint=scan_checkpoint,
            output_sink_id=raw.get("output_sink_id") or None,
            allow_software_volume_fallback=bool(
                raw.get("allow_software_volume_fallback", True)
            ),
            volume_control_mode=volume_control_mode,
            exclusive_device_access=bool(raw.get("exclusive_device_access", False)),
            qobuz_app_id=str(app_id).strip() if app_id else None,
            qobuz_app_secret=str(app_secret).strip() if app_secret else None,
            qobuz_stream_format_id=format_id,
            new_music_within_days=normalize_new_music_within_days(
                raw.get("new_music_within_days", NEW_MUSIC_LOCAL_WITHIN_DAYS_DEFAULT),
            ),
            download_folder=download_folder,
            shell_state=parse_shell_state(raw.get("shell_state")),
            labels_sync_enabled=bool(raw.get("labels_sync_enabled", False)),
            labels_sync_folder=labels_sync_folder,
            labels_sync_last_success_at=labels_sync_last_success_at,
            labels_sync_last_error=labels_sync_last_error,
        )
        return self._config

    def save(self) -> None:
        if self._config.volume_control_mode in ("hardware", "software"):
            self._config.volume_control_mode = None
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "music_folders": list(self._config.music_folders),
            "music_folder_added_at": dict(self._config.music_folder_added_at),
            "music_folder_auto_monitor": {
                path: enabled
                for path, enabled in self._config.music_folder_auto_monitor.items()
                if enabled
            },
            "music_folder_last_scan_at": dict(self._config.music_folder_last_scan_at),
            "music_folder_last_scan_errors": dict(self._config.music_folder_last_scan_errors),
            "music_folder_catalog_total": dict(self._config.music_folder_catalog_total),
            "music_folder_last_scan_kind": dict(self._config.music_folder_last_scan_kind),
            "music_folder_scan_checkpoint": dict(self._config.music_folder_scan_checkpoint),
            "output_sink_id": self._config.output_sink_id,
            "allow_software_volume_fallback": self._config.allow_software_volume_fallback,
            "volume_control_mode": self._config.volume_control_mode,
            "exclusive_device_access": self._config.exclusive_device_access,
            "qobuz_app_id": self._config.qobuz_app_id,
            "qobuz_app_secret": self._config.qobuz_app_secret,
            "qobuz_stream_format_id": self._config.qobuz_stream_format_id,
            "new_music_within_days": self._config.new_music_within_days,
            "download_folder": self._config.download_folder,
            "shell_state": self._config.shell_state.to_dict(),
            "labels_sync_enabled": self._config.labels_sync_enabled,
            "labels_sync_folder": self._config.labels_sync_folder,
            "labels_sync_last_success_at": self._config.labels_sync_last_success_at,
            "labels_sync_last_error": self._config.labels_sync_last_error,
        }
        self._path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    @property
    def config(self) -> AppConfig:
        return self._config

    def _canonical_folder_path(self, folder: str) -> str | None:
        target = _normalize_folder_path(folder)
        if target is None:
            return None
        for item in self._config.music_folders:
            item_path = _normalize_folder_path(item)
            if item_path == target:
                return item_path
        return None

    def add_music_folder(self, folder: str, *, auto_monitor: bool = False) -> None:
        path = _normalize_folder_path(folder)
        if path is None:
            return
        canonical = self._canonical_folder_path(path)
        if canonical is not None:
            path = canonical
        elif path not in self._config.music_folders:
            self._config.music_folders.append(path)
        self._config.music_folder_added_at[path] = time.time()
        self._config.music_folder_auto_monitor[path] = auto_monitor
        self.save()

    def remove_music_folder(self, folder: str) -> None:
        path = self._canonical_folder_path(folder)
        if path is None:
            return
        self._config.music_folders = [item for item in self._config.music_folders if item != path]
        self._config.music_folder_added_at.pop(path, None)
        self._config.music_folder_auto_monitor.pop(path, None)
        self._config.music_folder_last_scan_at.pop(path, None)
        self._config.music_folder_last_scan_errors.pop(path, None)
        self._config.music_folder_catalog_total.pop(path, None)
        self._config.music_folder_last_scan_kind.pop(path, None)
        self._config.music_folder_scan_checkpoint.pop(path, None)
        self.save()

    def record_folder_scan(
        self,
        folder: str,
        *,
        errors: int,
        scanned_at: float | None = None,
        scan_kind: Literal["full", "incremental"] = "full",
        catalog_total: int | None = None,
        checkpoint: str | Literal["clear"] | None = None,
    ) -> None:
        path = self._canonical_folder_path(folder)
        if path is None:
            return
        self._config.music_folder_last_scan_at[path] = scanned_at if scanned_at is not None else time.time()
        self._config.music_folder_last_scan_errors[path] = int(errors)
        self._config.music_folder_last_scan_kind[path] = scan_kind
        if scan_kind == "full" and catalog_total is not None and catalog_total > 0:
            self._config.music_folder_catalog_total[path] = int(catalog_total)
        if scan_kind == "full":
            if checkpoint == "clear":
                self._config.music_folder_scan_checkpoint.pop(path, None)
            elif errors == _SCAN_STATUS_INCOMPLETE:
                if isinstance(checkpoint, str) and checkpoint.strip():
                    self._config.music_folder_scan_checkpoint[path] = checkpoint.strip()
            elif errors != _SCAN_STATUS_FAILED:
                self._config.music_folder_scan_checkpoint.pop(path, None)
            elif isinstance(checkpoint, str) and checkpoint.strip():
                self._config.music_folder_scan_checkpoint[path] = checkpoint.strip()
        self.save()

    def folder_scan_checkpoint(self, folder: str) -> str | None:
        path = self._canonical_folder_path(folder)
        if path is None:
            return None
        return self._config.music_folder_scan_checkpoint.get(path)

    def set_folder_scan_checkpoint(self, folder: str, checkpoint: str | None) -> None:
        path = self._canonical_folder_path(folder)
        if path is None:
            return
        if checkpoint is None or not checkpoint.strip():
            self._config.music_folder_scan_checkpoint.pop(path, None)
        else:
            self._config.music_folder_scan_checkpoint[path] = checkpoint.strip()
        self.save()

    def folder_last_scan_at(self, folder: str) -> float | None:
        path = self._canonical_folder_path(folder)
        if path is None:
            return None
        return self._config.music_folder_last_scan_at.get(path)

    def folder_last_scan_errors(self, folder: str) -> int | None:
        path = self._canonical_folder_path(folder)
        if path is None:
            return None
        return self._config.music_folder_last_scan_errors.get(path)

    def folder_catalog_total(self, folder: str) -> int | None:
        path = self._canonical_folder_path(folder)
        if path is None:
            return None
        return self._config.music_folder_catalog_total.get(path)

    def folder_last_scan_kind(self, folder: str) -> str | None:
        path = self._canonical_folder_path(folder)
        if path is None:
            return None
        return self._config.music_folder_last_scan_kind.get(path)

    def set_folder_auto_monitor(self, folder: str, enabled: bool) -> None:
        path = self._canonical_folder_path(folder)
        if path is None:
            return
        self._config.music_folder_auto_monitor[path] = enabled
        self.save()

    def folder_auto_monitor_enabled(self, folder: str) -> bool:
        path = self._canonical_folder_path(folder)
        if path is None:
            return False
        return bool(self._config.music_folder_auto_monitor.get(path, False))

    def set_new_music_within_days(self, days: int) -> None:
        self._config.new_music_within_days = normalize_new_music_within_days(days)
        self.save()

    def set_download_folder(self, folder: str | None) -> None:
        if folder is None or not str(folder).strip():
            self._config.download_folder = None
        else:
            normalized = _normalize_folder_path(folder)
            self._config.download_folder = normalized or str(folder).strip()
        self.save()

    def set_labels_sync_enabled(self, enabled: bool) -> None:
        self._config.labels_sync_enabled = bool(enabled)
        self.save()

    def set_labels_sync_folder(self, folder: str | None) -> None:
        if folder is None or not str(folder).strip():
            self._config.labels_sync_folder = None
        else:
            normalized = _normalize_folder_path(folder)
            self._config.labels_sync_folder = normalized or str(folder).strip()
        self.save()

    def set_labels_sync_status(
        self,
        last_success_at: float | None,
        last_error: str | None,
    ) -> None:
        self._config.labels_sync_last_success_at = last_success_at
        self._config.labels_sync_last_error = (
            str(last_error).strip() if last_error else None
        ) or None
        self.save()

    def set_shell_state(self, state: ShellState) -> None:
        self._config.shell_state = state
        self.save()

    def update_shell_quality_tiers(self, enabled_quality_tiers: frozenset[str]) -> None:
        """Update in-memory shell quality tiers without writing config to disk."""
        self._config.shell_state = replace(
            self._config.shell_state,
            enabled_quality_tiers=enabled_quality_tiers,
        )
