#!/usr/bin/env bash
# TwitchTV Control Script
# Usage: switchtv [command]
#
# Commands:
#   (none)            Launch interactive TUI
#   start             Start the TwitchTV watcher
#   stop              Stop the TwitchTV watcher
#   status            Show current stream status
#   now               Switch to highest-priority stream NOW (don't wait for grace period)
#   watch             Run in foreground (for debugging)

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
MAIN_SCRIPT="$SCRIPT_DIR/marquee_daemon.py"
UI_SCRIPT="$SCRIPT_DIR/marquee_ui.py"
STATUS_FILE="$SCRIPT_DIR/.status.json"
CONTROL_FILE="$SCRIPT_DIR/.control"
TMUX_SESSION="marquee"

# Prefer the project's own venv (where textual etc. are actually installed)
# over whatever "python3" resolves to on PATH.
if [ -x "$SCRIPT_DIR/.venv/bin/python3" ]; then
    PYTHON="$SCRIPT_DIR/.venv/bin/python3"
else
    PYTHON="python3"
fi

case "${1:-ui}" in
    ui)
        exec "$PYTHON" "$UI_SCRIPT"
        ;;

    start)
        if pgrep -f "python3.*marquee_daemon.py" > /dev/null 2>&1; then
            echo "TwitchTV is already running"
            exit 0
        fi

        echo "Starting TwitchTV in background..."
        nohup "$PYTHON" "$MAIN_SCRIPT" > "$SCRIPT_DIR/.log" 2>&1 &
        sleep 1

        if pgrep -f "python3.*marquee_daemon.py" > /dev/null 2>&1; then
            echo "✓ TwitchTV started successfully"
        else
            echo "✗ Failed to start TwitchTV"
            exit 1
        fi
        ;;

    stop)
        if pgrep -f "python3.*marquee_daemon.py" > /dev/null 2>&1; then
            echo "Stopping TwitchTV..."
            pkill -f "python3.*marquee_daemon.py"
            sleep 1
            echo "✓ TwitchTV stopped"
        else
            echo "TwitchTV is not running"
        fi
        ;;

    status)
        if [ -f "$STATUS_FILE" ]; then
            echo "Current status:"
            cat "$STATUS_FILE" | jq '.' 2>/dev/null || cat "$STATUS_FILE"
        else
            echo "No status file found. Is TwitchTV running?"
        fi
        ;;

    now)
        echo "switch" > "$CONTROL_FILE"
        echo "Signaled to switch immediately"
        ;;

    watch)
        echo "Running TwitchTV in foreground (Ctrl+C to stop)..."
        exec "$PYTHON" "$MAIN_SCRIPT"
        ;;

    log)
        if [ -f "$SCRIPT_DIR/.log" ]; then
            tail -f "$SCRIPT_DIR/.log"
        else
            echo "No log file found"
        fi
        ;;

    *)
        cat << EOF
Marquee.tv Control Script

Usage: switchtv [command]

Commands:
  (none)     Launch interactive TUI
  start      Start TwitchTV in background
  stop       Stop TwitchTV
  status     Show current stream status
  now        Switch to next stream NOW (don't wait for grace period)
  watch      Run TwitchTV in foreground (for debugging)
  log        Follow the log file

Examples:
  switchtv               # Launch the interactive UI
  switchtv start         # Start watching
  switchtv status        # Check what's playing
  switchtv now           # Switch immediately to next stream
  switchtv stop          # Stop watching

EOF
        exit 1
        ;;
esac
