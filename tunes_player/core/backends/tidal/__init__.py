"""TIDAL streaming backend."""

from tunes_player.core.backends.tidal.client import TidalClient, TidalUnavailableError, tidalapi_available

__all__ = ["TidalClient", "TidalUnavailableError", "tidalapi_available"]
