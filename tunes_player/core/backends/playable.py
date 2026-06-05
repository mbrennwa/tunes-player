"""Resolved playback target for any source."""

from __future__ import annotations

from dataclasses import dataclass

from tunes_player.core.library.store import FileMetadata
from tunes_player.core.models import Track


@dataclass(frozen=True, slots=True)
class PlayableSource:
    """URI or path mpv can open, plus metadata for UI and MPRIS."""

    uri: str
    metadata: Track
    start_sec: float = 0
    format_label: str | None = None
    stream_metadata: FileMetadata | None = None

    @property
    def playback_target(self) -> str:
        """Argument for the playback engine (filesystem path or https URL)."""
        if self.uri.startswith("file://"):
            from urllib.parse import unquote, urlparse

            parsed = urlparse(self.uri)
            return unquote(parsed.path)
        return self.uri
