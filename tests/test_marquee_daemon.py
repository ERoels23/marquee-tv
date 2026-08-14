import json
from unittest import mock

from marquee_daemon import parse_control_command, TwitchTVController


def test_get_live_streams_queries_by_user_login_not_followed(monkeypatch):
    # Regression: /streams/followed only returns channels the Twitch account
    # actually follows, so any priority-list entry that isn't followed could
    # never register as live (and would never get a last_seen timestamp) even
    # while genuinely streaming. Query by explicit user_login instead.
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return mock.Mock(returncode=0, stdout=json.dumps({"data": [
            {"user_login": "alpha", "title": "t1", "game_name": "g1", "viewer_count": 5, "started_at": "2026-01-01T00:00:00Z"},
        ]}))

    monkeypatch.setattr("marquee_daemon.subprocess.run", fake_run)
    ctrl = TwitchTVController.__new__(TwitchTVController)
    ctrl.priority_list = ["alpha", "beta"]

    live = ctrl.get_live_streams()

    assert "user_login=alpha" in captured["cmd"]
    assert "user_login=beta" in captured["cmd"]
    assert "/streams/followed" not in " ".join(captured["cmd"])
    assert live == {"alpha": {"title": "t1", "game": "g1", "viewers": 5, "started_at": "2026-01-01T00:00:00Z"}}


def test_get_live_streams_empty_priority_list_skips_api_call(monkeypatch):
    def fake_run(*a, **kw):
        raise AssertionError("should not query the API with an empty priority list")

    monkeypatch.setattr("marquee_daemon.subprocess.run", fake_run)
    ctrl = TwitchTVController.__new__(TwitchTVController)
    ctrl.priority_list = []

    assert ctrl.get_live_streams() == {}


def test_parse_legacy_switch():
    assert parse_control_command("switch") == ("", None)


def test_parse_plain_switch_no_mode():
    assert parse_control_command("switch:jerma985") == ("jerma985", None)


def test_parse_switch_with_mode():
    assert parse_control_command("switch:jerma985:override") == ("jerma985", "override")
    assert parse_control_command("switch:jerma985:temporary") == ("jerma985", "temporary")
    assert parse_control_command("switch:jerma985:oneshot") == ("jerma985", "oneshot")


def test_parse_unrecognized_returns_none():
    assert parse_control_command("garbage") is None


def test_parse_is_case_insensitive():
    assert parse_control_command("SWITCH:Jerma985:OVERRIDE") == ("jerma985", "override")
