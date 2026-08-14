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


def test_get_live_streams_uses_data_despite_nonzero_exit_code(monkeypatch):
    # Regression: the installed twitch CLI can crash in its own unrelated
    # update-check code *after* already printing valid JSON to stdout,
    # exiting non-zero despite having done its job correctly. Judging
    # success by the exit code alone silently discarded real live-stream
    # data whenever this happened. Success should be judged by whether
    # stdout actually parses.
    def fake_run(cmd, **kwargs):
        return mock.Mock(returncode=2, stdout=json.dumps({"data": [
            {"user_login": "alpha", "title": "t1", "game_name": "g1", "viewer_count": 5, "started_at": "2026-01-01T00:00:00Z"},
        ]}), stderr="panic: runtime error: index out of range [0] with length 0")

    monkeypatch.setattr("marquee_daemon.subprocess.run", fake_run)
    ctrl = TwitchTVController.__new__(TwitchTVController)
    ctrl.priority_list = ["alpha"]

    live = ctrl.get_live_streams()

    assert live == {"alpha": {"title": "t1", "game": "g1", "viewers": 5, "started_at": "2026-01-01T00:00:00Z"}}


def test_get_live_streams_empty_priority_list_skips_api_call(monkeypatch):
    def fake_run(*a, **kw):
        raise AssertionError("should not query the API with an empty priority list")

    monkeypatch.setattr("marquee_daemon.subprocess.run", fake_run)
    ctrl = TwitchTVController.__new__(TwitchTVController)
    ctrl.priority_list = []

    assert ctrl.get_live_streams() == {}


def _fake_backfill_run(video_data=None, channel_data=None, user_map=None):
    """Builds a fake subprocess.run for backfill_last_seen tests: routes
    users/videos/channels commands to canned responses keyed by user_id."""
    user_map = user_map or {}
    video_data = video_data or {}
    channel_data = channel_data or {}

    def fake_run(cmd, **kwargs):
        joined = " ".join(cmd)
        if "users" in cmd:
            return mock.Mock(returncode=0, stdout=json.dumps({"data": [
                {"login": login, "id": uid} for login, uid in user_map.items()
            ]}))
        if "videos" in cmd:
            for uid, videos in video_data.items():
                if f"user_id={uid}" in joined:
                    return mock.Mock(returncode=0, stdout=json.dumps({"data": videos}))
            return mock.Mock(returncode=0, stdout=json.dumps({"data": []}))
        if "channels" in cmd:
            for uid, channels in channel_data.items():
                if f"broadcaster_id={uid}" in joined:
                    return mock.Mock(returncode=0, stdout=json.dumps({"data": channels}))
            return mock.Mock(returncode=0, stdout=json.dumps({"data": []}))
        raise AssertionError(f"unexpected command: {cmd}")

    return fake_run


def test_backfill_last_seen_combines_vod_timestamp_with_channel_category(monkeypatch):
    # VOD history has no category field at all, but /channels (Get Channel
    # Information) reports the channel's current game/title even while
    # offline — combine timestamp from the former with category from the
    # latter, since neither endpoint alone has everything.
    ctrl = TwitchTVController.__new__(TwitchTVController)
    ctrl.priority_list = ["alpha", "beta", "gamma"]
    ctrl.last_seen = {"gamma": {"at": "2026-01-01T00:00:00+00:00", "game": "g", "title": "t"}}  # already known
    save_calls = {"count": 0}
    ctrl._save_last_seen = lambda: save_calls.__setitem__("count", save_calls["count"] + 1)

    fake_run = _fake_backfill_run(
        user_map={"alpha": "111", "beta": "222"},
        video_data={
            "111": [{"created_at": "2026-08-01T10:00:00Z", "title": "Some VOD title"}],
            "222": [],  # VODs disabled/none available
        },
        channel_data={
            "111": [{"game_name": "Some Category", "title": "Current channel title"}],
        },
    )
    monkeypatch.setattr("marquee_daemon.subprocess.run", fake_run)
    ctrl.backfill_last_seen()

    # Title prefers the VOD's (what they were actually broadcasting), game
    # can only ever come from /channels.
    assert ctrl.last_seen["alpha"] == {"at": "2026-08-01T10:00:00Z", "game": "Some Category", "title": "Some VOD title"}
    assert "beta" not in ctrl.last_seen  # no VOD means no timestamp — nothing to record
    assert ctrl.last_seen["gamma"] == {"at": "2026-01-01T00:00:00+00:00", "game": "g", "title": "t"}  # untouched
    assert save_calls["count"] == 1


def test_backfill_last_seen_skips_entirely_when_nothing_missing(monkeypatch):
    ctrl = TwitchTVController.__new__(TwitchTVController)
    ctrl.priority_list = ["alpha"]
    # Has a timestamp, game, and title — nothing missing, so no API calls.
    ctrl.last_seen = {"alpha": {"at": "2026-01-01T00:00:00+00:00", "game": "g", "title": "t"}}

    def fake_run(*a, **kw):
        raise AssertionError("should not query the API when nothing is missing")

    monkeypatch.setattr("marquee_daemon.subprocess.run", fake_run)
    ctrl.backfill_last_seen()  # must not raise


def test_backfill_last_seen_retries_entries_missing_only_game(monkeypatch):
    # Entries with a timestamp and title but no game (e.g. backfilled before
    # the /channels lookup existed) should still be retried for category —
    # without re-querying /videos, since the timestamp is already known.
    ctrl = TwitchTVController.__new__(TwitchTVController)
    ctrl.priority_list = ["alpha"]
    ctrl.last_seen = {"alpha": {"at": "2026-01-01T00:00:00+00:00", "game": None, "title": "Existing title"}}
    ctrl._save_last_seen = lambda: None

    fake_run = _fake_backfill_run(
        user_map={"alpha": "111"},
        channel_data={"111": [{"game_name": "Newly fetched category", "title": "Channel's current title"}]},
    )
    monkeypatch.setattr("marquee_daemon.subprocess.run", fake_run)
    ctrl.backfill_last_seen()

    assert ctrl.last_seen["alpha"] == {
        "at": "2026-01-01T00:00:00+00:00",  # existing timestamp preserved, not overwritten
        "game": "Newly fetched category",  # missing game filled in
        "title": "Existing title",  # existing title preserved, not clobbered
    }


def test_backfill_last_seen_retries_entries_missing_only_title(monkeypatch):
    # When "at" is already known, /videos isn't re-queried (no need to
    # re-derive a timestamp we already have) — so a missing title in this
    # case can only be filled from /channels, not the original VOD's title.
    ctrl = TwitchTVController.__new__(TwitchTVController)
    ctrl.priority_list = ["alpha"]
    ctrl.last_seen = {"alpha": {"at": "2026-01-01T00:00:00+00:00", "game": "Just Chatting", "title": None}}
    ctrl._save_last_seen = lambda: None

    def fake_run(cmd, **kwargs):
        if "videos" in cmd:
            raise AssertionError("should not re-query /videos when at is already known")
        return _fake_backfill_run(
            user_map={"alpha": "111"},
            channel_data={"111": [{"game_name": "Should not override", "title": "Newly fetched title"}]},
        )(cmd, **kwargs)

    monkeypatch.setattr("marquee_daemon.subprocess.run", fake_run)
    ctrl.backfill_last_seen()

    assert ctrl.last_seen["alpha"] == {
        "at": "2026-01-01T00:00:00+00:00",  # existing timestamp preserved, not overwritten
        "game": "Just Chatting",  # existing game preserved, not clobbered by /channels
        "title": "Newly fetched title",  # missing title filled from /channels
    }


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
