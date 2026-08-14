"""Pure line-rendering for the Marquee.tv Textual UI. No I/O, no Textual imports.

All widths are in terminal *cells*, not Python characters — emoji/wide
indicators (🟢/⚪) render as 2 cells wide, so `rich.cells.cell_len`/
`set_cell_size` are used everywhere instead of `len()`/slicing to keep box
borders aligned.
"""
from dataclasses import dataclass
from typing import List, Optional

from rich.cells import cell_len, set_cell_size

from ui_format import format_viewers, format_uptime, format_last_seen, truncate_text

NAME_COL = 16


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
    username: str = ""


STATUS_DOT = "●"  # 1 cell wide, colorable


def _dot(is_live: bool) -> str:
    # Same glyph either way — live vs. offline is conveyed by color, applied
    # by the caller (this module only produces plain, unstyled text).
    return STATUS_DOT


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
    left1 = f"{_dot(header.is_live)} {header.name}"
    line1 = _justify(left1, viewers_text, width)

    uptime = f"live {format_uptime(header.started_at)}" if header.started_at else ""
    line2 = _justify(header.game, uptime, width)

    line3 = set_cell_size(truncate_text(header.title, width), width)
    return [line1, line2, line3]


UPTIME_COL = 11  # fits "live 99h59m"
VIEWERS_COL = 6  # fits e.g. "999.9k"


def render_row_collapsed(row: RowData, width: int, highlighted: bool = False) -> str:
    name = row.name
    if highlighted and row.username and row.username != row.name:
        name = f"{row.name} ({row.username})"
    if not highlighted:
        name = set_cell_size(truncate_text(name, NAME_COL), NAME_COL)
    # Category is intentionally left untruncated (plenty of horizontal room);
    # _justify's own overflow guard is the only thing that would ever cut it.
    left = f"{_dot(row.is_live)} {name}"
    if row.is_live and row.game:
        left += f"    {row.game}"

    if row.is_live:
        uptime_text = f"live {format_uptime(row.started_at)}" if row.started_at else ""
        viewers_text = format_viewers(row.viewers) if row.viewers is not None else ""
        # Fixed-width columns so uptime/viewers line up across rows regardless
        # of digit count. Uptime is left-justified within its column so "live"
        # itself starts at a consistent position (right-justifying the whole
        # string would shift "live" left/right depending on the hour count).
        right = f"{uptime_text.ljust(UPTIME_COL)} {viewers_text:>{VIEWERS_COL}}"
    else:
        right = ""
    return _justify(left, right, width)


def render_row_expanded_detail(row: RowData, width: int) -> str:
    if row.is_live:
        detail = row.title
    else:
        detail = f"last live: {format_last_seen(row.last_seen)}"
    return set_cell_size(truncate_text("  " + detail, width), width)
