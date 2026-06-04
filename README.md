# Tunes

**Tunes** is a shell to find, discover, and play music from local files and streaming
catalogs. It is free and open source, built for Linux (GNOME/GTK).


## Status

**Version 1.0.dev1** — public testing on Linux. Current functionality:

- **Local library** — scan configured folders into SQLite; play **FLAC, WAV, AIFF, ALAC, MP3, AAC, Ogg Vorbis**
- **Streaming** — **TIDAL** (OAuth, search, playback, New Releases, Suggest Music) and **Qobuz** (your App ID/Secret, account login, same discovery features)
- **Music shell** — unified **Release** grid from search, **New Releases**, or **Suggest Music**; filters for source, release type, and genre; sort row; release detail with hero artwork and queue sheet; **Back** restores prior shell context when drilling down from search
- **Federated search** — one search field over the local index plus signed-in catalogs
- **Playback** — mpv/libmpv queue, skip, seek; volume on the selected **output device** (PipeWire/Pulse sink or ALSA mixer) when available; optional software-volume fallback; playback format and path shown on Now Playing (including TIDAL stream quality)
- **Audio output** — ALSA hardware listed first for **sample-accurate bit-perfect** local listening (per-track rate/format, optional exclusive card access); PipeWire/Pulse sinks for normal desktop mixing (not bit-perfect — see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md))
- **Desktop** — MPRIS, media keys, in-app error toasts, Diagnostics log; experimental **`.deb`** with menu entry and icon on Debian/Ubuntu


### Not yet implemented

macOS/Windows ports, Deezer, playlists UI, cross-source deduplication, minimized compact controller, UPnP/AES67 output, inbound volume sync from hardware. Full roadmap and contributor handoff: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (rename notes: [docs/NAMING.md](docs/NAMING.md)).

## Design

Tunes is structured so **UI and playback engine stay replaceable** while one API drives the app:

- **`PlayerService`** (`core/services.py`) — playback, library, search, home feeds, config; no GTK or mpv imports in `core/`
- **Catalog model** — local files and streaming items share **`Release`** / **`Track`**; backends resolve a track to a **`PlayableSource`** URI for mpv
- **Playback** — headless **`MpvEngine`**; Linux **`VolumeController`** routes the slider to sink or ALSA mixer (unity gain in mpv when using device volume)
- **Bit-perfect (Linux v1)** — intentional scope: **direct ALSA `hw:` devices** only; default PipeWire sinks prioritize mixed desktop audio (Discord, notifications), not sample-accurate output

```
tunes_player/
├── core/
│   ├── services.py       # PlayerService facade
│   ├── home.py           # New Releases / Suggest Music aggregation
│   ├── shell_state.py    # Shell filters, sort, selection history
│   ├── backends/         # local, tidal/, qobuz/ → PlayableSource
│   ├── library/          # SQLite index, scanner, release grouping
│   └── playback/         # PlaybackEngine protocol, output_profile
├── engines/mpv.py        # libmpv
├── platform/linux/       # MPRIS, PipeWire/Pulse + ALSA volume, alsa_caps, exclusive session
└── ui/gtk/               # Libadwaita shell (grid, detail, now playing, preferences)
```

Future frontends (e.g. Qt on macOS) should reuse **`PlayerService`** unchanged.

## Requirements (Linux)

Runtime (typical Debian/Ubuntu packages):

- Python 3.11+
- PyGObject and typelibs: `python3-gi`, `python3-gi-cairo`, `gir1.2-gtk-4.0`, `gir1.2-adw-1`
- [mpv](https://mpv.io/) with libmpv (required for playback; missing libmpv shows an in-app message)
- **PipeWire** (or PulseAudio) on GNOME for sink listing and volume; **ALSA** for direct hardware output and bit-perfect path

Python dependencies (`pip install -e .`): `platformdirs`, `mutagen`, `python-mpv`, `tidalapi`.

PyGObject is provided by the system on GNOME; a normal venv cannot see it unless you use
`--system-site-packages` (recommended below) or install PyGObject into the venv yourself.

## Install from .deb (experimental)

On Debian 12+, Ubuntu 24.04+, or similar DEB-based distros, download the latest
`tunes-player_*.deb` from [GitHub Releases](https://github.com/mbrennwa/tunes-player/releases).

Then install with:

```bash
sudo apt install ./path/to/tunes-player_*.deb
```

`apt` installs declared package dependencies automatically (GTK, mpv, Python GI, and
related libraries).

After install, **Tunes** appears in the application menu (desktop entry and icon are included in the `.deb`).

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
