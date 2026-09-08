import datetime
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Union


@dataclass
class StreamerEntry:
    username: str
    nickname: Optional[str] = None
    is_separator: bool = False

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
            if line == "---":
                entries.append(StreamerEntry(username="", is_separator=True))
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


@dataclass
class TimeRule:
    start: datetime.time
    end: datetime.time
    order: List[str]  # usernames, priority order while this window is active


@dataclass
class TimeBlock:
    members: List[StreamerEntry]          # default (file) order
    rules: List[TimeRule] = field(default_factory=list)
    warning: Optional[str] = None         # set => rules are invalid, treat as a plain group

    def resolve(self, now: datetime.time) -> List[StreamerEntry]:
        if self.warning is None:
            for rule in self.rules:
                if _in_window(now, rule.start, rule.end):
                    return _apply_order(self.members, rule.order)
        return list(self.members)


def _apply_order(members: List[StreamerEntry], order: List[str]) -> List[StreamerEntry]:
    """Members named in `order` come first in that order; any not named are
    appended in their original (default) order."""
    by_name = {m.username: m for m in members}
    ordered = [by_name[name] for name in order if name in by_name]
    named = set(order)
    ordered += [m for m in members if m.username not in named]
    return ordered


_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def _parse_hhmm(text: str) -> Optional[datetime.time]:
    """Parse strict 'HH:MM' 24-hour time. Returns None on anything malformed."""
    m = _HHMM_RE.match(text.strip())
    if not m:
        return None
    return datetime.time(int(m.group(1)), int(m.group(2)))


def _in_window(t: datetime.time, start: datetime.time, end: datetime.time) -> bool:
    """True if `t` is in [start, end): start inclusive, end exclusive.
    If start > end the window wraps past midnight."""
    if start <= end:
        return start <= t < end
    return t >= start or t < end


def usernames(entries: List[StreamerEntry]) -> List[str]:
    return [e.username for e in entries if not e.is_separator]
