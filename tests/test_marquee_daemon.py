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


def test_backfill_last_seen_uses_most_recent_vod_for_missing_entries(monkeypatch):
    ctrl = TwitchTVController.__new__(TwitchTVController)
    ctrl.priority_list = ["alpha", "beta", "gamma"]
    ctrl.last_seen = {"gamma": "2026-01-01T00:00:00+00:00"}  # already known — must be left untouched
    save_calls = {"count": 0}
    ctrl._save_last_seen = lambda: save_calls.__setitem__("count", save_calls["count"] + 1)

    def fake_run(cmd, **kwargs):
        if "users" in cmd:
            return mock.Mock(returncode=0, stdout=json.dumps({"data": [
                {"login": "alpha", "id": "111"},
                {"login": "beta", "id": "222"},
            ]}))
        if "videos" in cmd:
            joined = " ".join(cmd)
            if "user_id=111" in joined:
                return mock.Mock(returncode=0, stdout=json.dumps({"data": [{"created_at": "2026-08-01T10:00:00Z"}]}))
            if "user_id=222" in joined:
                return mock.Mock(returncode=0, stdout=json.dumps({"data": []}))  # VODs disabled/none available
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr("marquee_daemon.subprocess.run", fake_run)
    ctrl.backfill_last_seen()

    assert ctrl.last_seen["alpha"] == "2026-08-01T10:00:00Z"
    assert "beta" not in ctrl.last_seen  # no VODs — left missing, not fabricated
    assert ctrl.last_seen["gamma"] == "2026-01-01T00:00:00+00:00"  # untouched
    assert save_calls["count"] == 1


def test_backfill_last_seen_skips_entirely_when_nothing_missing(monkeypatch):
    ctrl = TwitchTVController.__new__(TwitchTVController)
    ctrl.priority_list = ["alpha"]
    ctrl.last_seen = {"alpha": "2026-01-01T00:00:00+00:00"}

    def fake_run(*a, **kw):
        raise AssertionError("should not query the API when nothing is missing")

    monkeypatch.setattr("marquee_daemon.subprocess.run", fake_run)
    ctrl.backfill_last_seen()  # must not raise


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
