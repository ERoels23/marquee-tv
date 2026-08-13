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
    except (OSError, TypeError, ValueError):
        return False


def set_title(socket_path: Union[str, Path], title: str) -> bool:
    return send_command(socket_path, ["set_property", "title", title])
