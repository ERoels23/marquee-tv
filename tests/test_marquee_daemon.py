import datetime as _dt
import json
from unittest import mock

from marquee_daemon import parse_control_command, TwitchTVController
from priority_list import StreamerEntry, TimeRule, TimeBlock, parse_streamers_file


def test_resolve_priority_list_reorders_on_time(monkeypatch):
    ctrl = TwitchTVController.__new__(TwitchTVController)
    ctrl.priority_entries = [
        StreamerEntry(username="top"),
        TimeBlock(members=[StreamerEntry(username="a"), StreamerEntry(username="b")],
                  rules=[TimeRule(_dt.time(20, 0), _dt.time(8, 0), ["b", "a"])]),
    ]
    ctrl._prev_resolved_order = None
    ctrl._reorder_event = False
    ctrl._resolve_priority_list(_dt.time(12, 0))
    assert ctrl.priority_list == ["top", "a", "b"]
    assert ctrl._reorder_event is False
    ctrl._resolve_priority_list(_dt.time(23, 0))
    assert ctrl.priority_list == ["top", "b", "a"]
    assert ctrl._reorder_event is True
    ctrl._resolve_priority_list(_dt.time(23, 30))
    assert ctrl._reorder_event is False


def test_reload_that_reorders_does_not_fire_reorder_event(tmp_path, monkeypatch):
    # A manual streamers.txt edit is not a time-window crossing: even if the
    # resolved order changes, _reorder_event must stay False so no "priority
    # shifted" grace switch fires (only a newly-live promotion should switch
    # after an edit, matching pre-Phase-3 behaviour).
    import os

    f = tmp_path / "streamers.txt"
    f.write_text("a\nb\nc\n")
    monkeypatch.setattr("marquee_daemon.STREAMERS_FILE", f)

    ctrl = TwitchTVController.__new__(TwitchTVController)
    ctrl.priority_entries = parse_streamers_file(f)
    ctrl._prev_resolved_order = None
    ctrl._reorder_event = False
    ctrl._streamers_mtime = f.stat().st_mtime
    ctrl._resolve_priority_list(_dt.time(12, 0))
    assert ctrl.priority_list == ["a", "b", "c"]

    f.write_text("c\nb\na\n")  # user reorders the file
    os.utime(f, (f.stat().st_atime, f.stat().st_mtime + 10))
    ctrl.maybe_reload_priority_list()
    ctrl._resolve_priority_list(_dt.time(12, 0))

    assert ctrl.priority_list == ["c", "b", "a"]  # new order took effect
    assert ctrl._reorder_event is False           # but not treated as a crossing


def test_show_notification_reason_reorder_message(monkeypatch):
    sent = {}
    monkeypatch.setattr("marquee_daemon.subprocess.run",
                        lambda cmd, **kw: sent.update(msg=cmd[-1]) or mock.Mock())
    ctrl = TwitchTVController.__new__(TwitchTVController)
    ctrl.show_notification("beta", {"title": "t", "game": "g"}, reason="reorder")
    assert "priority" in sent["msg"].lower()
    assert "beta" in sent["msg"]


def test_show_notification_default_reason_is_live(monkeypatch):
    sent = {}
    monkeypatch.setattr("marquee_daemon.subprocess.run",
                        lambda cmd, **kw: sent.update(msg=cmd[-1]) or mock.Mock())
    ctrl = TwitchTVController.__new__(TwitchTVController)
    ctrl.show_notification("beta", {"title": "t", "game": "g"})
    assert "live" in sent["msg"].lower()


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


def test_parse_play_token():
    assert parse_control_command("play") == (None, "play")


def test_parse_stop_token():
    assert parse_control_command("stop") == (None, "stop")


def test_parse_play_is_case_insensitive():
    assert parse_control_command("  PLAY \n") == (None, "play")


def test_query_single_live_returns_info_when_live(monkeypatch):
    def fake_run(cmd, **kwargs):
        assert "user_login=alpha" in cmd
        return mock.Mock(returncode=0, stdout=json.dumps({"data": [
            {"user_login": "alpha", "title": "t", "game_name": "g", "viewer_count": 3,
             "started_at": "2026-01-01T00:00:00Z"},
        ]}))
    monkeypatch.setattr("marquee_daemon.subprocess.run", fake_run)
    ctrl = TwitchTVController.__new__(TwitchTVController)
    assert ctrl._query_single_live("alpha") == {
        "title": "t", "game": "g", "viewers": 3, "started_at": "2026-01-01T00:00:00Z",
    }


def test_query_single_live_returns_none_when_offline(monkeypatch):
    monkeypatch.setattr("marquee_daemon.subprocess.run",
                        lambda cmd, **kw: mock.Mock(returncode=0, stdout=json.dumps({"data": []})))
    ctrl = TwitchTVController.__new__(TwitchTVController)
    assert ctrl._query_single_live("alpha") is None


def test_query_single_live_returns_none_on_unparseable_output(monkeypatch):
    monkeypatch.setattr("marquee_daemon.subprocess.run",
                        lambda cmd, **kw: mock.Mock(returncode=1, stdout="not json", stderr="boom"))
    ctrl = TwitchTVController.__new__(TwitchTVController)
    assert ctrl._query_single_live("alpha") is None


def test_query_single_live_returns_none_on_malformed_stream_object(monkeypatch):
    # The twitch CLI is flaky; a stream object missing expected keys must not
    # crash out of _handle_stream_death (which has already latched
    # _handled_current_death). Treat it as "couldn't confirm live".
    monkeypatch.setattr("marquee_daemon.subprocess.run",
                        lambda cmd, **kw: mock.Mock(returncode=0, stdout=json.dumps(
                            {"data": [{"user_login": "alpha"}]})))
    ctrl = TwitchTVController.__new__(TwitchTVController)
    assert ctrl._query_single_live("alpha") is None


def test_highest_priority_skips_cooled_down_streamer(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("marquee_daemon.time.time", lambda: now[0])
    ctrl = TwitchTVController.__new__(TwitchTVController)
    ctrl.priority_list = ["alpha", "beta"]
    ctrl.cooldowns = {"alpha": 1200.0}
    live = {"alpha": {}, "beta": {}}
    assert ctrl.get_highest_priority_live(live) == "beta"


def test_highest_priority_returns_streamer_after_cooldown_expires(monkeypatch):
    now = [1300.0]
    monkeypatch.setattr("marquee_daemon.time.time", lambda: now[0])
    ctrl = TwitchTVController.__new__(TwitchTVController)
    ctrl.priority_list = ["alpha", "beta"]
    ctrl.cooldowns = {"alpha": 1200.0}
    assert ctrl.get_highest_priority_live({"alpha": {}, "beta": {}}) == "alpha"


def test_highest_priority_no_cooldowns_attr_safe():
    ctrl = TwitchTVController.__new__(TwitchTVController)
    ctrl.priority_list = ["alpha"]
    ctrl.cooldowns = {}
    assert ctrl.get_highest_priority_live({"alpha": {}}) == "alpha"


def _dead_ctrl(monkeypatch, still_live, ran_for):
    monkeypatch.setattr("marquee_daemon.time.time", lambda: 1000.0)
    ctrl = TwitchTVController.__new__(TwitchTVController)
    ctrl.priority_list = ["alpha", "beta"]
    ctrl.cooldowns = {}
    ctrl.live_streams = {"alpha": {"title": "t"}, "beta": {"title": "t2"}}
    ctrl.current_stream = "alpha"
    ctrl.current_stream_started_at = 1000.0 - ran_for
    ctrl.last_api_update = 999.0
    monkeypatch.setattr("marquee_daemon.time.monotonic", lambda: 1000.0)
    monkeypatch.setattr(ctrl, "_query_single_live",
                        lambda s: {"title": "t"} if still_live else None)
    return ctrl


def test_stream_death_when_offline_cools_down_and_drops(monkeypatch):
    ctrl = _dead_ctrl(monkeypatch, still_live=False, ran_for=3600)
    ctrl._handle_stream_death()
    assert ctrl.cooldowns["alpha"] == 1000.0 + 300
    assert "alpha" not in ctrl.live_streams
    assert ctrl.last_api_update == 0.0


def test_stream_death_short_session_still_live_cools_down(monkeypatch):
    ctrl = _dead_ctrl(monkeypatch, still_live=True, ran_for=5)
    ctrl._handle_stream_death()
    assert "alpha" in ctrl.cooldowns
    assert "alpha" not in ctrl.live_streams


def test_stream_death_real_session_still_live_is_deliberate_close(monkeypatch):
    ctrl = _dead_ctrl(monkeypatch, still_live=True, ran_for=3600)
    ctrl._handle_stream_death()
    assert "alpha" not in ctrl.cooldowns
    assert "alpha" in ctrl.live_streams


def test_explicit_switch_clears_target_cooldown(monkeypatch):
    now = [2000.0]
    monkeypatch.setattr("marquee_daemon.time.time", lambda: now[0])
    ctrl = TwitchTVController.__new__(TwitchTVController)
    ctrl.priority_list = ["alpha", "beta"]
    ctrl.cooldowns = {"beta": 9999.0}
    ctrl._clear_cooldown("beta")
    assert "beta" not in ctrl.cooldowns


def test_settle_after_death_drops_stale_control_target():
    # alpha just ended and _handle_stream_death popped it from live_streams;
    # a control target still pointing at alpha would KeyError at launch.
    ctrl = TwitchTVController.__new__(TwitchTVController)
    ctrl.live_streams = {"beta": {}}
    assert ctrl._settle_after_death("alpha") is None


def test_settle_after_death_keeps_still_live_control_target():
    ctrl = TwitchTVController.__new__(TwitchTVController)
    ctrl.live_streams = {"alpha": {}, "beta": {}}
    assert ctrl._settle_after_death("beta") == "beta"


def test_settle_after_death_none_stays_none():
    ctrl = TwitchTVController.__new__(TwitchTVController)
    ctrl.live_streams = {"alpha": {}}
    assert ctrl._settle_after_death(None) is None


def test_stop_playback_tears_down_and_clears_state(monkeypatch):
    killed = []
    monkeypatch.setattr("marquee_daemon.subprocess.run",
                        lambda cmd, **kw: killed.append(cmd) or mock.Mock(returncode=0))
    proc = mock.Mock()
    proc.poll.return_value = None
    ctrl = TwitchTVController.__new__(TwitchTVController)
    ctrl.playback_enabled = True
    ctrl.current_process = proc
    ctrl.current_stream = "alpha"
    ctrl.current_socket_path = None
    ctrl.switching_soon = "beta"
    ctrl.grace_period_start = "whatever"
    ctrl.manual_override = True
    ctrl._handled_current_death = True
    ctrl.current_stream_started_at = 123.0
    ctrl._stop_playback()
    assert ctrl.playback_enabled is False
    assert ctrl.current_stream is None
    assert ctrl.switching_soon is None
    assert ctrl.grace_period_start is None
    assert ctrl.manual_override is False
    assert ctrl._handled_current_death is False
    assert ctrl.current_stream_started_at is None
    proc.terminate.assert_called_once()
    assert ["pkill", "-x", "chatterino"] in killed


def test_teardown_terminates_player_kills_chatterino_and_unlinks_runtime_files(tmp_path, monkeypatch):
    # run()'s finally -> _teardown() is the ONLY teardown path under a
    # signal-driven SystemExit (marquee.sh stop / systemctl stop / UI
    # quit-and-stop), so it must terminate the mpv/streamlink process and
    # kill Chatterino — otherwise both are orphaned.
    status_file = tmp_path / ".status.json"
    control_file = tmp_path / ".control"
    status_file.write_text("{}")
    control_file.write_text("stop")
    monkeypatch.setattr("marquee_daemon.STATUS_FILE", status_file)
    monkeypatch.setattr("marquee_daemon.CONTROL_FILE", control_file)
    killed = []
    monkeypatch.setattr("marquee_daemon.subprocess.run",
                        lambda cmd, **kw: killed.append(cmd) or mock.Mock(returncode=0))
    proc = mock.Mock()
    ctrl = TwitchTVController.__new__(TwitchTVController)
    ctrl.current_process = proc

    ctrl._teardown()

    proc.terminate.assert_called_once()
    assert ["pkill", "-x", "chatterino"] in killed
    assert not status_file.exists()
    assert not control_file.exists()


def test_teardown_is_safe_with_no_running_process(tmp_path, monkeypatch):
    monkeypatch.setattr("marquee_daemon.STATUS_FILE", tmp_path / ".status.json")
    monkeypatch.setattr("marquee_daemon.CONTROL_FILE", tmp_path / ".control")
    monkeypatch.setattr("marquee_daemon.subprocess.run",
                        lambda cmd, **kw: mock.Mock(returncode=0))
    ctrl = TwitchTVController.__new__(TwitchTVController)
    ctrl.current_process = None
    ctrl._teardown()  # must not raise


def test_apply_control_play_enables_playback():
    ctrl = TwitchTVController.__new__(TwitchTVController)
    ctrl.playback_enabled = False
    ctrl._apply_playback_token("play")
    assert ctrl.playback_enabled is True


def test_apply_control_stop_calls_stop_playback(monkeypatch):
    ctrl = TwitchTVController.__new__(TwitchTVController)
    called = []
    monkeypatch.setattr(ctrl, "_stop_playback", lambda: called.append(True))
    ctrl._apply_playback_token("stop")
    assert called == [True]


def test_save_status_includes_playback_enabled(tmp_path, monkeypatch):
    monkeypatch.setattr("marquee_daemon.STATUS_FILE", tmp_path / ".status.json")
    ctrl = TwitchTVController.__new__(TwitchTVController)
    ctrl.playback_enabled = False
    ctrl.current_stream = None
    ctrl.current_process = None
    ctrl.switching_soon = None
    ctrl.grace_period_start = None
    ctrl.live_streams = {}
    ctrl.save_status()
    data = json.loads((tmp_path / ".status.json").read_text())
    assert data["playback_enabled"] is False
