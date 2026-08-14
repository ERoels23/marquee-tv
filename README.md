# Marquee.tv

A Python daemon + Textual TUI that automatically launches your highest-priority
live Twitch stream and intelligently switches between streams based on your
preferences — plus ad-hoc stream watching, in-place priority-list editing,
Chatterino tab sync, and live MPV title updates.

Marquee.tv is the rebuilt/renamed successor to the old "TwitchTV" curses tool.
The daemon/UI split and file-based IPC are unchanged; the UI is now Textual
instead of curses, and several new features were added (see below).

## Features

- **Priority-based streaming**: define streamers in order of preference in
  `streamers.txt`; the daemon launches the highest-priority currently-live
  stream.
- **Intelligent auto-switching**: when a higher-priority stream goes live
  while you're watching something lower-priority, a desktop notification
  fires and the daemon waits a 5-minute grace period before auto-switching,
  so you can finish what you're watching.
- **Automatic fallback**: when the current stream ends, the daemon
  immediately launches the next highest-priority live stream.
- **Ad-hoc stream watching**: press `/` in the UI to watch any streamer, live
  or not in your priority list, in one of three modes — see below.
- **Edit priority list in-place**: press `e` in the UI to open
  `streamers.txt` in your editor; it reloads automatically on save/exit, no
  restart needed. The daemon also hot-reloads `streamers.txt` on its own if
  you edit it outside the UI.
- **Info overlay**: press `i` to see the full, untruncated stream title plus
  the channel's bio, fetched from Twitch on demand.
- **Chatterino integration**: the daemon (and one-shot streams) automatically
  bring Chatterino to the right channel's tab whenever the stream changes.
  See the note under Troubleshooting — this is currently done by
  closing-and-reopening Chatterino, not a seamless in-place tab switch.
- **Live MPV window title updates**: the mpv window's title bar updates
  automatically if the streamer changes game/category or retitles mid-stream,
  without restarting playback.
- **Low-latency streaming**: uses Twitch low-latency streamlink settings.
- **Desktop notifications**: `notify-send` notifications for upcoming
  auto-switches.

## Requirements

- `streamlink` - Stream launcher
- `mpv` - Video player
- `chatterino` - Twitch chat client
- Twitch CLI (`twitch`) - For checking live streams and channel info
- Python 3 with a virtualenv (the Textual UI needs `textual==8.2.8` — see
  Setup below)
- `jq` - For pretty-printing `marquee.sh status` output (optional, falls back
  to raw JSON)
- `notify-send` - For desktop notifications

## Setup

### 1. Configure your priority list

Edit `streamers.txt` and add your Twitch streamers in order of preference:

```
# Marquee.tv Priority List
northernlion
strippin|Strip
kruzadar
malf
```

Comments (lines starting with `#`) are ignored. One streamer per line, in the
order you want to watch them. Append `|Nickname` after a username to give it
a display name in the UI (the underlying Twitch login is still used for API
calls and launching — only the display changes).

### 2. Authenticate with Twitch CLI

The daemon and UI use the Twitch CLI to check which of your followed
streamers are live (and, for ad-hoc streams, to look up any channel). Make
sure you're authenticated:

```bash
twitch auth login
```

### 3. Set up the Python environment

The UI is built on [Textual](https://textual.textualize.io/). Create a venv
and install it:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

`marquee.sh` and `marquee_ui.py` expect to find Textual in `.venv` (or
whatever interpreter their shebang resolves to) — make sure this step is done
before first launch.

## Usage

### Interactive TUI (recommended)

```bash
# Launch the interactive terminal UI
marquee.sh

# Or explicitly:
marquee.sh ui
```

The TUI is a single bordered "Marquee.tv" window containing:

- **NOW WATCHING** box — streamer name, live/offline indicator, viewer
  count, category, uptime, and stream title for whatever's currently
  playing. Shows "No stream active" when idle. The box's border label reads
  `NOW WATCHING (ad-hoc · override|temporary)` while an Override or
  Temporary ad-hoc stream is playing in this box, so you can always tell at
  a glance whether you're on your normal priority-list rotation or
  something else. (One-Shot streams play in a completely separate mpv
  window, so this box and its label are unaffected — see below.)
- **PRIORITY LIST** box — every streamer from `streamers.txt`, in order,
  with live/offline status, category, and viewer count. The highlighted row
  expands to show the full title + uptime (if live) or `last live: <relative
  time>` (if offline, from `.last_seen.json`).
- A footer with the current hotkey glossary.

**Keybindings:**

| Key | Action |
|---|---|
| `↑`/`↓` or `j`/`k` | Move the highlight up/down the priority list |
| `Enter` | Launch the highlighted stream immediately (no confirmation) |
| `/` | Start ad-hoc stream entry (see below) |
| `Esc` | Cancel ad-hoc entry / mode picker |
| `e` | Edit `streamers.txt` in `$EDITOR` (suspends the TUI, resumes on exit) |
| `i` | Toggle the info overlay (full title + channel bio) for the current stream |
| `s` | Start the daemon |
| `x` | Stop the daemon (and kill mpv) |
| `q` | Quit — press once to arm, press again to confirm (stops the daemon too) |

The footer always shows a short version of this:
`(Q)uit (S)tart (X)Stop (E)dit (/)Ad-hoc (I)nfo ↑↓/jk Nav ⏎ Launch`.

### Ad-hoc stream watching

Press `/` in the UI. The NOW WATCHING box turns into a text field — type any
Twitch username (it does not need to be in your priority list) and press
`Enter`. You'll then be prompted to pick a mode:

- **`O` — Override**: switches to this stream now and pins it. The daemon's
  normal auto-switch logic is suppressed entirely while pinned — it won't
  jump away even if a higher-priority stream goes live. The pin clears the
  next time you switch to anything else (from the list or another ad-hoc
  request).
- **`T` — Temporary**: switches to this stream now, but normal
  priority-list auto-switching behavior still applies — if a priority-list
  streamer you'd normally watch goes live, the usual grace-period switch will
  still happen.
- **`1` — One-Shot**: opens a completely separate mpv window for this
  streamer, entirely untracked by the daemon. Your normal priority-list
  stream (and Chatterino) keeps running untouched in the background, and the
  NOW WATCHING box keeps showing that normal stream unchanged — it does
  *not* relabel itself as ad-hoc, since the one-shot stream lives in its
  own window (identified by its own mpv title bar). Closing the one-shot
  mpv window just ends that one-shot session.

Override and Temporary both go through the daemon's normal `.control`
protocol; One-Shot is handled entirely inside the UI process.

### Editing the priority list

Press `e`. The TUI suspends and opens `streamers.txt` in `$EDITOR` (defaults
to `nvim` if unset). Save and quit the editor to return to the TUI — the list
reloads automatically, no restart needed. This works whether or not the
daemon is running; the daemon also independently hot-reloads `streamers.txt`
if you edit it some other way (e.g. directly, outside the UI) while it's
running.

### Info overlay

Press `i` while watching a stream to see its full, untruncated title and the
channel's bio (Twitch's `description` field — this is the channel's static
"About" text, not a per-stream description, so it won't change between
streams). The bio is fetched fresh from Twitch each time you open the
overlay, not cached or polled in the background. Press `i` again to close it.

### Command-line control

```bash
# Start the daemon (runs in background)
marquee.sh start

# Check current status
marquee.sh status

# Stop the daemon
marquee.sh stop

# Run the daemon in the foreground (for debugging/testing)
marquee.sh watch

# Follow the daemon log
marquee.sh log

# Force an immediate switch to the highest-priority live stream
# (legacy — the UI's Enter-to-launch supersedes this for most use)
marquee.sh now
```

### During a stream

When a higher-priority stream goes online while you're watching something
else:
1. A desktop notification appears.
2. The TUI's live-status row shows it as live immediately.
3. The daemon auto-switches after a 5-minute grace period, unless you're in
   Override mode (see Ad-hoc above) or you switch manually first.
4. If you close mpv, the daemon immediately switches to the next
   highest-priority live stream.

When your current stream ends, the daemon automatically launches the next
highest-priority live stream.

## How it works

1. **Initial check**: the daemon checks which streamers in the priority list
   are currently live (regardless of follow status).
2. **Launch**: launches the highest-priority currently-live streamer via
   `streamlink` + `mpv`, and (re)launches Chatterino pointed at that channel.
3. **Monitoring**: every 10 seconds the daemon checks for control signals
   (`.control`) and reloads `streamers.txt` if it changed; every 60 seconds
   it re-polls the Twitch API for live status (rate-limited separately from
   the 10s loop).
4. **Grace period**: if a higher-priority stream goes online while you're
   watching something lower-priority, the daemon waits 5 minutes before
   auto-switching (unless overridden or switched manually).
5. **Live title updates**: on each 60-second API poll, if the current
   stream's game or title changed, the daemon pushes an updated title to
   mpv's window over a per-stream JSON IPC socket — no restart needed.
6. **Last-seen tracking**: every poll, the daemon records a timestamp for
   every streamer currently live into `.last_seen.json`, so the UI can show
   "last live: X ago" for offline streamers.
7. **UI as a thin client**: while the daemon is running, `marquee_ui.py`
   reads live-stream data and status straight from `.status.json` instead of
   polling Twitch itself. If the daemon isn't running, the UI falls back to
   polling the Twitch API directly on the same 60s cadence, so the list still
   works standalone (last-seen data just won't update in that state, since
   only the daemon writes `.last_seen.json`).
8. **Auto-launch**: when the current stream ends, the daemon launches the
   next highest-priority live stream.

## Configuration

These live as constants near the top of `marquee_daemon.py`:

```python
CHECK_INTERVAL = 10       # seconds between control-signal/reload checks
API_UPDATE_INTERVAL = 60  # seconds between Twitch API polls
GRACE_PERIOD = 300        # seconds (5 minutes) before auto-switching
```

Streamlink/mpv player settings (quality, volume, low-latency flags) are set
in the `player_args`/`cmd` construction inside `launch_stream()` in
`marquee_daemon.py` (and mirrored in `spawn_one_shot()` in `marquee_ui.py`
for one-shot streams).

## Files

- `marquee_daemon.py` - background daemon: polling, auto-switch logic,
  stream launching, Chatterino sync, live title updates
- `marquee_ui.py` - Textual-based interactive TUI
- `marquee.sh` - control script / entry point (`ui`, `start`, `stop`,
  `status`, `now`, `watch`, `log`)
- `marquee.service` - systemd user unit for running the daemon standalone
- `streamers.txt` - priority list (edit this, or press `e` in the UI!)
- `marquee_model.py`, `marquee_render.py`, `mpv_ipc.py`, `priority_list.py`,
  `ui_format.py` - shared/internal modules (UI state machines, pure line
  rendering, MPV JSON-IPC helper, streamers.txt parsing, formatting helpers)
  used by both the daemon and the UI
- `requirements.txt`, `pyproject.toml` - Python dependencies and pytest
  config; `.venv/` - local virtualenv (create with the Setup steps above)
- `tests/` - pytest test suite
- `.status.json` - current status, written by the daemon (auto-generated)
- `.last_seen.json` - last-confirmed-live timestamps per streamer
  (auto-generated)
- `.control` - control file for UI→daemon signals (auto-generated,
  transient)
- `.log` - daemon log file, written by `marquee.sh start` (auto-generated)

## Troubleshooting

### Script not starting streams

Check the log: `marquee.sh log`

Common issues:
- `twitch` CLI not authenticated: run `twitch auth login`
- Streamer not in priority list: edit `streamers.txt` (or press `e` in the
  UI)
- No live streams: check if your streamers are actually live

### Chatterino window flashes / closes and reopens on every switch

This is expected, not a bug. This system's installed Chatterino build has no
way to activate a tab in an already-running window — asking it to open a
channel just spawns a whole new process/window instead of switching the
existing one. To avoid ending up with a pile of stale Chatterino windows, the
daemon (and one-shot streams) kill any running Chatterino instance and
relaunch it fresh on every stream switch. Chatterino restores its
previously-open tabs on startup, so you still end up on the right channel's
tab — you'll just see the window briefly disappear and reappear instead of a
silent in-place switch. If a future Chatterino build supports real tab
activation, this can be revisited.

### Chatterino not opening at all

Make sure Chatterino is installed and in your `PATH`:

```bash
which chatterino
```

### Notifications not showing

Ensure `notify-send` is installed and your desktop environment supports it:

```bash
notify-send "test"
```

### UI fails to launch / `ModuleNotFoundError: textual`

The Textual dependency lives in the project's venv, not system Python. Run
the Setup step 3 above (`python3 -m venv .venv && .venv/bin/pip install -r
requirements.txt`) if you haven't already, and make sure `marquee_ui.py`'s
shebang / `marquee.sh` are resolving to that venv's interpreter.

## Tips

- **Add an alias** to your `.zshrc` or `.bashrc`:
  ```bash
  alias marquee='/path/to/marquee.sh'
  ```

- **Auto-start on login** (systemd user service): a ready-to-use unit is
  already checked in as `marquee.service`. Symlink or copy it into
  `~/.config/systemd/user/`, then enable it:
  ```bash
  mkdir -p ~/.config/systemd/user
  cp marquee.service ~/.config/systemd/user/
  systemctl --user enable marquee
  systemctl --user start marquee
  ```
  If you're migrating from the old `twitchtv.service` unit, disable that one
  first: `systemctl --user disable twitchtv`.

## License

Freely modifiable for personal use.
