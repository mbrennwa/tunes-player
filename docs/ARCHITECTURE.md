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

- Play **local** audio files (FLAC, MP3, …).
- **Bit-perfect** output for audiophile use when enabled (no unnecessary DSP).
- **Hardware / endpoint volume** — adjust the sound device or sink, not only in-app
  soft gain.
- Eventually integrate **streaming** (Tidal, Qobuz) via unofficial APIs.
- Present sources as **one searchable library** (see [Unified catalog](#unified-catalog)).
- **Simple** Libadwaita GUI; native GNOME look (not Qt on Linux).

### Out of scope for v0.1

- Streaming auth and playback.
- Unified cross-source deduplication (beyond basic federated search).
- macOS / Windows UI.
- Flatpak (may come later).

### Naming

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

**Goal:** the UI slider (and MPRIS volume, keyboard keys) adjusts **endpoint volume**
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
2. `PlaybackEngine` + `MpvEngine` + queue; GTK transport bar.
3. **Bit-perfect profile + `VolumeController` (PipeWire/ALSA endpoint volume).**
4. DEB package with declared depends (`python3-gi`, `gir1.2-adw-1`, `mpv`, …).
5. One streaming backend (Tidal or Qobuz).
6. Federated catalog search (phase A).
7. Heuristic dedup / prefer-local.
8. Optional Qt UI for macOS.

---

## Pitfalls (do not)

- Import `gi` or `mpv` in `core/`.
- Resolve stream URLs in GTK signal handlers.
- Call mpv from the UI thread without marshaling events.
- Use bare `music` as package name on Debian.
- Assume a normal venv sees `gi` without `--system-site-packages`.
- Route the main volume slider through **mpv soft volume** while bit-perfect is enabled.
- Enable ReplayGain or resampling silently in bit-perfect mode.
