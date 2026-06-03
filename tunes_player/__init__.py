"""Tunes — cross-platform music player (Linux/GTK first)."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("tunes-player")
except PackageNotFoundError:
    __version__ = "0.0.0"
