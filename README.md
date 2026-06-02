# Tunes

**Tunes** is a free, open-source music player for Linux (GNOME/GTK first), with planned
support for macOS and Windows. It plays local audio files and streaming catalogs from
**TIDAL** and **Qobuz** (Deezer planned) — presented as one searchable library with
shared browse, playback, and discovery views.

Package and command name: **tunes-player**.

## Status

Early development (v0.1). Working today on Linux:

- **Local library** — scan music folders into SQLite; browse, search, and play **FLAC, WAV, AIFF, ALAC, MP3, AAC, Ogg Vorbis**
- **Streaming** — **TIDAL** (OAuth sign-in, search, playback, New Music, Suggestions) and **Qobuz** (user-supplied App ID/Secret, account login, search, playback, New Music, Suggestions)
- **Unified browse** — `Release` model across local and streaming; album grid, release detail, play queue
- **Discovery** — **New Music** (recent local imports + service new-release rails) and **Suggestions** (continue listening, editorial picks, similar tracks, local rediscover)
- **Federated search** — one search box merges local index with signed-in streaming catalogs
- **Playback** — mpv/libmpv queue, skip, seek; bit-perfect profile when device volume is available; PipeWire/Pulse sink volume via `wpctl`/`pactl`
- **Desktop integration** — MPRIS, GDK media keys, playback error toasts, file logging
- **Settings** — music folders, TIDAL/Qobuz accounts, output device, bit-perfect toggle, New Music cutoff

Still planned or incomplete: Deezer, cross-source deduplication, playlists, minimized
compact controller, UPnP/AES67 output, DEB packaging, macOS/Windows UI. See
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for roadmap and open items.

## Architecture

```
tunes_player/
├── core/           # Models, library, backends, playback logic — no GTK, no mpv
│   ├── services.py # PlayerService facade for all UIs
│   ├── home.py     # New Music / Suggestions types and merge limits
│   ├── backends/   # local, tidal/, qobuz/ → PlayableSource
│   ├── library/    # SQLite index, scanner, release grouping
│   └── playback/   # PlaybackEngine protocol
├── engines/        # MpvEngine (libmpv)
├── platform/       # Linux: MPRIS, PipeWire/Pulse volume
└── ui/gtk/         # Libadwaita UI
```

The **core** is UI- and engine-agnostic so other frontends (e.g. Qt on macOS) can share
the same `PlayerService` API.

**Architecture, roadmap, and TODO (handoff for contributors/agents):**
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) —
possible rename candidates: [docs/NAMING.md](docs/NAMING.md)

## Requirements (Linux)

Runtime (typical Debian/Ubuntu packages):

- Python 3.11+
- PyGObject and typelibs: `python3-gi`, `python3-gi-cairo`, `gir1.2-gtk-4.0`, `gir1.2-adw-1`
- [mpv](https://mpv.io/) with libmpv (required for playback; missing libmpv shows an in-app message)

Python dependencies (installed with `pip install -e .`): `platformdirs`, `mutagen`,
`python-mpv`, `tidalapi`.

PyGObject is provided by the system on GNOME; a normal venv cannot see it unless you use
`--system-site-packages` (recommended below) or install PyGObject into the venv yourself.

## Install from source

System dependencies (Debian/Ubuntu):

```bash
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 mpv libmpv2
```

If playback fails, check the log at `~/.local/share/tunes-player/tunes-player.log` or
Settings → Diagnostics. Set `TUNES_LOG_LEVEL=DEBUG` for verbose logging.

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
