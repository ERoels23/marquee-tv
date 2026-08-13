#!/usr/bin/env python3
"""Marquee.tv Textual UI — interactive dashboard for managing Twitch streams."""
import json
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Static

from priority_list import parse_streamers_file, StreamerEntry
from marquee_model import ListNavigator, AdHocFlow, AdHocFlowState, AdHocMode, QuitConfirm
from marquee_render import HeaderData, RowData, render_header, render_row_collapsed, render_row_expanded_detail, header_border_label

SCRIPT_DIR = Path(__file__).parent
STREAMERS_FILE = SCRIPT_DIR / "streamers.txt"
STATUS_FILE = SCRIPT_DIR / ".status.json"
LAST_SEEN_FILE = SCRIPT_DIR / ".last_seen.json"
CONTROL_FILE = SCRIPT_DIR / ".control"
TWITCH_USER_ID = "60132775"
API_UPDATE_INTERVAL = 60
REFRESH_INTERVAL = 1.0

OUTER_WIDTH = 70
MARGIN = 2


class MarqueeApp(App):
    BINDINGS = [
        Binding("up,k", "move_up", "Up", show=False),
        Binding("down,j", "move_down", "Down", show=False),
    ]

    def __init__(self):
        super().__init__()
        self.entries: List[StreamerEntry] = []
        self.live_streams: Dict[str, Dict] = {}
        self.last_seen: Dict[str, str] = {}
        self.current_stream: Optional[str] = None
        self.stream_alive = False
        self.ad_hoc_mode: Optional[str] = None
        self.last_api_poll = 0.0
        self.nav = ListNavigator(0)

    def compose(self) -> ComposeResult:
        yield Static(id="frame", markup=False)

    def on_mount(self) -> None:
        self.load_entries()
        self.refresh_data(force=True)
        self.render_frame()
        self.set_interval(REFRESH_INTERVAL, self.tick)

    def load_entries(self) -> None:
        self.entries = parse_streamers_file(STREAMERS_FILE)
        self.nav.set_count(len(self.entries))

    def daemon_running(self) -> bool:
        result = subprocess.run(
            ["pgrep", "-f", "python3.*marquee_daemon.py"],
            capture_output=True,
        )
        return result.returncode == 0

    def poll_live_streams_from_api(self) -> Dict[str, Dict]:
        """Direct Twitch API poll — fallback used only when the daemon isn't running."""
        try:
            result = subprocess.run(
                ["/usr/bin/twitch", "api", "get", "/streams/followed", "-q", f"user_id={TWITCH_USER_ID}"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                return {}
            data = json.loads(result.stdout)
            live = {}
            for stream in data.get('data', []):
                name = stream['user_name'].lower()
                live[name] = {
                    'title': stream['title'],
                    'game': stream['game_name'],
                    'viewers': stream['viewer_count'],
                    'started_at': stream.get('started_at'),
                }
            return live
        except Exception:
            return {}

    def refresh_data(self, force: bool = False) -> None:
        import time
        now = time.time()
        if not force and now - self.last_api_poll < API_UPDATE_INTERVAL:
            self._load_status_file()
            return
        self.last_api_poll = now
        if self.daemon_running() and STATUS_FILE.exists():
            self._load_status_file()
        else:
            self.live_streams = self.poll_live_streams_from_api()
        self._load_last_seen_file()

    def _load_status_file(self) -> None:
        if not STATUS_FILE.exists():
            return
        try:
            with open(STATUS_FILE, 'r') as f:
                status = json.load(f)
            self.current_stream = status.get('current_stream')
            self.stream_alive = status.get('stream_alive', False)
            self.live_streams = status.get('live_streams', self.live_streams)
        except (json.JSONDecodeError, OSError):
            pass

    def _load_last_seen_file(self) -> None:
        if not LAST_SEEN_FILE.exists():
            return
        try:
            with open(LAST_SEEN_FILE, 'r') as f:
                self.last_seen = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    def tick(self) -> None:
        self.refresh_data()
        self.render_frame()

    def _header_data(self) -> HeaderData:
        if not self.current_stream:
            return HeaderData(active=False)
        info = self.live_streams.get(self.current_stream, {})
        return HeaderData(
            active=True,
            name=self.current_stream,
            is_live=self.current_stream in self.live_streams,
            viewers=info.get('viewers'),
            game=info.get('game', ''),
            started_at=info.get('started_at'),
            title=info.get('title', ''),
            ad_hoc_mode=self.ad_hoc_mode,
        )

    def _row_data(self) -> List[RowData]:
        rows = []
        for entry in self.entries:
            info = self.live_streams.get(entry.username)
            is_live = info is not None
            rows.append(RowData(
                name=entry.display_name,
                is_live=is_live,
                viewers=info.get('viewers') if info else None,
                game=info.get('game', '') if info else '',
                title=info.get('title', '') if info else '',
                started_at=info.get('started_at') if info else None,
                last_seen=self.last_seen.get(entry.username),
            ))
        return rows

    def render_frame(self) -> None:
        inner_width = OUTER_WIDTH - 2
        header_box_width = inner_width - 2
        header_inner = header_box_width - 2

        lines: List[str] = []
        lines.append("Marquee.tv")
        lines.append("")
        header_lines = render_header(self._header_data(), header_inner)
        lines.extend(header_lines)
        lines.append("")

        rows = self._row_data()
        for i, row in enumerate(rows):
            width = inner_width if i == self.nav.index else inner_width - 2 * MARGIN
            collapsed = render_row_collapsed(row, width)
            prefix = "> " if i == self.nav.index else "  "
            lines.append(prefix + collapsed)
            if i == self.nav.index:
                lines.append("  " + render_row_expanded_detail(row, inner_width - 2))

        self.query_one("#frame", Static).update("\n".join(lines))

    def action_move_up(self) -> None:
        self.nav.move_up()
        self.render_frame()

    def action_move_down(self) -> None:
        self.nav.move_down()
        self.render_frame()


if __name__ == "__main__":
    MarqueeApp().run()
