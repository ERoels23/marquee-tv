from enum import Enum, auto
from typing import Optional, Tuple


class AdHocMode(Enum):
    OVERRIDE = "override"
    TEMPORARY = "temporary"
    ONESHOT = "oneshot"


class ListNavigator:
    """Navigates a list of `count` items, optionally skipping indices in
    `skip_indices` (e.g. separator rows) — those positions are never landed
    on by move_up/move_down or selected as the initial/clamped index."""

    def __init__(self, count: int, skip_indices=None):
        self.count = count
        self.skip_indices = set(skip_indices) if skip_indices else set()
        self.index = self._first_selectable()

    def _first_selectable(self) -> int:
        for i in range(self.count):
            if i not in self.skip_indices:
                return i
        return -1  # empty, or every index is skipped

    def _nearest_selectable(self, start: int) -> int:
        """Search backward from `start`, then fall back to the first
        selectable index — used when `start` itself turns out to be skipped."""
        for i in range(start, -1, -1):
            if i not in self.skip_indices:
                return i
        return self._first_selectable()

    def set_count(self, count: int, skip_indices=None) -> None:
        self.count = count
        self.skip_indices = set(skip_indices) if skip_indices else set()
        if count == 0:
            self.index = -1
        elif self.index < 0:
            self.index = self._first_selectable()
        elif self.index >= count:
            self.index = self._nearest_selectable(count - 1)
        elif self.index in self.skip_indices:
            self.index = self._nearest_selectable(self.index)

    def move_up(self) -> None:
        if self.count == 0 or self.index < 0:
            return
        i = self.index
        for _ in range(self.count):
            i = (i - 1) % self.count
            if i not in self.skip_indices:
                self.index = i
                return

    def move_down(self) -> None:
        if self.count == 0 or self.index < 0:
            return
        i = self.index
        for _ in range(self.count):
            i = (i + 1) % self.count
            if i not in self.skip_indices:
                self.index = i
                return


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
