# Marquee.tv Redesign — Design Spec

Date: 2026-08-13

## Summary

Rebrand and rebuild TwitchTV as **Marquee.tv**: a Textual-based TUI redesign, plus three new pieces of functionality — ad-hoc stream watching outside the priority list, an in-place `nvim` editor for the priority list, and Chatterino tab auto-switching. Includes a live MPV window title that stays in sync as streamers change category/title mid-stream.

## Current architecture (unchanged parts)

- `twitch_tv.py` — background daemon. Polls Twitch API (`twitch api get /streams/followed`) every 60s, launches `streamlink`+`mpv` for the highest-priority live streamer, auto-switches after a 10-minute grace period when a higher-priority stream goes live, writes `.status.json`, reads `.control` for switch signals.
- `twitchtv_ui.py` — curses TUI. Displays priority list, reads `.status.json`, writes `.control` to request switches, controls daemon start/stop via `switchtv.sh`.
- `switchtv.sh` — entrypoint (`start`/`stop`/`status`/`now`/`watch`/`log`/default-to-UI).
- `streamers.txt` — priority list, one `username` or `username|Nickname` per line, `#` comments.
- `twitchtv.service` — systemd user unit.

This spec **keeps the daemon/UI split and the file-based IPC pattern** (`.status.json`, `.control`) — it extends both rather than replacing the architecture.

## 1. Rename: TwitchTV → Marquee.tv

Full rename, including files and the systemd unit:

| Old | New |
|---|---|
| `twitch_tv.py` | `marquee_daemon.py` |
| `twitchtv_ui.py` | `marquee_ui.py` |
| `switchtv.sh` | `marquee.sh` |
| `twitchtv.service` | `marquee.service` |

- `marquee.service`'s `ExecStart` updated to the new daemon path.
- `README.md` updated throughout (name, commands, screenshots-in-prose).
- `.status.json`, `.control`, `streamers.txt`, `.log` keep their names — internal/config files, no user-facing rename needed.
- Out of scope: renaming the local working directory or the eventual GitHub repo — that's the user's call at publish time. The implementation plan should call out, as a manual post-merge step, that the user needs to: re-run `systemctl --user disable twitchtv && systemctl --user enable marquee`, and update their shell alias from `switchtv` to whatever they alias `marquee.sh` to.

## 2. Architecture change: daemon as single source of truth for stream data

**Problem:** daemon and UI currently each poll the Twitch API independently every 60s. Duplicate calls, and neither persists any history — which blocks the "last seen" requirement (§4).

**Change:**
- `marquee_daemon.py`'s `save_status()` is extended to also write the full `live_streams` dict (per streamer: `title`, `game`, `viewers`, `started_at`) into `.status.json`.
- `marquee_daemon.py` gains a new `.last_seen.json`: on every poll where the daemon is running, for every streamer currently in `live_streams`, write `{streamer: <iso timestamp of this poll>}`. This is the "last confirmed live" timestamp, updated continuously while live, frozen at the last value once they go offline.
- `marquee_ui.py` **stops** polling the Twitch API directly when the daemon is running — it reads `live_streams` and status straight from `.status.json`, and reads `.last_seen.json` for offline-row display.
- **Fallback:** if the daemon is not running (`x` was pressed, or it hasn't been started yet), `marquee_ui.py` falls back to its own direct Twitch API polling (same 60s cadence, same code path it has today) so the list still functions standalone. Last-seen data simply won't update while in this fallback state, since only the daemon writes `.last_seen.json`.
- **Exception:** this consolidation applies to the priority-list polling only. A one-shot ad-hoc stream (§4) is, by design, untracked by the daemon — the UI runs a separate, narrowly-scoped poll loop just for that single streamer regardless of whether the daemon is running, since the daemon has no way to know about it.

## 3. Daemon changes (`marquee_daemon.py`)

### Hot-reload `streamers.txt`
Each loop tick (existing 10s `CHECK_INTERVAL`), check the file's mtime. If changed, re-run `load_priority_list()`. No restart required.

### Chatterino tab sync
Replace the current "launch Chatterino only if not already running" logic in `launch_stream()`: **always** call `chatterino -a <streamer>` after launching a stream, to activate (or create) a tab for that channel.

**Revised after testing (this system's Chatterino build, 2.5.5-9):** `chatterino -a <channel>` does **not** activate a tab in an already-running window — it spawns an entirely separate new process/window every time, leaving old ones open. There's no tab-activation IPC available on this build. The adopted workaround: `pkill -x chatterino` any existing instance first, then launch fresh with `-a`. Chatterino persists its open tabs across restarts and restores them on next launch, so this still achieves "switch to this channel's tab" from the user's perspective (all previously-viewed channels' tabs are still there, with the new one now activated) without ever having two windows open simultaneously.

### Ad-hoc control protocol
`.control` file format extends from `switch:<streamer>` to `switch:<streamer>:<mode>`, where `<mode>` is `override`, `temporary`, or `oneshot`.

- **`override`**: daemon launches the stream exactly like a normal switch, but sets a new `manual_override: bool` flag. While set, the auto-switch comparison block (`if highest_priority != self.current_stream...`) is skipped entirely — no grace-period countdown will fire. The flag clears the next time the user switches to anything else (from the list or a new ad-hoc request).
- **`temporary`**: daemon launches the stream exactly like a normal switch, **no new logic**. Since the ad-hoc streamer isn't in `priority_list`, the existing grace-period auto-switch logic already takes over automatically the moment a real priority-list streamer goes live — this mode falls out of the current design for free.
- **`oneshot`**: daemon does nothing with this signal — the UI handles the entire lifecycle itself (§4), spawning an independent `streamlink`/`mpv` process the daemon never knows about. No audio ducking or muting (explicit user call — manual volume management).

### `started_at` parsing
`get_live_streams()` captures the `started_at` field from the Twitch API response (already present in the API's stream object, just not currently parsed) and includes it in each streamer's info dict, so uptime can be computed as `now - started_at`.

## 4. UI (`marquee_ui.py`, rebuilt in Textual)

Textual is the framework: closest Python analog to Ratatui (reactive widgets, CSS-like styling), and lets `marquee_daemon.py` stay completely untouched apart from the changes in §3.

### Layout
Outer bordered "Marquee.tv" window. Inside it, top to bottom:
1. **NOW WATCHING** box (own inner border), full width of the outer window.
2. **PRIORITY LIST** box (own inner border), slightly narrower than the NOW WATCHING box — creating margin on both sides for the breakout effect below.

### NOW WATCHING header
Fixed **3 lines**, always — height never changes regardless of state (idle/live/ad-hoc):
- **Line 1**: streamer name/nickname (left-justified) ... viewer count (right-justified) ... live/offline indicator, 🟢 or 🔴, no text label (far right).
- **Line 2**: category (left-justified) ... uptime, computed from `started_at` (right-justified).
- **Line 3**: stream title, hard-truncated to terminal width — no scrolling/marquee text.
- When idle: line 1 reads "No stream active", lines 2–3 stay blank.
- Border label reads `NOW WATCHING` normally, or `NOW WATCHING (ad-hoc · override|temporary|oneshot)` when watching an ad-hoc stream.

### Priority list rows
- **Collapsed row**: name (left) ... category (fixed middle column) ... viewer count (right, left of indicator) ... 🟢/🔴 indicator (far right). Same column order as the header, for visual consistency.
- **Highlighted row**: rendered bold, on a solid lavender background bar (Catppuccin Mocha lavender, matching the rest of the app's existing color language), **always this color regardless of live/offline status** — the indicator dot alone communicates live state. The bar visually "breaks out" past the Priority List box's own left/right border, widening to match the outer window's width, for exactly the highlighted row's line(s).
- **Highlighted + live**: expands to a second line underneath (also part of the breakout bar): the full stream title + uptime, same hard-truncation rule as the header.
- **Highlighted + offline**: expands to a second line: `last live: <relative time>`, computed from `.last_seen.json`. If a streamer has no entry in that file yet (never observed live since the daemon started tracking), show `last live: unknown`.
- Non-highlighted offline rows never expand.

### Footer hotkey glossary
A single line at the bottom of the outer "Marquee.tv" window (inside its border, below the Priority List box — same footer-bar style the old curses UI used), always visible:

```
(Q)uit  (S)tart  (X)Stop  (E)dit  (/)Ad-hoc  (I)nfo  ↑↓/jk Navigate  ⏎ Launch
```

### Navigation and keybindings
| Key | Action |
|---|---|
| ↑/↓ or j/k | Move highlight |
| Enter | Launch highlighted stream immediately — no confirmation, no number-entry |
| `/` | Header box becomes a text-entry field (replaces its normal content). Enter submits the typed name and shows an inline `[O]verride  [T]emporary  [1] One-Shot` picker in the same box. Picking a mode launches the ad-hoc stream (§3/§5) and the header reverts to normal display. |
| `e` | Suspends the TUI, opens `$EDITOR` (default `nvim` if unset) on `streamers.txt`, resumes the TUI on editor exit. No confirmation. |
| `i` | Opens an overlay showing the full untruncated title of the currently-watched stream plus the channel's bio (`GET /users` `description` field for that streamer — fetched on-demand only when `i` is pressed, never polled in the background). |
| `s` | Start daemon. No confirmation. |
| `x` | Stop daemon (and kill mpv, as today). No confirmation. |
| `q` | Shows a confirm-quit prompt (`Quit and stop daemon? [Enter]/[Esc]`) before stopping the daemon and exiting. |

### One-shot ad-hoc lifecycle (UI-owned)
When the mode picker selects One-Shot, `marquee_ui.py`:
1. Spawns its own `streamlink`/`mpv` subprocess for the given streamer (same `STREAMLINK_ARGS` base as the daemon uses, own `--input-ipc-server` socket — see §6), completely independent of the daemon's tracked process.
2. Does **not** touch the daemon's `.control` file — the daemon's own priority-list stream keeps running untouched in parallel.
3. Runs its own lightweight 60s poll loop (reusing `get_live_streams()`-equivalent logic) for just that one streamer, to feed the live title-update mechanism in §6.
4. When the one-shot mpv window is closed, the UI cleans up its subprocess handle and stops polling for it. No audio muting/ducking of either stream (explicit user call).

## 5. Info panel (`i` key)

Overlay showing:
- Full untruncated stream title (already available from `.status.json`/live poll).
- Channel bio: one `twitch api get users -q login=<streamer>` call, made only when `i` is pressed (not cached across sessions, not polled) — its `description` field. This is the channel's static "About" text, not a per-stream description (Twitch doesn't expose one) — worth noting since it won't change between streams.

## 6. Live MPV title updates

**Mechanism:** `STREAMLINK_ARGS` gains `--input-ipc-server=<socket path>`, giving every launched `mpv` a JSON IPC socket. A shared helper module (`mpv_ipc.py`) sends `{"command": ["set_property", "title", "<value>"]}\n` to a given socket path — used by both the daemon and the UI.

**Socket naming:** per-stream, e.g. `SCRIPT_DIR/.mpv-<streamer>.sock`, so a daemon-tracked stream and a simultaneous one-shot stream never collide.

**Daemon-controlled streams** (priority list + override/temporary ad-hoc): on each 60s API poll, if the current stream's `game` or `title` differs from the last value the daemon saw for it, send an IPC title update (`{author} ::: {game} ::: {title}` format, matching the existing `--title` template) over that stream's socket.

**One-shot streams**: handled by the UI's own poll loop (§4), using the same `mpv_ipc.py` helper and same socket-naming convention, entirely independent of the daemon.

## Out of scope

- `CHECK_INTERVAL`, `GRACE_PERIOD`, and streamlink quality/player args are unchanged.
- No audio ducking/muting between simultaneous streams (one-shot mode).
- No mouse support requirement (Textual provides it, but it's not a design goal here).
- No change to how the Twitch CLI authenticates or how `TWITCH_USER_ID` is configured.
