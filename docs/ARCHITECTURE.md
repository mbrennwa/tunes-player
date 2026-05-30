# Tunes — architecture and product notes

This document captures design decisions from project planning (for contributors and
automated agents). The README stays short; details live here.

## Product

| Item | Choice |
|------|--------|
| Display name | **Tunes** |
| Package / CLI | **tunes-player** |
| App ID | `io.github.mbrennwa.Tunes` |
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
- Eventually integrate **streaming** (Tidal, Qobuz) via unofficial APIs.
- Present sources as **one searchable library** (see [Unified catalog](#unified-catalog)).
- **Simple** Libadwaita GUI; native GNOME look (not Qt on Linux).

### Out of scope for v0.1

- Streaming auth and playback.
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


“Music” was avoided: GNOME ships **Music** (`gnome-music`). **Tunes** is distinct;
`iTunes` echo is acceptable for a FOSS niche player.

---

## Repository layout

```
tunes_player/
├── core/           # No GTK, no mpv — models, services, library, playback logic
│   ├── models.py
│   ├── services.py # PlayerService facade for all UIs
│   ├── backends/   # Local, Tidal, Qobuz → PlayableSource (planned)
│   ├── playback/   # Queue, controller, events (planned)
│   └── catalog/    # Unified search across sources (planned)
├── engines/        # PlaybackEngine implementations (planned)
│   └── mpv.py      # libmpv wrapper
├── platform/       # OS-specific, non-UI
│   └── linux/      # MPRIS, audio device hints (planned)
└── ui/
    └── gtk/        # Libadwaita only on Linux for now
```

**Rule:** `core/` must not import `gi`, `PySide6`, or `mpv`. UI must not call mpv
directly.

Future: `ui/qt/` for macOS/Windows; same `PlayerService` API.

---

## GUI separation

- **UI** (`ui/gtk/`): windows, lists, transport bar, settings; subscribes to events.
- **PlayerService** (`core/services.py`): stable API — `play`, `pause`, `search`,
  `subscribe(events)`.
- **No GTK types in core models** — use `art_uri: str`, opaque IDs like
  `local:…`, `tidal:…`.

GTK runs on the main loop; mpv callbacks post to a queue → GLib idle (same pattern
will work for Qt signals later).

**Why GTK on Linux:** native Libadwaita on GNOME. Qt was rejected for Linux primary UI
(native-widget concerns on GNOME). Qt remains the likely choice for a later macOS UI.

**Dev setup:** PyGObject comes from the system (`python3-gi`). Use:

`python3 -m venv .venv --system-site-packages`

### Minimized player (compact controller)

The main window should support a **minimized** layout so users can keep Tunes open
without dedicating screen space to browsing and artwork.

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
┌ Header: search · settings · minimize ─────────────────────────┐
├ Sidebar ──┬ Main pane (single navigation stack) ──────────────┤
│ Albums    │  Album grid · artist discography · album detail    │
│ Artists   │  (cover + track list) · search results             │
│ Queue →   │                                                   │
│           │  Queue opens as sheet/overlay, not a third column  │
├───────────┴───────────────────────────────────────────────────┤
│ Now Playing bar: art · title · transport · volume · queue   │
└───────────────────────────────────────────────────────────────┘
```

**Sidebar (v0.1):** **Albums**, **Artists**, **Queue** (opens play queue sheet). Use
`AdwNavigationSplitView` + `AdwSidebar`; collapse to navigation list on narrow widths.

**Main pane:** stack navigation — browse grid → album/artist detail; header search
replaces pane with results while active.

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

---

## Sound / playback separation

Same layering as GUI:

| Layer | Responsibility |
|-------|----------------|
| `core/backends/` | Resolve `Track` → `PlayableSource` (file path or HTTPS URL) |
| `core/playback/` | Queue, shuffle/repeat, state machine, gapless policy |
| `engines/mpv.py` | `PlaybackEngine` protocol — load URI, play/pause/seek, emit position |
| `platform/linux/audio.py` | Output device list, **endpoint volume**, bit-perfect mpv options |
| `platform/linux/mpris.py` | D-Bus controls from **PlayerService**, not from GTK or mpv |
| `ui/gtk/` | Displays state; calls `PlayerService` only |

### PlayableSource

```python
# Conceptual — implement in core when playback lands
@dataclass
class PlayableSource:
    uri: str              # file:///… or https://…
    metadata: Track
    start_sec: float = 0
```

Stream URLs are resolved at **play time** (they expire). Local backend uses
`file://`; streaming backends use service APIs.

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
- **Streaming** (Tidal/Qobuz) may already be lossy or transcoded; “bit-perfect” means
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
  maybe a separate “software volume” fallback when no hardware control exists — clearly
  labeled and **disables bit-perfect**).
- **MPRIS** volume property maps to the same **VolumeController**.
- Settings: output device / sink selection; optional “allow software volume fallback”.

**UI:** show when volume is **device** vs **software** (e.g. badge or subtitle in
preferences) so users know bit-perfect status is intact.

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

### Events (core → UI)

Examples: `TrackStarted`, `PositionChanged`, `TrackFinished`, `PlaybackError`.
UI never polls mpv properties directly.

---

## Unified catalog

“One music store” is phased:

| Phase | User experience | Effort (rough) |
|-------|-----------------|----------------|
| A | One search box; results tagged Local / Tidal / Qobuz | +2–4 weeks after streaming works |
| B | Merged list, heuristic duplicate titles | +2–4 weeks |
| C | Dedup via MusicBrainz / ISRC / UPC | +1–2 months |
| D | Unified playlists, “prefer local if duplicate” | ongoing |

**v0.1:** local search only. **First unified milestone:** federated search (phase A).

`core/catalog/` should fan out search to backends and merge results; UI only calls
`catalog.search(query)`.

**Prefer local:** when the same album exists locally and on a service, default play
local file (match on normalized artist/title or MBID later).

---

## Streaming

- **Unofficial** APIs only; not affiliated with Tidal/Qobuz.
- Users need **own subscriptions**; features can break when providers change auth.
- Likely libraries: `tidalapi` (LGPL), Qobuz tooling (often **GPL/AGPL** — affects
  combined work; may become optional plugin).
- README includes a user-facing disclaimer.

Implement streaming **after** local library + mpv playback work.

---

## License rationale

**GPL-3.0-or-later** is intentional:

| Dependency | License | Note |
|------------|---------|------|
| mpv / libmpv | GPLv2+ | Playback engine |
| mutagen (planned) | GPLv2+ | Tags |
| GTK / Libadwaita | LGPL | OK inside GPL app |
| tidalapi (planned) | LGPL-3.0 | OK inside GPL app |
| Qobuz libs (planned) | GPL/AGPL | Strong copyleft if linked |

MIT/Apache would only be realistic if the stack avoided GPL deps (e.g. GStreamer
instead of mpv). **AGPL** is unnecessary for a desktop app.

---

## Packaging and identifiers

| Layer | Value |
|-------|--------|
| GitHub repo | `mbrennwa/tunes-player` |
| Debian package | `tunes-player` (not bare `tunes`) |
| Binary | `tunes-player` |
| Desktop file | `data/io.github.mbrennwa.Tunes.desktop` |
| Config (planned) | `platformdirs` → e.g. `~/.config/tunes-player/` |

---

## Roadmap (ordered)

1. Local folder scan + SQLite library index.
2. `PlaybackEngine` + `MpvEngine` + queue; GTK transport bar (expanded + minimized compact controller); **MPRIS + media keys**.
3. **Bit-perfect profile + `VolumeController` (PipeWire/ALSA endpoint volume).**
4. DEB package with declared depends (`python3-gi`, `gir1.2-adw-1`, `mpv`, …).
5. One streaming backend (Tidal or Qobuz).
6. Federated catalog search (phase A).
7. Heuristic dedup / prefer-local.
8. Optional Qt UI for macOS.
9. Playlists UI (if needed for other users; not required for core browsing workflow).

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
