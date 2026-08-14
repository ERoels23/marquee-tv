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
