"""TIDAL streaming backend."""

from tunes_player.core.backends.tidal.client import TidalClient, TidalUnavailableError

__all__ = ["TidalClient", "TidalUnavailableError"]
