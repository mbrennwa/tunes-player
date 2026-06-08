# mpv playback work — agent handoff

Handoff context for continuing **GitHub issue [#34](https://github.com/mbrennwa/tunes-player/issues/34)** (mpv IPC disconnect / unexpected quit) and related subprocess-mpv work from **#29**. Last updated 2026-06-08 on branch `devel`.

---

## What this is (and is not)

| Track | Issue | Status on `devel` |
|-------|-------|-------------------|
| **mpv IPC disconnect / silent quit** | [#34](https://github.com/mbrennwa/tunes-player/issues/34) | **Open** — root cause not fixed |
| **Subprocess mpv migration** | [#29](https://github.com/mbrennwa/tunes-player/issues/29) (phases 1–4 merged) | Done; ongoing hardening |
| **GUI freeze on large All Local grid** | [#42](https://github.com/mbrennwa/tunes-player/issues/42) | **Fixed** in `faa17ff` — unrelated to mpv IPC; regression from `908ee31` full-grid play-button resync |
| **Brief 1–2 s audio dropouts** | Not filed separately | **Separate failure mode** from IPC death — see below |

**Owner constraint (issue #34 comment):** Do **not** ship reconnect/retry workarounds that merely cope with a dead mpv. Find **why mpv exits** and fix that trigger.

---

## User environment

- **Repo / branch:** `devel-work.git`, branch `devel` (pushed; includes `faa17ff`, `c802c9a`)
- **Run app:** `.venv/bin/tunes-player` (not a system install)
- **Data dir:** `~/.local/share/tunes-player/`
  - App log: `tunes-player.log`
  - mpv log: `mpv-playback.log` (truncated on each mpv start)
  - IPC socket: `mpv-playback.sock`
  - Playback cache: `playback-cache/` (NAS files staged locally)
- **Library:** ~1300 local releases on NAS mount (`music_gringotts/…`)
- **Outputs used:** Pulse/HDMI and USB direct ALSA
- **OS:** Debian, systemd 257

---

## Architecture (post-#29)

Playback uses a **subprocess mpv** with JSON IPC, not in-process libmpv.

```
PlayerService (tunes_player/core/services.py)
  └─ MpvPlaybackClient (tunes_player/engines/playback_client.py)
       └─ /usr/bin/mpv --idle=yes --input-ipc-server=…/mpv-playback.sock …
```

Key flows:

- **First track:** `_build_playlist_worker()` → `_build_prepared_track_load()` → `engine.load(…, mode="replace")`
- **Album queue:** same worker appends further tracks via `engine.load(…, mode="append")` (~1/s for TIDAL stream resolution)
- **Engine lifecycle:** dedicated owner thread + `_engine_init_lock`; `_ensure_engine()` / `_reset_engine_unlocked()` / `_terminate_stale_mpv_instances()`
- **NAS paths:** `network_playback_cache.py` — `resolve_playback_target()` is non-blocking; `schedule_playback_cache_warmup()` copies in background; play may use NAS path until cached

Commits that matter:

| Commit | What |
|--------|------|
| `6096242`…`8a25f2c` | #29 phases 1–4: subprocess mpv, drop libmpv |
| `908ee31` | Load-time UI sync (`_playback_load_active`), engine owner thread — **also regressed grid play-button sync to O(n)** (fixed in #42) |
| `4eb0c1b` | mpv-native playlist queue |
| `c802c9a` | mpv file logging + disconnect diagnostics (#34) |
| *(this commit)* | Targeted provenance logging — quit/reset/stale-kill caller chains, process snapshot on disconnect, mpv log archive (#34 phase 2) |

See `docs/ARCHITECTURE.md` for playback-process design notes.

---

## Issue #34 — problem statement

mpv sometimes **closes IPC** (or exits) without kernel-visible crash. Tunes loses control: audio stops, queue may not advance, **no user-facing error** — only WARNING logs.

Typical log lines:

```
WARNING tunes_player.engines.playback_client: mpv IPC disconnected before subprocess exit (pid=…)
WARNING tunes_player.core.services: Could not append … to mpv playlist
OSError: mpv IPC connection closed
```

**Not proven TIDAL-specific.** Observed during TIDAL album append bursts, mpv prewarm, and NAS `.m4a` album playback.

### Best-documented incidents

**1. TIDAL album build (~11:02, pre-mpv-logging)**

- First track plays; background worker appends ~13 tracks to mpv playlist
- IPC closes; append fails for remaining tracks
- User switches album → new mpv subprocess

**2. NAS album / HDMI (~15:51, with mpv log — strongest evidence)**

- Eagles *Hotel California* on NAS, Pulse/HDMI, ~76 s into track (not natural EOF)
- mpv pid 122077, uptime ~376 s
- **No append failures**, **no engine restart** after disconnect
- mpv log tail:

  ```
  [ 376.280][v][cplayer] finished playback, success (reason 3)
  [ 376.280][i][cplayer] Exiting... (Quit)
  ```

  `reason 3` = `MPV_END_FILE_REASON_QUIT` — clean **quit**, not segfault/error.

- Same session: **duplicate** `Starting subprocess mpv` at 15:45:38 (same second) → multi-process / race suspect

### Ruled out (for IPC disconnect)

- Segfault / OOM / systemd kill (empty `journalctl` / `dmesg` around 11:02 incident)
- Intentional `engine.quit()` on the disconnecting client (`_shutdown=True` suppresses the warning)
- TIDAL-only or DASH-only trigger (NAS `.m4a` reproduces same class)

### Leading hypotheses (ranked)

1. **Multiple `tunes-player` processes** — no single-instance lock; shared socket `$DATA_DIR/mpv-playback.sock`; `_terminate_stale_mpv_instances()` SIGTERMs mpv on another binding → first process logs IPC disconnect with `_shutdown=False`.
2. **Orphaned `MpvPlaybackClient`** — stale reader in process-global `_LIVE_CLIENTS` weak set after engine replacement race.
3. **Rapid `loadfile append` during album build** — plausible for TIDAL; less likely for 15:51 NAS case (no append errors logged).
4. **`--keep-open=no` + partial playlist** — downgraded; mid-track quit at 15:51 argues against natural EOF.

### Symptom gap (not root cause)

When IPC dies, `_ensure_engine()` is **not** called automatically; playback stays dead silently until user triggers a new load. Fixing that is observability/recovery, **not** the #34 fix the owner wants first.

### Separate: brief audio dropouts (~1–2 s, self-recovering)

Probably **not** IPC death (progress bar may keep moving; mpv still alive). Plausible causes:

- TIDAL `.mpd` classified as `local` in `buffer_policy.py` → aggressive demuxer settings while HTTPS still streams inside manifest
- USB direct ALSA `ao-reload` under load (`alsa_playback.py`)
- Pulse glitches

Diagnose separately; do not conflate with #34.

---

## Diagnostic tooling on `devel` (#34)

### Phase 1 — `c802c9a`

| Piece | Location |
|-------|----------|
| mpv `--log-file` | `tunes_player/core/playback/mpv_logging.py` |
| Log truncate on start | `prepare_mpv_log_file()` |
| Optional verbosity | env `TUNES_MPV_MSG_LEVEL` → `--msg-level=…` |
| Disconnect snapshot | `playback_client._log_ipc_disconnect_diagnostics()` — pid, playing, playlist pos/count, loaded URI, **last 20 lines of mpv log** |
| Tests | `tests/test_mpv_logging.py` |

### Phase 2 — targeted provenance

| Piece | Location | When it logs |
|-------|----------|--------------|
| Caller chain helper | `format_action_provenance()` in `mpv_logging.py` | — |
| Process snapshot | `describe_process_snapshot()` — `pgrep -f 'bin/tunes-player$'`, `pgrep -x mpv` | Unexpected IPC disconnect |
| Log preservation | `archive_mpv_log()` → `mpv-playback-disconnect-<timestamp>.log` | Unexpected IPC disconnect |
| mpv subprocess ready | `playback_client._start_process()` | After IPC connect |
| Intentional quit | `MpvPlaybackClient.quit()` | Every `engine.quit()` |
| Stale mpv SIGTERM | `_terminate_mpv_matching()` | Stale cleanup |
| Engine reset | `_reset_engine_unlocked()` | Engine teardown |
| Replace dead engine | `_ensure_engine_locked()` | Unavailable engine replaced |
| Engine creation | `_create_playback_engine()` | New `MpvPlaybackClient` |

Intentional shutdown: `MpvPlaybackClient.quit()` sets `_shutdown=True`; `_mark_ipc_disconnected()` **suppresses** disconnect warning when shutdown was requested.

### Live log verification (2026-06-08)

**Verified in production** (`~/.local/share/tunes-player/tunes-player.log`):

```
mpv subprocess ready pid=148937 client_id=140678410739328 socket=…/mpv-playback.sock …
Created playback engine engine_id=140678410739328 socket=… 
  (_create_playback_engine … <- _prewarm_playback_engine_worker …)
mpv client quit requested mpv_pid=148133 … 
  (quit … <- shutdown(services.py:1825) <- do_shutdown(app.py:1302) …)
```

- `engine_id` matches `client_id` — use to correlate disconnect vs live engine.
- App restart at 07:24:39 logged intentional quit; **no** spurious `IPC disconnected` after it.

**Not yet seen in production** (no unexpected IPC drop since phase 2):

- `mpv IPC disconnect process snapshot`
- `Preserved mpv log for disconnect diagnosis`
- `Resetting playback engine` / `Replacing unavailable playback engine`
- `Terminating stale mpv … (caller chain)`

Jun 7 disconnects (15:51 Eagles, etc.) used **phase 1 only** — tail but no snapshot/archive/provenance.

### Orphan mpv false alarm (2026-06-08)

Two mpv processes were observed briefly; the extra one was **not** Tunes failing to stop mpv:

```
bash → python (hung agent integration test in /tmp/tunes-mpv-log-test-…)
  └─ mpv on /tmp/…/test-mpv.sock
```

Parent Python **never exited** (test hung), so `@atexit _quit_live_mpv_clients()` and `client.quit()` never ran. Tunes mpv was correctly parented:

```
tunes-player → mpv on ~/.local/share/tunes-player/mpv-playback.sock
```

Tunes also sets **`PR_SET_PDEATHSIG`** (`_configure_mpv_child_process`) so mpv gets SIGTERM when its spawning process exits. Orphans from Tunes would imply parent hung/crashed without exiting, or `quit()` failed — not normal app quit (which logs `mpv client quit requested`).

**Do not** count stray mpv from agent tests or hung scripts as #34 evidence. Use socket path to distinguish (`mpv-playback.sock` vs `/tmp/…`).

---

## Investigation plan (next agent)

Priority order — **fix trigger, not symptom**:

1. ~~Add mpv file logging~~ — **done** (`c802c9a`)
2. ~~Log quit provenance~~ — **done** — caller chains on quit/reset/stale-kill
3. ~~Preserve mpv log on disconnect~~ — **done** — `archive_mpv_log()`
4. **Single-instance guard or per-process socket** — prevent two apps sharing one mpv socket; or detect + warn loudly
5. **Controlled repro** — deliberate dual `tunes-player` launch; NAS album + cache staging; TIDAL append burst
6. **Root fix** — only after trigger confirmed

### Commands at failure time

```bash
pgrep -af 'bin/tunes-player$'   # >1 line → multi-process suspect
pgrep -x mpv
ls -lt ~/.local/share/tunes-player/mpv-playback-disconnect-*.log 2>/dev/null | head
tail -40 ~/.local/share/tunes-player/tunes-player.log
tail -60 ~/.local/share/tunes-player/mpv-playback.log
```

Look for lines **immediately before** `IPC disconnected`:

- `mpv client quit requested` → intentional Tunes quit (compare `client_id`)
- `Resetting playback engine` → engine reset path
- `Terminating stale mpv` → stale cleanup / second instance suspect
- *(none of the above)* → external quit or mpv self-exit; check archived mpv log

### Out of scope (do not treat as #34 fix)

- Auto engine recreate + playlist rebuild on IPC loss
- Retry append loop on `OSError: mpv IPC connection closed`
- Any change whose purpose is to **cope with** dead mpv rather than **stop mpv dying**

Review existing `load()` retry on disconnect — may mask root cause during initial load.

---

## Key source files

| File | Role |
|------|------|
| `tunes_player/engines/playback_client.py` | IPC read loop, `_mark_ipc_disconnected`, `_start_process`, `_terminate_stale_mpv_instances`, `_LIVE_CLIENTS`, `quit()` |
| `tunes_player/core/services.py` | `_build_playlist_worker` (append loop), `_ensure_engine_locked`, `_reset_engine_unlocked`, `_playback_load_active`, `_playback_target_for_engine` |
| `tunes_player/core/playback/mpv_logging.py` | Log path, CLI args, tail, provenance, snapshot, archive |
| `tunes_player/core/playback/mpv_cli.py` | mpv argv construction |
| `tunes_player/core/playback/buffer_policy.py` | Demuxer/cache policy; `.mpd` → `local` class (dropout suspect) |
| `tunes_player/core/playback/network_playback_cache.py` | NAS staging (background; not main-thread blocker) |
| `tunes_player/platform/linux/alsa_playback.py` | Direct ALSA, `ao-reload` notes |
| `tunes_player/platform/linux/mpv_cleanup.py` | `terminate_mpv_using_audio_device()` (USB contention) |
| `tests/test_mpv_logging.py` | Logging helpers |
| `tests/test_playback_ipc.py` | IPC protocol |
| `tests/test_network_playback_cache.py` | Cache behavior |

---

## Related draft / issue body

- `.github/mpv-ipc-disconnect-issue-body.md` — full issue #34 body draft (largely synced to GitHub)
- GitHub issue #34 has owner comments reinforcing **root-cause-only** approach

---

## Recent session notes (avoid red herrings)

**GUI freeze (#42)** was mis-diagnosed initially as sync grid populate / scan_progress / ALSA volume. Logs showed startup ~1.5s for 1300 releases. **Actual regression:** `908ee31` replaced incremental play-button sync (`908d1c5`) with full-grid O(n) resync on every service event. Fixed in `faa17ff` by restoring `_last_art_playing_release_id` + `is_release_playing()` — **not an mpv issue**.

**Play click freeze (pre-#42-fix):** log stopped at `Staging network file for playback` — background NAS copy; main-thread freeze was from full-grid button sync, not staging.

**Do not revert** `908ee31` load-active playback semantics when touching grid sync — use `is_release_playing()`, not `state.is_playing` alone.

---

## Tests / dev commands

```bash
cd devel-work.git
.venv/bin/python -m unittest tests.test_mpv_logging -q
.venv/bin/tunes-player
```

---

## Suggested first steps for new agent

1. Read issue [#34](https://github.com/mbrennwa/tunes-player/issues/34) and owner comment on workarounds.
2. On next **unexpected** IPC disconnect, read `tunes-player.log` for quit/reset/stale lines **before** the disconnect warning; check `mpv-playback-disconnect-*.log`.
3. Reproduce **dual-process** hypothesis: two `.venv/bin/tunes-player` instances, watch logs.
4. Only then propose a structural fix (single-instance lock or per-process socket namespace).

**Root cause status:** still **unconfirmed**. Best hypothesis remains shared socket + multi-process or undlogged quit sender. Phase-2 logging is meant to answer “who sent quit?” on the next incident.
