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
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict
import threading

from priority_list import parse_streamers_file, usernames as pl_usernames
from mpv_ipc import set_title
from ui_format import build_mpv_title

# Configuration
SCRIPT_DIR = Path(__file__).parent
STREAMERS_FILE = SCRIPT_DIR / "streamers.txt"
STATUS_FILE = SCRIPT_DIR / ".status.json"
CONTROL_FILE = SCRIPT_DIR / ".control"
LAST_SEEN_FILE = SCRIPT_DIR / ".last_seen.json"
CHECK_INTERVAL = 10  # Check for new streams and control signals every 10 seconds
API_UPDATE_INTERVAL = 60  # Only query Twitch API every 60 seconds (rate limiting)
GRACE_PERIOD = 300  # 5 minutes before auto-switching (in seconds)


def mpv_socket_path(streamer: str) -> Path:
    return SCRIPT_DIR / f".mpv-{streamer}.sock"


def parse_control_command(raw: str):
    """Parse a raw .control file command.

    Returns (streamer, mode) where mode is None for a legacy/plain switch,
    or one of "override"/"temporary"/"oneshot". Returns None if unrecognized.
    """
    raw = raw.strip().lower()
    if raw == "switch":
        return ("", None)
    if raw.startswith("switch:"):
        rest = raw.split(":", 1)[1]
        if ":" in rest:
            streamer, mode = rest.split(":", 1)
            return (streamer, mode)
        return (rest, None)
    return None


class TwitchTVController:
    def __init__(self):
        self.priority_list: List[str] = []
        self.current_stream: Optional[str] = None
        self.current_process: Optional[subprocess.Popen] = None
        self.switching_soon: Optional[Dict] = None
        self.grace_period_start: Optional[datetime] = None
        self.running = True
        self.manual_override: bool = False
        self.previous_live_streams: set = set()  # Track what was live last check
        self.live_streams: Dict[str, Dict] = {}  # Cache of live streams
        self.last_api_update: float = 0  # Timestamp of last API call
        self.current_socket_path: Optional[Path] = None
        self.last_known_game: Optional[str] = None
        self.last_known_title: Optional[str] = None
        self.last_seen: Dict[str, Dict] = self._load_last_seen()
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
        Get live status for every streamer in the priority list, using Twitch CLI.
        Queries by explicit user_login rather than /streams/followed, since the
        priority list can (and does) include streamers the account doesn't
        follow — a followed-only query would silently never report them live.
        Returns dict mapping streamer name to stream info {title, game, viewers}
        """
        if not self.priority_list:
            return {}
        live_streams: Dict[str, Dict] = {}
        try:
            # Helix caps user_login at 100 per request; batch defensively.
            for i in range(0, len(self.priority_list), 100):
                batch = self.priority_list[i:i + 100]
                cmd = ["twitch", "api", "get", "streams"]
                for streamer in batch:
                    cmd += ["-q", f"user_login={streamer}"]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)

                # Judge success by whether stdout actually parses, not the
                # exit code: the twitch CLI can crash in its own unrelated
                # update-check code *after* already printing valid JSON,
                # which would otherwise discard perfectly good data.
                try:
                    data = json.loads(result.stdout)
                except json.JSONDecodeError:
                    print(f"Error querying Twitch API: {result.stderr}")
                    continue

                for stream in data.get('data', []):
                    streamer_name = stream['user_login'].lower()
                    live_streams[streamer_name] = {
                        'title': stream['title'],
                        'game': stream['game_name'],
                        'viewers': stream['viewer_count'],
                        'started_at': stream.get('started_at'),
                    }

            return live_streams

        except subprocess.TimeoutExpired:
            print("Timeout querying Twitch API")
            return live_streams
        except (json.JSONDecodeError, KeyError) as e:
            print(f"Error parsing Twitch API response: {e}")
            return live_streams

    def backfill_last_seen(self) -> None:
        """One-time startup pass: for any priority-list entry missing a
        timestamp, game, or title, ask Twitch to fill in what it can.

        Two endpoints, since neither alone has everything:
        - /videos (their most recent broadcast VOD) has a start timestamp,
          used only for "at" — reaches back as far as VOD retention allows
          (14 days, 60 for Partners), and returns nothing for channels with
          VODs disabled.
        - /channels (Get Channel Information) has game/title even while
          offline, since it reflects the channel's current configured
          category and title rather than live-only data — this is what
          makes category backfill possible at all (VOD history has no
          category field). It's "currently configured", not necessarily
          "as of their last broadcast", but the two are the same unless a
          streamer edits their info while offline.

        Best-effort only, not a substitute for the daemon's own polling.
        """
        missing = [
            s for s in self.priority_list
            if s not in self.last_seen
            or not self.last_seen[s].get('title')
            or not self.last_seen[s].get('game')
        ]
        if not missing:
            return
        print(f"Backfilling last-seen data for {len(missing)} streamer(s)...")

        user_ids: Dict[str, str] = {}
        try:
            for i in range(0, len(missing), 100):
                batch = missing[i:i + 100]
                cmd = ["twitch", "api", "get", "users"]
                for streamer in batch:
                    cmd += ["-q", f"login={streamer}"]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                # Judge success by whether stdout parses, not the exit code —
                # see get_live_streams for why.
                try:
                    data = json.loads(result.stdout)
                except json.JSONDecodeError:
                    continue
                for user in data.get('data', []):
                    user_ids[user['login'].lower()] = user['id']
        except (subprocess.TimeoutExpired, json.JSONDecodeError, KeyError) as e:
            print(f"Error resolving user IDs for last-seen backfill: {e}")
            return

        updated = False
        for streamer in missing:
            user_id = user_ids.get(streamer)
            if not user_id:
                continue
            existing = self.last_seen.get(streamer, {})
            at = existing.get('at')
            game = existing.get('game')
            title = existing.get('title')

            # Judge success by whether stdout parses, not the exit code — see
            # get_live_streams for why (the twitch CLI can crash in its own
            # unrelated update-check code after already printing good JSON).
            if not at:
                try:
                    result = subprocess.run(
                        ["twitch", "api", "get", "videos",
                         "-q", f"user_id={user_id}", "-q", "type=archive", "-q", "first=1"],
                        capture_output=True, text=True, timeout=10,
                    )
                    videos = json.loads(result.stdout).get('data', [])
                    if videos:
                        at = videos[0]['created_at']
                        title = title or videos[0].get('title')
                except (subprocess.TimeoutExpired, json.JSONDecodeError, KeyError):
                    pass

            if not game or not title:
                try:
                    result = subprocess.run(
                        ["twitch", "api", "get", "channels", "-q", f"broadcaster_id={user_id}"],
                        capture_output=True, text=True, timeout=10,
                    )
                    channels = json.loads(result.stdout).get('data', [])
                    if channels:
                        game = game or (channels[0].get('game_name') or None)
                        title = title or (channels[0].get('title') or None)
                except (subprocess.TimeoutExpired, json.JSONDecodeError, KeyError):
                    pass

            if at:  # nothing usable to record without at least a timestamp
                self.last_seen[streamer] = {"at": at, "game": game, "title": title}
                updated = True

        if updated:
            self._save_last_seen()

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

        # This system's Chatterino build doesn't activate tabs in an already-running
        # window via -a (spawns a new process instead) — so we close any existing
        # instance first. Chatterino persists its open tabs across restarts, so this
        # still achieves "switch to this channel's tab" from the user's perspective.
        subprocess.run(["pkill", "-x", "chatterino"], capture_output=True)
        for _ in range(20):  # poll up to ~2s for it to actually exit
            if subprocess.run(["pgrep", "-x", "chatterino"], capture_output=True).returncode != 0:
                break
            time.sleep(0.1)
        subprocess.Popen(
            ["chatterino", "-a", streamer],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        # Launch stream, with a per-streamer MPV IPC socket for live title updates
        if self.current_socket_path is not None:
            self.current_socket_path.unlink(missing_ok=True)
        socket_path = mpv_socket_path(streamer)
        socket_path.unlink(missing_ok=True)
        player_args = (
            "--profile=twitch --volume=60 --force-seekable=yes "
            "--demuxer-lavf-o=fflags=+genpts+discardcorrupt "
            f"--input-ipc-server={socket_path}"
        )
        cmd = [
            "streamlink",
            "--loglevel", "debug",
            "--player-verbose",
            "--player", "mpv",
            "--player-args", player_args,
            "--hls-live-edge", "3",
            "--twitch-low-latency",
            "--title", "{author} ::: {game} ::: {title}",
            f"twitch.tv/{streamer}", "best",
        ]
        self.current_socket_path = socket_path
        self.last_known_game = stream_info.get('game') if stream_info else None
        self.last_known_title = stream_info.get('title') if stream_info else None

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
        message = f"{new_streamer} went live! switching in 5 minutes"

        # Use notify-send for desktop notification
        subprocess.run(
            ["notify-send", "-u", "normal", "-t", "0", "Marquee.tv", message],
            capture_output=True
        )

        print(f"\n{'!'*60}")
        print(f"STREAM AVAILABLE: {new_streamer}")
        print(f"Title: {new_stream_info['title']}")
        print(f"Game:  {new_stream_info['game']}")
        print(f"Will auto-switch in 5 minutes")
        print(f"(Or close mpv to switch now)")
        print(f"{'!'*60}\n")

    def check_control_signal(self):
        """
        Check if user has signaled to switch.
        Returns: (streamer, mode) tuple, or None if no signal.
        mode is None for a plain/legacy switch, or "override"/"temporary"/"oneshot".
        """
        if CONTROL_FILE.exists():
            try:
                with open(CONTROL_FILE, 'r') as f:
                    command = f.read()
                CONTROL_FILE.unlink()
                return parse_control_command(command)
            except (OSError, ValueError):
                pass
        return None

    def _load_last_seen(self) -> Dict[str, Dict]:
        if not LAST_SEEN_FILE.exists():
            return {}
        try:
            with open(LAST_SEEN_FILE, 'r') as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
        # Migrate the legacy flat "streamer -> ISO timestamp" format to
        # {"at": ..., "game": ..., "title": ...} so existing last-seen data
        # isn't lost when this shape was introduced.
        return {
            streamer: ({"at": value, "game": None, "title": None} if isinstance(value, str) else value)
            for streamer, value in data.items()
        }

    def _save_last_seen(self):
        with open(LAST_SEEN_FILE, 'w') as f:
            json.dump(self.last_seen, f)

    def save_status(self):
        """Save current status to JSON file for querying"""
        status = {
            'timestamp': datetime.now().isoformat(),
            'current_stream': self.current_stream,
            'stream_alive': self.is_stream_alive(),
            'switching_soon': self.switching_soon,
            'grace_period_remaining': None,
            'live_streams': self.live_streams,
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
        # Runs in the background rather than blocking here: it can take tens
        # of seconds (up to 2 extra API calls per missing streamer), and
        # nothing about actually watching a stream depends on it.
        threading.Thread(target=self.backfill_last_seen, daemon=True).start()

        while self.running:
            try:
                self.maybe_reload_priority_list()
                # Only query API every API_UPDATE_INTERVAL seconds
                current_time = time.time()
                if current_time - self.last_api_update >= API_UPDATE_INTERVAL:
                    self.live_streams = self.get_live_streams()
                    self.last_api_update = current_time
                    now_iso = datetime.now(timezone.utc).isoformat()
                    for streamer, info in self.live_streams.items():
                        self.last_seen[streamer] = {
                            "at": now_iso, "game": info.get('game'), "title": info.get('title'),
                        }
                    self._save_last_seen()

                    if (self.current_stream and self.is_stream_alive()
                            and self.current_stream in self.live_streams
                            and self.current_socket_path):
                        info = self.live_streams[self.current_stream]
                        if info['game'] != self.last_known_game or info['title'] != self.last_known_title:
                            new_title = build_mpv_title(self.current_stream, info['game'], info['title'])
                            if set_title(self.current_socket_path, new_title):
                                self.last_known_game = info['game']
                                self.last_known_title = info['title']
                            else:
                                print(f"[{datetime.now().strftime('%H:%M:%S')}] Failed to push MPV title update, will retry next poll")

                current_live = set(self.live_streams.keys())
                newly_live = current_live - self.previous_live_streams
                self.previous_live_streams = current_live
                highest_priority = self.get_highest_priority_live(self.live_streams)

                # ALWAYS check for manual switch requests (user can switch anytime,
                # including before any stream has started — see below).
                control_target, control_mode = None, None
                control_signal = self.check_control_signal()
                if control_signal is not None:
                    target, mode = control_signal
                    if mode == "oneshot":
                        pass  # UI handles one-shot streams entirely on its own
                    elif target == "" and mode is None:
                        # Legacy "switch" command - switch to highest priority now
                        control_target, control_mode = highest_priority, None
                    elif target and target in self.live_streams:
                        control_target, control_mode = target, mode

                # If no stream is currently running, launch the requested stream
                # if one was specified (even if it isn't the highest priority),
                # otherwise fall back to the highest priority one.
                if not self.is_stream_alive():
                    launch_target = control_target or highest_priority
                    if launch_target:
                        print(f"User requested switch to {launch_target} (mode={control_mode})" if control_target
                              else f"[{datetime.now().strftime('%H:%M:%S')}] Launching highest priority stream")
                        self.launch_stream(launch_target, self.live_streams[launch_target])
                        self.manual_override = (control_mode == "override")
                    else:
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] No live streams in priority list")
                        self.manual_override = False
                    self.switching_soon = None
                    self.grace_period_start = None

                # If a stream is running, check for manual switches and higher priority streams
                elif self.is_stream_alive():
                    if control_target:
                        print(f"User requested switch to {control_target} (mode={control_mode})")
                        self.launch_stream(control_target, self.live_streams[control_target])
                        self.manual_override = (control_mode == "override")
                        self.switching_soon = None
                        self.grace_period_start = None

                    # Check for auto-switch to higher priority stream (ONLY if it JUST came online)
                    if self.manual_override:
                        if highest_priority != self.current_stream and highest_priority is not None:
                            print(f"[{datetime.now().strftime('%H:%M:%S')}] manual_override active; suppressing auto-switch to {highest_priority}")
                    elif highest_priority != self.current_stream and highest_priority is not None:
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
