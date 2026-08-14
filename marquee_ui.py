#!/usr/bin/env python3
"""Marquee.tv Textual UI — interactive dashboard for managing Twitch streams."""
import json
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional

from rich.cells import cell_len, set_cell_size
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from priority_list import parse_streamers_file, StreamerEntry
from marquee_model import ListNavigator, AdHocFlow, AdHocFlowState, AdHocMode
from marquee_render import (
    HeaderData, RowData, render_header, render_row_collapsed, render_row_expanded_detail,
    header_border_label, STATUS_DOT,
)
from mpv_ipc import set_title
from ui_format import build_mpv_title, format_uptime, format_viewers, format_last_seen

SCRIPT_DIR = Path(__file__).parent
STREAMERS_FILE = SCRIPT_DIR / "streamers.txt"
STATUS_FILE = SCRIPT_DIR / ".status.json"
LAST_SEEN_FILE = SCRIPT_DIR / ".last_seen.json"
CONTROL_FILE = SCRIPT_DIR / ".control"
API_UPDATE_INTERVAL = 60
REFRESH_INTERVAL = 1.0
TRANSITION_DURATION = 5.0
# Manual switches start their transition the instant Enter is pressed, before
# the daemon has even seen the request — its own poll interval plus the
# actual mpv relaunch can easily exceed TRANSITION_DURATION, so the "SWITCHING
# NOW..." animation keeps running past 5s (rather than reverting to the old,
# now-stale stream) as long as the target hasn't landed yet — bounded by this
# safety net so it can't get stuck forever if the switch never completes.
TRANSITION_MAX_WAIT = 30.0
PENDING_MANUAL_SWITCH_TIMEOUT = 30.0  # bounds how long a stale pending-switch can mislabel a later transition

MIN_OUTER_WIDTH = 60  # floor below which the box layout starts breaking down
MIN_OUTER_HEIGHT = 10  # floor below which there's no room to pad the border to fill height
MARGIN = 2

# Catppuccin Mocha accents (matches the user's terminal/desktop theme).
BORDER_COLOR = "#74c7ec"  # Sapphire — outer box, NOW WATCHING/PRIORITY LIST boxes, labels, footer
HIGHLIGHT_STYLE = "bold #1e1e2e on #b4befe"  # dark text on Lavender bar
LIVE_DOT_STYLE = "#a6e3a1"  # Green — lit
OFFLINE_DOT_STYLE = "#6c7086"  # Grey — unlit

class QuitConfirmModal(ModalScreen[Optional[str]]):
    """Centered popup letting the user pick whether quitting also stops the
    daemon. Dismisses with "stop", "keep", or None (escaped — stay open)."""

    # width: auto on a Vertical panel doesn't reliably measure Static children
    # in this Textual version (they resolve to a degenerate 0-width box) — use
    # a definite width instead so the layout actually has something to size
    # the children against.
    DEFAULT_CSS = f"""
    QuitConfirmModal {{
        align: center middle;
    }}
    QuitConfirmModal > Vertical {{
        width: 46;
        height: auto;
        border: round {BORDER_COLOR};
        padding: 1 2;
    }}
    """

    OPTIONS = [
        ("stop", "Quit and stop daemon"),
        ("keep", "Quit and keep daemon running"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.index = 0

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Quit Marquee.tv?")
            yield Static("")
            for i in range(len(self.OPTIONS)):
                yield Static("", id=f"quit-option-{i}")

    def on_mount(self) -> None:
        self._render_options()

    def _render_options(self) -> None:
        for i, (_, label) in enumerate(self.OPTIONS):
            widget = self.query_one(f"#quit-option-{i}", Static)
            if i == self.index:
                widget.update(Text(f"▶ {label}", style=HIGHLIGHT_STYLE))
            else:
                widget.update(f"  {label}")

    def on_key(self, event: events.Key) -> None:
        event.stop()
        event.prevent_default()
        if event.key in ("down", "j"):
            self.index = (self.index + 1) % len(self.OPTIONS)
            self._render_options()
        elif event.key in ("up", "k"):
            self.index = (self.index - 1) % len(self.OPTIONS)
            self._render_options()
        elif event.key == "enter":
            self.dismiss(self.OPTIONS[self.index][0])
        elif event.key == "escape":
            self.dismiss(None)


class InfoModal(ModalScreen):
    """Centered popup showing as much info as possible for whichever
    streamer is highlighted in the priority list — live stats if they're
    live, otherwise their last-known info — plus their channel bio."""

    DEFAULT_CSS = f"""
    InfoModal {{
        align: center middle;
    }}
    InfoModal > Vertical {{
        width: 70%;
        min-width: 42;
        max-width: 100;
        height: auto;
        border: round {BORDER_COLOR};
        padding: 1 2;
    }}
    """

    def __init__(self, row: RowData) -> None:
        super().__init__()
        self.row = row
        self.footer_key: Optional[str] = None

    def compose(self) -> ComposeResult:
        row = self.row
        name = row.name if row.username == row.name else f"{row.name} ({row.username})"
        status_line = Text("Status: ")
        status_line.append(STATUS_DOT, style=LIVE_DOT_STYLE if row.is_live else OFFLINE_DOT_STYLE)
        status_line.append(" Live" if row.is_live else " Offline")
        with Vertical():
            yield Static(f"Marquee.tv — Info: {name}")
            yield Static("")
            yield Static(status_line)
            if row.is_live:
                yield Static(f"Category: {row.game or 'unknown'}")
                uptime = format_uptime(row.started_at) if row.started_at else "unknown"
                yield Static(f"Uptime: {uptime}")
                viewers = format_viewers(row.viewers) if row.viewers is not None else "unknown"
                yield Static(f"Viewers: {viewers}")
                yield Static(f"Title: {row.title or '(no title)'}", id="info-title")
            else:
                last_seen = row.last_seen or {}
                yield Static(f"Last live: {format_last_seen(last_seen.get('at'))}")
                yield Static(f"Category: {last_seen.get('game') or 'unknown'}")
                yield Static(f"Title: {last_seen.get('title') or 'unknown'}", id="info-title")
            yield Static("")
            yield Static("Channel bio:")
            yield Static("(loading...)", id="bio")
            yield Static("")
            yield Static("", id="info-footer")

    def on_mount(self) -> None:
        self._render_footer()

    def _render_footer(self) -> None:
        style = HIGHLIGHT_STYLE if self.footer_key == "o" else None
        line = Text()
        line.append("(O)pen in Browser", style=style)
        line.append("   (I)/(Esc) Close")
        self.query_one("#info-footer", Static).update(line)

    def update_bio(self, bio: str) -> None:
        self.query_one("#bio", Static).update(bio)

    def on_key(self, event: events.Key) -> None:
        event.stop()
        event.prevent_default()
        if event.key in ("i", "escape"):
            self.dismiss()
        elif event.key == "o":
            self.footer_key = "o"
            self._render_footer()
            import webbrowser
            webbrowser.open(f"https://twitch.tv/{self.row.username}")
        else:
            event.stop()
            event.prevent_default()


class MarqueeApp(App):
    # Screen defaults to overflow-y: auto, which reserves a scrollbar gutter
    # (2 columns) whenever content is taller than the terminal — e.g. once the
    # priority list has enough entries. That silently narrows the #frame
    # widget's actual width below self._terminal_width, so every hand-sized
    # line becomes too wide for its box and wraps, breaking the whole layout.
    # Disable it: content that doesn't fit vertically is clipped, not scrolled.
    CSS = """
    Screen {
        overflow-y: hidden;
    }
    """

    BINDINGS = [
        Binding("up,k", "move_up", "Up", show=False),
        Binding("down,j", "move_down", "Down", show=False),
        Binding("enter", "launch", "Launch", show=False),
        Binding("slash", "ad_hoc_start", "Ad-hoc", show=False),
        Binding("escape", "ad_hoc_cancel", "Cancel", show=False),
        Binding("e", "edit_list", "Edit", show=False),
        Binding("i", "toggle_info", "Info", show=False),
        Binding("s", "start_service", "Start", show=False),
        Binding("x", "stop_service", "Stop", show=False),
        Binding("q", "request_quit", "Quit", show=False),
    ]

    def __init__(self):
        super().__init__()
        # "ansi-dark" maps background/foreground to the terminal's own native
        # ANSI default colors instead of a fixed RGB, so the app's background
        # matches whatever theme the user's terminal is configured with.
        self.theme = "ansi-dark"
        self.entries: List[StreamerEntry] = []
        self.live_streams: Dict[str, Dict] = {}
        self.last_seen: Dict[str, str] = {}
        self.current_stream: Optional[str] = None
        self.stream_alive = False
        self.ad_hoc_mode: Optional[str] = None
        self.last_api_poll = 0.0
        self._daemon_was_running = False
        self.nav = ListNavigator(0)
        self.ad_hoc = AdHocFlow()
        self.one_shots: Dict[str, dict] = {}
        self._pending_manual_switch: Optional[tuple] = None  # (streamer, monotonic_time)
        self.transition: Optional[Dict] = None  # {"kind": "auto"|"manual", "started": monotonic}
        self.last_footer_key: Optional[str] = None
        self._terminal_width = MIN_OUTER_WIDTH
        self._terminal_height = MIN_OUTER_HEIGHT
        self.list_scroll_offset = 0  # index of the first entry currently shown, for long lists

    def compose(self) -> ComposeResult:
        yield Static(id="frame", markup=False)

    def on_mount(self) -> None:
        self.load_entries()
        self._terminal_width = self.size.width
        self._terminal_height = self.size.height
        self.refresh_data(force=True)
        self.render_frame()
        self.set_interval(REFRESH_INTERVAL, self.tick)

    def on_resize(self, event: events.Resize) -> None:
        # self.size lags a cycle behind inside this handler; event.size is current.
        self._terminal_width = event.size.width
        self._terminal_height = event.size.height
        self.render_frame()

    def load_entries(self) -> None:
        self.entries = parse_streamers_file(STREAMERS_FILE)
        separator_indices = {i for i, e in enumerate(self.entries) if e.is_separator}
        self.nav.set_count(len(self.entries), skip_indices=separator_indices)

    def daemon_running(self) -> bool:
        result = subprocess.run(
            ["pgrep", "-f", "python3.*marquee_daemon.py"],
            capture_output=True,
        )
        return result.returncode == 0

    def poll_live_streams_from_api(self) -> Dict[str, Dict]:
        """Direct Twitch API poll — fallback used only when the daemon isn't running.
        Queries by explicit user_login rather than /streams/followed, since the
        priority list can include streamers the account doesn't follow — a
        followed-only query would silently never report them live."""
        usernames = [e.username for e in self.entries]
        if not usernames:
            return {}
        live: Dict[str, Dict] = {}
        try:
            for i in range(0, len(usernames), 100):  # Helix caps user_login at 100/request
                batch = usernames[i:i + 100]
                cmd = ["twitch", "api", "get", "streams"]
                for name in batch:
                    cmd += ["-q", f"user_login={name}"]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                # Judge success by whether stdout parses, not the exit code:
                # the twitch CLI can crash in its own unrelated update-check
                # code after already printing valid JSON.
                try:
                    data = json.loads(result.stdout)
                except json.JSONDecodeError:
                    continue
                for stream in data.get('data', []):
                    name = stream['user_login'].lower()
                    live[name] = {
                        'title': stream['title'],
                        'game': stream['game_name'],
                        'viewers': stream['viewer_count'],
                        'started_at': stream.get('started_at'),
                    }
            return live
        except Exception:
            return live

    def poll_single_stream_from_api(self, streamer: str) -> Optional[Dict]:
        """Query Twitch for one streamer's live info, regardless of follow status."""
        try:
            result = subprocess.run(
                ["twitch", "api", "get", "streams", "-q", f"user_login={streamer}"],
                capture_output=True, text=True, timeout=10,
            )
            # Judge success by whether stdout parses, not the exit code: the
            # twitch CLI can crash in its own unrelated update-check code
            # after already printing valid JSON.
            data = json.loads(result.stdout)
            streams = data.get('data', [])
            if not streams:
                return None
            stream = streams[0]
            return {
                'title': stream['title'],
                'game': stream['game_name'],
                'viewers': stream['viewer_count'],
                'started_at': stream.get('started_at'),
            }
        except Exception:
            return None

    def refresh_data(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self.last_api_poll < API_UPDATE_INTERVAL:
            if not self._daemon_was_running:
                # Cheap re-check: only pay for a pgrep call while we think the
                # daemon is offline, so a daemon started outside of (s) (e.g.
                # by selecting a stream directly) is detected within a tick
                # instead of sitting stale until the next full poll (up to
                # API_UPDATE_INTERVAL later).
                self._daemon_was_running = self.daemon_running() and STATUS_FILE.exists()
            if self._daemon_was_running:
                self._load_status_file()
            self._load_last_seen_file()  # cheap local file read — no reason to rate-limit it
            return
        self.last_api_poll = now
        self._daemon_was_running = self.daemon_running() and STATUS_FILE.exists()
        if self._daemon_was_running:
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
            previous_stream = self.current_stream
            self.current_stream = status.get('current_stream')
            self.stream_alive = status.get('stream_alive', False)
            self.live_streams = status.get('live_streams', self.live_streams)
        except (json.JSONDecodeError, OSError):
            return
        if (previous_stream is not None and self.current_stream is not None
                and self.current_stream != previous_stream):
            kind = "auto"
            if self._pending_manual_switch is not None:
                pending_streamer, pending_time = self._pending_manual_switch
                if (pending_streamer == self.current_stream
                        and time.monotonic() - pending_time < PENDING_MANUAL_SWITCH_TIMEOUT):
                    kind = "manual"
            self._pending_manual_switch = None
            self.transition = {"kind": kind, "started": time.monotonic(), "target": self.current_stream}

    def _load_last_seen_file(self) -> None:
        if not LAST_SEEN_FILE.exists():
            return
        try:
            with open(LAST_SEEN_FILE, 'r') as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return
        # Migrate the legacy flat "streamer -> ISO timestamp" format to
        # {"at": ..., "game": ..., "title": ...}.
        self.last_seen = {
            streamer: ({"at": value, "game": None, "title": None} if isinstance(value, str) else value)
            for streamer, value in data.items()
        }

    def tick(self) -> None:
        self.refresh_data()
        self.render_frame()

    def _transition_header_lines(self, width: int) -> Optional[List[str]]:
        """Returns the 3 NOW WATCHING lines for an in-progress switch transition,
        or None if no transition is active (caller falls back to render_header)."""
        if self.transition is None:
            return None
        elapsed = time.monotonic() - self.transition["started"]
        target = self.transition.get("target")
        waiting_on_target = (
            self.transition["kind"] == "manual"
            and target is not None
            and self.current_stream != target
        )
        expiry = TRANSITION_MAX_WAIT if waiting_on_target else TRANSITION_DURATION
        if elapsed >= expiry:
            self.transition = None
            return None
        headline = (
            "FOUND HIGHER PRIORITY STREAM" if self.transition["kind"] == "auto"
            else "NEW STREAM SELECTED"
        )
        dots = "." * ((int(elapsed) % 3) + 1)
        return [
            set_cell_size(headline, width),
            set_cell_size(f"SWITCHING NOW{dots}", width),
            " " * width,
        ]

    def _header_data(self) -> HeaderData:
        if not self._daemon_was_running:
            return HeaderData(active=False, inactive_message="Daemon Offline")
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
            if entry.is_separator:
                rows.append(RowData(name="", is_live=False, is_separator=True))
                continue
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
                username=entry.username,
            ))
        return rows

    def _compute_list_window(self, total: int, available_lines: int) -> tuple:
        """Decide which slice of the priority list to actually render, so a
        list longer than fits on screen scrolls (minimum-scroll: the window
        only moves enough to keep the highlighted entry in view) instead of
        silently clipping off the bottom with no way to tell it happened.
        Returns (start, end, show_top_indicator, show_bottom_indicator)."""
        if total == 0:
            return 0, 0, False, False
        idx = self.nav.index if 0 <= self.nav.index < total else 0

        # A highlighted row costs 4 lines (blank + collapsed + detail + blank)
        # vs. 1 for a normal row — the "+3" below accounts for that difference
        # whenever the window includes the highlighted entry, which it always
        # does here since scroll_offset always brackets idx.
        full_cost = (total - 1) + 4
        if full_cost <= available_lines:
            self.list_scroll_offset = 0
            return 0, total, False, False

        # Once windowing is active at all, always reserve room for both
        # indicators (even if only one ends up shown) rather than
        # conditionally reserving based on the current window's top/bottom
        # state — that would shrink capacity the moment an indicator first
        # appears, occasionally jumping the window by 2 rows in one step
        # instead of the usual 1 as the highlight crosses that boundary.
        max_count = max(1, available_lines - 2 - 3)
        start = self.list_scroll_offset
        if start > idx:
            start = idx
        if idx >= start + max_count:
            start = idx - max_count + 1
        start = max(0, min(start, max(0, total - max_count)))
        end = min(total, start + max_count)
        self.list_scroll_offset = start
        return start, end, start > 0, end < total

    @staticmethod
    def _styled_line(*parts: tuple) -> Text:
        """parts: (text, style_or_None) tuples, concatenated into one styled line."""
        line = Text()
        for content, style in parts:
            line.append(content, style=style)
        return line

    FOOTER_SEGMENTS = [
        (None, "(Q)uit"),
        ("s", "(S)tart"),
        ("x", "(X)Stop"),
        ("e", "(E)dit"),
        ("slash", "(/)Ad-hoc"),
        ("i", "(I)nfo"),
        ("enter", "⏎ Launch"),
    ]

    def _footer_parts(self, width: int) -> list:
        """Keybind glossary as (text, style) parts, with whichever key was
        last pressed (from FOOTER_SEGMENTS) highlighted like a selected row —
        immediate confirmation that the press registered, even before its
        effect (e.g. mpv/chatterino relaunching) is visible. Only the literal
        hotkey label itself is styled — separators and trailing fill stay
        unstyled, so the highlight never bleeds into surrounding whitespace."""
        joined_plain = " ".join(text for _, text in self.FOOTER_SEGMENTS)
        if cell_len(joined_plain) > width:
            return [(set_cell_size(joined_plain, width), None)]  # narrow-terminal fallback
        pad = width - cell_len(joined_plain)
        parts = []
        for i, (key, text) in enumerate(self.FOOTER_SEGMENTS):
            style = HIGHLIGHT_STYLE if key is not None and key == self.last_footer_key else None
            parts.append((text, style))
            if i < len(self.FOOTER_SEGMENTS) - 1:
                parts.append((" ", None))
        if pad:
            parts.append((" " * pad, None))
        return parts

    def render_frame(self) -> None:
        # Widths below are hand-tuned (not the plan's original formulas, which had
        # off-by-one/two errors) to keep every rendered line at exactly outer_width
        # cells — verify with rich.cells.cell_len if you touch these.
        outer_width = max(MIN_OUTER_WIDTH, self._terminal_width)
        inner_width = outer_width - 2
        header_box_width = inner_width - 4
        header_inner = header_box_width - 2
        list_box_width = inner_width - 2 * MARGIN - 2
        list_inner = list_box_width - 2

        B = BORDER_COLOR
        lines: List[Text] = []
        lines.append(Text(
            "╔═ Marquee.tv " + "═" * (outer_width - cell_len("╔═ Marquee.tv ") - 1) + "╗",
            style=B,
        ))

        label = f" {header_border_label(self.ad_hoc_mode)} "
        lines.append(Text("║ ┌" + label + "─" * (header_box_width - cell_len(label)) + "┐ ║", style=B))
        header_has_dot = False
        header_is_live = False
        if self.ad_hoc.state == AdHocFlowState.TYPING:
            header_lines = [
                set_cell_size(f"Watch streamer: {self.ad_hoc.buffer}", header_inner),
                " " * header_inner,
                " " * header_inner,
            ]
        elif self.ad_hoc.state == AdHocFlowState.MODE_SELECT:
            header_lines = [
                set_cell_size(f'Watch "{self.ad_hoc.pending_name}" as:', header_inner),
                set_cell_size("[O] Override   [T] Temporary   [1] One-Shot", header_inner),
                " " * header_inner,
            ]
        else:
            header_lines = self._transition_header_lines(header_inner)
            if header_lines is None:
                header_data = self._header_data()
                header_lines = render_header(header_data, header_inner)
                header_has_dot = header_data.active
                header_is_live = header_data.is_live
        for idx, text in enumerate(header_lines):
            if idx == 0 and header_has_dot:
                dot_style = LIVE_DOT_STYLE if header_is_live else OFFLINE_DOT_STYLE
                lines.append(self._styled_line(
                    ("║ │ ", B), (text[:1], dot_style), (text[1:], None), (" │ ║", B),
                ))
            else:
                lines.append(self._styled_line(("║ │ ", B), (text, None), (" │ ║", B)))
        lines.append(Text("║ └" + "─" * header_box_width + "┘ ║", style=B))
        lines.append(Text("║" + " " * inner_width + "║", style=B))

        list_label = " PRIORITY LIST "
        lines.append(Text(
            "║" + " " * MARGIN + "┌" + list_label
            + "─" * (list_box_width - cell_len(list_label)) + "┐" + " " * MARGIN + "║",
            style=B,
        ))

        terminal_height = max(MIN_OUTER_HEIGHT, self._terminal_height)
        # Remaining fixed lines after the rows: list bottom border, blank
        # spacer, footer, outer bottom border.
        available_lines = max(1, terminal_height - len(lines) - 4)
        rows = self._row_data()
        start, end, show_top, show_bottom = self._compute_list_window(len(rows), available_lines)
        if show_top:
            indicator = set_cell_size(f"▲ {start} more above", list_inner)
            lines.append(self._styled_line(
                ("║" + " " * MARGIN + "│ ", B), (indicator, None), (" │" + " " * MARGIN + "║", B),
            ))
        for i in range(start, end):
            row = rows[i]
            if row.is_separator:
                # A "---" line in streamers.txt: a horizontal rule connecting
                # with the box's own side borders, never highlighted or
                # landed on by navigation (ListNavigator skips it).
                lines.append(Text(
                    "║" + " " * MARGIN + "├" + "─" * (list_inner + 2) + "┤" + " " * MARGIN + "║",
                    style=B,
                ))
            elif i == self.nav.index:
                lines.append(Text("║" + " " * inner_width + "║", style=B))
                collapsed = render_row_collapsed(row, inner_width - 3, highlighted=True)
                highlight_dot_style = f"bold {LIVE_DOT_STYLE if row.is_live else OFFLINE_DOT_STYLE} on #b4befe"
                lines.append(self._styled_line(
                    ("║", B), ("▶ ", HIGHLIGHT_STYLE),
                    (collapsed[:1], highlight_dot_style), (collapsed[1:] + " ", HIGHLIGHT_STYLE),
                    ("║", B),
                ))
                detail = render_row_expanded_detail(row, inner_width - 4)
                lines.append(self._styled_line(
                    ("║", B), ("  " + detail + "  ", HIGHLIGHT_STYLE), ("║", B),
                ))
                lines.append(Text("║" + " " * inner_width + "║", style=B))
            else:
                collapsed = render_row_collapsed(row, list_inner)
                dot_style = LIVE_DOT_STYLE if row.is_live else OFFLINE_DOT_STYLE
                lines.append(self._styled_line(
                    ("║" + " " * MARGIN + "│ ", B),
                    (collapsed[:1], dot_style), (collapsed[1:], None),
                    (" │" + " " * MARGIN + "║", B),
                ))
        if show_bottom:
            indicator = set_cell_size(f"▼ {len(rows) - end} more below", list_inner)
            lines.append(self._styled_line(
                ("║" + " " * MARGIN + "│ ", B), (indicator, None), (" │" + " " * MARGIN + "║", B),
            ))
        nav_label = " ↑↓/jk "
        lines.append(Text(
            "║" + " " * MARGIN + "└" + nav_label
            + "─" * (list_box_width - cell_len(nav_label)) + "┘" + " " * MARGIN + "║",
            style=B,
        ))
        lines.append(Text("║" + " " * inner_width + "║", style=B))

        # Pad with blank bordered lines so the outer box reaches the bottom of
        # the terminal instead of shrink-wrapping to just the priority list —
        # footer + closing border still need to fit below this padding.
        padding_needed = terminal_height - len(lines) - 2
        for _ in range(max(0, padding_needed)):
            lines.append(Text("║" + " " * inner_width + "║", style=B))

        footer_parts = self._footer_parts(inner_width - 2)
        lines.append(self._styled_line(("║ ", B), *footer_parts, (" ║", B)))
        lines.append(Text("╚" + "═" * inner_width + "╝", style=B))

        frame = Text("\n").join(lines)
        self.query_one("#frame", Static).update(frame)

    def action_move_up(self) -> None:
        self.last_footer_key = None
        self.nav.move_up()
        self.render_frame()

    def action_move_down(self) -> None:
        self.last_footer_key = None
        self.nav.move_down()
        self.render_frame()

    def action_launch(self) -> None:
        self.last_footer_key = "enter"
        if not (0 <= self.nav.index < len(self.entries)):
            self.render_frame()
            return
        streamer = self.entries[self.nav.index].username
        with open(CONTROL_FILE, 'w') as f:
            f.write(f"switch:{streamer}")
        if not self.daemon_running():
            self.start_service()
        self.ad_hoc_mode = None
        self._pending_manual_switch = (streamer, time.monotonic())
        # Immediate feedback rather than waiting for the daemon's status file
        # to confirm the switch, which can lag up to its own poll interval.
        self.transition = {"kind": "manual", "started": time.monotonic(), "target": streamer}
        self.render_frame()

    def action_ad_hoc_start(self) -> None:
        self.last_footer_key = "slash"
        self.ad_hoc.start()
        self.render_frame()

    def action_ad_hoc_cancel(self) -> None:
        self.ad_hoc.cancel()
        self.render_frame()

    def action_edit_list(self) -> None:
        self.last_footer_key = "e"
        import subprocess as sp
        with self.suspend():
            try:
                sp.run(["nvim", str(STREAMERS_FILE)])
            except OSError:
                pass
        self.load_entries()
        self.render_frame()

    async def action_toggle_info(self) -> None:
        self.last_footer_key = "i"
        self.render_frame()  # bake the highlight into #frame even though the modal covers it
        if not (0 <= self.nav.index < len(self.entries)):
            return
        row = self._row_data()[self.nav.index]
        modal = InfoModal(row)
        # Await the mount so the modal's #bio widget exists before the bio-fetch
        # worker (which can complete almost instantly) tries to update it.
        await self.push_screen(modal)
        self.run_worker(self._load_channel_bio(row.username, modal), exclusive=True)

    async def _load_channel_bio(self, streamer: str, modal: "InfoModal") -> None:
        import asyncio
        bio = await asyncio.to_thread(self._fetch_channel_bio, streamer)
        if modal.is_attached:
            modal.update_bio(bio)

    def _fetch_channel_bio(self, streamer: str) -> str:
        import subprocess as sp
        try:
            result = sp.run(
                ["twitch", "api", "get", "users", "-q", f"login={streamer}"],
                capture_output=True, text=True, timeout=10,
            )
            # Judge success by whether stdout parses, not the exit code: the
            # twitch CLI can crash in its own unrelated update-check code
            # after already printing valid JSON.
            data = json.loads(result.stdout)
            users = data.get('data', [])
            if not users:
                return "(no bio available)"
            return users[0].get('description') or "(no bio set)"
        except Exception:
            return "(unable to fetch channel bio)"

    def start_service(self) -> None:
        import subprocess as sp
        sp.run([str(SCRIPT_DIR / "marquee.sh"), "start"], capture_output=True)

    def stop_service(self) -> None:
        import subprocess as sp
        sp.run([str(SCRIPT_DIR / "marquee.sh"), "stop"], capture_output=True)
        sp.run(["pkill", "-f", "mpv"], capture_output=True)
        sp.run(["pkill", "-x", "chatterino"], capture_output=True)

    async def action_start_service(self) -> None:
        self.last_footer_key = "s"
        already_running = self.daemon_running()
        self.render_frame()  # paint the highlight before the blocking start_service call
        if not already_running:
            import asyncio
            await asyncio.to_thread(self.start_service)
            self.refresh_data(force=True)
            self.render_frame()

    async def action_stop_service(self) -> None:
        self.last_footer_key = "x"
        running = self.daemon_running()
        self.render_frame()  # paint the highlight before the blocking stop_service call
        if running:
            import asyncio
            await asyncio.to_thread(self.stop_service)
            self.current_stream = None
            self.stream_alive = False
            self.refresh_data(force=True)
            self.render_frame()

    def action_request_quit(self) -> None:
        import subprocess as sp

        if not self.daemon_running():
            # Nothing to ask about — there's no daemon to optionally keep running.
            sp.run(["pkill", "-x", "chatterino"], capture_output=True)
            self.exit()
            return

        def handle_result(choice: Optional[str]) -> None:
            if choice == "stop":
                self.stop_service()  # also kills chatterino
                self.exit()
            elif choice == "keep":
                # Daemon (and its stream + chatterino) keep running — leave
                # chatterino open too, it's still the companion chat window
                # for whatever's still playing.
                self.exit()
            # None (escaped) — stay open, don't quit

        self.push_screen(QuitConfirmModal(), handle_result)

    async def on_key(self, event) -> None:
        if self.ad_hoc.state == AdHocFlowState.TYPING:
            event.stop()
            event.prevent_default()
            if event.key == "escape":
                self.ad_hoc.cancel()
            elif event.key == "enter":
                self.ad_hoc.submit_name()
            elif event.key == "backspace":
                self.ad_hoc.backspace()
            elif event.character and event.character.isprintable():
                self.ad_hoc.type_char(event.character)
            self.render_frame()
        elif self.ad_hoc.state == AdHocFlowState.MODE_SELECT:
            event.stop()
            event.prevent_default()
            if event.key == "escape":
                self.ad_hoc.cancel()
                self.render_frame()
                return
            key = event.key.lower()
            mode = None
            if key == "o":
                mode = AdHocMode.OVERRIDE
            elif key == "t":
                mode = AdHocMode.TEMPORARY
            elif key == "1":
                mode = AdHocMode.ONESHOT
            if mode is not None:
                result = self.ad_hoc.choose_mode(mode)
                if result:
                    streamer, chosen_mode = result
                    self.launch_ad_hoc(streamer, chosen_mode)
            self.render_frame()

    def launch_ad_hoc(self, streamer: str, mode) -> None:
        if mode == AdHocMode.ONESHOT:
            self.spawn_one_shot(streamer)
            return
        with open(CONTROL_FILE, 'w') as f:
            f.write(f"switch:{streamer}:{mode.value}")
        if not self.daemon_running():
            self.start_service()
        self.ad_hoc_mode = mode.value
        self._pending_manual_switch = (streamer, time.monotonic())
        # Immediate feedback rather than waiting for the daemon's status file
        # to confirm the switch, which can lag up to its own poll interval.
        self.transition = {"kind": "manual", "started": time.monotonic(), "target": streamer}
        self.render_frame()

    def spawn_one_shot(self, streamer: str) -> None:
        """Launch a fully independent streamlink/mpv process, untracked by the daemon."""
        socket_path = SCRIPT_DIR / f".mpv-oneshot-{streamer}.sock"
        socket_path.unlink(missing_ok=True)
        player_args = (
            "--profile=twitch --volume=60 --force-seekable=yes "
            "--demuxer-lavf-o=fflags=+genpts+discardcorrupt "
            f"--input-ipc-server={socket_path}"
        )
        cmd = [
            "streamlink", "--player", "mpv",
            "--player-args", player_args,
            "--hls-live-edge", "3", "--twitch-low-latency",
            "--title", "{author} ::: {game} ::: {title}",
            f"twitch.tv/{streamer}", "best",
        ]
        process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # This system's Chatterino build needs kill-before-relaunch (see marquee_daemon.py
        # launch_stream for the same pattern/reasoning) — apply it here too for consistency.
        subprocess.run(["pkill", "-x", "chatterino"], capture_output=True)
        for _ in range(20):
            if subprocess.run(["pgrep", "-x", "chatterino"], capture_output=True).returncode != 0:
                break
            time.sleep(0.1)
        try:
            subprocess.Popen(["chatterino", "-a", streamer], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            pass
        self.render_frame()
        timer = self.set_interval(API_UPDATE_INTERVAL, lambda: self._poll_one_shot_title(streamer), pause=False)
        if streamer in self.one_shots:
            self.one_shots[streamer]["timer"].stop()
        self.one_shots[streamer] = {"process": process, "timer": timer, "socket_path": socket_path}

    def _poll_one_shot_title(self, streamer: str) -> None:
        entry = self.one_shots.get(streamer)
        if entry is None:
            return
        if entry["process"].poll() is not None:
            # streamlink/mpv process has exited — stop polling and clean up
            entry["timer"].stop()
            entry["socket_path"].unlink(missing_ok=True)
            del self.one_shots[streamer]
            return
        info = self.poll_single_stream_from_api(streamer)
        if info:
            set_title(entry["socket_path"], build_mpv_title(streamer, info['game'], info['title']))


if __name__ == "__main__":
    MarqueeApp().run()
