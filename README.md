# Tunes

**Tunes** is a free, open-source music player for Linux (GNOME/GTK). It is a shell to
access, discover, and play music from local files and streaming catalogs (**TIDAL**,
**Qobuz**; others planned).

Package and command name: **tunes-player**.

## Status

Early development (1.0.dev0). Current functionality:

- **Local library** — scan music folders into SQLite; browse, search, and play **FLAC, WAV, AIFF, ALAC, MP3, AAC, Ogg Vorbis**
- **Streaming** — **TIDAL** (OAuth sign-in, search, playback, New Releases, Suggest Music) and **Qobuz** (user-supplied App ID/Secret, account login, search, playback, New Releases, Suggest Music)
- **Music shell** — `Release` grid driven by search, **New Releases**, or **Suggest Music** selections (optional per-source filter); release detail and play queue
- **Discovery** — **New Releases** (recent local imports + service new-release rails) and **Suggest Music** (continue listening, editorial picks, similar tracks, local rediscover)
- **Federated search** — search field merges local index with signed-in streaming catalogs
- **Playback** — mpv/libmpv queue, skip, seek; unity-gain when using device/sink volume (no extra mpv DSP); **sample-accurate bit-perfect** on **direct ALSA hardware** in Settings; PipeWire/Pulse sinks for normal desktop mixing; optional software-volume fallback; sink volume via `wpctl`/`pactl`
- **Desktop integration** — MPRIS, GDK media keys, playback error toasts, file logging
- **Settings** — music folders, TIDAL/Qobuz accounts, output device (with bit-perfect potential labels), software-volume fallback, New Releases cutoff

Still planned or incomplete: macOS and Windows, Deezer, cross-source deduplication,
playlists, minimized compact controller, UPnP/AES67 output, desktop .deb menu integration.
See
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for roadmap and open items.

## Architecture

```
tunes_player/
├── core/           # Models, library, backends, playback logic — no GTK, no mpv
│   ├── services.py # PlayerService facade for all UIs
│   ├── home.py     # New Releases / Suggestion types and merge limits
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

## Install from .deb (experimental)

On Debian 12+, Ubuntu 24.04+, or similar DEB-based distros, download the latest `.deb`
from [GitHub Releases](https://github.com/mbrennwa/tunes-player/releases).

Then install with:

```bash
sudo apt install ./path/to/tunes-player_*.deb
tunes-player
```

`apt` installs declared package dependencies automatically (GTK, mpv, Python GI, and
related libraries).

After install, **Tunes** appears in the application menu (desktop entry and icon are
included in the `.deb`).

Maintainers: new release builds are published when a `v*` tag is pushed (see
[docs/RELEASE.md](docs/RELEASE.md) and [.github/workflows/release-deb.yml](.github/workflows/release-deb.yml)).
To build a package locally, see [tools/howto-build-deb.txt](tools/howto-build-deb.txt).

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

Optional: install the launcher icon and `.desktop` entry for local development:

```bash
make icons
sudo make install
```

## Streaming disclaimer

Tunes is **not affiliated with Tidal, Qobuz, or any other streaming provider**.
Streaming requires your own paid subscriptions where applicable. Integrations may use official developer APIs (e.g. Tidal, Deezer) or user-supplied credentials (Qobuz App ID and App Secret — Tunes does not ship or distribute Qobuz keys). For Qobuz, open
**Settings → Sources**, enter and save your App ID and App Secret, then sign in with your Qobuz account email and password. Features can break when providers change authentication
or terms. Use at your own responsibility and in compliance with each service's terms of use.

## License

GPL-3.0-or-later — see [LICENSE](LICENSE).
