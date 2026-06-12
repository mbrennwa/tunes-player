"""Prepare PyGObject imports.

PyGObject 3.56+ warns about ``GLib.unix_signal_add_full`` while loading GLib
overrides, even when application code never calls it (pygobject#757). Filter
that import-time noise before the first ``gi.repository`` import.
"""

from __future__ import annotations

import warnings

try:
    from gi import PyGIDeprecationWarning
except ImportError:  # pragma: no cover - non-Linux / missing gi
    PyGIDeprecationWarning = DeprecationWarning  # type: ignore[misc, assignment]

warnings.filterwarnings(
    "ignore",
    message=(
        "GLib.unix_signal_add_full is deprecated; "
        "use GLibUnix.signal_add_full instead"
    ),
    category=PyGIDeprecationWarning,
)
