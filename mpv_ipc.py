import json
import socket
from pathlib import Path
from typing import List, Union


# Local AF_UNIX round-trip should complete in microseconds; capping well below
# the daemon's 10s poll interval keeps a stuck/refused socket from stalling the
# main loop (and therefore auto-switch/control-file responsiveness).
_SOCKET_TIMEOUT_SECONDS = 0.5


def send_command(socket_path: Union[str, Path], command: List[str]) -> bool:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(_SOCKET_TIMEOUT_SECONDS)
            sock.connect(str(socket_path))
            payload = json.dumps({"command": command}) + "\n"
            sock.sendall(payload.encode("utf-8"))
        return True
    except (OSError, TypeError, ValueError):
        return False


def set_title(socket_path: Union[str, Path], title: str) -> bool:
    return send_command(socket_path, ["set_property", "title", title])
