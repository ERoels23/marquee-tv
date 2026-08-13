#!/usr/bin/env python3
"""
TwitchTV Terminal UI - Interactive dashboard for managing Twitch streams
Displays live streams, allows switching, and shows auto-switch timers.
"""

import subprocess
import json
import time
import os
import sys
import curses
import threading
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Tuple

# Configuration
SCRIPT_DIR = Path(__file__).parent
STREAMERS_FILE = SCRIPT_DIR / "streamers.txt"
STATUS_FILE = SCRIPT_DIR / ".status.json"
CONTROL_FILE = SCRIPT_DIR / ".control"
API_UPDATE_INTERVAL = 60  # Only fetch from Twitch API every 60 seconds

# Twitch API user ID
TWITCH_USER_ID = "60132775"

# Catppuccin Mocha colors
COLORS = {
    'lavender': (177, 156, 217),
    'green': (166, 227, 161),
    'text': (205, 214, 244),
    'surface': (49, 50, 68),
    'overlay': (88, 91, 112),
    'base': (30, 30, 46),
}

# Column layout
NAME_COL_WIDTH = 14   # max nickname is 12 + 2 padding
GAME_COL_WIDTH = 15   # truncated with "..."
SCROLL_SEPARATOR = " " * 20  # gap between end of title and its repeat (acts as pause)
SCROLL_SPEED = 2      # advance 1 char every N refresh ticks (0.2s each ≈ 0.4s/char)


class TwitchTVUI:
    def __init__(self):
        self.priority_list: List[str] = []
        self.nicknames: Dict[str, str] = {}
        self.live_streams: Dict[str, Dict] = {}
        self.current_stream: Optional[str] = None
        self.stream_alive = False
        self.switching_soon: Optional[str] = None
        self.grace_period_remaining: Optional[int] = None
        self.service_running = False
        self.last_update = 0
        self.selected_input = ""
        self.confirmation_pending = False
        self.confirmation_stream = ""
        self.scroll_offsets: Dict[str, int] = {}
        self.scroll_tick: int = 0
        self.load_priority_list()

    def load_priority_list(self):
        """Load streamer priority list"""
        if not STREAMERS_FILE.exists():
            print(f"ERROR: {STREAMERS_FILE} not found!")
            sys.exit(1)

        self.priority_list = []
        self.nicknames = {}
        with open(STREAMERS_FILE, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '|' in line:
                    username, nickname = line.split('|', 1)
                    username = username.strip().lower()
                    nickname = nickname.strip()
                else:
                    username = line.lower()
                    nickname = None
                self.priority_list.append(username)
                if nickname:
                    self.nicknames[username] = nickname

        if not self.priority_list:
            print("ERROR: streamers.txt is empty!")
            sys.exit(1)

    def display_name(self, streamer: str) -> str:
        """Return nickname if set, otherwise the raw username"""
        return self.nicknames.get(streamer, streamer)

    def format_viewers(self, count: int) -> str:
        """Abbreviate viewer count to at most 4 characters"""
        if count < 10_000:
            return str(count)
        elif count < 1_000_000:
            return f"{count // 1000}k"
        else:
            m = count / 1_000_000
            return f"{m:.1f}M" if m < 10 else f"{int(m)}M"

    def truncate_text(self, text: str, max_len: int) -> str:
        """Truncate with dash to max_len characters"""
        if len(text) <= max_len:
            return text
        return text[:max_len - 1] + "-"

    def get_scroll_text(self, streamer: str, title: str, width: int) -> str:
        """Return a rolling window of title text; static if it fits"""
        if width <= 0:
            return ""
        if len(title) <= width:
            return title
        title_loop = title + SCROLL_SEPARATOR
        offset = self.scroll_offsets.get(streamer, 0)
        source = title_loop * 2  # always enough to slice `width` chars
        return source[offset:offset + width]

    def get_live_streams(self) -> Dict[str, Dict]:
        """Fetch live streams from Twitch API"""
        try:
            result = subprocess.run(
                ["/usr/bin/twitch", "api", "get", "/streams/followed", "-q", f"user_id={TWITCH_USER_ID}"],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode != 0:
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
        except Exception:
            return {}

    def load_status(self):
        """Load current status from daemon"""
        if STATUS_FILE.exists():
            try:
                with open(STATUS_FILE, 'r') as f:
                    status = json.load(f)
                    self.current_stream = status.get('current_stream')
                    self.stream_alive = status.get('stream_alive', False)
                    self.switching_soon = status.get('switching_soon')
                    self.grace_period_remaining = status.get('grace_period_remaining')
            except:
                pass

    def check_service_running(self) -> bool:
        """Check if TwitchTV daemon is running"""
        result = subprocess.run(
            ["pgrep", "-f", "python3.*twitch_tv.py"],
            capture_output=True
        )
        return result.returncode == 0

    def start_service(self):
        """Start the TwitchTV daemon"""
        subprocess.run([str(SCRIPT_DIR / "switchtv.sh"), "start"], capture_output=True)

    def stop_service(self):
        """Stop the TwitchTV daemon and kill any running stream"""
        subprocess.run([str(SCRIPT_DIR / "switchtv.sh"), "stop"], capture_output=True)
        # Also kill mpv to close the stream window
        subprocess.run(["pkill", "-f", "mpv"], capture_output=True)

    def switch_stream(self, streamer: str):
        """Signal daemon to switch to a stream"""
        # Write control signal
        with open(CONTROL_FILE, 'w') as f:
            f.write(f"switch:{streamer}")

    def update_data(self, force=False):
        """Update streams and status from API and status file"""
        current_time = time.time()

        # Only fetch from API every API_UPDATE_INTERVAL seconds (rate limiting)
        if not force and current_time - self.last_update < API_UPDATE_INTERVAL:
            # But always load status from file (very cheap operation)
            self.load_status()
            self.service_running = self.check_service_running()
            return

        # Full update with API call
        self.live_streams = self.get_live_streams()
        self.load_status()
        self.service_running = self.check_service_running()
        self.last_update = current_time

    def format_time_remaining(self, seconds: int) -> str:
        """Format seconds as mm:ss"""
        mins = seconds // 60
        secs = seconds % 60
        return f"{mins:02d}:{secs:02d}"

    def draw_ui(self, stdscr):
        """Main UI drawing function"""
        curses.curs_set(1)  # Show cursor
        stdscr.nodelay(True)  # Non-blocking input
        stdscr.leaveok(False)  # Respect cursor position

        # Initialize colors if terminal supports them
        try:
            curses.init_pair(1, curses.COLOR_MAGENTA, curses.COLOR_BLACK)  # Lavender
            curses.init_pair(2, curses.COLOR_GREEN, curses.COLOR_BLACK)     # Green
            curses.init_pair(3, curses.COLOR_WHITE, curses.COLOR_BLACK)     # Text
            curses.init_pair(4, curses.COLOR_CYAN, curses.COLOR_BLACK)      # Cyan (highlight)
            curses.init_pair(5, curses.COLOR_RED, curses.COLOR_BLACK)       # Red (offline)
        except:
            pass

        last_api_update = 0

        while True:
            try:
                # Handle user input
                key = stdscr.getch()

                if key == 10:  # Enter key
                    if self.confirmation_pending:
                        # Confirm stream switch
                        self.switch_stream(self.confirmation_stream)
                        self.confirmation_pending = False
                        self.selected_input = ""
                        # Give daemon time to process, then update
                        time.sleep(0.5)
                        self.update_data(force=True)
                    elif self.selected_input:
                        # Check if it's a letter command or number command
                        cmd = self.selected_input.lower()
                        if cmd == 'q':
                            # Quit: stop service and exit
                            if self.service_running:
                                self.stop_service()
                            return
                        elif cmd == 's':
                            # Start service
                            if not self.service_running:
                                self.start_service()
                                self.update_data(force=True)
                            self.selected_input = ""
                        elif cmd == 'x':
                            # Stop service
                            if self.service_running:
                                self.stop_service()
                                self.update_data(force=True)
                            self.selected_input = ""
                        else:
                            # Try to parse as stream number
                            try:
                                idx = int(cmd) - 1
                                if 0 <= idx < len(self.priority_list):
                                    streamer = self.priority_list[idx]
                                    # Only allow switching to live streams
                                    if streamer in self.live_streams:
                                        # Start service if not running
                                        if not self.service_running:
                                            self.start_service()
                                            time.sleep(1)  # Give daemon time to start
                                            self.update_data(force=True)
                                        self.confirmation_stream = streamer
                                        self.confirmation_pending = True
                            except ValueError:
                                pass
                            self.selected_input = ""
                elif key == curses.KEY_BACKSPACE or key == 127:
                    self.selected_input = self.selected_input[:-1]
                    self.confirmation_pending = False
                elif 48 <= key <= 57:  # Number keys 0-9
                    self.selected_input += chr(key)
                elif key in [ord('s'), ord('S'), ord('x'), ord('X'), ord('q'), ord('Q'), ord('c'), ord('C')]:
                    # Letter commands - add to input buffer
                    if key == ord('c') or key == ord('C'):
                        # Cancel confirmation
                        self.confirmation_pending = False
                        self.selected_input = ""
                    else:
                        self.selected_input += chr(key).lower()

                # Update data (checks status file frequently, API less frequently)
                self.update_data()

                # Clear screen
                stdscr.clear()
                height, width = stdscr.getmaxyx()

                # Title bar
                title = " TwitchTV "
                stdscr.attron(curses.color_pair(1))
                stdscr.addstr(0, 0, "┌" + "─" * (width - 2) + "┐")
                stdscr.addstr(1, 2, title, curses.A_BOLD)
                stdscr.attroff(curses.color_pair(1))

                # Status line
                status_text = f"Service: {'🟢 Running' if self.service_running else '🔴 Offline'} | "
                if self.current_stream:
                    status_text += f"Watching: {self.display_name(self.current_stream).upper()}"
                    if self.switching_soon:
                        status_text += f" | ⏱️  Switching to {self.display_name(self.switching_soon).upper()} in {self.format_time_remaining(self.grace_period_remaining or 0)}"
                else:
                    status_text += "No stream active"

                stdscr.addstr(2, 2, status_text[:width-4])

                # Divider
                stdscr.attron(curses.color_pair(5))
                stdscr.addstr(3, 0, "├" + "─" * (width - 2) + "┤")
                stdscr.attroff(curses.color_pair(5))

                # Stream list
                # Fixed x positions:
                #   x=2  : "nn. " (4) + name (NAME_COL_WIDTH) = 21
                #   x=21 : emoji (2 cells)
                #   x=23 : viewers or "OFF" (4 chars) + " " (1) = 5 chars → x=28
                #   x=28 : game (GAME_COL_WIDTH) + " " (1) → x=44
                #   x=44 : "[" + title (remaining) + "]"
                STATUS_X = 2 + 4 + NAME_COL_WIDTH        # 21
                GAME_X   = STATUS_X + 2 + 5              # 28  (emoji=2, "xxxx "=5)
                TITLE_X  = GAME_X + GAME_COL_WIDTH + 1   # 44

                line = 4
                for idx, streamer in enumerate(self.priority_list):
                    if line >= height - 6:
                        break

                    is_live = streamer in self.live_streams
                    is_current = streamer == self.current_stream
                    is_switching = streamer == self.switching_soon
                    stream_num = idx + 1
                    name = self.display_name(streamer)

                    try:
                        if is_live:
                            if is_current:
                                base_attr = curses.color_pair(1) | curses.A_BOLD
                            elif is_switching:
                                base_attr = curses.color_pair(4) | curses.A_BOLD
                            else:
                                base_attr = curses.color_pair(2)

                            stdscr.attron(base_attr)
                            # Number + name
                            stdscr.addstr(line, 2, f"{stream_num:2d}. {name:<{NAME_COL_WIDTH}s}")
                            # Status: emoji + viewer count
                            viewer_str = self.format_viewers(self.live_streams[streamer]['viewers'])
                            stdscr.addstr(line, STATUS_X, "🟢")
                            stdscr.addstr(line, STATUS_X + 2, f"{viewer_str:<4s} ")
                            # Game: bold for visual hierarchy
                            stdscr.attron(curses.A_BOLD)
                            game_str = self.truncate_text(self.live_streams[streamer]['game'], GAME_COL_WIDTH)
                            stdscr.addstr(line, GAME_X, f"{game_str:<{GAME_COL_WIDTH}s} ")
                            stdscr.attroff(curses.A_BOLD)
                            # Title: normal weight, framed in static [ ]
                            title_text_x = TITLE_X + 1
                            title_text_w = width - title_text_x - 3  # 2 right margin + 1 for "]"
                            if title_text_w > 0:
                                title = self.live_streams[streamer]['title']
                                stdscr.addstr(line, TITLE_X, "[")
                                stdscr.addstr(line, title_text_x, self.get_scroll_text(streamer, title, title_text_w))
                                stdscr.addstr(line, title_text_x + title_text_w, "]")
                            stdscr.attroff(base_attr)

                        else:
                            # Name in red
                            stdscr.attron(curses.color_pair(5))
                            stdscr.addstr(line, 2, f"{stream_num:2d}. {name:<{NAME_COL_WIDTH}s}")
                            stdscr.attroff(curses.color_pair(5))
                            # ⚫ OFF in dim grey
                            stdscr.attron(curses.A_DIM)
                            stdscr.addstr(line, STATUS_X, "⚫")
                            stdscr.addstr(line, STATUS_X + 2, "OFF  ")
                            stdscr.attroff(curses.A_DIM)

                    except curses.error:
                        pass

                    line += 1

                # Advance scroll offsets for live streams
                self.scroll_tick += 1
                if self.scroll_tick >= SCROLL_SPEED:
                    self.scroll_tick = 0
                    new_offsets = {}
                    for s in self.priority_list:
                        if s in self.live_streams:
                            title = self.live_streams[s]['title']
                            loop_len = len(title) + len(SCROLL_SEPARATOR)
                            new_offsets[s] = (self.scroll_offsets.get(s, 0) + 1) % loop_len
                    self.scroll_offsets = new_offsets

                # Bottom divider
                if line < height - 4:
                    stdscr.attron(curses.color_pair(5))
                    stdscr.addstr(line, 0, "├" + "─" * (width - 2) + "┤")
                    stdscr.attroff(curses.color_pair(5))
                    line += 1

                # Control buttons
                buttons = "(S)tart  (X) Stop  (Q)uit"
                stdscr.attron(curses.color_pair(3))
                stdscr.addstr(height - 3, 2, buttons)
                stdscr.attroff(curses.color_pair(3))

                # Input line
                if self.confirmation_pending:
                    prompt = f"Switch to {self.display_name(self.confirmation_stream).upper()}? (Enter to confirm, C to cancel): "
                else:
                    prompt = "Command (S/X/Q) or stream # (1-9, etc.): "

                # Draw the prompt and input
                try:
                    stdscr.addstr(height - 1, 2, prompt + self.selected_input)
                except curses.error:
                    pass

                # Footer
                stdscr.attron(curses.color_pair(5))
                stdscr.addstr(height - 4, 0, "└" + "─" * (width - 2) + "┘")
                stdscr.attroff(curses.color_pair(5))

                # Position cursor right after the input text (before refresh)
                cursor_x = 2 + len(prompt) + len(self.selected_input)
                cursor_y = height - 1
                if cursor_x < width - 1:
                    stdscr.move(cursor_y, cursor_x)

                stdscr.refresh()
                time.sleep(0.2)  # Refresh display frequently for responsive feedback

            except KeyboardInterrupt:
                return
            except curses.error:
                # Handle terminal resize
                pass
            except Exception as e:
                pass

    def run(self):
        """Start the UI"""
        try:
            curses.wrapper(self.draw_ui)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    ui = TwitchTVUI()
    ui.run()
