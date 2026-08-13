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
