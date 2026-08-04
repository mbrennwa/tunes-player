"""Filesystem RemoteStore for a user-chosen sync folder."""

from __future__ import annotations

import hashlib
from pathlib import Path

from tunes_player.core.labels_sync.store_protocol import ConflictError, RemoteObject


def _etag_for_path(path: Path) -> str:
    stat = path.stat()
    digest = hashlib.sha256()
    digest.update(str(stat.st_mtime_ns).encode())
    digest.update(b":")
    digest.update(str(stat.st_size).encode())
    return digest.hexdigest()[:32]


class FolderRemoteStore:
    """Read/write relative paths under a root directory."""

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root).expanduser().resolve()

    @property
    def root(self) -> Path:
        return self._root

    def _resolve(self, path: str) -> Path:
        relative = Path(path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"invalid remote path: {path!r}")
        return self._root / relative

    def get(self, path: str) -> RemoteObject | None:
        target = self._resolve(path)
        if not target.is_file():
            return None
        data = target.read_bytes()
        return RemoteObject(data=data, etag=_etag_for_path(target))

    def put(self, path: str, data: bytes, *, if_match: str | None = None) -> str:
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if if_match is not None:
            if not target.is_file():
                raise ConflictError("remote object missing for if_match put")
            current = _etag_for_path(target)
            if current != if_match:
                raise ConflictError("remote object etag mismatch")

        # Plain write — no fsync and no sibling *.tmp in the sync root.
        # OwnCloud/Nextcloud often stall hard on fsync, and partial temps get
        # picked up as extra sync objects.
        target.write_bytes(data)
        return _etag_for_path(target)
