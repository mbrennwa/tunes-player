## Summary

mpv sometimes drops its JSON IPC socket without a kernel-visible crash. Tunes loses control of playback (audio stops, queue may stop advancing) and only logs warnings — no user-facing error. This is **not proven TIDAL-specific**; logs show it during album/playlist builds (often TIDAL) but also during prewarm and NAS/local playback. Observed on both USB direct ALSA and Pulse/HDMI output.

**Goal:** understand **why mpv closes IPC** and fix that trigger — not add reconnect/retry workarounds that cope with a dead mpv.

## Symptoms

```
WARNING tunes_player.engines.playback_client: mpv IPC socket closed before subprocess exit
WARNING tunes_player.core.services: Could not append … to mpv playlist
OSError: mpv IPC connection closed
```

User-visible: playback stops unexpectedly; later queue tracks may never play.

Append-failure lines in the log so far are all `tidal:track:…`, but `_build_playlist_worker()` is source-agnostic — that likely reflects usage patterns in the log, not a TIDAL-only code path.

## Related but separate: brief audio dropouts (1–2 s)

Sometimes playback glitches for a second or two and **resumes on its own**, with **no terminal output**. This is **probably a different failure mode** from IPC disconnect:

| | Brief dropout (resumes) | IPC disconnect (this issue) |
|---|---|---|
| Audio | Gap, then continues | Stops; queue often broken |
| mpv IPC | Still alive | Dead |
| Typical cause | Buffer underrun, HTTPS segment stall, Pulse glitch | mpv closes IPC socket silently |

Plausible dropout causes (not confirmed for every report):

- **TIDAL `.mpd` on Pulse/HDMI:** cached manifest paths are classified as `local` → `demuxer_readahead_secs=1`, `cache=no` while audio is still fetched over HTTPS inside the manifest.
- **USB direct ALSA:** `ao-reload` under load (code comment in `alsa_playback.py`); stall recovery logs at WARNING when triggered — only one such event in the log so far.
- **ALSA xruns:** would log `ALSA xrun counter increased` at WARNING — **zero** such lines in `tunes-player.log`.

If the progress bar keeps moving through a silent gap → likely dropout issue, not IPC death. Dropouts may share the same playback stack but need **separate diagnosis** (buffer policy, streaming path, USB scheduling) — not IPC reconnect logic.

## Reproduction pattern (from `~/.local/share/tunes-player/tunes-player.log`)

**Best-documented case — TIDAL album build (2026-06-07 ~11:02):**

1. User starts an album; first track loads and plays.
2. Background `_build_playlist_worker` prepares and appends subsequent tracks to mpv (~1/sec for TIDAL stream resolution).
3. After ~13 successful appends, mpv IPC closes silently.
4. Append fails for remaining tracks on a dead engine reference.
5. User switches album or retries → new mpv subprocess starts.

| Time | Event |
|------|--------|
| 11:01:55 | First track loaded (`tidal_track_68669545.mpd`) |
| 11:01:56–11:02:07 | Tracks 68669546–68669558 prepared/appended |
| 11:02:08 | `mpv IPC socket closed before subprocess exit` |
| 11:02:09–10 | Append fails for 68669559, 68669560 |
| 11:02:19 | New mpv started (user changed album) |

**Other IPC disconnects in the same log (not all album-append scenarios):**

| Time | Context before disconnect |
|------|---------------------------|
| 23:56:48 | mpv prewarm only — no playback started |
| 00:21:15 | NAS network staging (`music_gringotts/…`) — no TIDAL nearby |
| 00:28:22 / 00:28:25 | TIDAL album append burst |
| 00:40:25 / 00:42:47 | TIDAL playback; no append-failure burst logged |
| 01:12:43 | Single TIDAL tracks (~2 min apart), not album build |
| 11:05:31 | TIDAL album replay, ~3 min after prior restart |

## What we ruled out

System logs around **11:01:50–11:02:15** (with sudo):

```bash
sudo journalctl --since "2026-06-07 11:01:50" --until "2026-06-07 11:02:15" | grep -iE "mpv|segfault|killed|core|trap"
sudo dmesg -T | grep -iE "mpv|segfault|traps|killed process"
```

Both returned **empty** → no segfault, OOM kill, or systemd kill logged.

Also absent in **early** app log at failure time (before mpv file logging):

- No `mpv:` stderr lines (entire log had zero mpv stderr despite stderr drain at WARNING)
- No `mpv end-file reason=error`
- No `Terminating stale mpv process` (SIGTERM stale kill — different from IPC `quit`)

**Note:** Intentional Tunes `engine.quit()` sets `_shutdown=True` and **suppresses** the IPC disconnect warning. Incidents that **do** log `mpv IPC disconnected` were **not** preceded by a normal `_reset_engine()` on the same client.

## Diagnostic logging (implemented on `devel`)

- `tunes_player/core/playback/mpv_logging.py` — `--log-file=$DATA_DIR/mpv-playback.log`, optional `TUNES_MPV_MSG_LEVEL`, log truncated on each mpv start
- `playback_client._log_ipc_disconnect_diagnostics()` — PID, `playing`, playlist pos/count, loaded URI, mpv log tail (20 lines)

## Confirmed incident with mpv log — NAS album / HDMI (2026-06-07 ~15:51)

| Field | Value |
|-------|--------|
| **Output** | Pulse/HDMI |
| **Source** | NAS — *Eagles / Hotel California* (`01 Hotel California.m4a`, direct NAS path, `network_file` + `cache=yes`) |
| **mpv pid / uptime** | 122077 / ~376 s (started 15:45:38 on Bruce Springsteen after USB→HDMI switch) |
| **Time on Eagles** | ~76 s (album switch 15:50:39) |
| **Tunes at disconnect** | `playing=False`, `playlist_pos=0`, `playlist_count=9`, `returncode=None` |
| **Append failures** | None |
| **Engine restart** | **None** after disconnect |

**mpv log tail:**
```
[ 376.280][v][cplayer] finished playback, success (reason 3)
[ 376.280][i][cplayer] Exiting... (Quit)
```

`reason 3` = `MPV_END_FILE_REASON_QUIT` — mpv shut down via **quit**, not segfault and not `reason=error`.

**Same session anomalies:** duplicate `Starting subprocess mpv` at **15:45:38** (same second, same track); heavy NAS cache eviction/staging throughout.

**Confirms:** NAS `.m4a` album triggers the same IPC-loss class as TIDAL — **not DASH-only**.

## Root cause analysis

**Primary failure:** mpv closes its IPC connection unexpectedly. With file logging, the best-documented case shows a clean **`Exiting... (Quit)`** — mpv process exit, not a silent socket drop with mpv still running.

**Leading hypotheses (ranked after code review):**

1. **Multiple `tunes-player` processes (strong suspect)** — there is **no single-instance lock**. Every process uses the same IPC socket (`$DATA_DIR/mpv-playback.sock`). On engine creation, `_start_process()` → `_terminate_stale_mpv_instances()` **SIGTERM**s any other mpv bound to that socket. A second process starting an engine would kill the first process’s mpv; the first client’s IPC reader logs disconnect with `_shutdown=False` (matches 15:51:55). Duplicate `Starting subprocess mpv` lines at **15:45:38** (same second) fit two concurrent engine creations. **Confirm at failure time:** `pgrep -a tunes-player` and `pgrep -a mpv`.

2. **Orphaned `MpvPlaybackClient`** — if `_engine` is replaced without `quit()` on the old client (or a client outlives its mpv), a stale reader can emit the IPC warning for a pid the live `_engine` no longer owns. Related to (1) if two processes or a creation race leave stray clients in the process-global `_LIVE_CLIENTS` weak set.

3. **Rapid `loadfile … append` during album build** — observed in TIDAL cases (`Could not append …` burst at 11:02). Same `_build_playlist_worker()` for NAS; at 15:51 **no append failures** were logged, so this is less likely for that incident but remains plausible for TIDAL cases.

4. **`--keep-open=no` + partial mpv playlist (downgraded)** — with multiple entries in mpv’s **internal** playlist, track 1 EOF should advance to track 2, not exit. At 15:51 the track had played ~76 s (not full length), so natural EOF is unlikely. `playlist_count=9` in the disconnect diagnostic is **Tunes-side** state (`max(_playlist_count, len(_playlist_uris))`), not proof mpv had 9 entries at quit. Pre-truncation mpv log needed to confirm.

**Not proven:** TIDAL-only or DASH-only trigger. **Ruled out for 15:51:** intentional `_reset_engine()` on the disconnecting client (`_shutdown=True` suppresses the warning). **Ruled out:** normal last-track EOF with full playlist in mpv (would advance, not quit mid-album).

### Why playback stays silent after disconnect

When IPC dies, `MpvPlaybackClient.is_available()` returns false (`_running=False`, `_sock_file=None`), but **nothing proactively recreates the engine** on disconnect alone. `_ensure_engine()` runs only on the next load/play path. If the user does not press play again, playback remains dead with no toast — matching 15:51 (cache staging continued, no `Starting subprocess mpv`). This is an **observability/recovery gap** (symptom), not the root cause of mpv exit.

### Separate failure: Qobuz stream open error (2026-06-07 ~13:08)

Not this issue. `mpv end-file reason=error: unrecognized file format` on a Qobuz HTTPS URL (ffmpeg `Stream ends prematurely at 1`) → `playback_error` → USB `ao-reload` recovery. Different path from IPC disconnect.

**Remaining observability gaps:**

| Gap | Effect |
|-----|--------|
| No log of **who sent quit** | Cannot distinguish Tunes bug vs mpv self-exit vs external IPC client |
| IPC disconnect → no `_ensure_engine()` retry | Playback stays dead silently after unexpected mpv exit |
| mpv log truncated on each restart | Pre-disconnect context lost if new mpv starts |

## Investigation plan (fix the trigger, not the symptom)

1. ~~**Add mpv file logging**~~ — done on `devel`.
2. **Log quit provenance** — stack trace or caller tag in `engine.quit()` / `_reset_engine_unlocked()`; log when `_terminate_stale_mpv_instances()` kills a pid.
3. **Single-instance guard or per-process socket** — prevent two `tunes-player` processes from sharing one mpv socket (or detect and warn).
4. **Preserve mpv log on disconnect** — copy log before truncate on restart so pre-quit context is not lost.
5. **Reproduce under control** — run two instances deliberately; NAS album with concurrent cache staging; TIDAL append burst.
6. **Fix the root** — only after trigger identified. Prevention, not recovery.

### Diagnostic commands at failure time

```bash
pgrep -a tunes-player    # more than one line → multi-process suspect
pgrep -a mpv
tail -30 ~/.local/share/tunes-player/tunes-player.log
tail -50 ~/.local/share/tunes-player/mpv-playback.log
```

## Out of scope (workarounds — do not treat as the fix)

- Engine recreate + playlist rebuild on IPC loss
- Retry append loop on `OSError: mpv IPC connection closed`
- Any change whose purpose is to **cope with** a dead mpv rather than **stop mpv from dying**

Existing `load()` retry on disconnect may mask the problem during initial load and should be reviewed in light of root-cause work.

## How to confirm scope

Reproduce with a **local or NAS album** (no TIDAL) of similar track count. If IPC drops there too, the bug is generic playlist/mpv stress. If only `.mpd`/streaming manifests fail, narrow to DASH demuxer path (TIDAL and Qobuz).

For **dropouts** (separate track): note output device and whether the progress bar keeps moving; check `tunes-player.log` for xrun/stall recovery lines.

## Environment (example incident)

- mpv via Pulse/HDMI: `--ao=pipewire,pulse,alsa,sndio --audio-device=pulse/Raptor Lake-P/U/H cAVS HDMI / DisplayPort 1 Output`
- Example used TIDAL LOSSLESS with local `.mpd` cache files
- Debian, systemd 257, no `systemd-coredump` installed

## Related code

- `tunes_player/engines/playback_client.py` — `_read_loop()`, `_mark_ipc_disconnected()` (warning only if `not _shutdown`), `quit()`, `_start_process()`, `_terminate_stale_mpv_instances()`, `_LIVE_CLIENTS` / `@atexit _quit_live_mpv_clients`
- `tunes_player/core/services.py` — `_build_playlist_worker()` (append loop ~L2705), `_ensure_engine_locked()`, `_reset_engine_unlocked()`, `_rebuild_engine_for_output_change()`
- `tunes_player/core/playback/mpv_logging.py` — `--log-file`, disconnect log tail
- `tunes_player/core/playback/buffer_policy.py` — `.mpd` paths classified as `local` (relevant to dropout investigation)
- `tunes_player/platform/linux/alsa_playback.py` — `ao-reload` dropout note (USB path)
- `tunes_player/platform/linux/mpv_cleanup.py` — `terminate_mpv_using_audio_device()` (USB contention; not HDMI path)
