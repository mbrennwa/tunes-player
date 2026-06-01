# Tunes — architecture, roadmap, and TODO

This document is the single place for design decisions, ordered milestones, and open
work items (for contributors and automated agents). The README stays short; details
live here.

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
- Eventually integrate **streaming** (Tidal, Deezer, Qobuz) via provider-specific APIs
  (official developer paths where available; see [Streaming](#streaming)).
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
│   ├── backends/   # local/, tidal/, deezer/, qobuz/ → PlayableSource (planned)
│   ├── playback/   # Queue, controller, events (planned)
│   └── catalog/    # Unified search across sources (planned)
├── engines/        # PlaybackEngine implementations (planned)
│   └── mpv.py      # libmpv wrapper
├── platform/       # OS-specific, non-UI
│   └── linux/      # MPRIS, PipeWire/ALSA, UPnP renderer (planned)
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
  `local:…`, `tidal:…`, `deezer:…`, `qobuz:…`.

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

### Settings (preferences window)

`AdwPreferencesWindow` with one page per concern — **not** one flat list. Streaming
accounts do **not** belong under **Library → Music folders** (local scan paths only).

| Page | v0.1 | Later |
|------|------|-------|
| **Library** | Music folders, scan options | Watch folders, rescan |
| **Sources** | — | Tidal, Deezer, Qobuz: per-service connect/disconnect; see [Settings](#settings-preferences-window) |
| **Audio** | Bit-perfect toggle, output device (placeholders today) | Local sink (PipeWire/ALSA), UPnP renderer, endpoint volume |

**Sources page (when streaming lands):**

- One **PreferencesGroup** per service (Tidal, Deezer, Qobuz).
- **Tidal / Deezer:** OAuth or documented sign-in; rows for login / logout, account
  label (“connected as …”), connection status, enable/disable — not filesystem paths.
- **Qobuz:** account sign-in (username + password, same API surface as other third-party
  clients) plus **App ID** and **App Secret** in Settings until/unless Qobuz grants
  Tunes its own client credentials — see [Qobuz credentials](#qobuz-credentials). v1
  treats app credentials as user-supplied (“advanced”); help text explains how to obtain
  them. Tunes must **not** ship or redistribute Qobuz app credentials in source or
  binaries and must **not** auto-scrape secrets from Qobuz’s web player.
- Auth and tokens live in **core/backends/** (and config on disk via `platformdirs`);
  Settings UI only calls **PlayerService** (or a small settings facade), never
  streaming APIs directly from GTK.
- README [streaming disclaimer](#streaming) applies; UI should link or repeat it on
  first connect (extra emphasis for Qobuz credential setup).

**Where streaming appears outside Settings:**

- **Browse / search:** unified results tagged Local / Tidal / Deezer / Qobuz (see
  [Unified catalog](#unified-catalog)); no per-service sidebar library section.
- **Now Playing:** optional source badge on the bar (deferred).
- **Playlists:** cross-source playlists remain catalog phase D — not v0.1.

Implement the **Sources** page with the first streaming backend (roadmap step 7), not
with local folder scanning.

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

### Output endpoints (planned — not implemented yet)

Tunes supports more than one **output type**. Endpoints are not all “ALSA cards”;
network renderers use a different control path than local mpv playback.

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

| Phase | User experience | Effort (rough) |
|-------|-----------------|----------------|
| A | One search box; results tagged Local / Tidal / Deezer / Qobuz | +2–4 weeks after streaming works |
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

Tunes is **not affiliated** with any streaming provider. Users need **own paid
subscriptions** where required. Features can break when providers change auth or terms.
README includes a user-facing [disclaimer](../README.md#streaming-disclaimer).

Implement streaming **after** local library + mpv playback work (roadmap steps 7–9).
One **provider abstraction** in `core/backends/` (auth, catalog/search, resolve
`PlayableSource` at play time) is proven with the **first** backend, then reused.

### Provider strategy (integration order)

Commercial hi-fi apps often have **formal partnerships** with lossless services; a small
FOSS desktop player should not depend on that. There is no Spotify-like open ecosystem
for lossless streaming — plan per provider:

| Provider | Access model (working assumption) | Role in Tunes |
|----------|-------------------------------------|---------------|
| **Tidal** | Official developer platform; OAuth | **First** streaming backend — prove abstraction, auth, playback |
| **Deezer** | Documented developer API | **Second** — validate streaming/playback; add if API stays clean |
| **Qobuz** | No simple public third-party path for hobby apps | **Later, optional** — config-driven client; v1 user-supplied app credentials ([Qobuz credentials](#qobuz-credentials)) |

Optional: contact Qobuz about third-party open-source clients. Official app credentials
for Tunes would switch the default from user-supplied keys to bundled defaults without
changing the backend API shape.

### Backend layout (planned)

```text
tunes_player/core/backends/
  local/
  tidal/       # oauth, api, playback
  deezer/      # auth, api, playback
  qobuz/       # api client, config-driven app credentials, session, playback
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
| **v1 (planned)** | User enters in **Settings → Sources → Qobuz**; persisted in `platformdirs` config (e.g. `~/.config/tunes-player/config.json`). Same pattern as apps that use your own API keys (YouTube, TMDb). | Required: username + password → `user_auth_token` stored under the data dir (session file, analogous to TIDAL). |
| **If Qobuz grants Tunes official credentials** | Ship defaults in the app (like LMS today); remove or hide the App ID / Secret fields from Settings. Config may still allow overrides for debugging. | Unchanged — subscribers still sign in with their Qobuz account. |

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

| Provider | Likely dependency | License note |
|----------|-------------------|--------------|
| Tidal | `tidalapi` | LGPL-3.0 — OK inside GPL app |
| Deezer | TBD (REST/client) | Confirm before linking |
| Qobuz | community tooling (if any) | Often GPL/AGPL — may be optional or isolated module |

See [License rationale](#license-rationale).

---

## License rationale

**GPL-3.0-or-later** is intentional:

| Dependency | License | Note |
|------------|---------|------|
| mpv / libmpv | GPLv2+ | Playback engine |
| mutagen (planned) | GPLv2+ | Tags |
| GTK / Libadwaita | LGPL | OK inside GPL app |
| tidalapi (planned) | LGPL-3.0 | OK inside GPL app |
| Deezer client (planned) | TBD | Confirm before linking |
| Qobuz libs (planned) | GPL/AGPL | Strong copyleft if linked; optional module |

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
| Config (planned) | `platformdirs` → e.g. `~/.config/tunes-player/` |

---

## Roadmap (ordered)

1. Local folder scan + SQLite library index.
2. `PlaybackEngine` + `MpvEngine` + queue; GTK transport bar (expanded + minimized compact controller); **MPRIS + media keys**.
3. **Local output (priority):** bit-perfect profile + output device selection + **`VolumeController`** (PipeWire / ALSA endpoint volume). See [Output endpoints](#output-endpoints-planned--not-implemented-yet).
4. **External control interface** — bidirectional sync with external tools; inbound volume from device/DAC/stack → Tunes UI + MPRIS. See [External control interface](#external-control-interface-requirement).
5. DEB package with declared depends (`python3-gi`, `gir1.2-adw-1`, `mpv`, …).
6. **UPnP / DLNA Media Renderer** output — SSDP discovery, push `PlayableSource` URI, transport/volume sync; Settings lists renderers alongside local sinks. Not started until step 3 lands.
7. **Streaming — Tidal** (provider abstraction + OAuth; Settings → Sources).
8. **Streaming — Deezer** (second backend if developer API and playback are viable).
9. **Streaming — Qobuz** (optional; config-driven app credentials — v1 user-supplied in Settings, account login; no bundled or auto-scraped credentials in releases).
10. Federated catalog search (phase A).
11. Heuristic dedup / prefer-local.
12. Optional Qt UI for macOS.
13. Playlists UI (if needed for other users; not required for core browsing workflow).
14. **(Optional)** AES67 / Dante (or Ravenna) LAN output — pro-audio adapter; only if there is clear demand; does not precede local + UPnP.

---

## TODO

Trackable open items. Ordered milestones are in [Roadmap](#roadmap-ordered) above.

### Control / integration

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
