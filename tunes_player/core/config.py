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

APP_NAME = "tunes-player"


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
    bit_perfect: bool = True
    output_sink_id: str | None = None
    qobuz_app_id: str | None = None
    qobuz_app_secret: str | None = None
    qobuz_stream_format_id: int = 27
    new_music_within_days: int = NEW_MUSIC_LOCAL_WITHIN_DAYS_DEFAULT


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
        app_id = raw.get("qobuz_app_id")
        app_secret = raw.get("qobuz_app_secret")
        self._config = AppConfig(
            music_folders=folders,
            music_folder_added_at=added_at,
            bit_perfect=bool(raw.get("bit_perfect", True)),
            output_sink_id=raw.get("output_sink_id") or None,
            qobuz_app_id=str(app_id).strip() if app_id else None,
            qobuz_app_secret=str(app_secret).strip() if app_secret else None,
            qobuz_stream_format_id=format_id,
            new_music_within_days=normalize_new_music_within_days(
                raw.get("new_music_within_days", NEW_MUSIC_LOCAL_WITHIN_DAYS_DEFAULT),
            ),
        )
        return self._config

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "music_folders": list(self._config.music_folders),
            "music_folder_added_at": dict(self._config.music_folder_added_at),
            "bit_perfect": self._config.bit_perfect,
            "output_sink_id": self._config.output_sink_id,
            "qobuz_app_id": self._config.qobuz_app_id,
            "qobuz_app_secret": self._config.qobuz_app_secret,
            "qobuz_stream_format_id": self._config.qobuz_stream_format_id,
            "new_music_within_days": self._config.new_music_within_days,
        }
        self._path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    @property
    def config(self) -> AppConfig:
        return self._config

    def add_music_folder(self, folder: str) -> None:
        path = str(Path(folder).expanduser().resolve())
        if path not in self._config.music_folders:
            self._config.music_folders.append(path)
        self._config.music_folder_added_at[path] = time.time()
        self.save()

    def remove_music_folder(self, folder: str) -> None:
        path = str(Path(folder).expanduser().resolve())
        self._config.music_folders = [item for item in self._config.music_folders if item != path]
        self._config.music_folder_added_at.pop(path, None)
        self.save()

    def set_new_music_within_days(self, days: int) -> None:
        self._config.new_music_within_days = normalize_new_music_within_days(days)
        self.save()
