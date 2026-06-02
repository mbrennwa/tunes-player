"""Qobuz streaming backend."""

from tunes_player.core.backends.qobuz.client import QobuzClient, QobuzUnavailableError

__all__ = ["QobuzClient", "QobuzUnavailableError"]
