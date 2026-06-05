"""Application configuration persisted on disk."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir

from tunes_player.core.home import (
    NEW_MUSIC_LOCAL_WITHIN_DAYS_DEFAULT,
    NEW_MUSIC_LOCAL_WITHIN_DAYS_MAX,
    NEW_MUSIC_LOCAL_WITHIN_DAYS_MIN,
)
from tunes_player.core.shell_state import ShellState, parse_shell_state

APP_NAME = "tunes-player"


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
    output_sink_id: str | None = None
    allow_software_volume_fallback: bool = True
    exclusive_device_access: bool = False
    qobuz_app_id: str | None = None
    qobuz_app_secret: str | None = None
    qobuz_stream_format_id: int = 27
    new_music_within_days: int = NEW_MUSIC_LOCAL_WITHIN_DAYS_DEFAULT
    shell_state: ShellState = field(default_factory=ShellState)


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
    def database_path(self) -> Path:
        return self.data_dir / "library.db"

    def load(self) -> AppConfig:
        if not self._path.is_file():
            self._config = AppConfig()
            return self._config

        raw = json.loads(self._path.read_text(encoding="utf-8"))
        folders = [str(item) for item in raw.get("music_folders", []) if item]
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
        app_id = raw.get("qobuz_app_id")
        app_secret = raw.get("qobuz_app_secret")
        self._config = AppConfig(
            music_folders=folders,
            music_folder_added_at=added_at,
            music_folder_auto_monitor=auto_monitor,
            music_folder_last_scan_at=last_scan_at,
            music_folder_last_scan_errors=last_scan_errors,
            output_sink_id=raw.get("output_sink_id") or None,
            allow_software_volume_fallback=bool(
                raw.get("allow_software_volume_fallback", True)
            ),
            exclusive_device_access=bool(raw.get("exclusive_device_access", False)),
            qobuz_app_id=str(app_id).strip() if app_id else None,
            qobuz_app_secret=str(app_secret).strip() if app_secret else None,
            qobuz_stream_format_id=format_id,
            new_music_within_days=normalize_new_music_within_days(
                raw.get("new_music_within_days", NEW_MUSIC_LOCAL_WITHIN_DAYS_DEFAULT),
            ),
            shell_state=parse_shell_state(raw.get("shell_state")),
        )
        return self._config

    def save(self) -> None:
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
            "output_sink_id": self._config.output_sink_id,
            "allow_software_volume_fallback": self._config.allow_software_volume_fallback,
            "exclusive_device_access": self._config.exclusive_device_access,
            "qobuz_app_id": self._config.qobuz_app_id,
            "qobuz_app_secret": self._config.qobuz_app_secret,
            "qobuz_stream_format_id": self._config.qobuz_stream_format_id,
            "new_music_within_days": self._config.new_music_within_days,
            "shell_state": self._config.shell_state.to_dict(),
        }
        self._path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    @property
    def config(self) -> AppConfig:
        return self._config

    def add_music_folder(self, folder: str, *, auto_monitor: bool = False) -> None:
        path = str(Path(folder).expanduser().resolve())
        if path not in self._config.music_folders:
            self._config.music_folders.append(path)
        self._config.music_folder_added_at[path] = time.time()
        self._config.music_folder_auto_monitor[path] = auto_monitor
        self.save()

    def remove_music_folder(self, folder: str) -> None:
        path = str(Path(folder).expanduser().resolve())
        self._config.music_folders = [item for item in self._config.music_folders if item != path]
        self._config.music_folder_added_at.pop(path, None)
        self._config.music_folder_auto_monitor.pop(path, None)
        self._config.music_folder_last_scan_at.pop(path, None)
        self._config.music_folder_last_scan_errors.pop(path, None)
        self.save()

    def record_folder_scan(self, folder: str, *, errors: int, scanned_at: float | None = None) -> None:
        path = str(Path(folder).expanduser().resolve())
        if path not in self._config.music_folders:
            return
        self._config.music_folder_last_scan_at[path] = scanned_at if scanned_at is not None else time.time()
        self._config.music_folder_last_scan_errors[path] = int(errors)
        self.save()

    def folder_last_scan_at(self, folder: str) -> float | None:
        path = str(Path(folder).expanduser().resolve())
        return self._config.music_folder_last_scan_at.get(path)

    def folder_last_scan_errors(self, folder: str) -> int | None:
        path = str(Path(folder).expanduser().resolve())
        return self._config.music_folder_last_scan_errors.get(path)

    def set_folder_auto_monitor(self, folder: str, enabled: bool) -> None:
        path = str(Path(folder).expanduser().resolve())
        if path not in self._config.music_folders:
            return
        self._config.music_folder_auto_monitor[path] = enabled
        self.save()

    def folder_auto_monitor_enabled(self, folder: str) -> bool:
        path = str(Path(folder).expanduser().resolve())
        return bool(self._config.music_folder_auto_monitor.get(path, False))

    def set_new_music_within_days(self, days: int) -> None:
        self._config.new_music_within_days = normalize_new_music_within_days(days)
        self.save()

    def set_shell_state(self, state: ShellState) -> None:
        self._config.shell_state = state
        self.save()
