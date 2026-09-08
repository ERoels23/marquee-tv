from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple, Union


@dataclass
class StreamerEntry:
    username: str
    nickname: Optional[str] = None
    is_separator: bool = False
    block_label: Optional[str] = None   # UI-only: label drawn into a block's top rule line
    block_warning: bool = False         # UI-only: render block_label as a warning

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


def _parse_block(lines: List[str], i: int) -> Tuple[TimeBlock, int]:
    """Parse from the line after `@block` up to and including `@end`.
    Returns (TimeBlock, index_after_end). Always returns a TimeBlock, with
    .warning set if anything is malformed."""
    members: List[StreamerEntry] = []
    raw_rules: List[Tuple[str, str, List[str]]] = []
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
        if raw.startswith("@"):
            structural_warning = structural_warning or f"unrecognized directive: {raw!r}"
            continue
        members.append(_parse_streamer_line(raw))
    else:
        # for/else: exhausted the lines without hitting `@end`
        structural_warning = structural_warning or "@block without a matching @end"

    return _build_block(members, raw_rules, structural_warning), i


def _parse_rules(
    raw_rules: List[Tuple[str, str, List[str]]],
    member_names: set,
) -> Tuple[List[TimeRule], List[str]]:
    """Parse raw (start, end, names) tuples into TimeRules.
    Returns (rules, warnings); `warnings` are rule-content problems in the
    order encountered (the first is the one that surfaces to the user)."""
    rules: List[TimeRule] = []
    warnings: List[str] = []
    for start_str, end_str, names in raw_rules:
        s, e = _parse_hhmm(start_str), _parse_hhmm(end_str)
        if s is None:
            warnings.append(f"invalid time {start_str!r} (expected HH:MM, 00:00-23:59)")
            continue
        if e is None:
            warnings.append(f"invalid time {end_str!r} (expected HH:MM, 00:00-23:59)")
            continue
        if s == e:
            warnings.append(f"window {start_str}-{end_str} is zero-length; omit @when for all-day")
            continue
        if not names:
            warnings.append(f"@when {start_str}-{end_str} names no streamers")
            continue
        if len(set(names)) != len(names):
            warnings.append(f"@when {start_str}-{end_str} lists a streamer twice")
        unknown = [x for x in names if x not in member_names]
        if unknown:
            warnings.append(
                f"@when {start_str}-{end_str} names non-member streamer(s): {', '.join(unknown)}"
            )
        rules.append(TimeRule(s, e, names))
    return rules, warnings


def _windows_overlap(rules: List[TimeRule]) -> bool:
    """True if any two rule windows cover the same minute of day. Marks a
    1440-entry minute-of-day array, midnight-wrap aware. This is the same
    [start, end) predicate as `_in_window` — keep the two in sync."""
    coverage = [0] * (24 * 60)
    for rule in rules:
        s_min = rule.start.hour * 60 + rule.start.minute
        e_min = rule.end.hour * 60 + rule.end.minute
        minutes = range(s_min, e_min) if s_min < e_min else \
            [m % 1440 for m in range(s_min, e_min + 1440)]
        for m in minutes:
            coverage[m] += 1
    return any(c > 1 for c in coverage)


def _build_block(
    members: List[StreamerEntry],
    raw_rules: List[Tuple[str, str, List[str]]],
    structural_warning: Optional[str] = None,
) -> TimeBlock:
    """Assemble a TimeBlock, choosing at most one warning by priority tier:
    rule-content problems > structural problems > emptiness."""
    member_names = {m.username for m in members}
    rules, rule_warnings = _parse_rules(raw_rules, member_names)
    if _windows_overlap(rules):
        rule_warnings.append("@when windows overlap")

    structural = [structural_warning] if structural_warning else []
    empty: List[str] = []
    if not members:
        empty.append("block has no member streamers")
    if not raw_rules:
        empty.append("block has no @when rules")

    warning = next(
        (w for tier in (rule_warnings, structural, empty) for w in tier),
        None,
    )
    if warning:
        who = ", ".join(m.username for m in members) or "(no members)"
        warning = f"block ({who}): {warning}"
    return TimeBlock(members=members, rules=rules, warning=warning)


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

    def active_rule(self, now: datetime.time) -> Optional[TimeRule]:
        """The @when rule in force at `now`, or None (default order applies).
        Always None when this block has a warning."""
        if self.warning is not None:
            return None
        return next((r for r in self.rules if _in_window(now, r.start, r.end)), None)

    def resolve(self, now: datetime.time) -> List[StreamerEntry]:
        rule = self.active_rule(now)
        return _apply_order(self.members, rule.order) if rule else list(self.members)


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


def resolve_entries(
    entries: List[Union[StreamerEntry, TimeBlock]],
    now: Optional[datetime.time] = None,
) -> List[StreamerEntry]:
    """Flatten a parsed priority list to a plain ordered list of StreamerEntry,
    expanding each TimeBlock into its effective order for `now`
    (default: the current wall-clock time). Separators are preserved."""
    if now is None:
        now = datetime.datetime.now().time()
    out: List[StreamerEntry] = []
    for item in entries:
        if isinstance(item, TimeBlock):
            out.extend(item.resolve(now))
        else:
            out.append(item)
    return out


def usernames(entries) -> List[str]:
    """Flat list of usernames in priority order, skipping separators.

    Tolerates an unflattened list (one still containing `TimeBlock`s): a
    block's members are included in default order, so a caller that forgets
    to `resolve_entries()` first degrades to default order rather than
    raising `AttributeError`.
    """
    out: List[str] = []
    for e in entries:
        if isinstance(e, TimeBlock):
            out.extend(m.username for m in e.members)
        elif not e.is_separator:
            out.append(e.username)
    return out
