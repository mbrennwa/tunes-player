"""Remote file store protocol for label sync backends."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RemoteObject:
    data: bytes
    etag: str


class RemoteStore(Protocol):
    def get(self, path: str) -> RemoteObject | None:
        """Return object bytes and etag, or None if missing."""

    def put(self, path: str, data: bytes, *, if_match: str | None = None) -> str:
        """Write bytes. If ``if_match`` is set and does not match, raise ConflictError.

        Returns the new etag.
        """


class ConflictError(Exception):
    """Remote object changed since the caller’s etag."""
