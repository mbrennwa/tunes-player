"""Entry point for ``python -m tunes_player`` and the ``tunes-player`` script."""

from __future__ import annotations

import sys

_GTK_SETUP_HINT = """
PyGObject (gi) is not available in this Python environment.

On Debian/Ubuntu, install GTK bindings and typelibs, then recreate the venv with
access to system packages:

  sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1
  deactivate
  rm -rf .venv
  python3 -m venv .venv --system-site-packages
  source .venv/bin/activate
  pip install -e .
  tunes-player
"""


def main() -> int:
    if sys.platform.startswith("linux"):
        try:
            from tunes_player.ui.gtk.app import run
        except ModuleNotFoundError as exc:
            if exc.name in ("gi", "gi.repository"):
                print(_GTK_SETUP_HINT.strip(), file=sys.stderr)
                return 1
            raise
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
