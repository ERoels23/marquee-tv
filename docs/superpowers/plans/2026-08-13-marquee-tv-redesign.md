# Marquee.tv Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebrand TwitchTV as Marquee.tv, rebuild the curses UI in Textual with a nested-box layout and breakout-highlight list, and add ad-hoc stream watching, in-place `nvim` priority-list editing, Chatterino tab sync, and live MPV title updates.

**Architecture:** Extract pure, unit-testable logic (parsing, formatting, state machines, line-rendering) into small standalone modules with no I/O; keep `marquee_daemon.py` (renamed `twitch_tv.py`) as the only Twitch-API-polling process and the sole owner of stream state, extended with hot-reload, ad-hoc mode handling, and MPV IPC; rebuild `marquee_ui.py` (renamed `twitchtv_ui.py`) as a Textual `App` that reads the daemon's state files and renders everything through the pure rendering module.

**Tech Stack:** Python 3, Textual 8.2.8 (pinned), Rich (Textual dependency, used directly for `rich.cells` cell-width-aware string padding), pytest, pytest-asyncio (for Textual `Pilot` tests), existing `streamlink`/`mpv`/`chatterino`/`twitch` CLI/`jq`/`notify-send` system tools.

**Design reference:** `docs/superpowers/specs/2026-08-13-marquee-tv-redesign-design.md`

**Note on prototyping:** every pure module below (`ui_format.py`, `priority_list.py`, `mpv_ipc.py`, `marquee_model.py`, `marquee_render.py`) and the specific Textual APIs used (`Static(markup=False)`, `.content`, `BINDINGS`/`action_*`, `App.suspend()`, `on_key()` interception of global bindings during text entry) were prototyped and verified against the actually-installed Textual 8.2.8 during planning — the code below is the verified version, not a guess from memory.

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `requirements.txt` | New | Pins `textual==8.2.8` |
| `pyproject.toml` | New | `pytest` config: adds repo root to `pythonpath`, sets `testpaths` |
| `ui_format.py` | New | Pure formatting: viewer counts, cell-width-aware truncation, uptime, last-seen, MPV title string |
| `priority_list.py` | New | Pure `streamers.txt` parsing (shared by daemon + UI) |
| `mpv_ipc.py` | New | Send JSON commands to an MPV `--input-ipc-server` unix socket |
| `marquee_model.py` | New | Pure UI state machines: list navigation, ad-hoc input/mode-select flow, quit-confirm |
| `marquee_render.py` | New | Pure line-rendering: header box content, list row content (collapsed/expanded), cell-width safe |
| `marquee_daemon.py` | Renamed from `twitch_tv.py`, modified | Background daemon: hot-reload, ad-hoc modes, `.last_seen.json`, Chatterino sync, live MPV titles |
| `marquee_ui.py` | Renamed from `twitchtv_ui.py`, full rewrite | Textual `App`: composes the above into the running TUI |
| `marquee.sh` | Renamed from `switchtv.sh`, modified | Entrypoint, updated script paths |
| `marquee.service` | Renamed from `twitchtv.service`, modified | systemd unit, updated `ExecStart` |
| `README.md` | Modified | Rebrand, updated commands |
| `tests/test_ui_format.py` | New | |
| `tests/test_priority_list.py` | New | |
| `tests/test_mpv_ipc.py` | New | |
| `tests/test_marquee_model.py` | New | |
| `tests/test_marquee_render.py` | New | |
| `tests/test_marquee_daemon.py` | New | Tests `parse_control_command` (pure function extracted from the daemon) |
| `tests/test_marquee_ui.py` | New | Textual `Pilot` smoke tests for navigation, ad-hoc typing, quit-confirm |

`.status.json`, `.control`, `.last_seen.json`, `.log`, `streamers.txt` keep their current names (internal/config files).

---

## Task 1: Project setup

**Files:**
- Create: `requirements.txt`
- Create: `pyproject.toml`
- Create: `tests/` (directory, no `__init__.py` needed)

- [ ] **Step 1: Create `requirements.txt`**

```
textual==8.2.8
```

- [ ] **Step 2: Create `pyproject.toml`**

```toml
[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
asyncio_mode = "auto"
```

- [ ] **Step 3: Create the virtualenv and install dependencies**

Run:
```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt pytest pytest-asyncio
```
Expected: completes with no errors, `.venv/bin/textual` and `.venv/bin/pytest` both exist.

- [ ] **Step 4: Create the tests directory**

```bash
mkdir -p tests
```

- [ ] **Step 5: Commit**

```bash
git add requirements.txt pyproject.toml
git commit -m "chore: add requirements.txt and pytest config for Marquee.tv rebuild"
```

---

## Task 2: `ui_format.py` — formatting helpers

**Files:**
- Create: `ui_format.py`
- Test: `tests/test_ui_format.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ui_format.py
from datetime import datetime, timezone
from ui_format import format_viewers, truncate_text, format_uptime, format_last_seen, build_mpv_title


def test_format_viewers_small():
    assert format_viewers(842) == "842"


def test_format_viewers_thousands():
    assert format_viewers(12400) == "12k"


def test_format_viewers_millions():
    assert format_viewers(1_500_000) == "1.5M"


def test_truncate_text_no_op():
    assert truncate_text("short", 20) == "short"


def test_truncate_text_dash():
    assert truncate_text("a very long title here", 10) == "a very lo-"
    assert len(truncate_text("a very long title here", 10)) == 10


def test_format_uptime():
    now = datetime(2026, 8, 13, 12, 14, 0, tzinfo=timezone.utc)
    assert format_uptime("2026-08-13T10:00:00Z", now=now) == "2h14m"


def test_format_last_seen_minutes():
    now = datetime(2026, 8, 13, 12, 30, 0, tzinfo=timezone.utc)
    assert format_last_seen("2026-08-13T12:00:00Z", now=now) == "30m ago"


def test_format_last_seen_days():
    now = datetime(2026, 8, 13, 12, 0, 0, tzinfo=timezone.utc)
    assert format_last_seen("2026-08-10T12:00:00Z", now=now) == "3d ago"


def test_format_last_seen_unknown():
    assert format_last_seen(None) == "unknown"


def test_build_mpv_title():
    assert build_mpv_title("Jerma", "Just Chatting", "doing bits") == "Jerma ::: Just Chatting ::: doing bits"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_ui_format.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ui_format'`

- [ ] **Step 3: Write the implementation**

```python
# ui_format.py
from datetime import datetime, timezone
from typing import Optional

from rich.cells import cell_len, set_cell_size


def format_viewers(count: int) -> str:
    if count < 10_000:
        return str(count)
    elif count < 1_000_000:
        return f"{count // 1000}k"
    else:
        m = count / 1_000_000
        return f"{m:.1f}M" if m < 10 else f"{int(m)}M"


def truncate_text(text: str, max_len: int) -> str:
    """Truncate to `max_len` terminal cells (accounting for wide/emoji glyphs), dash-suffixed."""
    if max_len <= 0:
        return ""
    if cell_len(text) <= max_len:
        return text
    if max_len == 1:
        return set_cell_size(text, 1)
    return set_cell_size(text, max_len - 1) + "-"


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def format_uptime(started_at: str, now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    started = _parse_iso(started_at)
    total_minutes = max(0, int((now - started).total_seconds() // 60))
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours}h{minutes:02d}m"


def format_last_seen(last_seen_iso: Optional[str], now: Optional[datetime] = None) -> str:
    if not last_seen_iso:
        return "unknown"
    now = now or datetime.now(timezone.utc)
    seconds = (now - _parse_iso(last_seen_iso)).total_seconds()
    if seconds < 60:
        return "just now"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}m ago"
    hours = int(minutes // 60)
    if hours < 24:
        return f"{hours}h ago"
    days = int(hours // 24)
    return f"{days}d ago"


def build_mpv_title(author: str, game: str, title: str) -> str:
    return f"{author} ::: {game} ::: {title}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_ui_format.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
git add ui_format.py tests/test_ui_format.py
git commit -m "feat: add ui_format formatting helpers"
```

---

## Task 3: `priority_list.py` — shared streamers.txt parser

**Files:**
- Create: `priority_list.py`
- Test: `tests/test_priority_list.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_priority_list.py
from priority_list import parse_streamers_file, usernames


def test_parse_basic(tmp_path):
    f = tmp_path / "streamers.txt"
    f.write_text("# comment\n\njerma985|Jerma\ncosmonaut_variety_hour\n")
    entries = parse_streamers_file(f)
    assert len(entries) == 2
    assert entries[0].username == "jerma985"
    assert entries[0].nickname == "Jerma"
    assert entries[0].display_name == "Jerma"
    assert entries[1].username == "cosmonaut_variety_hour"
    assert entries[1].nickname is None
    assert entries[1].display_name == "cosmonaut_variety_hour"


def test_parse_lowercases_username(tmp_path):
    f = tmp_path / "streamers.txt"
    f.write_text("NorthernLion|NL\n")
    entries = parse_streamers_file(f)
    assert entries[0].username == "northernlion"


def test_parse_empty_file(tmp_path):
    f = tmp_path / "streamers.txt"
    f.write_text("# only comments\n\n")
    assert parse_streamers_file(f) == []


def test_usernames_helper(tmp_path):
    f = tmp_path / "streamers.txt"
    f.write_text("a|A\nb\n")
    entries = parse_streamers_file(f)
    assert usernames(entries) == ["a", "b"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_priority_list.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'priority_list'`

- [ ] **Step 3: Write the implementation**

```python
# priority_list.py
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


@dataclass
class StreamerEntry:
    username: str
    nickname: Optional[str] = None

    @property
    def display_name(self) -> str:
        return self.nickname if self.nickname else self.username


def parse_streamers_file(path: Path) -> List[StreamerEntry]:
    entries: List[StreamerEntry] = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "|" in line:
                username, nickname = line.split("|", 1)
                username = username.strip().lower()
                nickname = nickname.strip() or None
            else:
                username = line.lower()
                nickname = None
            entries.append(StreamerEntry(username=username, nickname=nickname))
    return entries


def usernames(entries: List[StreamerEntry]) -> List[str]:
    return [e.username for e in entries]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_priority_list.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add priority_list.py tests/test_priority_list.py
git commit -m "feat: add shared priority_list parser"
```

---

## Task 4: `mpv_ipc.py` — MPV JSON IPC helper

**Files:**
- Create: `mpv_ipc.py`
- Test: `tests/test_mpv_ipc.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_mpv_ipc.py
import json
import socket
import threading

from mpv_ipc import send_command, set_title


def test_send_command_delivers_json_payload(tmp_path):
    sock_path = tmp_path / "mpv.sock"
    received = {}

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(sock_path))
    server.listen(1)

    def accept_once():
        conn, _ = server.accept()
        received["raw"] = conn.recv(4096)
        conn.close()

    t = threading.Thread(target=accept_once, daemon=True)
    t.start()

    ok = send_command(sock_path, ["set_property", "title", "hello"])
    t.join(timeout=2)
    server.close()

    assert ok is True
    payload = json.loads(received["raw"].decode("utf-8").strip())
    assert payload == {"command": ["set_property", "title", "hello"]}


def test_send_command_returns_false_when_socket_missing(tmp_path):
    ok = send_command(tmp_path / "does-not-exist.sock", ["get_property", "title"])
    assert ok is False


def test_set_title_wraps_command(tmp_path):
    sock_path = tmp_path / "mpv2.sock"
    received = {}

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(sock_path))
    server.listen(1)

    def accept_once():
        conn, _ = server.accept()
        received["raw"] = conn.recv(4096)
        conn.close()

    t = threading.Thread(target=accept_once, daemon=True)
    t.start()

    set_title(sock_path, "Jerma ::: Just Chatting ::: bit stuff")
    t.join(timeout=2)
    server.close()

    payload = json.loads(received["raw"].decode("utf-8").strip())
    assert payload["command"] == ["set_property", "title", "Jerma ::: Just Chatting ::: bit stuff"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_mpv_ipc.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mpv_ipc'`

- [ ] **Step 3: Write the implementation**

```python
# mpv_ipc.py
import json
import socket
from pathlib import Path
from typing import List, Union


def send_command(socket_path: Union[str, Path], command: List[str]) -> bool:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(1)
            sock.connect(str(socket_path))
            payload = json.dumps({"command": command}) + "\n"
            sock.sendall(payload.encode("utf-8"))
        return True
    except OSError:
        return False


def set_title(socket_path: Union[str, Path], title: str) -> bool:
    return send_command(socket_path, ["set_property", "title", title])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_mpv_ipc.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add mpv_ipc.py tests/test_mpv_ipc.py
git commit -m "feat: add mpv_ipc JSON IPC socket helper"
```

---

## Task 5: `marquee_model.py` — pure UI state machines

**Files:**
- Create: `marquee_model.py`
- Test: `tests/test_marquee_model.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_marquee_model.py
from marquee_model import ListNavigator, AdHocFlow, AdHocFlowState, AdHocMode, QuitConfirm


def test_navigator_wraps_down():
    nav = ListNavigator(3)
    assert nav.index == 0
    nav.move_down()
    nav.move_down()
    nav.move_down()
    assert nav.index == 0  # wrapped back around


def test_navigator_wraps_up():
    nav = ListNavigator(3)
    nav.move_up()
    assert nav.index == 2  # wrapped to the end


def test_navigator_empty_list_noop():
    nav = ListNavigator(0)
    assert nav.index == -1
    nav.move_down()
    nav.move_up()
    assert nav.index == -1


def test_navigator_set_count_clamps_index():
    nav = ListNavigator(5)
    nav.index = 4
    nav.set_count(2)
    assert nav.index == 1


def test_adhoc_flow_full_sequence():
    flow = AdHocFlow()
    flow.start()
    flow.type_char("j")
    flow.type_char("e")
    flow.type_char("r")
    flow.backspace()
    flow.type_char("r")
    flow.type_char("m")
    flow.type_char("a")
    assert flow.buffer == "jerma"
    assert flow.submit_name() is True
    assert flow.pending_name == "jerma"
    result = flow.choose_mode(AdHocMode.OVERRIDE)
    assert result == ("jerma", AdHocMode.OVERRIDE)


def test_adhoc_flow_empty_submit_rejected():
    flow = AdHocFlow()
    flow.start()
    assert flow.submit_name() is False


def test_adhoc_flow_choose_mode_without_submit_returns_none():
    flow = AdHocFlow()
    flow.start()
    flow.type_char("x")
    assert flow.choose_mode(AdHocMode.TEMPORARY) is None


def test_adhoc_flow_cancel_resets():
    flow = AdHocFlow()
    flow.start()
    flow.type_char("x")
    flow.cancel()
    assert flow.buffer == ""
    assert flow.state == AdHocFlowState.IDLE


def test_quit_confirm_requires_arm_then_confirm():
    qc = QuitConfirm()
    assert qc.confirm() is False  # not armed yet
    qc.request()
    assert qc.confirm() is True
    assert qc.confirm() is False  # disarmed after confirming


def test_quit_confirm_cancel():
    qc = QuitConfirm()
    qc.request()
    qc.cancel()
    assert qc.confirm() is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_marquee_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'marquee_model'`

- [ ] **Step 3: Write the implementation**

```python
# marquee_model.py
from enum import Enum, auto
from typing import Optional, Tuple


class AdHocMode(Enum):
    OVERRIDE = "override"
    TEMPORARY = "temporary"
    ONESHOT = "oneshot"


class ListNavigator:
    def __init__(self, count: int):
        self.count = count
        self.index = 0 if count > 0 else -1

    def set_count(self, count: int) -> None:
        self.count = count
        if count == 0:
            self.index = -1
        elif self.index < 0:
            self.index = 0
        elif self.index >= count:
            self.index = count - 1

    def move_up(self) -> None:
        if self.count == 0:
            return
        self.index = (self.index - 1) % self.count

    def move_down(self) -> None:
        if self.count == 0:
            return
        self.index = (self.index + 1) % self.count


class AdHocFlowState(Enum):
    IDLE = auto()
    TYPING = auto()
    MODE_SELECT = auto()


class AdHocFlow:
    def __init__(self):
        self.state = AdHocFlowState.IDLE
        self.buffer = ""
        self.pending_name = ""

    def start(self) -> None:
        self.state = AdHocFlowState.TYPING
        self.buffer = ""

    def type_char(self, ch: str) -> None:
        if self.state == AdHocFlowState.TYPING:
            self.buffer += ch

    def backspace(self) -> None:
        if self.state == AdHocFlowState.TYPING:
            self.buffer = self.buffer[:-1]

    def submit_name(self) -> bool:
        if self.state != AdHocFlowState.TYPING or not self.buffer.strip():
            return False
        self.pending_name = self.buffer.strip().lower()
        self.state = AdHocFlowState.MODE_SELECT
        return True

    def choose_mode(self, mode: AdHocMode) -> Optional[Tuple[str, AdHocMode]]:
        if self.state != AdHocFlowState.MODE_SELECT:
            return None
        result = (self.pending_name, mode)
        self.cancel()
        return result

    def cancel(self) -> None:
        self.state = AdHocFlowState.IDLE
        self.buffer = ""
        self.pending_name = ""


class QuitConfirm:
    def __init__(self):
        self.armed = False

    def request(self) -> None:
        self.armed = True

    def cancel(self) -> None:
        self.armed = False

    def confirm(self) -> bool:
        was_armed = self.armed
        self.armed = False
        return was_armed
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_marquee_model.py -v`
Expected: PASS (10 passed)

- [ ] **Step 5: Commit**

```bash
git add marquee_model.py tests/test_marquee_model.py
git commit -m "feat: add marquee_model UI state machines"
```

---

## Task 6: `marquee_render.py` — pure line rendering

**Files:**
- Create: `marquee_render.py`
- Test: `tests/test_marquee_render.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_marquee_render.py
from rich.cells import cell_len

from marquee_render import (
    HeaderData, RowData, render_header, render_row_collapsed,
    render_row_expanded_detail, header_border_label,
)


def test_render_header_idle():
    header = HeaderData(active=False)
    lines = render_header(header, 40)
    assert lines[0] == "No stream active".ljust(40)
    assert lines[1] == " " * 40
    assert lines[2] == " " * 40


def test_render_header_live_alignment():
    header = HeaderData(
        active=True, name="Jerma", is_live=True, viewers=12400,
        game="Just Chatting", started_at="2026-08-13T10:00:00Z",
        title="doing bit stuff",
    )
    lines = render_header(header, 40)
    assert len(lines) == 3
    assert all(cell_len(l) == 40 for l in lines)
    assert lines[0].startswith("Jerma")
    assert lines[0].rstrip().endswith("\U0001F7E2")
    assert "12k viewers" in lines[0]
    assert lines[1].startswith("Just Chatting")
    assert "live" in lines[1]


def test_render_header_title_truncates_hard():
    header = HeaderData(active=True, name="X", title="a" * 100)
    lines = render_header(header, 20)
    assert cell_len(lines[2]) == 20
    assert lines[2].endswith("-")  # truncate_text's dash marker


def test_header_border_label_normal():
    assert header_border_label(None) == "NOW WATCHING"


def test_header_border_label_ad_hoc():
    assert header_border_label("override") == "NOW WATCHING (ad-hoc · override)"


def test_render_row_collapsed_live():
    row = RowData(name="Jerma", is_live=True, viewers=12400, game="Just Chatting")
    line = render_row_collapsed(row, 50)
    assert cell_len(line) == 50
    assert line.startswith("Jerma")
    assert line.rstrip().endswith("\U0001F7E2")
    assert "12k" in line
    assert "Just Chatting" in line


def test_render_row_collapsed_offline_no_viewers_or_game():
    row = RowData(name="Northernlion", is_live=False)
    line = render_row_collapsed(row, 50)
    assert cell_len(line) == 50
    assert line.startswith("Northernlion")
    assert line.rstrip().endswith("\U0001F534")
    assert "None" not in line


def test_render_row_expanded_detail_live():
    row = RowData(
        name="Jerma", is_live=True, title="doing bit stuff",
        started_at="2026-08-13T10:00:00Z",
    )
    detail = render_row_expanded_detail(row, 60)
    assert cell_len(detail) == 60
    assert '"doing bit stuff"' in detail
    assert "live" in detail


def test_render_row_expanded_detail_offline_unknown():
    row = RowData(name="Bob", is_live=False, last_seen=None)
    detail = render_row_expanded_detail(row, 60)
    assert "last live: unknown" in detail


def test_render_row_expanded_detail_offline_with_last_seen():
    row = RowData(name="Bob", is_live=False, last_seen="2026-08-10T12:00:00Z")
    detail = render_row_expanded_detail(row, 60)
    assert "last live:" in detail
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_marquee_render.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'marquee_render'`

- [ ] **Step 3: Write the implementation**

```python
# marquee_render.py
"""Pure line-rendering for the Marquee.tv Textual UI. No I/O, no Textual imports.

All widths are in terminal *cells*, not Python characters — emoji indicators
(🟢/🔴) render as 2 cells wide, so `rich.cells.cell_len`/`set_cell_size` are
used everywhere instead of `len()`/slicing to keep box borders aligned.
"""
from dataclasses import dataclass
from typing import List, Optional

from rich.cells import cell_len, set_cell_size

from ui_format import format_viewers, format_uptime, format_last_seen, truncate_text

NAME_COL = 16
GAME_COL = 18


@dataclass
class HeaderData:
    active: bool
    name: str = ""
    is_live: bool = False
    viewers: Optional[int] = None
    game: str = ""
    started_at: Optional[str] = None
    title: str = ""
    ad_hoc_mode: Optional[str] = None  # "override" | "temporary" | "oneshot" | None


@dataclass
class RowData:
    name: str
    is_live: bool
    viewers: Optional[int] = None
    game: str = ""
    title: str = ""
    started_at: Optional[str] = None
    last_seen: Optional[str] = None


def _dot(is_live: bool) -> str:
    return "\U0001F7E2" if is_live else "\U0001F534"  # green / red circle


def _justify(left: str, right: str, width: int) -> str:
    """Left-justify `left`, right-justify `right`, >=1 space between, exactly `width` cells."""
    right = set_cell_size(right, min(cell_len(right), width))
    max_left = max(0, width - cell_len(right) - 1)
    left = truncate_text(left, max_left) if left else ""
    gap = max(1, width - cell_len(left) - cell_len(right))
    line = f"{left}{' ' * gap}{right}"
    return set_cell_size(line, width)


def header_border_label(ad_hoc_mode: Optional[str]) -> str:
    if ad_hoc_mode is None:
        return "NOW WATCHING"
    return f"NOW WATCHING (ad-hoc · {ad_hoc_mode})"


def render_header(header: HeaderData, width: int) -> List[str]:
    """Returns exactly 3 lines, each exactly `width` cells wide."""
    if not header.active:
        return [
            set_cell_size("No stream active", width),
            " " * width,
            " " * width,
        ]

    viewers_text = f"{format_viewers(header.viewers)} viewers" if header.viewers is not None else ""
    right1 = f"{viewers_text} {_dot(header.is_live)}".strip()
    line1 = _justify(header.name, right1, width)

    uptime = f"live {format_uptime(header.started_at)}" if header.started_at else ""
    line2 = _justify(header.game, uptime, width)

    line3 = set_cell_size(truncate_text(header.title, width), width)
    return [line1, line2, line3]


def render_row_collapsed(row: RowData, width: int) -> str:
    name = set_cell_size(truncate_text(row.name, NAME_COL), NAME_COL)
    game = set_cell_size(truncate_text(row.game, GAME_COL), GAME_COL) if row.is_live else " " * GAME_COL
    viewers_text = format_viewers(row.viewers) if row.is_live and row.viewers is not None else ""
    right = f"{viewers_text} {_dot(row.is_live)}".strip() if viewers_text else _dot(row.is_live)
    left = f"{name}{game}"
    max_left = max(0, width - cell_len(right) - 1)
    left = truncate_text(left, max_left)
    gap = max(1, width - cell_len(left) - cell_len(right))
    return set_cell_size(f"{left}{' ' * gap}{right}", width)


def render_row_expanded_detail(row: RowData, width: int) -> str:
    if row.is_live:
        uptime = format_uptime(row.started_at) if row.started_at else ""
        detail = f'"{row.title}" · live {uptime}' if uptime else f'"{row.title}"'
    else:
        detail = f"last live: {format_last_seen(row.last_seen)}"
    return set_cell_size(truncate_text("  " + detail, width), width)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_marquee_render.py -v`
Expected: PASS (10 passed)

- [ ] **Step 5: Commit**

```bash
git add marquee_render.py tests/test_marquee_render.py
git commit -m "feat: add marquee_render pure line-rendering module"
```

---

## Task 7: Rename `twitch_tv.py` → `marquee_daemon.py`, switch to shared parser

This task only renames the file and swaps `load_priority_list` to use `priority_list.parse_streamers_file`, preserving all existing behavior — no new features yet. Every later daemon task modifies this same file.

**Files:**
- Create (via rename): `marquee_daemon.py`
- Delete: `twitch_tv.py`

- [ ] **Step 1: Rename the file with git**

```bash
git mv twitch_tv.py marquee_daemon.py
```

- [ ] **Step 2: Replace `load_priority_list` to use the shared parser**

In `marquee_daemon.py`, add the import near the top (after the existing stdlib imports):

```python
from priority_list import parse_streamers_file, usernames as pl_usernames
```

Replace the existing `load_priority_list` method body:

```python
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
```

- [ ] **Step 3: Manually verify the daemon still starts and loads the list**

Run: `.venv/bin/python marquee_daemon.py &`
Expected: prints `Loaded N streamers from priority list` with the same count as before (13, per the current `streamers.txt`), then `Starting TwitchTV...` and the priority list. Stop it with:
```bash
kill %1
```

- [ ] **Step 4: Commit**

```bash
git add marquee_daemon.py
git commit -m "refactor: rename twitch_tv.py to marquee_daemon.py, use shared priority_list parser"
```

---

## Task 8: Daemon hot-reload of `streamers.txt`

**Files:**
- Modify: `marquee_daemon.py`

- [ ] **Step 1: Track the file's mtime and add a reload check**

In `__init__`, after `self.load_priority_list()`, add:

```python
        self._streamers_mtime = STREAMERS_FILE.stat().st_mtime
```

Add a new method right after `load_priority_list`:

```python
    def maybe_reload_priority_list(self):
        """Reload streamers.txt if it changed on disk since the last check."""
        try:
            mtime = STREAMERS_FILE.stat().st_mtime
        except FileNotFoundError:
            return
        if mtime != self._streamers_mtime:
            self._streamers_mtime = mtime
            entries = parse_streamers_file(STREAMERS_FILE)
            new_list = pl_usernames(entries)
            if new_list:
                self.priority_list = new_list
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Reloaded priority list ({len(new_list)} streamers)")
```

- [ ] **Step 2: Call it every loop tick**

In `run()`, inside the `while self.running:` loop, as the first line of the `try:` block (before the `current_time = time.time()` line), add:

```python
                self.maybe_reload_priority_list()
```

- [ ] **Step 3: Manually verify hot-reload**

```bash
.venv/bin/python marquee_daemon.py &
sleep 1
echo "# test comment appended $(date)" >> streamers.txt
sleep 11
```
Expected: within ~10s (one `CHECK_INTERVAL` tick), the daemon's stdout prints `Reloaded priority list (13 streamers)` (count unchanged since a comment doesn't add a streamer). Then clean up:
```bash
kill %1
git checkout streamers.txt
```

- [ ] **Step 4: Commit**

```bash
git add marquee_daemon.py
git commit -m "feat: hot-reload streamers.txt in the daemon loop"
```

---

## Task 9: Capture `started_at`, extend `.status.json` with live-stream snapshot

**Files:**
- Modify: `marquee_daemon.py`

- [ ] **Step 1: Capture `started_at` in `get_live_streams`**

In `get_live_streams`, inside the `for stream in data.get('data', []):` loop, change:

```python
                live_streams[streamer_name] = {
                    'title': stream['title'],
                    'game': stream['game_name'],
                    'viewers': stream['viewer_count']
                }
```

to:

```python
                live_streams[streamer_name] = {
                    'title': stream['title'],
                    'game': stream['game_name'],
                    'viewers': stream['viewer_count'],
                    'started_at': stream.get('started_at'),
                }
```

- [ ] **Step 2: Include the snapshot in `save_status`**

In `save_status`, add `'live_streams': self.live_streams,` to the `status` dict:

```python
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
```

- [ ] **Step 3: Manually verify**

```bash
.venv/bin/python marquee_daemon.py &
sleep 3
cat .status.json | .venv/bin/python -m json.tool | head -20
kill %1
```
Expected: JSON output includes a `"live_streams"` key with an object per currently-live streamer, each containing `title`, `game`, `viewers`, `started_at`.

- [ ] **Step 4: Commit**

```bash
git add marquee_daemon.py
git commit -m "feat: capture started_at and write live-stream snapshot to .status.json"
```

---

## Task 10: `.last_seen.json` tracking

**Files:**
- Modify: `marquee_daemon.py`

- [ ] **Step 1: Add the constant and in-memory dict**

Near the top, alongside the other file constants:

```python
LAST_SEEN_FILE = SCRIPT_DIR / ".last_seen.json"
```

In `__init__`, add:

```python
        self.last_seen: Dict[str, str] = self._load_last_seen()
```

Add the loader and writer methods near `save_status`:

```python
    def _load_last_seen(self) -> Dict[str, str]:
        if LAST_SEEN_FILE.exists():
            try:
                with open(LAST_SEEN_FILE, 'r') as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save_last_seen(self):
        with open(LAST_SEEN_FILE, 'w') as f:
            json.dump(self.last_seen, f)
```

- [ ] **Step 2: Update last-seen timestamps on every API refresh**

In `run()`, find the block:

```python
                if current_time - self.last_api_update >= API_UPDATE_INTERVAL:
                    self.live_streams = self.get_live_streams()
                    self.last_api_update = current_time
```

Replace it with:

```python
                if current_time - self.last_api_update >= API_UPDATE_INTERVAL:
                    self.live_streams = self.get_live_streams()
                    self.last_api_update = current_time
                    now_iso = datetime.now().isoformat()
                    for streamer in self.live_streams:
                        self.last_seen[streamer] = now_iso
                    self._save_last_seen()
```

- [ ] **Step 3: Manually verify**

```bash
.venv/bin/python marquee_daemon.py &
sleep 3
cat .last_seen.json | .venv/bin/python -m json.tool
kill %1
```
Expected: JSON object mapping each currently-live streamer to an ISO timestamp close to "now".

- [ ] **Step 4: Commit**

```bash
git add marquee_daemon.py
git commit -m "feat: track last-seen-live timestamps in .last_seen.json"
```

---

## Task 11: Chatterino tab sync (`-a` flag)

**Files:**
- Modify: `marquee_daemon.py`

- [ ] **Step 1: Replace the conditional Chatterino launch with an always-activate call**

In `launch_stream`, replace:

```python
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
```

with:

```python
        # Activate (or create) this streamer's tab in the running Chatterino window.
        # If Chatterino isn't running yet, this starts it with that tab open.
        subprocess.Popen(
            ["chatterino", "-a", streamer],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
```

- [ ] **Step 2: Manually verify**

```bash
pkill chatterino 2>/dev/null
.venv/bin/python marquee_daemon.py &
sleep 5
```
Expected: Chatterino opens with a tab for the launched streamer active. Switch the daemon to a different streamer via `.control` (or wait for a natural switch) and confirm the existing Chatterino window's active tab follows, rather than a new window opening.
```bash
kill %1
```

- [ ] **Step 3: Commit**

```bash
git add marquee_daemon.py
git commit -m "feat: sync Chatterino's active tab via -a flag on every stream launch"
```

---

## Task 12: Ad-hoc control protocol (override/temporary/oneshot)

**Files:**
- Modify: `marquee_daemon.py`
- Test: `tests/test_marquee_daemon.py`

- [ ] **Step 1: Write the failing test for the pure parser**

```python
# tests/test_marquee_daemon.py
from marquee_daemon import parse_control_command


def test_parse_legacy_switch():
    assert parse_control_command("switch") == ("", None)


def test_parse_plain_switch_no_mode():
    assert parse_control_command("switch:jerma985") == ("jerma985", None)


def test_parse_switch_with_mode():
    assert parse_control_command("switch:jerma985:override") == ("jerma985", "override")
    assert parse_control_command("switch:jerma985:temporary") == ("jerma985", "temporary")
    assert parse_control_command("switch:jerma985:oneshot") == ("jerma985", "oneshot")


def test_parse_unrecognized_returns_none():
    assert parse_control_command("garbage") is None


def test_parse_is_case_insensitive():
    assert parse_control_command("SWITCH:Jerma985:OVERRIDE") == ("jerma985", "override")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_marquee_daemon.py -v`
Expected: FAIL — `ImportError: cannot import name 'parse_control_command'` (module exists from Task 7, but the daemon's `__main__` block will also execute on import — see Step 3 note)

- [ ] **Step 3: Add the pure parser function**

Add this module-level function in `marquee_daemon.py`, above the `TwitchTVController` class definition:

```python
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
```

Note: `marquee_daemon.py`'s `if __name__ == "__main__":` guard already prevents the daemon from starting on import, so importing `parse_control_command` in tests is safe.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_marquee_daemon.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Wire the parser into `check_control_signal` and add the `manual_override` flag**

In `__init__`, add:

```python
        self.manual_override: bool = False
```

Replace `check_control_signal`:

```python
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
```

- [ ] **Step 6: Update `run()` to use the new return shape and set `manual_override`**

Replace the manual-switch handling block in `run()`:

```python
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
```

with:

```python
                    # ALWAYS check for manual switch requests (user can switch anytime)
                    control_signal = self.check_control_signal()
                    if control_signal is not None:
                        target, mode = control_signal
                        if mode == "oneshot":
                            pass  # UI handles one-shot streams entirely on its own
                        elif target == "" and mode is None:
                            # Legacy "switch" command - switch to highest priority now
                            print("User requested immediate switch!")
                            if highest_priority and highest_priority in self.live_streams:
                                self.launch_stream(highest_priority, self.live_streams[highest_priority])
                                self.manual_override = False
                                self.switching_soon = None
                                self.grace_period_start = None
                        elif target and target in self.live_streams:
                            print(f"User requested switch to {target} (mode={mode})")
                            self.launch_stream(target, self.live_streams[target])
                            self.manual_override = (mode == "override")
                            self.switching_soon = None
                            self.grace_period_start = None
```

- [ ] **Step 7: Guard the auto-switch block with the override flag**

Change:

```python
                    # Check for auto-switch to higher priority stream (ONLY if it JUST came online)
                    if highest_priority != self.current_stream and highest_priority is not None:
```

to:

```python
                    # Check for auto-switch to higher priority stream (ONLY if it JUST came online)
                    if self.manual_override:
                        pass  # user pinned an ad-hoc override stream; don't auto-switch away
                    elif highest_priority != self.current_stream and highest_priority is not None:
```

(the rest of that `if`/`elif` chain, including the existing `else:` clause resetting `switching_soon`/`grace_period_start`, is unchanged and still applies)

- [ ] **Step 8: Manually verify override behavior**

```bash
.venv/bin/python marquee_daemon.py &
sleep 2
echo "switch:some_offline_or_low_priority_test_name:override" > .control
sleep 12
```
Expected: since the target isn't actually live, nothing switches (the `target in self.live_streams` guard rejects it) — this confirms the parser/guard chain doesn't crash on an invalid ad-hoc target. For a full behavioral check, substitute a streamer you know is currently live in place of `some_offline_or_low_priority_test_name` and confirm the daemon switches to it and does **not** auto-switch away even if a higher-priority streamer from `streamers.txt` is also live.
```bash
kill %1
```

- [ ] **Step 9: Commit**

```bash
git add marquee_daemon.py tests/test_marquee_daemon.py
git commit -m "feat: add ad-hoc override/temporary/oneshot control protocol"
```

---

## Task 13: Live MPV title updates (daemon side)

**Files:**
- Modify: `marquee_daemon.py`

- [ ] **Step 1: Add socket-path helper and IPC imports**

Near the top imports, add:

```python
from mpv_ipc import set_title
from ui_format import build_mpv_title
```

Add a module-level helper function near `parse_control_command`:

```python
def mpv_socket_path(streamer: str) -> Path:
    return SCRIPT_DIR / f".mpv-{streamer}.sock"
```

- [ ] **Step 2: Pass `--input-ipc-server` per-launch and track last-known game/title**

In `__init__`, add:

```python
        self.current_socket_path: Optional[Path] = None
        self.last_known_game: Optional[str] = None
        self.last_known_title: Optional[str] = None
```

In `launch_stream`, replace the command-building line:

```python
        # Launch stream
        cmd = STREAMLINK_ARGS + [f"twitch.tv/{streamer}", "best"]
```

with:

```python
        # Launch stream, with a per-streamer MPV IPC socket for live title updates
        socket_path = mpv_socket_path(streamer)
        socket_path.unlink(missing_ok=True)
        player_args = (
            "--profile=twitch --volume=75 --force-seekable=yes "
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
```

(This inlines what was `STREAMLINK_ARGS` since the socket path is now per-launch; the module-level `STREAMLINK_ARGS` constant can stay in the file for reference but is no longer used by `launch_stream` — remove it to avoid dead code: delete the `STREAMLINK_ARGS = [...]` block near the top of the file.)

- [ ] **Step 3: Push a title update whenever game/title changes on a live-stream poll**

In `run()`, immediately after the `self._save_last_seen()` line added in Task 10, add:

```python
                    if (self.current_stream and self.is_stream_alive()
                            and self.current_stream in self.live_streams
                            and self.current_socket_path):
                        info = self.live_streams[self.current_stream]
                        if info['game'] != self.last_known_game or info['title'] != self.last_known_title:
                            new_title = build_mpv_title(self.current_stream, info['game'], info['title'])
                            set_title(self.current_socket_path, new_title)
                            self.last_known_game = info['game']
                            self.last_known_title = info['title']
```

- [ ] **Step 4: Manually verify**

```bash
.venv/bin/python marquee_daemon.py &
sleep 3
ls -la .mpv-*.sock
```
Expected: a `.mpv-<streamer>.sock` file exists for the currently-launched stream. Full title-update behavior (title actually changing when the streamer re-categorizes) can't be forced on demand — confirm instead that `mpv --input-ipc-server=<path> ...` is present in the running process's args:
```bash
ps aux | grep mpv | grep input-ipc-server
kill %1
rm -f .mpv-*.sock
```

- [ ] **Step 5: Commit**

```bash
git add marquee_daemon.py
git commit -m "feat: push live MPV title updates over IPC when game/title changes"
```

---

## Task 14: Rename `switchtv.sh` → `marquee.sh`

**Files:**
- Create (via rename): `marquee.sh`
- Delete: `switchtv.sh`

- [ ] **Step 1: Rename with git**

```bash
git mv switchtv.sh marquee.sh
```

- [ ] **Step 2: Update internal script references**

In `marquee.sh`, update the variable block:

```bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAIN_SCRIPT="$SCRIPT_DIR/twitch_tv.py"
UI_SCRIPT="$SCRIPT_DIR/twitchtv_ui.py"
STATUS_FILE="$SCRIPT_DIR/.status.json"
CONTROL_FILE="$SCRIPT_DIR/.control"
TMUX_SESSION="twitchtv"
```

to:

```bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAIN_SCRIPT="$SCRIPT_DIR/marquee_daemon.py"
UI_SCRIPT="$SCRIPT_DIR/marquee_ui.py"
STATUS_FILE="$SCRIPT_DIR/.status.json"
CONTROL_FILE="$SCRIPT_DIR/.control"
TMUX_SESSION="marquee"
```

Also update every `pgrep -f "python3.*twitch_tv.py"` and `pkill -f "python3.*twitch_tv.py"` in the file to `pgrep -f "python3.*marquee_daemon.py"` / `pkill -f "python3.*marquee_daemon.py"` (there are three occurrences: in `start`, `stop`, and the `status`/`watch` sections — search for `twitch_tv.py` to find all of them), and update the "TwitchTV Control Script" / usage banner text at the bottom to say "Marquee.tv Control Script".

- [ ] **Step 3: Manually verify**

```bash
grep -n "twitch_tv\|twitchtv_ui" marquee.sh
```
Expected: no output (all references updated).

```bash
./marquee.sh status
```
Expected: runs without error (prints "No status file found..." if the daemon isn't running).

- [ ] **Step 4: Commit**

```bash
git add marquee.sh
git commit -m "refactor: rename switchtv.sh to marquee.sh, update internal script references"
```

---

## Task 15: Rename `twitchtv.service` → `marquee.service`

**Files:**
- Create (via rename): `marquee.service`
- Delete: `twitchtv.service`

- [ ] **Step 1: Rename with git**

```bash
git mv twitchtv.service marquee.service
```

- [ ] **Step 2: Update the unit file**

Update `Description`, `Documentation`, and `ExecStart` in `marquee.service`:

```ini
[Unit]
Description=Marquee.tv Automatic Stream Watcher
Documentation=file:///mnt/Wrestler_Ted/claudes_room/TwitchTV/README.md
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/mnt/Wrestler_Ted/claudes_room/TwitchTV/marquee_daemon.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

# Run with personal user environment
Environment="DISPLAY=:0"
Environment="XAUTHORITY=%h/.Xauthority"

# Allow killing the player on stop
KillMode=process

[Install]
WantedBy=default.target
```

- [ ] **Step 3: Commit**

```bash
git add marquee.service
git commit -m "refactor: rename twitchtv.service to marquee.service, update ExecStart path"
```

(Note for the manual post-merge step, not part of this commit: re-running `systemctl --user disable twitchtv && systemctl --user enable marquee` and re-aliasing `switchtv` → `marquee.sh` is the user's responsibility once this lands, since it touches systemd state outside the repo.)

---

## Task 16: `marquee_ui.py` skeleton — data source, App, first render

**Files:**
- Create: `marquee_ui.py`
- Test: `tests/test_marquee_ui.py`

This task establishes the Textual `App`, the data-loading logic (reading `.status.json`/`.last_seen.json` when the daemon is running, falling back to a direct Twitch API poll otherwise, per the design's §2), and a first working render of the idle state. Interactive features are added in the following tasks.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_marquee_ui.py
from pathlib import Path

import pytest

from marquee_ui import MarqueeApp


@pytest.mark.asyncio
async def test_app_boots_and_shows_idle_header(tmp_path, monkeypatch):
    streamers_file = tmp_path / "streamers.txt"
    streamers_file.write_text("teststreamer|Test\n")
    monkeypatch.setattr("marquee_ui.STREAMERS_FILE", streamers_file)
    monkeypatch.setattr("marquee_ui.STATUS_FILE", tmp_path / ".status.json")
    monkeypatch.setattr("marquee_ui.LAST_SEEN_FILE", tmp_path / ".last_seen.json")
    monkeypatch.setattr(MarqueeApp, "poll_live_streams_from_api", lambda self: {})

    app = MarqueeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        frame = app.query_one("#frame")
        assert "No stream active" in frame.content
        assert "Test" in frame.content  # nickname from streamers.txt shows in the list
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_marquee_ui.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'marquee_ui'`

- [ ] **Step 3: Write the implementation**

```python
# marquee_ui.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_marquee_ui.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Manually verify visually**

```bash
.venv/bin/python marquee_ui.py
```
Expected: TUI opens showing "Marquee.tv", an idle "No stream active" header, and the priority list rows from `streamers.txt`. Press `Ctrl+C` or `q`-then-nothing (no quit binding yet — use `Ctrl+C`) to exit. Note the row layout here is a simple stacked list, not yet the nested-box breakout design — that visual polish is intentionally deferred; this task's goal is a working data pipeline and render loop.

- [ ] **Step 6: Commit**

```bash
git add marquee_ui.py tests/test_marquee_ui.py
git commit -m "feat: add marquee_ui Textual app skeleton with daemon-or-API data source"
```

---

## Task 17: Nested-box layout with breakout highlight row

**Files:**
- Modify: `marquee_ui.py`
- Modify: `tests/test_marquee_ui.py`

This replaces the plain stacked-list rendering from Task 16 with the approved nested-box design: an outer "Marquee.tv" border, a bordered "NOW WATCHING" box, and a "PRIORITY LIST" box whose highlighted row breaks out to the outer box's width. Since assembling nested box-drawing characters is fiddly by hand, this task's verification leans on the manual preview step rather than pixel-exact string assertions on the full frame (the *content* of each line, produced by `marquee_render.py`'s already-tested functions, is what's locked down precisely — the border assembly is refined by eye).

- [ ] **Step 1: Replace `render_frame` with the nested-box version**

```python
    def render_frame(self) -> None:
        from rich.cells import cell_len, set_cell_size

        inner_width = OUTER_WIDTH - 2
        header_box_width = inner_width - 2
        header_inner = header_box_width - 2
        list_box_width = inner_width - 2 * MARGIN
        list_inner = list_box_width - 2

        lines: List[str] = []
        lines.append("╔═ Marquee.tv " + "═" * (OUTER_WIDTH - cell_len("╔═ Marquee.tv ") - 1) + "╗")

        label = f" {header_border_label(self.ad_hoc_mode)} "
        lines.append("║ ┌" + label + "─" * (header_box_width - cell_len(label) - 1) + "┐ ║")
        for text in render_header(self._header_data(), header_inner):
            lines.append("║ │ " + text + " │ ║")
        lines.append("║ └" + "─" * header_box_width + "┘ ║")
        lines.append("║" + " " * inner_width + "║")

        list_label = " PRIORITY LIST "
        lines.append(
            "║" + " " * MARGIN + "┌" + list_label
            + "─" * (list_box_width - cell_len(list_label) - 1) + "┐" + " " * MARGIN + "║"
        )
        rows = self._row_data()
        for i, row in enumerate(rows):
            if i == self.nav.index:
                collapsed = render_row_collapsed(row, inner_width - 2)
                lines.append("║▶ " + collapsed + " ║")
                detail = render_row_expanded_detail(row, inner_width - 4)
                lines.append("║  " + detail + "  ║")
            else:
                collapsed = render_row_collapsed(row, list_inner)
                lines.append("║" + " " * MARGIN + "│ " + collapsed + " │" + " " * MARGIN + "║")
        lines.append("║" + " " * MARGIN + "└" + "─" * list_box_width + "┘" + " " * MARGIN + "║")
        lines.append("║" + " " * inner_width + "║")

        footer = "(Q)uit  (S)tart  (X)Stop  (E)dit  (/)Ad-hoc  (I)nfo  ↑↓/jk Navigate  ⏎ Launch"
        lines.append("║ " + set_cell_size(footer, inner_width - 2) + " ║")
        lines.append("╚" + "═" * inner_width + "╝")

        self.query_one("#frame", Static).update("\n".join(lines))
```

- [ ] **Step 2: Update the skeleton test's assertions for the new frame shape**

In `tests/test_marquee_ui.py`, the existing assertions (`"No stream active" in frame.content`, `"Test" in frame.content`) still hold true structurally since those strings still appear verbatim inside the new box — no change needed. Run it to confirm:

Run: `.venv/bin/pytest tests/test_marquee_ui.py -v`
Expected: PASS (1 passed, unchanged)

- [ ] **Step 3: Manually verify the box renders correctly**

```bash
.venv/bin/python marquee_ui.py
```
Expected: outer "Marquee.tv" border containing a "NOW WATCHING" box and a narrower "PRIORITY LIST" box below it; the highlighted row (index 0 by default) visibly extends past the list box's left/right border to match the outer box width, matching the approved mockup. If the right-edge border characters (`║`) don't line up in a column down the screen, adjust `OUTER_WIDTH`, `MARGIN`, or the literal spacing in the `lines.append(...)` calls above until they do — this is expected hand-tuning, not a sign of a logic bug (the underlying content strings are already tested to be exactly the right cell-width).

- [ ] **Step 4: Commit**

```bash
git add marquee_ui.py
git commit -m "feat: nested-box layout with breakout highlight row"
```

---

## Task 18: Navigation + Enter-to-launch

**Files:**
- Modify: `marquee_ui.py`
- Modify: `tests/test_marquee_ui.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_marquee_ui.py`:

```python
@pytest.mark.asyncio
async def test_enter_launches_highlighted_stream(tmp_path, monkeypatch):
    streamers_file = tmp_path / "streamers.txt"
    streamers_file.write_text("alpha\nbeta\n")
    control_file = tmp_path / ".control"
    monkeypatch.setattr("marquee_ui.STREAMERS_FILE", streamers_file)
    monkeypatch.setattr("marquee_ui.STATUS_FILE", tmp_path / ".status.json")
    monkeypatch.setattr("marquee_ui.LAST_SEEN_FILE", tmp_path / ".last_seen.json")
    monkeypatch.setattr("marquee_ui.CONTROL_FILE", control_file)
    monkeypatch.setattr(MarqueeApp, "poll_live_streams_from_api", lambda self: {})

    app = MarqueeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.nav.index = 1  # highlight "beta"
        await pilot.press("enter")
        assert control_file.read_text() == "switch:beta"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_marquee_ui.py -v -k launch`
Expected: FAIL — `enter` has no bound action, nothing writes to `control_file`

- [ ] **Step 3: Add the launch action**

Add to `BINDINGS`:

```python
        Binding("enter", "launch", "Launch", show=False),
```

Add the action method:

```python
    def action_launch(self) -> None:
        if not (0 <= self.nav.index < len(self.entries)):
            return
        streamer = self.entries[self.nav.index].username
        with open(CONTROL_FILE, 'w') as f:
            f.write(f"switch:{streamer}")
        self.ad_hoc_mode = None
        self.render_frame()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_marquee_ui.py -v -k launch`
Expected: PASS

- [ ] **Step 5: Manually verify navigation**

```bash
.venv/bin/python marquee_ui.py
```
Expected: `↑`/`↓`/`j`/`k` move the highlighted row (wrapping at the ends), and it visually breaks out per Task 17. `Ctrl+C` to exit (quit-confirm isn't wired until Task 21).

- [ ] **Step 6: Commit**

```bash
git add marquee_ui.py tests/test_marquee_ui.py
git commit -m "feat: arrow/vim navigation and Enter-to-launch"
```

---

## Task 19: `/` ad-hoc flow (typing + mode picker) and one-shot lifecycle

**Files:**
- Modify: `marquee_ui.py`
- Modify: `tests/test_marquee_ui.py`

- [ ] **Step 1: Write the failing test for the typing flow not leaking into global bindings**

Add to `tests/test_marquee_ui.py`:

```python
@pytest.mark.asyncio
async def test_slash_starts_typing_and_s_does_not_trigger_start(tmp_path, monkeypatch):
    streamers_file = tmp_path / "streamers.txt"
    streamers_file.write_text("alpha\n")
    monkeypatch.setattr("marquee_ui.STREAMERS_FILE", streamers_file)
    monkeypatch.setattr("marquee_ui.STATUS_FILE", tmp_path / ".status.json")
    monkeypatch.setattr("marquee_ui.LAST_SEEN_FILE", tmp_path / ".last_seen.json")
    monkeypatch.setattr(MarqueeApp, "poll_live_streams_from_api", lambda self: {})
    monkeypatch.setattr(MarqueeApp, "start_service", lambda self: None)

    started = {"called": False}

    def fake_start(self):
        started["called"] = True

    monkeypatch.setattr(MarqueeApp, "start_service", fake_start)

    app = MarqueeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("slash")
        assert app.ad_hoc.state == AdHocFlowState.TYPING
        await pilot.press("s")
        await pilot.press("o")
        await pilot.press("m")
        await pilot.press("e")
        assert app.ad_hoc.buffer == "some"
        assert started["called"] is False  # "s" must not have triggered start_service


@pytest.mark.asyncio
async def test_adhoc_submit_shows_mode_picker_then_writes_control(tmp_path, monkeypatch):
    streamers_file = tmp_path / "streamers.txt"
    streamers_file.write_text("alpha\n")
    control_file = tmp_path / ".control"
    monkeypatch.setattr("marquee_ui.STREAMERS_FILE", streamers_file)
    monkeypatch.setattr("marquee_ui.STATUS_FILE", tmp_path / ".status.json")
    monkeypatch.setattr("marquee_ui.LAST_SEEN_FILE", tmp_path / ".last_seen.json")
    monkeypatch.setattr("marquee_ui.CONTROL_FILE", control_file)
    monkeypatch.setattr(MarqueeApp, "poll_live_streams_from_api", lambda self: {})

    app = MarqueeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("slash")
        for ch in "gronk":
            await pilot.press(ch)
        await pilot.press("enter")
        assert app.ad_hoc.state == AdHocFlowState.MODE_SELECT
        await pilot.press("o")  # Override
        assert control_file.read_text() == "switch:gronk:override"
        assert app.ad_hoc_mode == "override"
```

Add the import at the top of the test file:

```python
from marquee_model import AdHocFlowState
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_marquee_ui.py -v -k adhoc`
Expected: FAIL — no `/` binding, no `ad_hoc` attribute wired to key handling yet

- [ ] **Step 3: Wire up `AdHocFlow` in `__init__` and `compose`**

In `__init__`, add:

```python
        self.ad_hoc = AdHocFlow()
```

- [ ] **Step 4: Add the `/` binding and `on_key` interception**

Add to `BINDINGS`:

```python
        Binding("slash", "ad_hoc_start", "Ad-hoc", show=False),
        Binding("escape", "ad_hoc_cancel", "Cancel", show=False),
```

Add the action and the key-interception handler:

```python
    def action_ad_hoc_start(self) -> None:
        self.ad_hoc.start()
        self.render_frame()

    def action_ad_hoc_cancel(self) -> None:
        self.ad_hoc.cancel()
        self.render_frame()

    async def on_key(self, event) -> None:
        if self.ad_hoc.state == AdHocFlowState.TYPING:
            event.stop()
            event.prevent_default()
            if event.key == "enter":
                self.ad_hoc.submit_name()
            elif event.key == "backspace":
                self.ad_hoc.backspace()
            elif event.character and event.character.isprintable():
                self.ad_hoc.type_char(event.character)
            self.render_frame()
        elif self.ad_hoc.state == AdHocFlowState.MODE_SELECT:
            event.stop()
            event.prevent_default()
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
```

Import `AdHocFlowState` alongside the other `marquee_model` imports (already imported as `AdHocFlow, AdHocFlowState, AdHocMode, QuitConfirm` in Task 16 — no change needed there).

- [ ] **Step 5: Add `launch_ad_hoc`, including the one-shot lifecycle**

```python
    def launch_ad_hoc(self, streamer: str, mode) -> None:
        from marquee_model import AdHocMode as M
        if mode == M.ONESHOT:
            self.spawn_one_shot(streamer)
            return
        with open(CONTROL_FILE, 'w') as f:
            f.write(f"switch:{streamer}:{mode.value}")
        self.ad_hoc_mode = mode.value
        self.render_frame()

    def spawn_one_shot(self, streamer: str) -> None:
        """Launch a fully independent streamlink/mpv process, untracked by the daemon."""
        import subprocess as sp
        from mpv_ipc import set_title as _set_title
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
        sp.Popen(cmd, stdout=sp.DEVNULL, stderr=sp.DEVNULL)
        sp.Popen(["chatterino", "-a", streamer], stdout=sp.DEVNULL, stderr=sp.DEVNULL)
        self.ad_hoc_mode = "oneshot"
        self.render_frame()
        # One-shot's own lightweight title-poll loop, independent of the daemon
        self.set_interval(API_UPDATE_INTERVAL, lambda: self._poll_one_shot_title(streamer, socket_path), pause=False)

    def _poll_one_shot_title(self, streamer: str, socket_path: Path) -> None:
        from mpv_ipc import set_title as _set_title
        from ui_format import build_mpv_title as _build_title
        info = self.poll_live_streams_from_api().get(streamer)
        if info:
            _set_title(socket_path, _build_title(streamer, info['game'], info['title']))
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_marquee_ui.py -v -k adhoc`
Expected: PASS (2 passed)

- [ ] **Step 7: Manually verify the full ad-hoc flow visually**

```bash
.venv/bin/python marquee_ui.py
```
Expected: pressing `/` turns typing focus on (header still shows old content until you wire the header-box display in a future polish pass — functionally, buffer accumulates correctly per the test above); typing a name then Enter arms the mode picker; pressing `o`/`t`/`1` writes the appropriate `.control` line (check with `cat .control` immediately after, before the daemon consumes it) or spawns a one-shot mpv window for `1`.

- [ ] **Step 8: Commit**

```bash
git add marquee_ui.py tests/test_marquee_ui.py
git commit -m "feat: ad-hoc stream input, mode picker, and one-shot lifecycle"
```

---

## Task 20: `e` — edit priority list in nvim

**Files:**
- Modify: `marquee_ui.py`

- [ ] **Step 1: Add the binding and action**

Add to `BINDINGS`:

```python
        Binding("e", "edit_list", "Edit", show=False),
```

Add the action:

```python
    def action_edit_list(self) -> None:
        import os
        import subprocess as sp
        editor = os.environ.get("EDITOR", "nvim")
        with self.suspend():
            sp.run([editor, str(STREAMERS_FILE)])
        self.load_entries()
        self.render_frame()
```

- [ ] **Step 2: Manually verify**

```bash
.venv/bin/python marquee_ui.py
```
Press `e`. Expected: the TUI suspends, `nvim` opens on `streamers.txt` in the same terminal, editing works normally; quitting `nvim` (`:wq`) returns to the Marquee.tv TUI, and if you changed the list, the row order/nicknames reflect the edit immediately (no restart needed).

- [ ] **Step 3: Commit**

```bash
git add marquee_ui.py
git commit -m "feat: e opens streamers.txt in \$EDITOR, reloads list on return"
```

---

## Task 21: `i` — info overlay (full title + channel bio)

**Files:**
- Modify: `marquee_ui.py`

- [ ] **Step 1: Add the binding, action, and overlay state**

In `__init__`, add:

```python
        self.info_visible = False
        self.channel_bio: Optional[str] = None
        self.quit_confirm = QuitConfirm()
```

(`quit_confirm` is needed here, ahead of Task 22, because Step 2 below's `render_frame` footer references it — `QuitConfirm` is already imported via Task 16's import line.)

Add to `BINDINGS`:

```python
        Binding("i", "toggle_info", "Info", show=False),
```

Add the action and bio fetcher:

```python
    def action_toggle_info(self) -> None:
        if self.info_visible:
            self.info_visible = False
        else:
            if not self.current_stream:
                return
            self.channel_bio = self._fetch_channel_bio(self.current_stream)
            self.info_visible = True
        self.render_frame()

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
```

- [ ] **Step 2: Render the overlay when visible**

Replace `render_frame` (defined in Task 16, replaced in Task 17) with this complete version, which adds an early-return overlay branch before the existing box-drawing body:

```python
    def render_frame(self) -> None:
        from rich.cells import cell_len, set_cell_size

        if self.info_visible and self.current_stream:
            info = self.live_streams.get(self.current_stream, {})
            overlay = [
                f"Marquee.tv — Info: {self.current_stream}",
                "",
                f"Title: {info.get('title', '(unknown)')}",
                "",
                "Channel bio:",
                self.channel_bio or "(loading...)",
                "",
                "Press i to close",
            ]
            self.query_one("#frame", Static).update("\n".join(overlay))
            return

        inner_width = OUTER_WIDTH - 2
        header_box_width = inner_width - 2
        header_inner = header_box_width - 2
        list_box_width = inner_width - 2 * MARGIN
        list_inner = list_box_width - 2

        lines: List[str] = []
        lines.append("╔═ Marquee.tv " + "═" * (OUTER_WIDTH - cell_len("╔═ Marquee.tv ") - 1) + "╗")

        label = f" {header_border_label(self.ad_hoc_mode)} "
        lines.append("║ ┌" + label + "─" * (header_box_width - cell_len(label) - 1) + "┐ ║")
        for text in render_header(self._header_data(), header_inner):
            lines.append("║ │ " + text + " │ ║")
        lines.append("║ └" + "─" * header_box_width + "┘ ║")
        lines.append("║" + " " * inner_width + "║")

        list_label = " PRIORITY LIST "
        lines.append(
            "║" + " " * MARGIN + "┌" + list_label
            + "─" * (list_box_width - cell_len(list_label) - 1) + "┐" + " " * MARGIN + "║"
        )
        rows = self._row_data()
        for i, row in enumerate(rows):
            if i == self.nav.index:
                collapsed = render_row_collapsed(row, inner_width - 2)
                lines.append("║▶ " + collapsed + " ║")
                detail = render_row_expanded_detail(row, inner_width - 4)
                lines.append("║  " + detail + "  ║")
            else:
                collapsed = render_row_collapsed(row, list_inner)
                lines.append("║" + " " * MARGIN + "│ " + collapsed + " │" + " " * MARGIN + "║")
        lines.append("║" + " " * MARGIN + "└" + "─" * list_box_width + "┘" + " " * MARGIN + "║")
        lines.append("║" + " " * inner_width + "║")

        footer = "(Q)uit  (S)tart  (X)Stop  (E)dit  (/)Ad-hoc  (I)nfo  ↑↓/jk Navigate  ⏎ Launch"
        if self.quit_confirm.armed:
            footer = "Press q again to quit"
        lines.append("║ " + set_cell_size(footer, inner_width - 2) + " ║")
        lines.append("╚" + "═" * inner_width + "╝")

        self.query_one("#frame", Static).update("\n".join(lines))
```

Note: this version also folds in the quit-confirm footer message ("Press q again to quit") that Task 22's bindings will need — that's why Step 1 above initializes `self.quit_confirm` now rather than in Task 22, avoiding a third pass over `render_frame`.

- [ ] **Step 3: Manually verify**

```bash
.venv/bin/python marquee_ui.py
```
With a live stream active (start the daemon first via `s`, see Task 22), press `i`. Expected: overlay shows the full untruncated title and the channel's bio text fetched on demand. Press `i` again to return to the normal view.

- [ ] **Step 4: Commit**

```bash
git add marquee_ui.py
git commit -m "feat: i opens info overlay with full title and channel bio"
```

---

## Task 22: `s`/`x` start/stop daemon, `q` confirm-quit

**Files:**
- Modify: `marquee_ui.py`
- Modify: `tests/test_marquee_ui.py`

- [ ] **Step 1: Write the failing test for quit-confirm**

Add to `tests/test_marquee_ui.py`:

```python
@pytest.mark.asyncio
async def test_quit_requires_confirmation(tmp_path, monkeypatch):
    streamers_file = tmp_path / "streamers.txt"
    streamers_file.write_text("alpha\n")
    monkeypatch.setattr("marquee_ui.STREAMERS_FILE", streamers_file)
    monkeypatch.setattr("marquee_ui.STATUS_FILE", tmp_path / ".status.json")
    monkeypatch.setattr("marquee_ui.LAST_SEEN_FILE", tmp_path / ".last_seen.json")
    monkeypatch.setattr(MarqueeApp, "poll_live_streams_from_api", lambda self: {})
    monkeypatch.setattr(MarqueeApp, "stop_service", lambda self: None)

    exited = {"called": False}
    monkeypatch.setattr(MarqueeApp, "exit", lambda self, *a, **kw: exited.__setitem__("called", True))

    app = MarqueeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("q")
        assert exited["called"] is False  # first press just arms confirmation
        assert app.quit_confirm.armed is True
        await pilot.press("q")
        assert exited["called"] is True  # second press confirms
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_marquee_ui.py -v -k quit`
Expected: FAIL — no `q` binding yet

- [ ] **Step 3: Add service control and bindings**

`self.quit_confirm = QuitConfirm()` was already added to `__init__` in Task 21 (needed there for the footer's confirm-quit message) — no change needed to `__init__` in this task.

Add to `BINDINGS`:

```python
        Binding("s", "start_service", "Start", show=False),
        Binding("x", "stop_service", "Stop", show=False),
        Binding("q", "request_quit", "Quit", show=False),
```

Add the methods:

```python
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
        if self.quit_confirm.confirm():
            if self.daemon_running():
                self.stop_service()
            self.exit()
        else:
            self.quit_confirm.request()
            self.render_frame()
```

Note the ordering in `action_request_quit`: `self.quit_confirm.confirm()` both checks *and* disarms in one call (per its `QuitConfirm` contract from Task 5) — so on the **first** `q` press it returns `False` (not armed yet) and we arm it via `request()`; on the **second** `q` press it returns `True` and we proceed to quit.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_marquee_ui.py -v -k quit`
Expected: PASS

- [ ] **Step 5: Manually verify**

```bash
.venv/bin/python marquee_ui.py
```
Press `s` — expected: daemon starts (`pgrep -f marquee_daemon.py` shows it running). Press `x` — expected: daemon and any running mpv stop. Press `q` once — expected: the footer line changes to "Press q again to quit" (per the `render_frame` branch already added in Task 21). Press `q` again — expected: app exits, daemon stopped.

- [ ] **Step 6: Commit**

```bash
git add marquee_ui.py tests/test_marquee_ui.py
git commit -m "feat: s/x start-stop daemon, q requires a second press to quit"
```

---

## Task 23: README update

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Rewrite `README.md`**

Update throughout: title/heading from "TwitchTV" to "Marquee.tv", replace `switchtv` command examples with `marquee.sh` (or the alias the user sets up), update the file list section to the renamed files (`marquee_daemon.py`, `marquee_ui.py`, `marquee.sh`), update the systemd section to reference `marquee.service`, and add a short "New in this version" section documenting: ad-hoc stream watching (`/` + Override/Temporary/One-Shot), `e` to edit the priority list in `$EDITOR`/nvim, live MPV window titles, and Chatterino tab auto-sync. Keep the existing Setup/Requirements/Troubleshooting structure intact, just updated for the new names and features.

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: update README for Marquee.tv rename and new features"
```

---

## Task 24: Full automated test suite + end-to-end manual verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full automated test suite**

```bash
.venv/bin/pytest -v
```
Expected: all tests across `tests/test_ui_format.py`, `tests/test_priority_list.py`, `tests/test_mpv_ipc.py`, `tests/test_marquee_model.py`, `tests/test_marquee_render.py`, `tests/test_marquee_daemon.py`, `tests/test_marquee_ui.py` pass.

- [ ] **Step 2: End-to-end manual walkthrough**

With real Twitch credentials already authenticated (`twitch auth login`, per the existing setup):

```bash
.venv/bin/python marquee_ui.py
```

Walk through, in order:
1. Press `s` to start the daemon; confirm a stream launches (or "No live streams" if none are live) and Chatterino opens with the right tab active.
2. Navigate the list with `j`/`k`/arrows; confirm the highlighted row breaks out visually and shows the expanded second line.
3. Press `Enter` on a different live entry; confirm it switches and Chatterino's tab follows.
4. Press `/`, type a streamer name not in your list who's currently live, `Enter`, then `o` for Override; confirm it launches and that a lower/higher priority stream going live does *not* pull it away.
5. Press `/` again, pick `1` for One-Shot on another live streamer; confirm a second independent mpv window opens while the first keeps playing.
6. Press `e`; confirm `nvim` opens on `streamers.txt`, make a trivial edit (e.g. reorder two lines), save and quit; confirm the list updates immediately in the UI without restarting anything.
7. Press `i` on the currently-watched stream; confirm the full title and channel bio display.
8. Press `q` once (nothing happens), `q` again; confirm the app exits and the daemon stops.

- [ ] **Step 3: Report any deviations found during manual walkthrough back for a follow-up fix task — do not silently patch and re-declare done without re-running the affected automated tests.**
