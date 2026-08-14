from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


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


def usernames(entries: List[StreamerEntry]) -> List[str]:
    return [e.username for e in entries if not e.is_separator]
