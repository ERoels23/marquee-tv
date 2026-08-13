#!/usr/bin/env python3
"""
TwitchTV - Automatic Twitch stream launcher with priority-based switching
Launches the highest-priority live stream and auto-switches when higher priority streams go online.
"""

import subprocess
import json
import time
import os
import signal
import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List, Dict
import threading

from priority_list import parse_streamers_file, usernames as pl_usernames

# Configuration
SCRIPT_DIR = Path(__file__).parent
STREAMERS_FILE = SCRIPT_DIR / "streamers.txt"
STATUS_FILE = SCRIPT_DIR / ".status.json"
CONTROL_FILE = SCRIPT_DIR / ".control"
CHECK_INTERVAL = 10  # Check for new streams and control signals every 10 seconds
API_UPDATE_INTERVAL = 60  # Only query Twitch API every 60 seconds (rate limiting)
GRACE_PERIOD = 600  # 10 minutes before auto-switching (in seconds)

# Twitch API user ID (from your twitchlive function)
TWITCH_USER_ID = "60132775"

# Streamlink player args (from twitchll)
STREAMLINK_ARGS = [
    "streamlink",
    "--loglevel", "debug",
    "--player-verbose",
    "--player", "mpv",
    "--player-args", "--profile=twitch --volume=75 --force-seekable=yes --demuxer-lavf-o=fflags=+genpts+discardcorrupt",
    "--hls-live-edge", "3",
    "--twitch-low-latency",
    "--title", "{author} ::: {game} ::: {title}",
]


class TwitchTVController:
    def __init__(self):
        self.priority_list: List[str] = []
        self.current_stream: Optional[str] = None
        self.current_process: Optional[subprocess.Popen] = None
        self.switching_soon: Optional[Dict] = None
        self.grace_period_start: Optional[datetime] = None
        self.running = True
        self.previous_live_streams: set = set()  # Track what was live last check
        self.live_streams: Dict[str, Dict] = {}  # Cache of live streams
        self.last_api_update: float = 0  # Timestamp of last API call
        self.load_priority_list()
        self._streamers_mtime = STREAMERS_FILE.stat().st_mtime

    def load_priority_list(self):
        """Load and validate streamers priority list"""
        if not STREAMERS_FILE.exists():
            print(f"ERROR: {STREAMERS_FILE} not found!")
            print("Please create streamers.txt with one streamer per line")
            sys.exit(1)

        entries = parse_streamers_file(STREAMERS_FILE)
        self.priority_list = pl_usernames(entries)

        if not self.priority_list:
            print("ERROR: streamers.txt is empty!")
            sys.exit(1)

        print(f"Loaded {len(self.priority_list)} streamers from priority list")

    def maybe_reload_priority_list(self):
        """Reload streamers.txt if it changed on disk since the last check."""
        try:
            mtime = STREAMERS_FILE.stat().st_mtime
        except FileNotFoundError:
            return
        if mtime != self._streamers_mtime:
            entries = parse_streamers_file(STREAMERS_FILE)
            new_list = pl_usernames(entries)
            if new_list:
                self.priority_list = new_list
                self._streamers_mtime = mtime
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Reloaded priority list ({len(new_list)} streamers)")

    def get_live_streams(self) -> Dict[str, Dict]:
        """
        Get list of live streams from followed accounts using Twitch CLI.
        Returns dict mapping streamer name to stream info {title, game, viewers}
        """
        try:
            result = subprocess.run(
                ["/usr/bin/twitch", "api", "get", "/streams/followed", "-q", f"user_id={TWITCH_USER_ID}"],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode != 0:
                print(f"Error querying Twitch API: {result.stderr}")
                return {}

            data = json.loads(result.stdout)
            live_streams = {}

            for stream in data.get('data', []):
                streamer_name = stream['user_name'].lower()
                live_streams[streamer_name] = {
                    'title': stream['title'],
                    'game': stream['game_name'],
                    'viewers': stream['viewer_count']
                }

            return live_streams

        except subprocess.TimeoutExpired:
            print("Timeout querying Twitch API")
            return {}
        except (json.JSONDecodeError, KeyError) as e:
            print(f"Error parsing Twitch API response: {e}")
            return {}

    def get_highest_priority_live(self, live_streams: Dict) -> Optional[str]:
        """Return the highest priority streamer that's currently live"""
        for streamer in self.priority_list:
            if streamer in live_streams:
                return streamer
        return None

    def is_stream_alive(self) -> bool:
        """Check if current mpv process is still running"""
        if self.current_process is None:
            return False
        return self.current_process.poll() is None

    def launch_stream(self, streamer: str, stream_info: Optional[Dict] = None):
        """Launch a Twitch stream using streamlink"""
        # Kill any existing process
        if self.current_process is not None:
            try:
                self.current_process.terminate()
                self.current_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.current_process.kill()

        # Launch Chatterino if not already running
        subprocess.run(
            ["pgrep", "-x", "chatterino"],
            capture_output=True
        )
        if subprocess.run(["pgrep", "-x", "chatterino"], capture_output=True).returncode != 0:
            subprocess.Popen(
                ["chatterino"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

        # Launch stream
        cmd = STREAMLINK_ARGS + [f"twitch.tv/{streamer}", "best"]

        print(f"\n{'='*60}")
        print(f"Launching: {streamer}")
        if stream_info:
            print(f"Title: {stream_info['title']}")
            print(f"Game:  {stream_info['game']}")
            print(f"Viewers: {stream_info['viewers']}")
        print(f"{'='*60}\n")

        self.current_process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        self.current_stream = streamer
        self.switching_soon = None
        self.grace_period_start = None
        self.save_status()

    def show_notification(self, new_streamer: str, new_stream_info: Dict):
        """Show a desktop notification about upcoming stream switch"""
        message = f"🔄 Switching to {new_streamer} in 10 minutes\n{new_stream_info['title']}\n(Close mpv to switch now)"

        # Use notify-send for desktop notification
        subprocess.run(
            ["notify-send", "-u", "normal", "-t", "0", "TwitchTV", message],
            capture_output=True
        )

        print(f"\n{'!'*60}")
        print(f"STREAM AVAILABLE: {new_streamer}")
        print(f"Title: {new_stream_info['title']}")
        print(f"Game:  {new_stream_info['game']}")
        print(f"Will auto-switch in 10 minutes")
        print(f"(Or close mpv to switch now)")
        print(f"{'!'*60}\n")

    def check_control_signal(self) -> Optional[str]:
        """
        Check if user has signaled to switch.
        Returns: streamer name (from UI), "" (legacy switch), or None (no signal)
        """
        if CONTROL_FILE.exists():
            try:
                with open(CONTROL_FILE, 'r') as f:
                    command = f.read().strip().lower()
                CONTROL_FILE.unlink()

                # Handle new "switch:streamer" format from UI
                if command.startswith("switch:"):
                    return command.split(":", 1)[1]

                # Handle legacy "switch" command (auto-switch to highest priority)
                if command == "switch":
                    return ""

            except:
                pass
        return None

    def save_status(self):
        """Save current status to JSON file for querying"""
        status = {
            'timestamp': datetime.now().isoformat(),
            'current_stream': self.current_stream,
            'stream_alive': self.is_stream_alive(),
            'switching_soon': self.switching_soon,
            'grace_period_remaining': None
        }

        if self.grace_period_start:
            remaining = GRACE_PERIOD - (datetime.now() - self.grace_period_start).total_seconds()
            status['grace_period_remaining'] = max(0, int(remaining))

        with open(STATUS_FILE, 'w') as f:
            json.dump(status, f)

    def run(self):
        """Main event loop"""
        print("Starting TwitchTV...")
        print(f"Priority list: {', '.join(self.priority_list)}")

        while self.running:
            try:
                self.maybe_reload_priority_list()
                # Only query API every API_UPDATE_INTERVAL seconds
                current_time = time.time()
                if current_time - self.last_api_update >= API_UPDATE_INTERVAL:
                    self.live_streams = self.get_live_streams()
                    self.last_api_update = current_time

                current_live = set(self.live_streams.keys())
                newly_live = current_live - self.previous_live_streams
                self.previous_live_streams = current_live
                highest_priority = self.get_highest_priority_live(self.live_streams)

                # If no stream is currently running, launch the highest priority one
                if not self.is_stream_alive():
                    if highest_priority:
                        self.launch_stream(highest_priority, self.live_streams[highest_priority])
                    else:
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] No live streams in priority list")
                    self.switching_soon = None
                    self.grace_period_start = None

                # If a stream is running, check for manual switches and higher priority streams
                elif self.is_stream_alive():
                    # ALWAYS check for manual switch requests (user can switch anytime)
                    switch_target = self.check_control_signal()
                    if switch_target == "":
                        # Legacy "switch" command - switch to highest priority now
                        print("User requested immediate switch!")
                        if highest_priority and highest_priority in self.live_streams:
                            self.launch_stream(highest_priority, self.live_streams[highest_priority])
                            self.switching_soon = None
                            self.grace_period_start = None
                    elif switch_target:
                        # User requested switch to specific stream (from UI)
                        target_lower = switch_target.lower()
                        if target_lower in self.live_streams:
                            print(f"User requested switch to {target_lower}")
                            self.launch_stream(target_lower, self.live_streams[target_lower])
                            # Clear auto-switch since user manually chose this stream
                            self.switching_soon = None
                            self.grace_period_start = None

                    # Check for auto-switch to higher priority stream (ONLY if it JUST came online)
                    if highest_priority != self.current_stream and highest_priority is not None:
                        # Check if this higher-priority stream is NEWLY live (not already live)
                        if highest_priority in newly_live:
                            # This stream just went live while we're watching something lower-priority
                            if self.switching_soon != highest_priority:
                                self.switching_soon = highest_priority
                                self.grace_period_start = datetime.now()
                                self.show_notification(highest_priority, self.live_streams[highest_priority])

                        # Check if grace period has elapsed
                        if self.grace_period_start and self.switching_soon == highest_priority:
                            elapsed = (datetime.now() - self.grace_period_start).total_seconds()
                            if elapsed >= GRACE_PERIOD:
                                print(f"Grace period elapsed, switching to {highest_priority}")
                                self.launch_stream(highest_priority, self.live_streams[highest_priority])
                                self.switching_soon = None
                                self.grace_period_start = None
                    else:
                        # Current stream is highest priority or no higher priority streams live
                        self.switching_soon = None
                        self.grace_period_start = None

                self.save_status()
                time.sleep(CHECK_INTERVAL)

            except KeyboardInterrupt:
                print("\nShutdown requested")
                self.running = False
            except Exception as e:
                print(f"Error in main loop: {e}")
                time.sleep(CHECK_INTERVAL)

        # Cleanup
        if self.current_process:
            self.current_process.terminate()
        STATUS_FILE.unlink(missing_ok=True)
        CONTROL_FILE.unlink(missing_ok=True)


def handle_signal(signum, frame):
    """Handle shutdown signals"""
    print("\nReceived signal, shutting down...")
    sys.exit(0)


if __name__ == "__main__":
    # Handle signals
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    controller = TwitchTVController()
    controller.run()
