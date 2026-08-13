# TwitchTV - Automatic Stream Launcher

A Python script that automatically launches your highest-priority Twitch streams and intelligently switches streams based on your preferences.

## Features

- **Priority-based streaming**: Define streamers in order of preference, script launches the highest-priority currently-live stream
- **Intelligent auto-switching**: When a higher-priority stream goes live, shows a 10-minute notification before auto-switching
- **Manual override**: Press "S" (via `switchtv now`) during grace period to switch immediately
- **Automatic fallback**: When current stream ends, immediately launches the next highest-priority live stream
- **Chatterino integration**: Automatically launches Chatterino alongside streams
- **Low-latency streaming**: Uses Twitch low-latency settings for optimal viewing
- **Desktop notifications**: Shows notify-send notifications for upcoming stream switches

## Requirements

- `streamlink` - Stream launcher
- `mpv` - Video player
- `chatterino` - Twitch chat client
- Twitch CLI (`twitch`) - For checking live streams
- Python 3.6+
- `jq` - For JSON parsing (optional, fallback parser included)
- `notify-send` - For desktop notifications

## Setup

### 1. Configure your priority list

Edit `streamers.txt` and add your Twitch streamers in order of preference:

```
# TwitchTV Priority List
northernlion
strippin
kruzadar
malf
```

Comments (lines starting with #) are ignored. One streamer per line. List them in the order you want to watch them.

### 2. Authenticate with Twitch CLI

The script uses the Twitch CLI to check which of your followed streamers are live. Make sure you're authenticated:

```bash
twitch auth login
```

This should be already set up since you have the `twitchlive` function working.

## Usage

### Interactive TUI (Recommended)

```bash
# Launch the interactive terminal UI
switchtv

# Or explicitly:
switchtv ui
```

The TUI provides a live dashboard showing:
- **Status bar**: Service state and current stream info
- **Stream list**: All streamers in priority order with live status and game info
- **Color coding**:
  - 🟣 Lavender = Currently watching
  - 🔵 Cyan = Auto-switch pending (with countdown timer)
  - 🟢 Green = Live and available to switch to
  - ⚫ Gray = Offline

**Controls in the TUI:**

All commands require typing the input and pressing **Enter** to execute:

- **Stream switching**: Type stream number (1, 2, 3, etc.) + **Enter** → confirmation prompt appears → **Enter** again to confirm
- **Service control**: Type command + **Enter** to execute immediately:
  - **S** - Start the TwitchTV daemon
  - **X** - Stop the daemon (kills stream and service)
  - **Q** - Exit the UI (kills stream and service first)
- **During confirmation**: Type **C** + **Enter** to cancel a pending stream switch

### Command-line Controls

```bash
# Start TwitchTV daemon (runs in background)
switchtv start

# Check current status
switchtv status

# Stop TwitchTV
switchtv stop

# Run daemon in foreground (for debugging/testing)
switchtv watch

# Follow the daemon log
switchtv log

# Force immediate switch (legacy, deprecated - use UI instead)
switchtv now
```

### During a stream

When a higher-priority stream goes online:
1. A desktop notification appears (if using daemon directly)
2. The TUI shows a countdown timer for the auto-switch
3. You can manually switch anytime by typing the stream number in the TUI
4. If you close mpv, it immediately switches to the next live stream

When your current stream ends:
- The script automatically launches the next highest-priority live stream

## How it works

1. **Initial check**: Script checks which followed streamers are currently live
2. **Launch**: Launches the highest-priority currently-live streamer
3. **Monitoring**: Every 60 seconds, checks for newly-live streams
4. **Grace period**: If a higher-priority stream goes online, waits 10 minutes (grace period) before switching
5. **User control**: You can force an immediate switch at any time with `switchtv now`
6. **Auto-launch**: When current stream ends, launches next highest-priority stream

## Configuration

### Adjust check interval (in twitch_tv.py)

```python
CHECK_INTERVAL = 60  # seconds (default: 60)
```

### Adjust grace period (in twitch_tv.py)

```python
GRACE_PERIOD = 600  # seconds (default: 10 minutes)
```

### Customize mpv/streamlink settings

Edit `STREAMLINK_ARGS` in `twitch_tv.py` to change player settings, volume, quality, etc.

## Files

- `twitch_tv.py` - Main daemon script
- `twitchtv_ui.py` - Interactive terminal UI
- `switchtv.sh` - Control script (entry point)
- `streamers.txt` - Priority list (edit this!)
- `.status.json` - Current status (auto-generated)
- `.control` - Control file for inter-process communication (auto-generated)
- `.log` - Daemon log file (auto-generated)

## Troubleshooting

### Script not starting streams

Check the log: `switchtv log`

Common issues:
- `twitch` CLI not authenticated: Run `twitch auth login`
- Streamers not in priority list: Edit `streamers.txt`
- No live streams: Check if your streamers are actually live

### Chatterino not opening

Make sure Chatterino is installed and in your PATH:

```bash
which chatterino
```

### Notifications not showing

Ensure `notify-send` is installed and your desktop environment supports it:

```bash
notify-send "test"
```

## Tips

- **Add an alias** to your `.zshrc` or `.bashrc`:
  ```bash
  alias twitchtv='switchtv'
  ```

- **Auto-start on login** (systemd user service):
  Create `~/.config/systemd/user/twitchtv.service` with:
  ```ini
  [Unit]
  Description=TwitchTV Stream Watcher
  After=network-online.target

  [Service]
  Type=simple
  ExecStart=/mnt/Wrestler_Ted/claudes_room/TwitchTV/twitch_tv.py
  Restart=on-failure
  RestartSec=10

  [Install]
  WantedBy=default.target
  ```
  Then enable with:
  ```bash
  systemctl --user enable twitchtv
  systemctl --user start twitchtv
  ```

## License

Freely modifiable for personal use.
