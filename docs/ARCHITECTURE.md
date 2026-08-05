# Tunes — technical architecture

Design decisions and how the codebase is structured. The README stays short; this
document describes what Tunes is, how it works, and what is planned. Work tracking
lives on GitHub Issues.

## Product

| Item | Choice |
|------|--------|
| Display name | **Tunes** |
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
  **TIDAL** and **Qobuz** are implemented; **Deezer** and **Spotify** are not.
- Present sources as **one searchable library** (see [Unified catalog](#unified-catalog)).
- **Simple** Libadwaita GUI; native GNOME look (not Qt on Linux).

### Out of scope for v0.1

- **Deezer** and **Spotify** streaming backends (no supported full-playback API for mpv-style
  clients today; see [Streaming](#streaming)).
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

---

## Repository layout

```
tunes_player/
├── core/              # No GTK, no mpv — models, services, library, playback logic
│   ├── models.py      # Release, Track, Artist, Source
│   ├── services.py    # PlayerService facade for all UIs
│   ├── config.py      # ConfigManager, AppConfig (platformdirs)
│   ├── home.py        # New Releases / Suggestions item types and merge limits
│   ├── volume.py      # VolumeController protocol
│   ├── backends/
│   │   ├── playable.py, resolve.py, local.py
│   │   ├── tidal/     # client (tidalapi), convert, ids
│   │   └── qobuz/     # in-tree REST client, convert, ids
│   ├── library/       # db, store, scanner, scan_worker, release_logic, art_cache
│   └── playback/
│       ├── engine.py     # PlaybackEngine protocol
│       ├── mpv_cli.py    # mpv CLI/property helpers (base_audio_options, …)
│       └── mpv_events.py # end-file reason helpers
├── engines/
│   ├── factory.py         # create_playback_engine()
│   └── mpv.py             # MpvEngine — in-process libmpv
├── platform/
│   └── linux/         # MPRIS, PipeWire/Pulse volume (audio.py)
└── ui/
    └── gtk/           # app, views, preferences, now_playing, release_grid, …
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
  local groups). SQLite and tag metadata still use `album_*` column names intentionally.

GTK runs on the main loop; mpv callbacks post to a queue → GLib idle (same pattern
will work for Qt signals later).

**Album art (`art_uri`):**

- `Release.art_uri` is the canonical cover value for UI and MPRIS.
- **Local** art is backed by the `album_art` SQLite table and on-disk cache under
  `{data_dir}/art/`; URIs use the `tunes://art/local/…` scheme. `art_updated` events
  mean local cache rows changed — refresh via `PlayerService.refresh_local_release_art_uris()`.
- **Streaming** art is an HTTPS URL set when the catalog row is fetched (TIDAL/Qobuz APIs).
  It lives on the in-memory `Release` object (and shell cache payload), not in
  `LibraryStore`. UI must not re-resolve streaming covers through the local store.

**Errors and logging:**

- User-facing playback failures set `PlayerService.last_error()` and emit
  `playback_error`; the GTK shell shows an `Adw.Toast` (see `ui/gtk/errors.py`).
- Diagnostics use the stdlib `logging` package (`tunes_player.core.logging_config`),
  configured at app startup. Log file: `{user_state_dir}/tunes-player.log` (typically
  `~/.local/state/tunes-player/tunes-player.log`; #76), size-rotated at 5 MiB with 3
  backups (~20 MiB worst case; #72). An existing log under the former data-dir path is
  moved once on startup. Override verbosity with `TUNES_LOG_LEVEL=DEBUG`.
- Optional stderr mirroring: `TUNES_LOG_STDERR=1` (otherwise stderr is WARNING+).
- Optional position poll spam: `TUNES_POSITION_POLL_LOG=1`.
- Release-grid rebuild tracing for #75 (`tunes_player.core.grid_trace`):
  **off by default**. Enable with `TUNES_GRID_TRACE=1` (INFO lines prefixed
  `grid_trace`).
- Playback health monitor (#67): enabled by default. A daemon thread
  (`tunes-playback-health`) compares GTK-published mpv samples
  (`time-pos`, `core-idle`, `paused-for-cache`, `ao`, …) with PipeWire/Pulse sink
  state (`pactl`/`wpctl`) and ALSA PCM health (`AlsaXrunMonitor`: xruns plus
  `hw_ptr`/`appl_ptr` advancement when using direct ALSA). Logs an INFO heartbeat
  every 10s and WARNING on sustained mismatches. The monitor is primarily
  **diagnostic** (including for album auto-advance / track-boundary freezes, #66).
  Sustained soft stalls (`alsa_feed_stalled` / `alsa_not_running`) may still trigger
  a separate mid-track direct-ALSA safety-net recovery (`ao-reload` then full reload;
  no PipeWire fallback). Near track end (#66), soft stalls advance the queue instead
  of reloading AO (missed `end-file` / idle-at-EOF). `time_pos_stalled` mid-track
  freezes the progress bar (no wall-clock extrapolation) and shows a toast.
  Disable with `TUNES_PLAYBACK_HEALTH_LOG=0`.
- Direct ALSA track replace (#66): every URI change reopens the audio output
  (`stop` + `ao-reload`) before `play`. USB “keep device open” only skips redundant
  format/buffer property churn, not AO reopen. Queue advance on mpv `end-file` EOF
  does not require `time-pos` near duration (poll-based near-end advance remains as
  a fallback when `end-file` is missed).
- Optional startup probe (`engines.factory.probe_playback_engine`) warns when the
  `mpv` binary is missing before the user presses Play.

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

**Structure:** one window — *music shell in the middle, control at the bottom*.

```text
┌ Header: back · (title) · Settings menu ────────────────────────┐
├ Search… · New Releases · Suggest Music ────────────────────────────┤
├ [All] [Local] [TIDAL] [Qobuz]  (only if multiple sources) ─────┤
├ Main pane: release grid → release detail (single NavigationView) ┤
├─────────────────────────────────────────────────────────────────┤
│ Now Playing bar: art · title · transport · volume · queue       │
└─────────────────────────────────────────────────────────────────┘
```

**Shell (`ui/gtk/app.py`):** every grid is a **bounded selection** — federated **search**
(Enter in the search field), **New Releases**, or **Suggest Music** — optionally narrowed by
**source chips** (shown only when more than one source is configured). There is no
full-catalog browse and no sidebar. **Settings** opens from the header menu
(`AdwPreferencesWindow`). Session state (`shell_state` in `config.json`) restores the
last selection on relaunch; first launch with no sources shows an onboarding empty state.

**Main pane:** one `AdwNavigationView` — grid root → release detail. Queue opens as a
sheet from the Now Playing bar.

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
| **Application** | New Releases cutoff (days); **Downloads folder** for Save to disk (not a library Source unless the user adds it under Local files); Diagnostics log path |
| **Sources** | **Local files:** music folders, scan library. **Streaming:** TIDAL sign-in/out (OAuth via browser); Qobuz App ID/Secret, save credentials, email/password sign-in/out |
| **Audio** | Output device dropdown (PipeWire/Pulse sinks, bit-perfect potential labels); allow software-volume fallback when no sink control |

**Unifying principle:** Local files and streaming services are both **sources** of music.
The **Sources** page groups local folders and streaming accounts; credentials and session
files live in **core/backends/** and `platformdirs` config/data dirs. Settings UI calls
**PlayerService** only — never streaming APIs from GTK.

**Qobuz:** user-supplied App ID and App Secret (required before sign-in); see
[Qobuz credentials](#qobuz-credentials). Tunes does **not** ship or auto-scrape Qobuz keys.

**Where streaming appears outside Settings:**

- **New Releases / Suggest Music:** merged selections from local + signed-in services.
- **Search:** federated release results (local first, then streaming append).
- **Release detail / playback:** same views for `local:…`, `tidal:…`, `qobuz:…` IDs.
- **Now Playing:** quality hint (e.g. FLAC metadata, “TIDAL”, “QOBUZ”) plus **playback
  path** (`ALSA bit-perfect`, resample notes, `via PipeWire`). The path line is
  **authoritative from the engine** (negotiated mpv/ALSA state), not only pre-load
  inference in `PlayerService`.
- **Playlists:** not implemented — catalog phase D.

---

## Sound / playback separation

Same layering as GUI:

| Layer | Responsibility |
|-------|----------------|
| `core/backends/` | Resolve `Track` → `PlayableSource` (file path or HTTPS URL) |
| `PlayerService` | Queue, play/pause/skip/seek, volume, federated search, home feeds |
| `engines/mpv.py` | `PlaybackEngine` — in-process libmpv via python-mpv (load, play/pause/seek, events) |
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

- Default and preferred **decoder/output** on **all** OSes (cross-platform).
- Music-only: run **headless** (no video surface) — avoids embedding mpv in GTK/Qt.
- **Today:** **in-process libmpv** via `python-mpv`; `MpvEngine` in `engines/mpv.py`
  runs on the GTK main thread. `PlayerService` owns queue, device profile, and
  transport; mpv property observers enqueue `EngineEvent`s drained on the UI thread.

### Bit-perfect playback (requirement)

Audiophile use is a **product requirement**, not an optional extra — but on Linux v1 it
is scoped to **direct ALSA hardware**, not the default PipeWire/Pulse sink path.

**Goal:** for focused local listening, audio reaches the DAC in the **original format**
(sample rate, bit depth, channel layout) without Tunes applying sample manipulation
(volume gain, resampling, ReplayGain, EQ, etc.).

**Linux v1 — two output paths:**

| Path | Bit-perfect (sample-accurate) | What Tunes guarantees |
|------|-------------------------------|---------------------|
| **ALSA `alsa:hw:…`** (listed first in Settings) | Yes, when file format matches hardware caps and device volume is used | Per-track rate/format via mpv; optional **Exclusive device access**; honest resample labels |
| **PipeWire / Pulse sink** (default for most desktops) | **No** — not a roadmap priority | Unity gain in mpv (no soft volume); **sink volume**; UI note **via PipeWire** |

PipeWire is the normal **mixed desktop** path (Discord, notifications, other apps).
Mixing and sink rate policy happen outside Tunes, so strict bit-perfect is already
impossible there. Users who want sample-accurate local FLAC/WAV should pick the **ALSA
hardware** entry for their DAC, not the PipeWire sink with the same name.

**Engine (mpv) — constraints on the unity-gain / ALSA paths:**

- Keep mpv **volume at 100%**; do not use mpv soft volume for listening level.
- Disable **ReplayGain** and other DSP that modifies samples.
- On **direct ALSA**, avoid resampling where hardware caps allow (see `output_profile.py`).
- On **PipeWire sinks**, route to `pulse/…` for volume integration only — do not claim
  bit-perfect in UI or docs for that path.
- On Windows/macOS later: exclusive / hog mode where available (WASAPI exclusive,
  CoreAudio hog).

**Caveats to document in UI:**

- **Bit-perfect + software volume are incompatible** — lowering level inside the
  player changes samples. Volume must move to the **device or sink**.
- **Streaming** (Tidal/Deezer/Qobuz) may already be lossy or transcoded; “bit-perfect” means
  **no additional processing in Tunes**, not that the stream is hi-res MQA/bitstream.
- **Local FLAC/WAV** is the primary bit-perfect use case for v1 (ALSA hw only).

Implement bit-perfect as an explicit **settings profile** applied when constructing
the playback engine (`MpvEngine` / in-process mpv), not
scattered mpv flags in UI code.

**Not planned (v1):** PipeWire pro-audio / rate-matched sink bit-perfect. ALSA hw +
optional exclusive access covers solo listening; PipeWire remains the correct default
for everyday desktop use.

**Linux v1 implements:** unity-gain mpv (`volume=100`, no ReplayGain); volume via
**VolumeController** (not mpv soft gain on the bit-perfect path); honest UI labels for
bit-perfect vs software-volume fallback and resampling; Tunes-only output routing
without changing the system default sink; per-sink bit-perfect potential hints;
direct ALSA `hw:` with per-track rate/format matching (`alsa_caps`, `output_profile`);
optional **Exclusive device access** (`pw-cli` suspend on card).

**Later (other platforms):** WASAPI exclusive (Windows), CoreAudio hog mode (macOS);
optional UI footnote when hardware volume is below max but playback is labeled bit-perfect.

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

### Output endpoints

**Today:** local PipeWire/Pulse sink selection and volume, plus direct ALSA hw devices
(`platform/linux/audio.py`, Settings → Audio → Output device). See [Volume control](#volume-control-requirement).

Networked output (UPnP/DLNA renderers, AES67/Dante, and related options) is not
implemented. Design notes are in [GitHub issue #60](https://github.com/mbrennwa/tunes-player/issues/60).

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

**Today:** outbound control via **MPRIS** and GDK media keys is implemented. Inbound
`VolumeController.subscribe()` — syncing hardware or stack volume changes back into the
UI — is not wired yet.

### Events (core → UI)

Examples: `TrackStarted`, `PositionChanged`, `TrackFinished`, `PlaybackError`.
UI never polls mpv properties directly.

---

## Unified catalog

“One music store” evolves in phases:

1. **Federated search (today):** one search box; results tagged by source (Local, TIDAL,
   Qobuz). **`PlayerService.search()`** queries the local store first, then appends
   signed-in streaming backends.
2. **Heuristic merge (planned):** merged browse lists with duplicate-title collapsing.
3. **Strong dedup (planned):** MusicBrainz / ISRC / UPC identity. Today the release grid
   shows one tile per catalog quality tier (`core/release_quality_tiles.py`); the shell
   quality filter gates which tiles appear and sets the playback / Save-to-disk quality
   ceiling (`PlaybackPreference`).
4. **Unified playlists (planned):** cross-source playlists with “prefer local if duplicate”
   playback policy.

A dedicated `core/catalog/` module may appear if dedup/merge logic outgrows
**PlayerService**.

**Prefer local (planned):** when the same album exists locally and on a service, default
to the local file (match on normalized artist/title or MBID).

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
| New Releases | `list_recently_added_items()` | `list_new_release_items()` |
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
- **Rank:** New Releases by recency (`added_ns`); Suggestions by `suggestion_added_ns()`
  (local first, then streaming by source name order).
- **Badge source** in UI via `source_label()` (`ui/gtk/util.py`).

### Local rails (implemented in library store)

Local library populates home feeds via `LibraryStore`:

- **Recently added (local):** releases from folders added within the New Releases window.
- **Continue listening:** `play_history` table (recorded on playback).
- **Rediscover (local):** highly-played releases not played recently
  (`SUGGESTIONS_REDISCOVER_IDLE_MONTHS` in `core/home.py`).

Favorites / star ratings are not implemented yet.

### Discover views (implemented)

**New Releases** — flat release grid (`ReleaseGridView`). `PlayerService.list_recently_added_items()`
merges:

- Local releases added within **New Releases cutoff** days (Settings → Application; default 90).
- TIDAL new-release rails (when signed in; filtered by the same cutoff).
- Qobuz featured new/recent releases (when signed in; filtered by cutoff).

Deduped by `release.id`, sorted by `added_ns`, capped at 300.

**Suggest Music** — flat release grid (same widget). `PlayerService.list_suggestion_items()`
collects TIDAL track radio (when playing TIDAL), **continue listening** (`play_history`),
TIDAL / Qobuz editorial catalogs, and local **rediscover**, then dedupes by `release.id`.

**Sort order** (higher `added_ns` first): all **local** releases, then streaming by source
name — **Qobuz**, **TIDAL**. Within each group, recent plays or catalog order apply.

`Source.DEEZER` and `deezer:…` IDs are reserved for a future backend but Deezer does not
appear in the source filter or UI labels until implemented.

LLM-based recommendations are out of scope. **Last.fm** (optional later): scrobble from
`play_history`, similar-artist API, opt-in credentials in Settings.

---

## Streaming

Tunes is **not affiliated** with any streaming provider. Users need **own paid
subscriptions** where required. Features can break when providers change auth or terms.
README includes a user-facing [disclaimer](../README.md#streaming-disclaimer).

**Save to disk** (right-click release/track → Save to disk…) resolves the same stream
URLs used for playback, stages bytes under `{data_dir}/download-cache/{job_id}/` as
`NNNN.ext.tunes-partial`, tags the file, then atomically renames into the configured
**Downloads folder** (`download_folder` in config / Settings → Application). That folder
is **not** a music Source by default; Tunes does not move files into other library roots.
Paths under a configured music folder (e.g. if the user later adds the downloads folder
under Sources) are indexed via **`enqueue_incremental_scan`** (not a full-library rescan).
First use (or an unset/unwritable folder) opens a folder picker and persists the choice.
TIDAL DASH/MPD streams are remuxed with **ffmpeg**.

**Stream quality:** there is no separate download-quality setting. Saves use the highest
format allowed by the current shell quality filter (same `PlaybackPreference` ceiling as
playback).

**Concurrency (#68):** at most **two** tracks download simultaneously within a job.
Only **one job** runs at a time; further Save-to-disk actions are **queued in memory**
and start when the active job finishes (or is cancelled).

**Downloads menu (#83):** a header button left of Settings opens a Firefox-style popover
listing **Ongoing** (active job + progress/cancel), **Upcoming** (queued jobs, removable),
and **Completed** (in-session history of finished/failed jobs, cleared on quit). Routine
status is only in that menu; toasts are reserved for Save-to-disk **errors**.

**Already on disk (#68):** before starting a job, if the release appears in the local
library (`ids.release_id` / casefold search) or files already exist under the Downloads
folder at the expected path, show **Cancel / Download anyway**. Cancel aborts; Download
anyway proceeds and still uses `unique_destination` (no overwrite).

**Deferred (#68):** hover download control on release tiles; playlist Save to disk.

**Quit / resume (Firefox-like, #70):** closing or quitting while a job is active shows a
Stay / Quit confirm. Quit stops the transfer and persists a `manifest.json` in the job
dir (track ids, destination, completed indices). **Queued (upcoming) jobs are dropped**
on quit — they are not persisted. The next app start auto-resumes interrupted disk jobs
(job-level: skip fully staged tracks; re-fetch the in-flight track).
**Multi-track jobs** promote into Downloads only when every track stages successfully
(album-atomic); partial albums are not left in the downloads tree. Single-track jobs
promote immediately on success. See issues #68, #70, #81, and #83.

**TIDAL** and **Qobuz** backends are implemented in `core/backends/` with OAuth or
account login, federated search, stream URL resolution at play time, and New Releases /
Suggestions feeds.

**Deezer** is not implemented: the documented API exposes 30-second previews, not
full-track URLs for mpv; new OAuth app registration on the developer portal is often
unavailable. Unofficial gateway playback used by other FOSS tools is out of scope until
Deezer offers a supported full-playback path.

**Spotify** is not implemented: the Web API does not expose full-track URLs for mpv;
playback requires the Web Playback SDK, Spotify Connect, or the official app.
Unofficial librespot-style integration is out of scope until a supported native path
exists.

One **provider abstraction** pattern — auth, catalog/search, resolve `PlayableSource` at
play time — is shared across backends via `resolve_track()` and `PlayerService`.

### Provider strategy (integration order)

Commercial hi-fi apps often have **formal partnerships** with lossless services; a small
FOSS desktop player should not depend on that. There is no Spotify-like open ecosystem
for lossless streaming — plan per provider:

| Provider | Access model | In Tunes |
|----------|--------------|----------|
| **Tidal** | Official developer platform; OAuth via `tidalapi` | Implemented (`core/backends/tidal/`) |
| **Qobuz** | JSON API; user-supplied app credentials | Implemented (`core/backends/qobuz/`) |
| **Deezer** | Documented developer API (previews only); partnership TBD | Not implemented |
| **Spotify** | Web API + SDK/Connect (no direct stream URLs) | Not implemented |

Optional: contact Qobuz about third-party open-source clients. Official app credentials
for Tunes would switch the default from user-supplied keys to bundled defaults without
changing the backend API shape.

### Backend layout

```text
tunes_player/core/backends/
  playable.py, resolve.py, local.py
  tidal/       # TidalClient — oauth, search, streams, home feeds (tidalapi)
  qobuz/       # QobuzClient — config credentials, session, signed stream URLs
  deezer/      # (not implemented)
  spotify/     # (not implemented)
```

**TIDAL catalog quality (grid enrich):** CD vs hi-res filter buckets use album/track
`media_metadata_tags` / OpenAPI `mediaTags`. Peak rate/depth labels use album fields when
present, otherwise a **serialized, cached** first-track stream resolution probe
(`core/backends/tidal/catalog_stream_probe.py`) so enrich does not burst the same
rate limit as playback. Playback stream URLs are still resolved only at play time.

### Auth and credentials

- **Tidal:** OAuth via developer registration; tokens in config (see `platformdirs`).
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
| Spotify | TBD (SDK/Connect) | Confirm before linking |
| Qobuz | in-tree client (urllib) | No third-party SDK linked |

See [License rationale](#license-rationale).

---

## License rationale

**GPL-3.0-or-later** is intentional:

| Dependency | License | Note |
|------------|---------|------|
| mpv / python-mpv (in-process) | GPLv2+ | Playback engine |
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
| Desktop file | `data/tunes.player.desktop` (basename must match GTK app ID) |
| Config | `platformdirs` → `~/.config/tunes-player/config.json` |
| Library DB | `~/.local/share/tunes-player/library.db` |
| Logs | `~/.local/state/tunes-player/tunes-player.log` |
| Sessions | `tidal-session.json`, `qobuz-session.json` under data dir |

DEB packages are built from `debian/` via `tools/build-deb.sh`; release workflow is
documented in [RELEASE.md](RELEASE.md).

---

## Planned work

Areas described elsewhere in this document but not fully built, or only partially
implemented. These are design targets — progress is tracked on GitHub Issues.

| Area | Concept |
|------|---------|
| **UI** | [Minimized compact controller](#minimized-player-compact-controller--not-implemented); playlists browser; folder browse; full-screen Now Playing |
| **Catalog** | Heuristic merge, MusicBrainz/ISRC dedup, unified playlists, prefer-local playback ([Unified catalog](#unified-catalog)) |
| **Control** | Inbound volume sync via `VolumeController.subscribe()` ([External control interface](#external-control-interface-requirement)) |
| **Streaming** | Deezer and Spotify backends when supported playback APIs exist ([Streaming](#streaming)) |
| **Output** | Networked endpoints ([#60](https://github.com/mbrennwa/tunes-player/issues/60)) |
| **Platform** | Qt UI for macOS/Windows; WASAPI/CoreAudio bit-perfect parity ([Bit-perfect playback](#bit-perfect-playback-requirement)) |
| **Discover** | Last.fm scrobbling and similar-artist recommendations (opt-in; see [Home content](#home-content-local--streaming)) |

---

## Pitfalls (do not)

- Import `gi` or `mpv` in `core/`.
- Resolve stream URLs in GTK signal handlers.
- Call mpv from the UI thread without marshaling events.
- Use bare `music` as package name on Debian.
- Assume a normal venv sees `gi` without `--system-site-packages`.
- Route the main volume slider through **mpv soft volume** while bit-perfect is enabled.
- Enable ReplayGain or resampling silently in bit-perfect mode.
- Document or label **PipeWire/Pulse sinks** as bit-perfect (they are not; ALSA hw is).
- Handle media keys only in focused GTK windows without **MPRIS** (breaks GNOME /
  headset / lock-screen control when unfocused).
