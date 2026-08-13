import json
import socket
import threading

from mpv_ipc import send_command, set_title


def test_send_command_delivers_json_payload(tmp_path):
    sock_path = tmp_path / "mpv.sock"
    received = {}

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(sock_path))
    server.listen(1)

    def accept_once():
        conn, _ = server.accept()
        received["raw"] = conn.recv(4096)
        conn.close()

    t = threading.Thread(target=accept_once, daemon=True)
    t.start()

    ok = send_command(sock_path, ["set_property", "title", "hello"])
    t.join(timeout=2)
    server.close()

    assert ok is True
    payload = json.loads(received["raw"].decode("utf-8").strip())
    assert payload == {"command": ["set_property", "title", "hello"]}


def test_send_command_returns_false_when_socket_missing(tmp_path):
    ok = send_command(tmp_path / "does-not-exist.sock", ["get_property", "title"])
    assert ok is False


def test_set_title_wraps_command(tmp_path):
    sock_path = tmp_path / "mpv2.sock"
    received = {}

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(sock_path))
    server.listen(1)

    def accept_once():
        conn, _ = server.accept()
        received["raw"] = conn.recv(4096)
        conn.close()

    t = threading.Thread(target=accept_once, daemon=True)
    t.start()

    set_title(sock_path, "Jerma ::: Just Chatting ::: bit stuff")
    t.join(timeout=2)
    server.close()

    payload = json.loads(received["raw"].decode("utf-8").strip())
    assert payload["command"] == ["set_property", "title", "Jerma ::: Just Chatting ::: bit stuff"]


def test_send_command_returns_false_for_unserializable_command(tmp_path):
    sock_path = tmp_path / "mpv3.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(sock_path))
    server.listen(1)

    class NotSerializable:
        pass

    ok = send_command(sock_path, [NotSerializable()])
    server.close()
    assert ok is False
