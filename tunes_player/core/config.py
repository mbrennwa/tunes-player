"""Application configuration persisted on disk."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir

APP_NAME = "tunes-player"


@dataclass
class AppConfig:
    music_folders: list[str] = field(default_factory=list)
    bit_perfect: bool = True
    output_sink_id: str | None = None


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
        folders = raw.get("music_folders", [])
        self._config = AppConfig(
            music_folders=[str(item) for item in folders if item],
            bit_perfect=bool(raw.get("bit_perfect", True)),
            output_sink_id=raw.get("output_sink_id") or None,
        )
        return self._config

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "music_folders": list(self._config.music_folders),
            "bit_perfect": self._config.bit_perfect,
            "output_sink_id": self._config.output_sink_id,
        }
        self._path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    @property
    def config(self) -> AppConfig:
        return self._config

    def add_music_folder(self, folder: str) -> None:
        path = str(Path(folder).expanduser().resolve())
        if path not in self._config.music_folders:
            self._config.music_folders.append(path)
            self.save()

    def remove_music_folder(self, folder: str) -> None:
        path = str(Path(folder).expanduser().resolve())
        self._config.music_folders = [item for item in self._config.music_folders if item != path]
        self.save()
