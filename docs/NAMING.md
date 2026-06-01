# Naming — alternatives to “Tunes”

Working name today: **Tunes** (display) / **tunes-player** (package, CLI, repo).
This note collects rename candidates discussed for a future decision. No rename is
scheduled; implementation still uses `tunes-player` everywhere.

## What the name should suggest

- **One player** for local files and (later) streaming backends — not a single merged
  catalog. Sources stay separate; search/playback are federated (see
  [ARCHITECTURE.md](ARCHITECTURE.md#unified-catalog)).
- **FOSS**, GPL, niche GNOME/Linux player — not a commercial store competitor.
- Avoid confusion with **iTunes** / **Apple Music** and with major existing music brands.

## Current name (baseline)

| | |
|--|--|
| **Tunes** | Short, memorable; echoes **iTunes** because of shared `-tunes` in a music app. |
| **tunes-player** | Clear package name; distinct from bare “Tunes”. |

Earlier note: **Music** was avoided because GNOME ships **Music** (`gnome-music`).

## Leading candidates

### Panmelos (preferred direction)

| | |
|--|--|
| **Form** | Greek: *pan-* (all) + *melos* (song, melody). |
| **Pronunciation** | PAN-mel-os |
| **Package (if adopted)** | `panmelos-player` |
| **Fits** | “All sources, one app” without implying one merged library. |
| **Pros** | Distinct; no iTunes echo; not boring textbook Latin; unlikely AllMusic clash. |
| **Cons** | Spelling/pronunciation not obvious; needs a one-line tagline in README/About. |

Example tagline: *Local files and streaming — one player.*

### Allmusic

| | |
|--|--|
| **Form** | English: all + music. |
| **Package (if adopted)** | `allmusic-player` |
| **Fits** | Same “everything in one app” story as Panmelos, in plain English. |
| **Pros** | Immediately understandable. |
| **Cons** | Very close to **[AllMusic](https://www.allmusic.com/)** (major music database since
  1991) — search, trademark, and “are you AllMusic?” confusion. Sounds like metadata,
  not a player. |

## Other candidates (worth keeping on the list)

### English / compound

| Name | Note |
|------|------|
| **Alltunes** | “All sources” story; `-tunes` still near **iTunes**; legacy **allTunes** Windows
  store software exists. |
| **Holoplay** | “Whole play” / holistic playback; no Latin; no iTunes echo. |
| **Sourcebox** | Emphasizes sources; box/library metaphor; generic English. |
| **Altunes** | Alternative tunes; still `-tunes`. |
| **Detune** / **Detunix** | Audio term + “de-iTunes” joke; slightly negative connotation. |
| **Bitunes** | Bit-perfect + tunes; nerdy; still `-tunes`. |

### Acronyms (deprioritized)

| Name | Note |
|------|------|
| **YAMP** / **YAMPL** | Yet Another Music Player — crowded name on GitHub/Android. |
| **MYAMPL** / **MIMP** / **MIMUP** | Personal / awkward; not seriously pursued. |

### Latin (-ix / literary)

| Name | Note |
|------|------|
| **Cacofonix** | Asterix bard pun only — not a product name. |
| **Cento** | Patchwork (sources stitched in the UI/search); literate, obscure. |
| **Farrago** | Hotchpotch of sources; cheeky. |
| **Vates** | Bard; short, mythic. |
| **Tessera** | Mosaic tile — each source a piece of one UI. |
| **Rhapsodia** | Stitched song; long. |
| **Fons** / **Omnia** / **Concentus** | Correct Latin; felt too dull for branding. |
| **Sonifex** | *sonus* + *-fex* (like **Pontifex**); good pun — **Sonifex Ltd** (UK pro audio)
  is a hard collision. |
| **Fonifex** / **Omnifex** | Same *-fex* pattern; source-maker / all-maker. |

### Greek (*melos* = melody, song)

| Name | Note |
|------|------|
| **Melos** | Strong root; island name / other uses; alone does not say “combined sources”. |
| **Synmelos** | Together + melody — integration emphasis (stronger than we need if there is
  no merged library). |
| **Holomelos** | Whole + melody — Holoplay-adjacent. |
| **Rhapsomelos** | Stitched + melody; best “many sources” metaphor; long. |
| **Melotheca** | Melody + store/chest — library box. |

## Rejected or low priority

- **iTunes**-adjacent bare **Tunes** — acceptable for now but Apple echo remains.
- **Sonifex** — pun quality high, brand collision with broadcast audio vendor.
- **Allmusic** — meaning good, [AllMusic](https://www.allmusic.com/) collision bad.
- **Fons**, **Omnia**, **Concentus** — fine words, weak product feel.
- **MIMUP**, **YAMPL** — not pursued.

## Decision checklist (when renaming)

1. Search: GitHub, Flathub, web, trademark databases for the short name.
2. Pick **display name** + **package name** (`*-player` suffix matches current pattern).
3. Plan renames: `application_id`, data dir (`~/.local/share/…`), MPRIS identity, desktop
   file, icon name, docs, GitHub repo (optional).
4. README/About: one-line non-affiliation (Apple; streaming providers — already elsewhere).
5. Avoid marketing copy that claims a **single merged library** unless catalog design
   catches up.

## References

- Product table: [ARCHITECTURE.md § Product](ARCHITECTURE.md#product)
- Catalog model (federated, not necessarily one DB):
  [ARCHITECTURE.md § Unified catalog](ARCHITECTURE.md#unified-catalog)
