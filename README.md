# Tunes

**Tunes** is a free, open-source music player for Linux (GNOME/GTK first), with planned
support for macOS and Windows. It plays local audio files and, in the future, streaming
catalogs from Tidal, Deezer, and Qobuz (and similar services) — presented as one
searchable library. **Tidal** is the planned first streaming integration; see
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#streaming).

Package and command name: **tunes-player**.

## Status

Early development. v0.1 targets:

- Local music library (scan, browse, search, play) — **FLAC, WAV, AIFF, ALAC, MP3, AAC, Ogg Vorbis**
- Simple Libadwaita GUI on Linux
- Bit-perfect local playback; volume on output device/sink (not in-app soft gain only)
- DEB packaging for Debian/Ubuntu

**Planned output (see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#output-endpoints-planned--not-implemented-yet)):**
local PipeWire/ALSA first, then UPnP network renderers; optional later AES67/Dante for pro gear.

Streaming integration and unified cross-source search are on the roadmap.

## Architecture

```
tunes_player/
├── core/       # Models, library, playback, catalog — no GTK, no mpv
├── engines/    # PlaybackEngine (mpv) — planned
├── platform/   # MPRIS, OS-specific glue — planned
└── ui/gtk/     # Libadwaita UI (Linux)
```

The **core** is UI- and engine-agnostic so other frontends (e.g. Qt on macOS) and a
shared **mpv** backend can be added without rewriting library or playback logic.

**Architecture, roadmap, and TODO (handoff for contributors/agents):**
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) —
possible rename candidates: [docs/NAMING.md](docs/NAMING.md)

## Requirements (Linux)

Runtime (typical Debian/Ubuntu packages):

- Python 3.11+
- PyGObject and typelibs: `python3-gi`, `python3-gi-cairo`, `gir1.2-gtk-4.0`, `gir1.2-adw-1`
- [mpv](https://mpv.io/) with libmpv (required for playback; missing libmpv shows an in-app message)

PyGObject is provided by the system on GNOME; a normal venv cannot see it unless you use
`--system-site-packages` (recommended below) or install PyGObject into the venv yourself.

## Install from source

System dependencies (Debian/Ubuntu):

```bash
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 mpv libmpv2
```

If playback fails, check the log at `~/.local/share/tunes-player/tunes-player.log` or
Settings → Library → Diagnostics. Set `TUNES_LOG_LEVEL=DEBUG` for verbose logging.

Clone and install into a venv that can use system `gi`:

```bash
git clone https://github.com/mbrennwa/tunes-player.git
cd tunes-player
python3 -m venv .venv --system-site-packages
source .venv/bin/activate
pip install -e .
tunes-player
```

## Streaming disclaimer

Tunes is **not affiliated with Tidal, Deezer, Qobuz, or any other streaming provider**.
Streaming requires your own paid subscriptions where applicable. Integrations may use
official developer APIs (e.g. Tidal, Deezer) or user-supplied credentials (Qobuz App ID
and App Secret — Tunes does not ship or distribute Qobuz keys). For Qobuz, open
**Settings → Sources**, enter and save your App ID and App Secret, then sign in with your
Qobuz account email and password. Features can break when providers change authentication
or terms. Use at your own responsibility and in compliance with each service's terms of use.

## License

GPL-3.0-or-later — see [LICENSE](LICENSE). Rationale (mpv, mutagen, streaming deps):
[docs/ARCHITECTURE.md#license-rationale](docs/ARCHITECTURE.md#license-rationale).
