# Tunes — architecture, roadmap, and TODO

This document is the single place for design decisions, ordered milestones, and open
work items (for contributors and automated agents). The README stays short; details
live here.

## Product

| Item | Choice |
|------|--------|
| Display name | **Tunes** (rename candidates: [docs/NAMING.md](NAMING.md)) |
| Package / CLI / icon name | **tunes-player** |
| GTK / D-Bus app ID | `tunes.player` |
| License | **GPL-3.0-or-later** (see [License rationale](#license-rationale)) |
| Distribution | Public FOSS; **DEB** for Debian/Ubuntu first (not Flatpak-first) |
| Initial platform | **Linux / GNOME / GTK 4 + Libadwaita** |
| Later platforms | macOS / Windows via separate UI (e.g. Qt), shared **core** |

### Goals

- Play **local** audio files — Tier 1: FLAC, WAV, AIFF, ALAC, MP3, AAC, Ogg Vorbis.
- **Bit-perfect** output for audiophile use when enabled (no unnecessary DSP).
- **Hardware / endpoint volume** — adjust the sound device or sink, not only in-app
  soft gain.
- **Media keys** — play/pause, skip, volume, mute from keyboard and OS (see
  [Media keys](#media-keys-requirement)).
- Integrate **streaming** (Tidal, Deezer, Qobuz) via provider-specific APIs
  (official developer paths where available; see [Streaming](#streaming)).
  **TIDAL** and **Qobuz** are implemented; **Deezer** is not started.
- Present sources as **one searchable library** (see [Unified catalog](#unified-catalog)).
- **Simple** Libadwaita GUI; native GNOME look (not Qt on Linux).

### Out of scope for v0.1

- **Deezer** streaming backend.
- Unified cross-source deduplication (beyond basic federated search).
- **Playlists** (create, edit, browse) — see [Main window layout](#main-window-layout-minimal).
- macOS / Windows UI.
- Flatpak (may come later).
- Local formats beyond [Tier 1](#local-audio-formats-v01) (e.g. Opus, WavPack, APE, DSD, cue sheets).

### Local audio formats (v0.1)

**Tier 1 — supported out of the box** for scan, metadata, and playback:

| Format | Extensions | Notes |
|--------|------------|--------|
| FLAC | `.flac` | Primary audiophile lossless |
| WAV | `.wav` | Uncompressed PCM |
| AIFF | `.aiff`, `.aif` | Uncompressed |
| ALAC | `.m4a` | Lossless; distinguish from AAC by probe |
| MP3 | `.mp3` | Lossy |
| AAC | `.m4a`, `.aac` | Lossy |
| Vorbis | `.ogg` | Lossy |

**Implementation:**

- **Playback:** mpv.
- **Tags / album art:** mutagen (primary); probe codec for `.m4a` (ALAC vs AAC).
- **Scanner:** index Tier 1 extensions; verify readable before adding to library.

Bit-perfect mode targets **lossless Tier 1** (FLAC, WAV, AIFF, ALAC). Lossy formats
use the same no-extra-DSP path but are not “hi-res” sources.

Other formats may play via mpv later without full library indexing until explicitly added.

### Naming

**Music** was avoided: GNOME ships **Music** (`gnome-music`). **Tunes** is distinct;
`iTunes` echo is acceptable for a FOSS niche player for now.

Possible renames (e.g. **Panmelos**, **Allmusic**, **Holoplay**, **Sourcebox**, and
others) with rationale and collisions are in **[docs/NAMING.md](NAMING.md)**.

---

## Implementation status (current)

| Area | Status |
|------|--------|
| Local scan + SQLite index | Done (`core/library/`, multiprocessing scan worker) |
| `Release` / `Track` models | Done — unified release across local + streaming |
| `PlayableSource` + `resolve_track` | Done (`core/backends/`) |
| `PlaybackEngine` + `MpvEngine` | Done — queue, skip, seek, events |
| Device volume + bit-perfect profile | Partial — PipeWire/Pulse via `wpctl`/`pactl`; exclusive ALSA not done |
| MPRIS + GDK media keys | Done |
| GTK shell (Browse, New Music, Suggestions, search, queue sheet) | Done |
| Settings (Sources, Audio, Application, Diagnostics) | Done |
| TIDAL (OAuth, search, playback, New Music, Suggestions) | Done |
| Qobuz (credentials, login, search, playback, New Music, Suggestions) | Done |
| Federated search (phase A) | Done in `PlayerService.search()` — no separate `catalog/` module yet |
| New Music + Suggestions aggregation | Done in `core/home.py` + `PlayerService` |
| Deezer | Not started |
| Minimized compact controller | Not started |
| UPnP / AES67 output | Not started |
| DEB packaging | Not started |

---

## Repository layout

```
tunes_player/
├── core/              # No GTK, no mpv — models, services, library, playback logic
│   ├── models.py      # Release, Track, Artist, Source
│   ├── services.py    # PlayerService facade for all UIs
│   ├── config.py      # ConfigManager, AppConfig (platformdirs)
│   ├── home.py        # New Music / Suggestions item types and merge limits
│   ├── volume.py      # VolumeController protocol
│   ├── backends/
│   │   ├── playable.py, resolve.py, local.py
│   │   ├── tidal/     # client (tidalapi), convert, ids
│   │   └── qobuz/     # in-tree REST client, convert, ids
│   ├── library/       # db, store, scanner, scan_worker, release_logic, art_cache
│   └── playback/
│       └── engine.py  # PlaybackEngine protocol
├── engines/
│   └── mpv.py         # MpvEngine (libmpv, headless)
├── platform/
│   └── linux/         # MPRIS, PipeWire/Pulse volume (audio.py)
└── ui/
    └── gtk/           # app, views, preferences, now_playing, album_grid, …
```

Federated search and home aggregation live in **`PlayerService`** and **`core/home.py`**
today; a dedicated `core/catalog/` module may appear if merge/dedup logic grows.

**Rule:** `core/` must not import `gi`, `PySide6`, or `mpv`. UI must not call mpv
directly.

Future: `ui/qt/` for macOS/Windows; same `PlayerService` API.

---

## GUI separation

- **UI** (`ui/gtk/`): windows, lists, transport bar, settings; subscribes to events.
- **PlayerService** (`core/services.py`): stable API — `play`, `pause`, `search`,
  `subscribe(events)`.
- **No GTK types in core models** — use `art_uri: str`, opaque IDs like
  `local:…`, `tidal:…`, `qobuz:…` (Deezer reserved: `deezer:…`).
- **Release** is the primary browse/playback unit (album, EP, single, partial/synthetic
  local groups); `Album` is a backward-compatible alias in `core/models.py`.

GTK runs on the main loop; mpv callbacks post to a queue → GLib idle (same pattern
will work for Qt signals later).

**Errors and logging:**

- User-facing playback failures set `PlayerService.last_error()` and emit
  `playback_error`; the GTK shell shows an `Adw.Toast` (see `ui/gtk/errors.py`).
- Diagnostics use the stdlib `logging` package (`tunes_player.core.logging_config`),
  configured at app startup. Log file: `{user_data_dir}/tunes-player.log` (typically
  `~/.local/share/tunes-player/tunes-player.log`). Override verbosity with
  `TUNES_LOG_LEVEL=DEBUG`.
- Optional startup probe (`engines/mpv.probe_playback_engine`) warns when libmpv is
  missing before the user presses Play.

**Why GTK on Linux:** native Libadwaita on GNOME. Qt was rejected for Linux primary UI
(native-widget concerns on GNOME). Qt remains the likely choice for a later macOS UI.

**Dev setup:** PyGObject comes from the system (`python3-gi`). Use:

`python3 -m venv .venv --system-site-packages`

### Minimized player (compact controller) — not implemented

The main window should support a **minimized** layout so users can keep Tunes open
without dedicating screen space to browsing and artwork. **This is not built yet**;
only the full expanded shell exists today.

| Mode | What the user sees |
|------|---------------------|
| **Expanded** (default) | Full library UI — lists, search, artwork, transport bar, volume, etc. |
| **Minimized** | A small **compact controller** only: **play/pause**, **previous**, **next** (skip). |

**Behavior:**

- Minimize/restore is a **UI layout toggle** on the main window (e.g. header-bar
  control or keyboard shortcut). It is not “quit” — playback and queue continue
  unchanged via **PlayerService**.
- The compact controller stays **always on top of the music session**: same
  `PlayerService` API (`play`, `pause`, `skip_next`, `skip_previous`); no duplicate
  playback state in the widget.
- Optional later: show **current track title** (single line, ellipsized) or album art
  thumbnail in minimized mode — not required for v0.1.
- Window geometry: minimized mode uses a **small fixed or minimum size** (roughly
  transport-bar height × narrow width); restoring expanded mode returns the previous
  size and layout.
- **MPRIS** and background playback behave the same in both modes; minimized mode is
  for users who want a tiny on-screen remote while working elsewhere.

**Implementation notes (when UI lands):**

- Keep expanded/minimized state in `ui/gtk/` (e.g. `Adw.Application` or window
  settings); persist preference in config if users expect it across sessions.
- Same transport controls as the full **transport bar** — one component, two layouts
  (reuse buttons/signals rather than a second control path to core).

### Main window layout (minimal)

Reference players: **GNOME Music** (Libadwaita split view + bottom bar), **iTunes /
Apple Music** (sidebar library + album detail), **Roon** (persistent Now Playing bar,
queue overlay — not multi-zone or DSP UI). Tunes stays **library-first**, **GNOME-native**,
and simpler than Roon.

**Structure:** one window, three zones — *browse in the middle, control at the bottom*.

```text
┌ Header: back · search · (title) ─────────────────────────────┐
├ Sidebar ──┬ Main pane (navigation stacks) ────────────────────┤
│ Browse    │  Local release grid · release detail (art + tracks)│
│ New Music │  Merged recently-added grid (local + streaming)    │
│ Suggestions│ Merged suggestion grid (continue, editorial, etc.) │
│ Settings… │  Search toggled from header → federated results   │
│           │  Queue opens as sheet from Now Playing bar         │
├───────────┴───────────────────────────────────────────────────┤
│ Now Playing bar: art · title · transport · volume · queue   │
└───────────────────────────────────────────────────────────────┘
```

**Sidebar (current):** **Browse** (local indexed releases), **New Music**, **Suggestions**,
and **Settings…** at the bottom. Fixed-width sidebar via `AdwNavigationSplitView`
(`ui/gtk/app.py`). There is no separate Artists browse section.

**Main pane:** `AdwNavigationView` per section — release grid → release detail; header
search toggles a federated results view (local + signed-in TIDAL/Qobuz).

**Now Playing bar:** always visible in expanded mode; shared widget with [minimized
mode](#minimized-player-compact-controller). Optional subtitle for audiophile context
(format, sample rate, bit-perfect / device-volume indicator). Click bar (not buttons)
may open a larger Now Playing sheet later — not required for v0.1.

**Playlists — later, low priority:** not in v0.1 sidebar. Primary use case is album/
artist browsing and the **play queue**; playlists are deferred for users who want them,
not a driver for early UI work. When added: sidebar entry, CRUD in core, same main-pane
patterns as albums. Unified cross-source playlists remain [catalog phase D](#unified-catalog).

**Deferred after v0.1 UI skeleton:** folder browse (Roon-style), full-screen Now Playing,
lyrics, streaming source badges in browse views.

### Settings (preferences window)

`AdwPreferencesWindow` with one page per concern (`ui/gtk/preferences.py`):

| Page | Contents |
|------|----------|
| **Application** | New Music cutoff (days) — local and Qobuz featured releases |
| **Sources** | **Local files:** music folders, scan library. **Streaming:** TIDAL sign-in/out (OAuth via browser); Qobuz App ID/Secret, save credentials, email/password sign-in/out |
| **Audio** | Bit-perfect toggle (subtitle reflects device vs software volume), output device dropdown (PipeWire/Pulse sinks) |
| **Diagnostics** | Log file path (copy button) |

**Unifying principle:** Local files and streaming services are both **sources** of music.
The **Sources** page groups local folders and streaming accounts; credentials and session
files live in **core/backends/** and `platformdirs` config/data dirs. Settings UI calls
**PlayerService** only — never streaming APIs from GTK.

**Qobuz:** user-supplied App ID and App Secret (required before sign-in); see
[Qobuz credentials](#qobuz-credentials). Tunes does **not** ship or auto-scrape Qobuz keys.

**Where streaming appears outside Settings:**

- **Browse:** local indexed releases only.
- **New Music / Suggestions:** merged grids from local + signed-in services.
- **Search:** federated release results (local first, then streaming append).
- **Release detail / playback:** same views for `local:…`, `tidal:…`, `qobuz:…` IDs.
- **Now Playing:** quality hint (e.g. FLAC metadata, “TIDAL”, “QOBUZ”); source badge deferred.
- **Playlists:** not implemented — catalog phase D.

---

## Sound / playback separation

Same layering as GUI:

| Layer | Responsibility |
|-------|----------------|
| `core/backends/` | Resolve `Track` → `PlayableSource` (file path or HTTPS URL) |
| `PlayerService` | Queue, play/pause/skip/seek, volume, federated search, home feeds |
| `engines/mpv.py` | `PlaybackEngine` — load URI, play/pause/seek, emit position |
| `platform/linux/audio.py` | Output device list, **endpoint volume**, mpv audio-device mapping |
| `platform/linux/mpris.py` | D-Bus controls from **PlayerService**, not from GTK or mpv |
| `ui/gtk/` | Displays state; calls `PlayerService` only |

### PlayableSource

Implemented in `core/backends/playable.py`:

```python
@dataclass
class PlayableSource:
    uri: str              # file:///… or https://…
    metadata: Track
    start_sec: float = 0
```

`resolve_track()` in `core/backends/resolve.py` dispatches by ID prefix (`local:`,
`tidal:`, `qobuz:`) to the matching backend. Stream URLs are resolved at **play time**
(they expire). Local backend uses `file://`; streaming backends use service APIs.

### mpv

- Default and preferred engine on **all** OSes (cross-platform).
- Music-only: run **headless** (no video surface) — avoids embedding mpv in GTK/Qt.
- **python-mpv** / libmpv stays inside `engines/`, not in UI.

### Bit-perfect playback (requirement)

Audiophile use is a **product requirement**, not an optional extra.

**Goal:** when bit-perfect mode is on, audio reaches the DAC in the **original format**
(sample rate, bit depth, channel layout) without Tunes applying sample manipulation
(volume gain, resampling, ReplayGain, EQ, etc.).

**Engine (mpv) — typical constraints when bit-perfect is enabled:**

- Keep mpv **volume at 100%**; do not use mpv soft volume for listening level.
- Disable **ReplayGain** and other DSP that modifies samples.
- Avoid **resampling** (configure AO/backend so output matches source rate where
  possible).
- Prefer a direct path to the chosen device (ALSA device, PipeWire node, etc.).
- On Windows/macOS later: exclusive / hog mode where available (WASAPI exclusive,
  CoreAudio hog).

**Caveats to document in UI:**

- **Bit-perfect + software volume are incompatible** — lowering level inside the
  player changes samples. Volume must move to the **device or sink**.
- **Streaming** (Tidal/Deezer/Qobuz) may already be lossy or transcoded; “bit-perfect” means
  **no additional processing in Tunes**, not that the stream is hi-res MQA/bitstream.
- **Local FLAC/WAV** is the primary bit-perfect use case for v1.

Implement bit-perfect as an explicit **settings profile** applied when constructing
`MpvEngine` (see `platform/*/audio.py`), not scattered mpv flags in UI code.

### Volume control (requirement)

Volume must **not** rely only on in-app soft gain wired into the player.

**Goal:** the UI slider (and MPRIS volume, media keys) adjusts **endpoint volume**
— the selected **output sink**, **ALSA mixer control**, or **DAC hardware volume**
 exposed by the stack — so listening level changes without Tunes modifying PCM when
 bit-perfect mode is active.

**Architecture:**

```text
VolumeController (protocol, core or platform)
  ├── get_level() / set_level(0.0–1.0)
  ├── subscribe(level_changed)
  └── list_controllable_endpoints()   # optional: pick “DAC PCM” vs “PulseAudio sink”

Linux implementations (platform/linux/audio.py):
  ├── PipeWire / WirePlumber (preferred on modern GNOME)
  ├── PulseAudio sink volume (fallback)
  └── ALSA mixer (direct card control, audiophile setups)
```

- **PlayerService.set_volume()** → **VolumeController**, not `mpv.volume` (except
  a separate “software volume” fallback when no hardware control exists — clearly
  labeled and **disables bit-perfect**).
- **Software volume fallback:** mpv applies linear gain through its float audio filter
  chain (`volume` filter, ~32-bit float samples). That is the best precision libmpv
  offers; it is **not** 64-bit and **not** bit-perfect. Prefer device/sink volume.
- **MPRIS** volume property maps to the same **VolumeController**.
- Settings: output device / sink selection; optional “allow software volume fallback”.

**UI:** show when volume is **device** vs **software** (e.g. badge or subtitle in
preferences) so users know bit-perfect status is intact.

### Output endpoints (planned — local only today)

Tunes supports more than one **output type**. Endpoints are not all “ALSA cards”;
network renderers use a different control path than local mpv playback.

**Today:** local PipeWire/Pulse sink selection and volume only (`platform/linux/audio.py`,
Settings → Audio → Output device). UPnP and pro-audio adapters are not implemented.

**Priorities (product order):**

| Priority | Type | Typical hardware | Implementation sketch |
|----------|------|------------------|------------------------|
| **1 — Local** | Linux audio sink | DAC, headphones, USB interface on the Tunes machine | `mpv` → PipeWire node or ALSA device; **VolumeController** on that sink; full bit-perfect control |
| **2 — Network (open)** | UPnP / DLNA **Media Renderer** | TVs, AVRs, hi-fi streamers, NAS-friendly speakers (any OS on device) | SSDP discovery; push `PlayableSource.uri` via AVTransport; sync transport/volume from renderer; device decodes (lossless URL when possible) |
| **3 — Optional / later** | **AES67** / **Dante** / Ravenna | Studio/install, some high-end gear | PCM over Ethernet; PTP/multicast complexity; only if there is demand — treat as separate pro-audio adapter |

**Explicitly not on the roadmap:** Logitech Media Server / Squeezelite (awkward fit for
a standalone player). **Deferred / low priority:** Snapcast, PipeWire/Pulse **network**
sinks (Linux-only receivers), AirPlay sender, Google Cast — may revisit for compatibility
but not core architecture.

**Local (priority 1)** — see [Volume control](#volume-control-requirement) and
`platform/linux/audio.py` (planned): list sinks and ALSA devices; apply bit-perfect mpv
profile to the selected local endpoint.

**UPnP renderer (priority 2)** — for non-Linuxy LAN devices:

- `core/backends/` still resolves `Track` → `PlayableSource` (often `http://` to a
  short-lived file server on the Tunes host, or a reachable `file://` if the renderer
  shares storage).
- A **renderer adapter** (e.g. `platform/linux/upnp.py` or `engines/upnp_renderer.py`)
  implements play/pause/seek/volume against the device’s UPnP services, not mpv PCM output.
- **PlayerService** chooses engine by selected output: **local** → `MpvEngine`; **UPnP**
  → renderer adapter. Queue and library logic stay in `core/`.
- **Bit-perfect** on renderers is *best-effort*: send lossless sources without transcoding
  in Tunes; document that the device may still resample or apply DSP.
- **OpenHome** (UPnP profile for hi-fi) is an enhancement on the same stack if needed
  for gapless/playlist quality on supported brands.

**AES67 / Dante (optional)** — cool for pro installs; heavy (timing, multicast, licensing
of stacks). Spec-only until someone needs it; do not block v1 local + UPnP work.

```text
Settings → Output
  ├── Local: PipeWire sink / ALSA device   → MpvEngine + VolumeController
  ├── Network: UPnP Media Renderer (list)  → RendererAdapter (URI push)
  └── (future) AES67 / Dante endpoint      → pro adapter
```

### Media keys (requirement)

Tunes must respond to **hardware media keys** and OS-level media controls — the same
actions as the transport bar and volume slider, without requiring the window to be
focused.

| Input | Action | Routes through |
|-------|--------|----------------|
| Play / Pause (toggle) | `PlayerService.play()` / `pause()` | core playback |
| Next / Previous | `skip_next` / `skip_previous` | core queue |
| Volume up / down | step endpoint volume | **VolumeController** |
| Mute (if exposed) | mute or restore endpoint | **VolumeController** |
| Stop (if exposed) | pause (keep queue position) | core playback |

**Linux / GNOME (primary):**

- **MPRIS** (`platform/linux/mpris.py`) is the main integration: GNOME Shell, Bluetooth
  headsets, lock screen, and other clients send play/pause/next/prev/volume over D-Bus
  when Tunes is a registered media player. Implement MPRIS on top of **PlayerService**
  and **VolumeController**, not GTK or mpv directly.
- **GTK media keys** (`Gtk.EventControllerKey` on the application): handle
  `XF86AudioPlay`, `XF86AudioPause`, `XF86AudioNext`, `XF86AudioPrev`,
  `XF86AudioStop`, `XF86AudioRaiseVolume`, `XF86AudioLowerVolume`,
  `XF86AudioMute` when the app has keyboard focus — same service calls as MPRIS.
- Background / unfocused control relies on **MPRIS**; do not duplicate global shortcut
  hacks in the UI layer.

**Volume keys:** use **VolumeController** (endpoint/sink volume), not mpv soft volume,
so behavior matches the slider and bit-perfect rules (see [Volume control](#volume-control-requirement)).

**Other platforms (later):** macOS media keys / Now Playing; Windows global media keys
— implement in `platform/` with the same **PlayerService** / **VolumeController** API.

Works in **minimized** mode, while browsing the library, and when the window is in the
background (via MPRIS on Linux).

### External control interface (requirement)

Tunes must expose a **control interface for external tools** — scripts, desktop
integrations, hardware utilities, and other apps — not only accept commands from the
Tunes UI.

**Primary example:** when the user changes volume on the **playback device or DAC**
(hardware knob, PipeWire/ALSA mixer, UPnP renderer volume, etc.), the new level must
**sync back into Tunes** — UI slider, MPRIS `Volume` property, and any other in-app
volume display stay consistent without the user touching the app.

**Architecture:**

- **Outbound (external → Tunes):** existing **MPRIS** surface on Linux; same
  **PlayerService** / **VolumeController** API as the GTK UI (see [Media keys](#media-keys-requirement)).
- **Inbound (device/stack → Tunes):** **VolumeController** change notifications
  (`subscribe(level_changed)` — see [Volume control](#volume-control-requirement));
  propagate to **PlayerService** events so UI and MPRIS update.
- Other platforms: equivalent D-Bus, IPC, or OS hooks in `platform/` with the same
  core service boundary — no GTK or mpv in the integration layer.

Do not require the Tunes window to be focused; external volume changes must be
observable while browsing or in minimized mode.

### Events (core → UI)

Examples: `TrackStarted`, `PositionChanged`, `TrackFinished`, `PlaybackError`.
UI never polls mpv properties directly.

---

## Unified catalog

“One music store” is phased:

| Phase | User experience | Status |
|-------|-----------------|--------|
| A | One search box; results tagged Local / Tidal / Deezer / Qobuz | **Done** — `PlayerService.search()` merges local + signed-in backends |
| B | Merged list, heuristic duplicate titles | Not started |
| C | Dedup via MusicBrainz / ISRC / UPC | Not started |
| D | Unified playlists, “prefer local if duplicate” | Not started |

Search is implemented in **`PlayerService.search()`** (local store first, then TIDAL and
Qobuz append). A dedicated `core/catalog/` module may appear if dedup/merge grows beyond
the service facade.

**Prefer local:** when the same album exists locally and on a service, default play
local file (match on normalized artist/title or MBID later) — not implemented yet.

---

## Home content (local + streaming)

Tunes should not copy any one provider’s “home” page. Instead, the UI presents a set
of generic **Home rails** (sections) populated by multiple **providers** (Local,
Tidal, Deezer, Qobuz).

### Rails (v1 shape)

Each provider can implement any subset:

- **Continue listening** — recently played tracks/albums.
- **Recently added** — local imports, and/or service new releases.
- **Favorites** — starred/liked content.
- **New releases** — service catalog rails (not applicable to local).
- **Featured / editorial** — service picks (not applicable to local).
- **Recommendations** — optional later; service API or local heuristics.

### Provider surface (as implemented)

Backends are concrete clients (`TidalClient`, `QobuzClient`, `LibraryStore`) called from
**PlayerService**, not a shared protocol yet. Typical methods:

| Concern | Local (`LibraryStore`) | TIDAL / Qobuz clients |
|---------|------------------------|------------------------|
| Search | `search_releases()` | `search_releases()` |
| Browse | `list_releases()`, `get_release()`, `get_release_tracks()` | same |
| Play | `resolve_local_track()` via `resolve_track()` | `resolve_playable()`, `queue_for_track()` |
| New Music | `list_recently_added_items()` | `list_new_release_items()` |
| Suggestions | `list_continue_listening_entries()`, `list_rediscover_items()` | `list_suggestion_items()`, `list_similar_items()` (TIDAL) |
| Auth | — | OAuth (TIDAL) or credentials + login (Qobuz) |

Items use core `Release` / `Track` models and opaque IDs.

### Aggregation (implemented)

Home feeds are aggregated in **`core/home.py`** (types and limits) and
**`PlayerService`** (`list_recently_added_items`, `list_suggestion_items`), not in a
separate catalog module.

- **Normalize** to `RecentlyAddedItem(release, added_ns)` with opaque IDs
  (`local:…`, `tidal:…`, `qobuz:…`).
- **Deduplicate** by `release.id` within each view (same ID from multiple providers
  keeps the highest `added_ns`).
- **Rank:** New Music by recency (`added_ns`); Suggestions by `suggestion_added_ns()`
  (local first, then streaming by source name order).
- **Badge source** in UI via `source_label()` (`ui/gtk/util.py`).

### Local rails (implemented in library store)

Local library populates home feeds via `LibraryStore`:

- **Recently added (local):** releases from folders added within the New Music window.
- **Continue listening:** `play_history` table (recorded on playback).
- **Rediscover (local):** highly-played releases not played recently
  (`SUGGESTIONS_REDISCOVER_IDLE_MONTHS` in `core/home.py`).

Favorites / star ratings are not implemented yet.

### Discover views (implemented)

**New Music** — flat album grid (`RecentlyAddedGridView`). `PlayerService.list_recently_added_items()`
merges:

- Local releases added within **New Music cutoff** days (Settings → Application; default 90).
- TIDAL new-release rails (when signed in; not filtered by cutoff).
- Qobuz featured new/recent releases (when signed in; filtered by cutoff).

Deduped by `release.id`, sorted by `added_ns`, capped at 300.

**Suggestions** — flat album grid (same UX as New Music). `PlayerService.list_suggestion_items()`
collects TIDAL track radio (when playing TIDAL), **continue listening** (`play_history`),
TIDAL / Qobuz editorial catalogs, and local **rediscover**, then dedupes by `release.id`.

**Sort order** (higher `added_ns` first): all **local** releases, then streaming by source
name — **Deezer**, **Qobuz**, **TIDAL** (Deezer not implemented yet). Within each group,
recent plays or catalog order apply.

LLM-based recommendations are out of scope. **Last.fm** (optional later): scrobble from
`play_history`, similar-artist API, opt-in credentials in Settings.

---

## Streaming

Tunes is **not affiliated** with any streaming provider. Users need **own paid
subscriptions** where required. Features can break when providers change auth or terms.
README includes a user-facing [disclaimer](../README.md#streaming-disclaimer).

**TIDAL** and **Qobuz** backends are implemented in `core/backends/` with OAuth or
account login, federated search, stream URL resolution at play time, and New Music /
Suggestions feeds. **Deezer** is planned next.

One **provider abstraction** pattern — auth, catalog/search, resolve `PlayableSource` at
play time — is shared across backends via `resolve_track()` and `PlayerService`.

### Provider strategy (integration order)

Commercial hi-fi apps often have **formal partnerships** with lossless services; a small
FOSS desktop player should not depend on that. There is no Spotify-like open ecosystem
for lossless streaming — plan per provider:

| Provider | Access model | Status in Tunes |
|----------|--------------|-----------------|
| **Tidal** | Official developer platform; OAuth via `tidalapi` | **Done** — first streaming backend |
| **Qobuz** | JSON API; user-supplied app credentials | **Done** — in-tree REST client |
| **Deezer** | Documented developer API | **Not started** — planned second streaming backend |

Optional: contact Qobuz about third-party open-source clients. Official app credentials
for Tunes would switch the default from user-supplied keys to bundled defaults without
changing the backend API shape.

### Backend layout

```text
tunes_player/core/backends/
  playable.py, resolve.py, local.py
  tidal/       # TidalClient — oauth, search, streams, home feeds (tidalapi)
  qobuz/       # QobuzClient — config credentials, session, signed stream URLs
  deezer/      # (planned)
```

### Auth and credentials

- **Tidal:** OAuth via developer registration; tokens in config (see `platformdirs`).
- **Deezer:** documented API auth (details when implementing).
- **Qobuz:** see [Qobuz credentials](#qobuz-credentials).

#### Qobuz credentials

Qobuz’s JSON API expects an **app id**, a signing **secret** (for signed endpoints such as
stream URLs), and a per-user **auth token** after login. Community clients (e.g. the
Lyrion/LMS plugin) bundle app credentials in the plugin package; Tunes uses the same API
but does **not** copy or ship those values.

**Single code path:** `core/backends/qobuz/` reads `app_id` and `app_secret` from
configuration at runtime (optional second app id if needed for web-style token flows).
There is no parallel “hard-coded vs user” implementation — only the **source** of those
strings changes.

| Phase | App id / secret | Account login |
|-------|-----------------|---------------|
| **v1 (current)** | User enters in **Settings → Sources → Qobuz**; persisted in `platformdirs` config (`~/.config/tunes-player/config.json`). | Required: email + password → session in `~/.local/share/tunes-player/qobuz-session.json`. |
| **If Qobuz grants Tunes official credentials** | Ship defaults in the app; hide or pre-fill App ID / Secret fields. Config may still allow overrides for debugging. | Unchanged — subscribers still sign in with their Qobuz account. |

**Policy (v1):**

- Do **not** commit app credentials to the repository, example configs, or releases.
- Do **not** implement runtime extraction from Qobuz’s web player or other official apps.
- **Development:** maintainers may paste credentials into a **local** config only (e.g. keys
  obtained from one’s own web-player inspection or another client install) to exercise the
  backend against the live API.

**Implementation notes:** password is MD5-hashed (UTF-8) for `user/login`; signed requests
follow [Qobuz signed-request auth](https://github.com/Qobuz/api-documentation#signed-requests-authentification-).
Credentials can rotate when Qobuz changes client fingerprints — users (or a future bundled
update) replace app id/secret; session tokens are separate.

### Libraries and license

| Provider | Dependency | License note |
|----------|------------|--------------|
| Tidal | `tidalapi` | LGPL-3.0 — OK inside GPL app |
| Deezer | TBD (REST/client) | Confirm before linking |
| Qobuz | in-tree client (urllib) | No third-party SDK linked |

See [License rationale](#license-rationale).

---

## License rationale

**GPL-3.0-or-later** is intentional:

| Dependency | License | Note |
|------------|---------|------|
| mpv / libmpv | GPLv2+ | Playback engine |
| mutagen | GPLv2+ | Tags / local metadata |
| GTK / Libadwaita | LGPL | OK inside GPL app |
| tidalapi | LGPL-3.0 | TIDAL backend |
| Deezer client (planned) | TBD | Confirm before linking |

MIT/Apache would only be realistic if the stack avoided GPL deps (e.g. GStreamer
instead of mpv). **AGPL** is unnecessary for a desktop app.

---

## Packaging and identifiers

| Layer | Value |
|-------|--------|
| GitHub repo | `mbrennwa/tunes-player` |
| Debian package | `tunes-player` (not bare `tunes`) |
| Binary | `tunes-player` |
| Desktop file | `data/tunes-player.desktop` |
| Config | `platformdirs` → `~/.config/tunes-player/config.json` |
| Library DB | `~/.local/share/tunes-player/library.db` |
| Sessions | `tidal-session.json`, `qobuz-session.json` under data dir |

---

## Roadmap (ordered)

Status as of current tree — see [Implementation status](#implementation-status-current).

1. ~~Local folder scan + SQLite library index.~~ **Done**
2. ~~`PlaybackEngine` + `MpvEngine` + queue; GTK transport bar; **MPRIS + media keys**.~~ **Done** (minimized compact controller still open)
3. **Local output:** bit-perfect profile + output device selection + **`VolumeController`**. **Partial** — PipeWire/Pulse sink volume works; exclusive ALSA / full bit-perfect path open (see TODO)
4. **External control interface** — inbound volume from device/stack → Tunes UI + MPRIS. **Partial** — outbound MPRIS done; inbound `VolumeController.subscribe()` not wired
5. DEB package with declared depends (`python3-gi`, `gir1.2-adw-1`, `mpv`, …). **Not started**
6. **UPnP / DLNA Media Renderer** output. **Not started**
7. ~~**Streaming — Tidal**.~~ **Done**
8. **Streaming — Deezer**. **Not started**
9. ~~**Streaming — Qobuz**.~~ **Done**
10. ~~Federated catalog search (phase A).~~ **Done** in `PlayerService.search()`
11. Heuristic dedup / prefer-local. **Not started**
12. Optional Qt UI for macOS. **Not started**
13. Playlists UI. **Not started**
14. **(Optional)** AES67 / Dante LAN output. **Not started**

---

## TODO

Trackable open items. Ordered milestones are in [Roadmap](#roadmap-ordered) above.

### Streaming

- [ ] **Deezer backend** — auth, search, playback, home feeds (second streaming provider).

### Discover / recommendations

- [ ] **Last.fm (optional)** — opt-in scrobbling from `play_history`; use Last.fm
  similar-artist / recommendation APIs to enrich Suggestions without LLM.

### Control / integration

- [ ] **Minimized compact controller** — small always-on-top transport-only window mode.
- [ ] **External control interface** — expose a control surface for external tools;
  sync device/DAC/stack volume changes back into Tunes (UI, MPRIS, related state).
  Builds on `VolumeController.subscribe()` and MPRIS/D-Bus. See
  [External control interface](#external-control-interface-requirement).

### Bit-perfect playback (mpv output profile)

mpv remains the right engine (decode, seek, formats, streaming, cross-platform).
Bit-perfect is an **output policy** on top of mpv — not a reason to replace it.
Today only the mpv-side half is done (volume 100%, ReplayGain off, device/sink
volume when `_effective_bit_perfect()` is true). PCM may still be resampled or
mixed by PipeWire/Pulse before the DAC.

**Done:**

- [x] mpv soft gain disabled when bit-perfect is effective (`volume=100`, no ReplayGain).
- [x] Volume slider routes to **VolumeController** (PipeWire/Pulse sink), not mpv gain.
- [x] UI indicates bit-perfect vs software-volume fallback.

**Still needed for proper bit-perfect:**

- [ ] **Output path** — when bit-perfect is on, prefer direct ALSA (or a PipeWire
  pro-audio / exclusive node) instead of the default mixed/resampling sink path.
- [ ] **Exclusive / hog mode** — where supported, take exclusive device access so
  nothing else resamples or mixes on that output.
- [ ] **Rate and format matching** — configure mpv/AO so output matches source sample
  rate, bit depth, and channel layout (no silent resampling).
- [ ] **Device selection** — let users pick a specific ALSA `hw:` (or equivalent)
  device, not only a Pulse/PipeWire sink name.
- [ ] **Profile wiring** — apply the above in `engines/mpv.py` + `platform/linux/audio.py`
  when constructing `MpvEngine` (see [Bit-perfect playback](#bit-perfect-playback-requirement));
  keep PipeWire-first path for non-bit-perfect / device-volume use.
- [ ] **UI honesty** — show which output path is active (e.g. sink vs direct ALSA,
  exclusive on/off) so users know bit-perfect status is intact.
- [ ] **Platform parity (later)** — WASAPI exclusive (Windows), CoreAudio hog mode
  (macOS); same `PlaybackEngine` profile idea, different platform backends.

---

## Pitfalls (do not)

- Import `gi` or `mpv` in `core/`.
- Resolve stream URLs in GTK signal handlers.
- Call mpv from the UI thread without marshaling events.
- Use bare `music` as package name on Debian.
- Assume a normal venv sees `gi` without `--system-site-packages`.
- Route the main volume slider through **mpv soft volume** while bit-perfect is enabled.
- Enable ReplayGain or resampling silently in bit-perfect mode.
- Handle media keys only in focused GTK windows without **MPRIS** (breaks GNOME /
  headset / lock-screen control when unfocused).
