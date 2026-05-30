"""Entry point for ``python -m tunes_player`` and the ``tunes-player`` script."""

from __future__ import annotations

import sys


def main() -> int:
    if sys.platform.startswith("linux"):
        from tunes_player.ui.gtk.app import run

        return run()

    print(
        "Tunes is not yet available on this platform.",
        file=sys.stderr,
    )
    print(
        "Linux (GNOME/GTK) is supported in early development.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
