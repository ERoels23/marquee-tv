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


def _parse_streamer_line(line: str) -> StreamerEntry:
    if "|" in line:
        username, nickname = line.split("|", 1)
        username = username.strip().lower()
        nickname = nickname.strip() or None
    else:
        username = line.lower()
        nickname = None
    return StreamerEntry(username=username, nickname=nickname)


_WHEN_RE = re.compile(r"^@when\s+(\S+)\s*-\s*(\S+)\s*:\s*(.+)$", re.IGNORECASE)


def parse_streamers_file(path: Path) -> List[Union[StreamerEntry, TimeBlock]]:
    entries: List[Union[StreamerEntry, TimeBlock]] = []
    with open(path, "r") as f:
        lines = [ln.rstrip("\n") for ln in f]
    i = 0
    while i < len(lines):
        raw = lines[i].strip()
        if not raw or raw.startswith("#"):
            i += 1
            continue
        if raw == "---":
            entries.append(StreamerEntry(username="", is_separator=True))
            i += 1
            continue
        if raw.lower() == "@block":
            block, i = _parse_block(lines, i + 1)
            entries.append(block)
            continue
        entries.append(_parse_streamer_line(raw))
        i += 1
    return entries


def _parse_block(lines: List[str], i: int) -> tuple:
    """Parse from the line after `@block` up to and including `@end`.
    Returns (TimeBlock, index_after_end). Always returns a TimeBlock, with
    .warning set if anything is malformed."""
    members: List[StreamerEntry] = []
    raw_rules: list = []  # (start_str, end_str, [names])
    structural_warning: Optional[str] = None
    n = len(lines)
    while i < n:
        raw = lines[i].strip()
        i += 1
        if not raw or raw.startswith("#"):
            continue
        low = raw.lower()
        if low == "@end":
            break
        if low == "@block":
            structural_warning = structural_warning or "nested @block is not allowed"
            continue
        if raw == "---":
            structural_warning = structural_warning or "--- separators are not allowed inside a block"
            continue
        if low.startswith("@when"):
            m = _WHEN_RE.match(raw)
            if not m:
                structural_warning = structural_warning or f"malformed @when line: {raw!r}"
                continue
            names = [x.strip().lower() for x in m.group(3).split(",") if x.strip()]
            raw_rules.append((m.group(1), m.group(2), names))
            continue
        members.append(_parse_streamer_line(raw))
    else:
        structural_warning = structural_warning or "@block without a matching @end"

    block = _build_block(members, raw_rules)
    if structural_warning and block.warning is None:
        block.warning = structural_warning
    return block, i


def _build_block(members: List[StreamerEntry], raw_rules: list) -> TimeBlock:
    member_names = {m.username for m in members}
    warning = None
    parsed_rules: List[TimeRule] = []

    def warn(msg):
        nonlocal warning
        warning = warning or msg

    if not members:
        warn("block has no member streamers")
    if not raw_rules:
        warn("block has no @when rules")

    coverage = [0] * (24 * 60)
    for start_str, end_str, names in raw_rules:
        s, e = _parse_hhmm(start_str), _parse_hhmm(end_str)
        if s is None:
            warn(f"invalid time {start_str!r} (expected HH:MM, 00:00-23:59)")
            continue
        if e is None:
            warn(f"invalid time {end_str!r} (expected HH:MM, 00:00-23:59)")
            continue
        if s == e:
            warn(f"window {start_str}-{end_str} is zero-length; omit @when for all-day")
            continue
        if len(set(names)) != len(names):
            warn(f"@when {start_str}-{end_str} lists a streamer twice")
        unknown = [x for x in names if x not in member_names]
        if unknown:
            warn(f"@when {start_str}-{end_str} names non-member streamer(s): {', '.join(unknown)}")
        s_min, e_min = s.hour * 60 + s.minute, e.hour * 60 + e.minute
        minutes = range(s_min, e_min) if s_min < e_min else \
            [m % 1440 for m in range(s_min, e_min + 1440)]
        for m in minutes:
            coverage[m] += 1
        parsed_rules.append(TimeRule(s, e, names))

    if any(c > 1 for c in coverage):
        warn("@when windows overlap")

    block = TimeBlock(members=members, rules=parsed_rules, warning=warning)
    if warning:
        who = ", ".join(m.username for m in members) or "(no members)"
        block.warning = f"block ({who}): {warning}"
    return block


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
