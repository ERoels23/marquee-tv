#!/usr/bin/env python3
"""Marquee.tv Textual UI — interactive dashboard for managing Twitch streams."""
import json
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from priority_list import parse_streamers_file, StreamerEntry
from marquee_model import ListNavigator, AdHocFlow, AdHocFlowState, AdHocMode
from marquee_render import HeaderData, RowData, render_header, render_row_collapsed, render_row_expanded_detail, header_border_label
from mpv_ipc import set_title
from ui_format import build_mpv_title

SCRIPT_DIR = Path(__file__).parent
STREAMERS_FILE = SCRIPT_DIR / "streamers.txt"
STATUS_FILE = SCRIPT_DIR / ".status.json"
LAST_SEEN_FILE = SCRIPT_DIR / ".last_seen.json"
CONTROL_FILE = SCRIPT_DIR / ".control"
TWITCH_USER_ID = "60132775"
API_UPDATE_INTERVAL = 60
REFRESH_INTERVAL = 1.0
TRANSITION_DURATION = 5.0

MIN_OUTER_WIDTH = 60  # floor below which the box layout starts breaking down
MARGIN = 2

# Catppuccin Mocha accents (matches the user's terminal/desktop theme).
BORDER_COLOR = "#74c7ec"  # Sapphire — outer box, NOW WATCHING/PRIORITY LIST boxes, labels, footer
HIGHLIGHT_STYLE = "bold #1e1e2e on #b4befe"  # dark text on Lavender bar

_MODAL_PANEL_CSS = f"""
    align: center middle;

    & > Vertical {{
        width: auto;
        height: auto;
        max-width: 80%;
        border: round {BORDER_COLOR};
        padding: 1 2;
    }}
"""


class QuitConfirmModal(ModalScreen[bool]):
    """Centered popup asking whether to quit and stop the daemon."""

    DEFAULT_CSS = f"QuitConfirmModal {{{_MODAL_PANEL_CSS}}}"

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Quit and stop daemon?")
            yield Static("[Enter] Quit   [Esc] Cancel")

    def on_key(self, event: events.Key) -> None:
        event.stop()
        event.prevent_default()
        if event.key == "enter":
            self.dismiss(True)
        elif event.key == "escape":
            self.dismiss(False)


class InfoModal(ModalScreen):
    """Centered popup showing the full title and channel bio for a stream."""

    DEFAULT_CSS = f"InfoModal {{{_MODAL_PANEL_CSS}}}"

    def __init__(self, streamer: str, title: str) -> None:
        super().__init__()
        self.streamer = streamer
        self.title_text = title

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(f"Marquee.tv — Info: {self.streamer}")
            yield Static("")
            yield Static(f"Title: {self.title_text}", id="info-title")
            yield Static("")
            yield Static("Channel bio:")
            yield Static("(loading...)", id="bio")
            yield Static("")
            yield Static("Press i or Esc to close")

    def update_bio(self, bio: str) -> None:
        self.query_one("#bio", Static).update(bio)

    def on_key(self, event: events.Key) -> None:
        if event.key in ("i", "escape"):
            event.stop()
            event.prevent_default()
            self.dismiss()
        else:
            event.stop()
            event.prevent_default()


class MarqueeApp(App):
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
        self._pending_manual_switch: Optional[str] = None
        self.transition: Optional[Dict] = None  # {"kind": "auto"|"manual", "started": monotonic}
        self._terminal_width = MIN_OUTER_WIDTH

    def compose(self) -> ComposeResult:
        yield Static(id="frame", markup=False)

    def on_mount(self) -> None:
        self.load_entries()
        self._terminal_width = self.size.width
        self.refresh_data(force=True)
        self.render_frame()
        self.set_interval(REFRESH_INTERVAL, self.tick)

    def on_resize(self, event: events.Resize) -> None:
        # self.size lags a cycle behind inside this handler; event.size is current.
        self._terminal_width = event.size.width
        self.render_frame()

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

    def poll_single_stream_from_api(self, streamer: str) -> Optional[Dict]:
        """Query Twitch for one streamer's live info, regardless of follow status."""
        try:
            result = subprocess.run(
                ["/usr/bin/twitch", "api", "get", "streams", "-q", f"user_login={streamer}"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                return None
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
            if self._daemon_was_running:
                self._load_status_file()
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
            kind = "manual" if self._pending_manual_switch == self.current_stream else "auto"
            self._pending_manual_switch = None
            self.transition = {"kind": kind, "started": time.monotonic()}

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

    def _transition_header_lines(self, width: int) -> Optional[List[str]]:
        """Returns the 3 NOW WATCHING lines for an in-progress switch transition,
        or None if no transition is active (caller falls back to render_header)."""
        from rich.cells import set_cell_size

        if self.transition is None:
            return None
        elapsed = time.monotonic() - self.transition["started"]
        if elapsed >= TRANSITION_DURATION:
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

    @staticmethod
    def _styled_line(*parts: tuple) -> Text:
        """parts: (text, style_or_None) tuples, concatenated into one styled line."""
        line = Text()
        for content, style in parts:
            line.append(content, style=style)
        return line

    def render_frame(self) -> None:
        from rich.cells import cell_len, set_cell_size

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
                header_lines = render_header(self._header_data(), header_inner)
        for text in header_lines:
            lines.append(self._styled_line(("║ │ ", B), (text, None), (" │ ║", B)))
        lines.append(Text("║ └" + "─" * header_box_width + "┘ ║", style=B))
        lines.append(Text("║" + " " * inner_width + "║", style=B))

        list_label = " PRIORITY LIST "
        lines.append(Text(
            "║" + " " * MARGIN + "┌" + list_label
            + "─" * (list_box_width - cell_len(list_label)) + "┐" + " " * MARGIN + "║",
            style=B,
        ))
        rows = self._row_data()
        for i, row in enumerate(rows):
            if i == self.nav.index:
                collapsed = render_row_collapsed(row, inner_width - 3)
                lines.append(self._styled_line(
                    ("║", B), ("▶ " + collapsed + " ", HIGHLIGHT_STYLE), ("║", B),
                ))
                detail = render_row_expanded_detail(row, inner_width - 4)
                lines.append(self._styled_line(("║  ", B), (detail, None), ("  ║", B)))
            else:
                collapsed = render_row_collapsed(row, list_inner)
                lines.append(self._styled_line(
                    ("║" + " " * MARGIN + "│ ", B), (collapsed, None), (" │" + " " * MARGIN + "║", B),
                ))
        lines.append(Text("║" + " " * MARGIN + "└" + "─" * list_box_width + "┘" + " " * MARGIN + "║", style=B))
        lines.append(Text("║" + " " * inner_width + "║", style=B))

        footer = "(Q)uit (S)tart (X)Stop (E)dit (/)Ad-hoc (I)nfo ↑↓/jk Nav ⏎ Launch"
        lines.append(Text("║ " + set_cell_size(footer, inner_width - 2) + " ║", style=B))
        lines.append(Text("╚" + "═" * inner_width + "╝", style=B))

        frame = Text("\n").join(lines)
        self.query_one("#frame", Static).update(frame)

    def action_move_up(self) -> None:
        self.nav.move_up()
        self.render_frame()

    def action_move_down(self) -> None:
        self.nav.move_down()
        self.render_frame()

    def action_launch(self) -> None:
        if not (0 <= self.nav.index < len(self.entries)):
            return
        streamer = self.entries[self.nav.index].username
        with open(CONTROL_FILE, 'w') as f:
            f.write(f"switch:{streamer}")
        self.ad_hoc_mode = None
        self._pending_manual_switch = streamer
        self.render_frame()

    def action_ad_hoc_start(self) -> None:
        self.ad_hoc.start()
        self.render_frame()

    def action_ad_hoc_cancel(self) -> None:
        self.ad_hoc.cancel()
        self.render_frame()

    def action_edit_list(self) -> None:
        import subprocess as sp
        with self.suspend():
            try:
                sp.run(["nvim", str(STREAMERS_FILE)])
            except OSError:
                pass
        self.load_entries()
        self.render_frame()

    async def action_toggle_info(self) -> None:
        if not self.current_stream:
            return
        info = self.live_streams.get(self.current_stream, {})
        modal = InfoModal(self.current_stream, info.get('title', '(unknown)'))
        # Await the mount so the modal's #bio widget exists before the bio-fetch
        # worker (which can complete almost instantly) tries to update it.
        await self.push_screen(modal)
        self.run_worker(self._load_channel_bio(self.current_stream, modal), exclusive=True)

    async def _load_channel_bio(self, streamer: str, modal: "InfoModal") -> None:
        import asyncio
        bio = await asyncio.to_thread(self._fetch_channel_bio, streamer)
        if modal.is_attached and self.current_stream == streamer:
            modal.update_bio(bio)

    def _fetch_channel_bio(self, streamer: str) -> str:
        import subprocess as sp
        try:
            result = sp.run(
                ["/usr/bin/twitch", "api", "get", "users", "-q", f"login={streamer}"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                return "(unable to fetch channel bio)"
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

    def action_start_service(self) -> None:
        if not self.daemon_running():
            self.start_service()
            self.refresh_data(force=True)
            self.render_frame()

    def action_stop_service(self) -> None:
        if self.daemon_running():
            self.stop_service()
            self.refresh_data(force=True)
            self.render_frame()

    def action_request_quit(self) -> None:
        def handle_result(confirmed: bool) -> None:
            if confirmed:
                if self.daemon_running():
                    self.stop_service()
                self.exit()

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
        self.ad_hoc_mode = mode.value
        self._pending_manual_switch = streamer
        self.render_frame()

    def spawn_one_shot(self, streamer: str) -> None:
        """Launch a fully independent streamlink/mpv process, untracked by the daemon."""
        socket_path = SCRIPT_DIR / f".mpv-oneshot-{streamer}.sock"
        socket_path.unlink(missing_ok=True)
        player_args = (
            "--profile=twitch --volume=75 --force-seekable=yes "
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
        subprocess.Popen(["chatterino", "-a", streamer], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
