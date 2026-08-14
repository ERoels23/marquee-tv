import json
import time
from pathlib import Path
from unittest import mock

import pytest

from textual.widgets import Static

from marquee_ui import MarqueeApp, InfoModal, QuitConfirmModal, PENDING_MANUAL_SWITCH_TIMEOUT
from marquee_model import AdHocFlowState


@pytest.mark.asyncio
async def test_app_boots_and_shows_daemon_offline_when_daemon_not_running(tmp_path, monkeypatch):
    streamers_file = tmp_path / "streamers.txt"
    streamers_file.write_text("teststreamer|Test\n")
    monkeypatch.setattr("marquee_ui.STREAMERS_FILE", streamers_file)
    monkeypatch.setattr("marquee_ui.STATUS_FILE", tmp_path / ".status.json")
    monkeypatch.setattr("marquee_ui.LAST_SEEN_FILE", tmp_path / ".last_seen.json")
    monkeypatch.setattr(MarqueeApp, "poll_live_streams_from_api", lambda self: {})
    monkeypatch.setattr(MarqueeApp, "daemon_running", lambda self: False)

    app = MarqueeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        frame = app.query_one("#frame")
        assert "Daemon Offline" in frame.content
        assert "No stream active" not in frame.content
        assert "Test" in frame.content  # priority list still shows regardless


@pytest.mark.asyncio
async def test_app_shows_no_stream_active_when_daemon_running_but_idle(tmp_path, monkeypatch):
    streamers_file = tmp_path / "streamers.txt"
    streamers_file.write_text("teststreamer|Test\n")
    status_file = tmp_path / ".status.json"
    status_file.write_text(json.dumps({"current_stream": None, "stream_alive": False, "live_streams": {}}))
    monkeypatch.setattr("marquee_ui.STREAMERS_FILE", streamers_file)
    monkeypatch.setattr("marquee_ui.STATUS_FILE", status_file)
    monkeypatch.setattr("marquee_ui.LAST_SEEN_FILE", tmp_path / ".last_seen.json")
    monkeypatch.setattr(MarqueeApp, "poll_live_streams_from_api", lambda self: {})
    monkeypatch.setattr(MarqueeApp, "daemon_running", lambda self: True)

    app = MarqueeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        frame = app.query_one("#frame")
        assert "No stream active" in frame.content
        assert "Daemon Offline" not in frame.content


@pytest.mark.asyncio
async def test_stopping_daemon_shows_daemon_offline_not_stale_stream_name(tmp_path, monkeypatch):
    # Regression: stopping the daemon left current_stream/live_streams stale,
    # so the NOW WATCHING box kept showing the previously-playing channel
    # instead of reflecting that nothing is being managed anymore.
    streamers_file = tmp_path / "streamers.txt"
    streamers_file.write_text("teststreamer\n")
    status_file = tmp_path / ".status.json"
    status_file.write_text(json.dumps({
        "current_stream": "teststreamer", "stream_alive": True,
        "live_streams": {"teststreamer": {"title": "t", "game": "g", "viewers": 1, "started_at": None}},
    }))
    monkeypatch.setattr("marquee_ui.STREAMERS_FILE", streamers_file)
    monkeypatch.setattr("marquee_ui.STATUS_FILE", status_file)
    monkeypatch.setattr("marquee_ui.LAST_SEEN_FILE", tmp_path / ".last_seen.json")
    monkeypatch.setattr(MarqueeApp, "poll_live_streams_from_api", lambda self: {})

    daemon_state = {"running": True}
    monkeypatch.setattr(MarqueeApp, "daemon_running", lambda self: daemon_state["running"])
    monkeypatch.setattr(MarqueeApp, "stop_service", lambda self: daemon_state.__setitem__("running", False))

    app = MarqueeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.current_stream == "teststreamer"

        await pilot.press("x")
        await pilot.pause()

        assert app.current_stream is None
        frame = app.query_one("#frame")
        assert "Daemon Offline" in frame.content
        assert "teststreamer" not in frame.content.split("PRIORITY LIST")[0]  # not in NOW WATCHING box


@pytest.mark.asyncio
async def test_frame_width_tracks_terminal_and_updates_on_resize(tmp_path, monkeypatch):
    from rich.cells import cell_len

    streamers_file = tmp_path / "streamers.txt"
    streamers_file.write_text("teststreamer\n")
    monkeypatch.setattr("marquee_ui.STREAMERS_FILE", streamers_file)
    monkeypatch.setattr("marquee_ui.STATUS_FILE", tmp_path / ".status.json")
    monkeypatch.setattr("marquee_ui.LAST_SEEN_FILE", tmp_path / ".last_seen.json")
    monkeypatch.setattr(MarqueeApp, "poll_live_streams_from_api", lambda self: {})

    app = MarqueeApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        first_line = app.query_one("#frame").content.split("\n")[0].plain
        assert cell_len(first_line) == 120

        await pilot.resize_terminal(160, 40)
        await pilot.pause()
        first_line = app.query_one("#frame").content.split("\n")[0].plain
        assert cell_len(first_line) == 160


@pytest.mark.asyncio
async def test_blank_line_spacing_around_highlighted_row(tmp_path, monkeypatch):
    streamers_file = tmp_path / "streamers.txt"
    streamers_file.write_text("alpha\nbeta\ngamma\n")
    monkeypatch.setattr("marquee_ui.STREAMERS_FILE", streamers_file)
    monkeypatch.setattr("marquee_ui.STATUS_FILE", tmp_path / ".status.json")
    monkeypatch.setattr("marquee_ui.LAST_SEEN_FILE", tmp_path / ".last_seen.json")
    monkeypatch.setattr(MarqueeApp, "poll_live_streams_from_api", lambda self: {})

    def is_blank_breakout(line) -> bool:
        text = line.plain
        return text.startswith("║") and text.endswith("║") and text[1:-1].strip() == ""

    app = MarqueeApp()
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()

        # Blank line above and below the highlighted row, regardless of its
        # position in the list — including at the very top or bottom.
        for index in (0, 1, 2):
            app.nav.index = index
            app.render_frame()
            lines = app.query_one("#frame").content.split("\n")
            marker_idx = next(i for i, l in enumerate(lines) if l.plain.startswith("║▶"))
            assert is_blank_breakout(lines[marker_idx - 1])
            assert is_blank_breakout(lines[marker_idx + 2])  # past collapsed + detail lines


@pytest.mark.asyncio
async def test_frame_height_fills_terminal_and_updates_on_resize(tmp_path, monkeypatch):
    streamers_file = tmp_path / "streamers.txt"
    streamers_file.write_text("teststreamer\n")
    monkeypatch.setattr("marquee_ui.STREAMERS_FILE", streamers_file)
    monkeypatch.setattr("marquee_ui.STATUS_FILE", tmp_path / ".status.json")
    monkeypatch.setattr("marquee_ui.LAST_SEEN_FILE", tmp_path / ".last_seen.json")
    monkeypatch.setattr(MarqueeApp, "poll_live_streams_from_api", lambda self: {})

    app = MarqueeApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        lines = app.query_one("#frame").content.split("\n")
        assert len(lines) == 30  # border fills the whole terminal, not just content
        assert lines[-2].plain.startswith("║ (Q)uit")  # footer pinned just above the bottom border
        assert lines[-1].plain.startswith("╚")

        await pilot.resize_terminal(120, 40)
        await pilot.pause()
        lines = app.query_one("#frame").content.split("\n")
        assert len(lines) == 40


@pytest.mark.asyncio
async def test_frame_width_matches_widget_width_when_content_taller_than_terminal(tmp_path, monkeypatch):
    # Screen defaults to overflow-y: auto, which reserves a scrollbar gutter
    # once content is taller than the terminal (e.g. a long priority list on a
    # short terminal) — that silently narrows the #frame widget below the
    # width render_frame() assumed, causing every line to wrap and the whole
    # box layout to break. Regression test for that scenario specifically.
    from rich.cells import cell_len

    streamers_file = tmp_path / "streamers.txt"
    streamers_file.write_text("\n".join(f"streamer{i}" for i in range(15)) + "\n")
    monkeypatch.setattr("marquee_ui.STREAMERS_FILE", streamers_file)
    monkeypatch.setattr("marquee_ui.STATUS_FILE", tmp_path / ".status.json")
    monkeypatch.setattr("marquee_ui.LAST_SEEN_FILE", tmp_path / ".last_seen.json")
    monkeypatch.setattr(MarqueeApp, "poll_live_streams_from_api", lambda self: {})

    app = MarqueeApp()
    async with app.run_test(size=(100, 24)) as pilot:  # content renders taller than 24 rows
        await pilot.pause()
        frame = app.query_one("#frame")
        first_line = frame.content.split("\n")[0].plain
        assert cell_len(first_line) == frame.size.width == 100


def test_manual_switch_sets_manual_transition(tmp_path, monkeypatch):
    status_file = tmp_path / ".status.json"
    monkeypatch.setattr("marquee_ui.STATUS_FILE", status_file)
    app = MarqueeApp()
    app.current_stream = "alpha"
    app._pending_manual_switch = ("beta", time.monotonic())
    status_file.write_text(json.dumps({"current_stream": "beta", "stream_alive": True, "live_streams": {}}))

    app._load_status_file()

    assert app.transition is not None
    assert app.transition["kind"] == "manual"
    assert app._pending_manual_switch is None  # cleared once observed


def test_auto_switch_sets_auto_transition(tmp_path, monkeypatch):
    status_file = tmp_path / ".status.json"
    monkeypatch.setattr("marquee_ui.STATUS_FILE", status_file)
    app = MarqueeApp()
    app.current_stream = "alpha"
    status_file.write_text(json.dumps({"current_stream": "beta", "stream_alive": True, "live_streams": {}}))

    app._load_status_file()  # no pending manual switch recorded — daemon-initiated

    assert app.transition is not None
    assert app.transition["kind"] == "auto"


def test_stale_pending_manual_switch_does_not_mislabel_a_later_unrelated_switch(tmp_path, monkeypatch):
    # A manual switch can be requested without current_stream ever changing (e.g.
    # relaunching the already-current stream, or targeting an entry that went
    # offline before the daemon acted on it) — _pending_manual_switch would then
    # sit there unconsumed. If an unrelated auto-switch later lands on that same
    # streamer name, it must not be mislabeled "manual" just because the stale
    # flag happens to match.
    status_file = tmp_path / ".status.json"
    monkeypatch.setattr("marquee_ui.STATUS_FILE", status_file)
    app = MarqueeApp()
    app.current_stream = "alpha"
    app._pending_manual_switch = ("beta", time.monotonic() - PENDING_MANUAL_SWITCH_TIMEOUT - 1)
    status_file.write_text(json.dumps({"current_stream": "beta", "stream_alive": True, "live_streams": {}}))

    app._load_status_file()

    assert app.transition["kind"] == "auto"
    assert app._pending_manual_switch is None


def test_no_transition_on_initial_boot(tmp_path, monkeypatch):
    status_file = tmp_path / ".status.json"
    monkeypatch.setattr("marquee_ui.STATUS_FILE", status_file)
    app = MarqueeApp()
    assert app.current_stream is None
    status_file.write_text(json.dumps({"current_stream": "beta", "stream_alive": True, "live_streams": {}}))

    app._load_status_file()

    assert app.transition is None


def test_transition_header_lines_content_and_expiry():
    app = MarqueeApp()

    app.transition = {"kind": "auto", "started": time.monotonic()}
    lines = app._transition_header_lines(40)
    assert lines is not None
    assert "FOUND HIGHER PRIORITY STREAM" in lines[0]
    assert "SWITCHING NOW" in lines[1]

    app.transition = {"kind": "manual", "started": time.monotonic()}
    lines = app._transition_header_lines(40)
    assert "NEW STREAM SELECTED" in lines[0]

    app.transition = {"kind": "auto", "started": time.monotonic() - 10}
    lines = app._transition_header_lines(40)
    assert lines is None
    assert app.transition is None  # expired transition clears itself


def test_refresh_data_does_not_clobber_fresh_api_data_with_stale_status_file(tmp_path, monkeypatch):
    status_file = tmp_path / ".status.json"
    status_file.write_text(json.dumps({
        "current_stream": "stale_streamer",
        "stream_alive": True,
        "live_streams": {"stale_streamer": {"title": "OLD STALE TITLE", "game": "g", "viewers": 1, "started_at": None}},
    }))
    monkeypatch.setattr("marquee_ui.STREAMERS_FILE", tmp_path / "streamers.txt")
    (tmp_path / "streamers.txt").write_text("teststreamer\n")
    monkeypatch.setattr("marquee_ui.STATUS_FILE", status_file)
    monkeypatch.setattr("marquee_ui.LAST_SEEN_FILE", tmp_path / ".last_seen.json")
    monkeypatch.setattr(MarqueeApp, "daemon_running", lambda self: False)
    monkeypatch.setattr(MarqueeApp, "poll_live_streams_from_api", lambda self: {
        "teststreamer": {"title": "FRESH API TITLE", "game": "g", "viewers": 2, "started_at": None}
    })

    app = MarqueeApp()
    app.load_entries()
    app.refresh_data(force=True)
    assert "teststreamer" in app.live_streams
    assert "stale_streamer" not in app.live_streams

    app.refresh_data(force=False)  # simulates a cheap tick within the 60s window
    assert "teststreamer" in app.live_streams
    assert "stale_streamer" not in app.live_streams


def test_refresh_data_reloads_last_seen_on_cheap_tick(tmp_path, monkeypatch):
    # Regression: the cheap (non-force) tick path only re-read .status.json,
    # never .last_seen.json, so "last live" info could sit stale for up to
    # a full 60s API_UPDATE_INTERVAL between full polls.
    status_file = tmp_path / ".status.json"
    last_seen_file = tmp_path / ".last_seen.json"
    monkeypatch.setattr("marquee_ui.STREAMERS_FILE", tmp_path / "streamers.txt")
    (tmp_path / "streamers.txt").write_text("teststreamer\n")
    monkeypatch.setattr("marquee_ui.STATUS_FILE", status_file)
    monkeypatch.setattr("marquee_ui.LAST_SEEN_FILE", last_seen_file)
    monkeypatch.setattr(MarqueeApp, "daemon_running", lambda self: False)
    monkeypatch.setattr(MarqueeApp, "poll_live_streams_from_api", lambda self: {})

    app = MarqueeApp()
    app.load_entries()
    app.refresh_data(force=True)
    assert app.last_seen == {}

    last_seen_file.write_text(json.dumps({"teststreamer": "2026-08-14T00:00:00+00:00"}))
    app.refresh_data(force=False)  # cheap tick — must still pick up the new file

    # Legacy flat-string format is migrated to the richer shape on load.
    assert app.last_seen.get("teststreamer") == {
        "at": "2026-08-14T00:00:00+00:00", "game": None, "title": None,
    }


@pytest.mark.asyncio
async def test_enter_starts_daemon_when_not_running(tmp_path, monkeypatch):
    streamers_file = tmp_path / "streamers.txt"
    streamers_file.write_text("alpha\nbeta\n")
    control_file = tmp_path / ".control"
    monkeypatch.setattr("marquee_ui.STREAMERS_FILE", streamers_file)
    monkeypatch.setattr("marquee_ui.STATUS_FILE", tmp_path / ".status.json")
    monkeypatch.setattr("marquee_ui.LAST_SEEN_FILE", tmp_path / ".last_seen.json")
    monkeypatch.setattr("marquee_ui.CONTROL_FILE", control_file)
    monkeypatch.setattr(MarqueeApp, "poll_live_streams_from_api", lambda self: {})
    monkeypatch.setattr(MarqueeApp, "daemon_running", lambda self: False)
    start_calls = {"count": 0}
    monkeypatch.setattr(MarqueeApp, "start_service", lambda self: start_calls.__setitem__("count", start_calls["count"] + 1))

    app = MarqueeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.nav.index = 1  # highlight "beta"
        await pilot.press("enter")
        assert control_file.read_text() == "switch:beta"
        assert start_calls["count"] == 1  # daemon wasn't running — Enter must start it


@pytest.mark.asyncio
async def test_enter_launches_highlighted_stream(tmp_path, monkeypatch):
    streamers_file = tmp_path / "streamers.txt"
    streamers_file.write_text("alpha\nbeta\n")
    control_file = tmp_path / ".control"
    monkeypatch.setattr("marquee_ui.STREAMERS_FILE", streamers_file)
    monkeypatch.setattr("marquee_ui.STATUS_FILE", tmp_path / ".status.json")
    monkeypatch.setattr("marquee_ui.LAST_SEEN_FILE", tmp_path / ".last_seen.json")
    monkeypatch.setattr("marquee_ui.CONTROL_FILE", control_file)
    monkeypatch.setattr(MarqueeApp, "poll_live_streams_from_api", lambda self: {})
    monkeypatch.setattr(MarqueeApp, "daemon_running", lambda self: True)  # avoid a real start_service() call

    app = MarqueeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.nav.index = 1  # highlight "beta"
        await pilot.press("enter")
        assert control_file.read_text() == "switch:beta"


@pytest.mark.asyncio
async def test_enter_shows_transition_text_immediately(tmp_path, monkeypatch):
    # Regression: the transition text previously only appeared once the
    # daemon's status file confirmed the switch (up to its own poll interval
    # later), instead of the instant it's requested.
    streamers_file = tmp_path / "streamers.txt"
    streamers_file.write_text("alpha\nbeta\n")
    control_file = tmp_path / ".control"
    monkeypatch.setattr("marquee_ui.STREAMERS_FILE", streamers_file)
    monkeypatch.setattr("marquee_ui.STATUS_FILE", tmp_path / ".status.json")
    monkeypatch.setattr("marquee_ui.LAST_SEEN_FILE", tmp_path / ".last_seen.json")
    monkeypatch.setattr("marquee_ui.CONTROL_FILE", control_file)
    monkeypatch.setattr(MarqueeApp, "poll_live_streams_from_api", lambda self: {})
    monkeypatch.setattr(MarqueeApp, "daemon_running", lambda self: True)

    app = MarqueeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.transition is None
        app.nav.index = 1
        await pilot.press("enter")
        assert app.transition is not None
        assert app.transition["kind"] == "manual"
        frame = app.query_one("#frame")
        assert "NEW STREAM SELECTED" in frame.content


@pytest.mark.asyncio
async def test_enter_with_empty_streamer_list_does_not_crash_or_write_control(tmp_path, monkeypatch):
    streamers_file = tmp_path / "streamers.txt"
    streamers_file.write_text("")
    control_file = tmp_path / ".control"
    monkeypatch.setattr("marquee_ui.STREAMERS_FILE", streamers_file)
    monkeypatch.setattr("marquee_ui.STATUS_FILE", tmp_path / ".status.json")
    monkeypatch.setattr("marquee_ui.LAST_SEEN_FILE", tmp_path / ".last_seen.json")
    monkeypatch.setattr("marquee_ui.CONTROL_FILE", control_file)
    monkeypatch.setattr(MarqueeApp, "poll_live_streams_from_api", lambda self: {})

    app = MarqueeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.nav.index == -1
        await pilot.press("enter")
        assert not control_file.exists()


@pytest.mark.asyncio
async def test_slash_starts_typing_and_s_does_not_trigger_start(tmp_path, monkeypatch):
    streamers_file = tmp_path / "streamers.txt"
    streamers_file.write_text("alpha\n")
    monkeypatch.setattr("marquee_ui.STREAMERS_FILE", streamers_file)
    monkeypatch.setattr("marquee_ui.STATUS_FILE", tmp_path / ".status.json")
    monkeypatch.setattr("marquee_ui.LAST_SEEN_FILE", tmp_path / ".last_seen.json")
    monkeypatch.setattr(MarqueeApp, "poll_live_streams_from_api", lambda self: {})

    started = {"called": False}

    def fake_start(self):
        started["called"] = True

    monkeypatch.setattr(MarqueeApp, "start_service", fake_start)

    app = MarqueeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("slash")
        assert app.ad_hoc.state == AdHocFlowState.TYPING
        await pilot.press("s")
        await pilot.press("o")
        await pilot.press("m")
        await pilot.press("e")
        assert app.ad_hoc.buffer == "some"
        assert started["called"] is False  # "s" must not have triggered start_service


@pytest.mark.asyncio
async def test_adhoc_submit_shows_mode_picker_then_writes_control(tmp_path, monkeypatch):
    streamers_file = tmp_path / "streamers.txt"
    streamers_file.write_text("alpha\n")
    control_file = tmp_path / ".control"
    monkeypatch.setattr("marquee_ui.STREAMERS_FILE", streamers_file)
    monkeypatch.setattr("marquee_ui.STATUS_FILE", tmp_path / ".status.json")
    monkeypatch.setattr("marquee_ui.LAST_SEEN_FILE", tmp_path / ".last_seen.json")
    monkeypatch.setattr("marquee_ui.CONTROL_FILE", control_file)
    monkeypatch.setattr(MarqueeApp, "poll_live_streams_from_api", lambda self: {})
    monkeypatch.setattr(MarqueeApp, "daemon_running", lambda self: True)  # avoid a real start_service() call

    app = MarqueeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("slash")
        for ch in "gronk":
            await pilot.press(ch)
        await pilot.press("enter")
        assert app.ad_hoc.state == AdHocFlowState.MODE_SELECT
        await pilot.press("o")  # Override
        assert control_file.read_text() == "switch:gronk:override"
        assert app.ad_hoc_mode == "override"


@pytest.mark.asyncio
async def test_oneshot_does_not_touch_control_file_and_tracks_process(tmp_path, monkeypatch):
    streamers_file = tmp_path / "streamers.txt"
    streamers_file.write_text("alpha\n")
    control_file = tmp_path / ".control"
    monkeypatch.setattr("marquee_ui.STREAMERS_FILE", streamers_file)
    monkeypatch.setattr("marquee_ui.STATUS_FILE", tmp_path / ".status.json")
    monkeypatch.setattr("marquee_ui.LAST_SEEN_FILE", tmp_path / ".last_seen.json")
    monkeypatch.setattr("marquee_ui.CONTROL_FILE", control_file)
    monkeypatch.setattr("marquee_ui.SCRIPT_DIR", tmp_path)
    monkeypatch.setattr(MarqueeApp, "poll_live_streams_from_api", lambda self: {})

    fake_process = mock.Mock()
    fake_process.poll.return_value = None  # still running
    monkeypatch.setattr("marquee_ui.subprocess.Popen", lambda *a, **kw: fake_process)
    monkeypatch.setattr("marquee_ui.subprocess.run", lambda *a, **kw: mock.Mock(returncode=1))

    app = MarqueeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("slash")
        for ch in "gronk":
            await pilot.press(ch)
        await pilot.press("enter")
        await pilot.press("1")  # One-Shot

        assert not control_file.exists()
        assert "gronk" in app.one_shots
        assert app.one_shots["gronk"]["process"] is fake_process
        assert app.one_shots["gronk"]["timer"] is not None


@pytest.mark.asyncio
async def test_oneshot_poll_stops_timer_when_process_exits(tmp_path, monkeypatch):
    streamers_file = tmp_path / "streamers.txt"
    streamers_file.write_text("alpha\n")
    monkeypatch.setattr("marquee_ui.STREAMERS_FILE", streamers_file)
    monkeypatch.setattr("marquee_ui.STATUS_FILE", tmp_path / ".status.json")
    monkeypatch.setattr("marquee_ui.LAST_SEEN_FILE", tmp_path / ".last_seen.json")
    monkeypatch.setattr(MarqueeApp, "poll_live_streams_from_api", lambda self: {})

    app = MarqueeApp()
    fake_process = mock.Mock()
    fake_process.poll.return_value = 0  # already exited
    fake_timer = mock.Mock()
    socket_path = tmp_path / ".mpv-oneshot-gronk.sock"
    socket_path.touch()
    app.one_shots["gronk"] = {"process": fake_process, "timer": fake_timer, "socket_path": socket_path}

    app._poll_one_shot_title("gronk")

    fake_timer.stop.assert_called_once()
    assert "gronk" not in app.one_shots
    assert not socket_path.exists()


def test_poll_live_streams_from_api_queries_by_user_login_not_followed(monkeypatch):
    # Regression: /streams/followed only returns channels the Twitch account
    # actually follows, so any priority-list entry that isn't followed could
    # never register as live (and would never get a last_seen timestamp) even
    # while genuinely streaming. Query by explicit user_login instead.
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return mock.Mock(returncode=0, stdout=json.dumps({"data": [
            {"user_login": "alpha", "title": "t1", "game_name": "g1", "viewer_count": 5, "started_at": "2026-01-01T00:00:00Z"},
            {"user_login": "beta", "title": "t2", "game_name": "g2", "viewer_count": 9, "started_at": None},
        ]}))

    monkeypatch.setattr("marquee_ui.subprocess.run", fake_run)
    app = MarqueeApp.__new__(MarqueeApp)
    from priority_list import StreamerEntry
    app.entries = [StreamerEntry(username="alpha"), StreamerEntry(username="beta"), StreamerEntry(username="gamma")]

    live = app.poll_live_streams_from_api()

    assert "user_login=alpha" in captured["cmd"]
    assert "user_login=beta" in captured["cmd"]
    assert "user_login=gamma" in captured["cmd"]
    assert "/streams/followed" not in " ".join(captured["cmd"])
    assert live == {
        "alpha": {"title": "t1", "game": "g1", "viewers": 5, "started_at": "2026-01-01T00:00:00Z"},
        "beta": {"title": "t2", "game": "g2", "viewers": 9, "started_at": None},
    }


def test_poll_live_streams_from_api_uses_data_despite_nonzero_exit_code(monkeypatch):
    # Regression: the installed twitch CLI can crash in its own unrelated
    # update-check code *after* already printing valid JSON to stdout,
    # exiting non-zero despite having done its job correctly. Success should
    # be judged by whether stdout actually parses, not the exit code.
    def fake_run(cmd, **kwargs):
        return mock.Mock(returncode=2, stdout=json.dumps({"data": [
            {"user_login": "alpha", "title": "t1", "game_name": "g1", "viewer_count": 5, "started_at": None},
        ]}), stderr="panic: runtime error: index out of range [0] with length 0")

    monkeypatch.setattr("marquee_ui.subprocess.run", fake_run)
    app = MarqueeApp.__new__(MarqueeApp)
    from priority_list import StreamerEntry
    app.entries = [StreamerEntry(username="alpha")]

    live = app.poll_live_streams_from_api()

    assert live == {"alpha": {"title": "t1", "game": "g1", "viewers": 5, "started_at": None}}


def test_poll_single_stream_from_api_queries_by_user_login(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return mock.Mock(returncode=0, stdout='{"data": [{"title": "t", "game_name": "g", "viewer_count": 5, "started_at": "2026-01-01T00:00:00Z"}]}')

    monkeypatch.setattr("marquee_ui.subprocess.run", fake_run)
    app = MarqueeApp.__new__(MarqueeApp)  # no need for full init for this pure method
    result = app.poll_single_stream_from_api("somestreamer")

    assert "user_login=somestreamer" in captured["cmd"]
    assert "/streams/followed" not in " ".join(captured["cmd"])
    assert result == {"title": "t", "game": "g", "viewers": 5, "started_at": "2026-01-01T00:00:00Z"}


@pytest.mark.asyncio
async def test_oneshot_poll_updates_title_when_process_still_running(tmp_path, monkeypatch):
    monkeypatch.setattr("marquee_ui.STREAMERS_FILE", tmp_path / "streamers.txt")
    (tmp_path / "streamers.txt").write_text("alpha\n")
    monkeypatch.setattr("marquee_ui.STATUS_FILE", tmp_path / ".status.json")
    monkeypatch.setattr("marquee_ui.LAST_SEEN_FILE", tmp_path / ".last_seen.json")
    monkeypatch.setattr(MarqueeApp, "poll_live_streams_from_api", lambda self: {})

    app = MarqueeApp()
    fake_process = mock.Mock()
    fake_process.poll.return_value = None  # still running
    socket_path = tmp_path / ".mpv-oneshot-gronk.sock"
    app.one_shots["gronk"] = {"process": fake_process, "timer": mock.Mock(), "socket_path": socket_path}

    monkeypatch.setattr(app, "poll_single_stream_from_api", lambda streamer: {
        "title": "live title", "game": "Just Chatting", "viewers": 10, "started_at": None
    })
    set_title_calls = []
    monkeypatch.setattr("marquee_ui.set_title", lambda sock, title: set_title_calls.append((sock, title)))

    app._poll_one_shot_title("gronk")

    assert len(set_title_calls) == 1
    assert set_title_calls[0][0] == socket_path
    assert "gronk" in set_title_calls[0][1]
    assert "Just Chatting" in set_title_calls[0][1]
    assert "gronk" in app.one_shots  # entry NOT removed, process still running


@pytest.mark.asyncio
async def test_e_opens_editor_and_reloads_list(tmp_path, monkeypatch):
    streamers_file = tmp_path / "streamers.txt"
    streamers_file.write_text("alpha\n")
    monkeypatch.setattr("marquee_ui.STREAMERS_FILE", streamers_file)
    monkeypatch.setattr("marquee_ui.STATUS_FILE", tmp_path / ".status.json")
    monkeypatch.setattr("marquee_ui.LAST_SEEN_FILE", tmp_path / ".last_seen.json")
    monkeypatch.setattr(MarqueeApp, "poll_live_streams_from_api", lambda self: {})

    from contextlib import contextmanager

    @contextmanager
    def fake_suspend(self):
        # simulate the user editing the file while "suspended"
        streamers_file.write_text("alpha\nbeta\n")
        yield

    run_calls = []

    def fake_run(*a, **kw):
        run_calls.append(a)
        return mock.Mock(returncode=1, stdout="")  # e.g. pgrep-style "not found"

    monkeypatch.setattr(MarqueeApp, "suspend", fake_suspend)
    # action_edit_list does `import subprocess as sp` locally, but that local name
    # still refers to the one global `subprocess` module object, so patching
    # subprocess.run globally is what actually takes effect here. This also
    # intercepts other subprocess.run call sites (e.g. daemon_running's pgrep),
    # so the fake must return a plausible CompletedProcess-like object for those too.
    monkeypatch.setattr("subprocess.run", fake_run)

    app = MarqueeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert len(app.entries) == 1
        await pilot.press("e")
        assert len(app.entries) == 2  # reloaded after "editing"
        assert any(str(streamers_file) in call[0] for call in run_calls)


@pytest.mark.asyncio
async def test_e_survives_missing_editor_binary(tmp_path, monkeypatch):
    streamers_file = tmp_path / "streamers.txt"
    streamers_file.write_text("alpha\n")
    monkeypatch.setattr("marquee_ui.STREAMERS_FILE", streamers_file)
    monkeypatch.setattr("marquee_ui.STATUS_FILE", tmp_path / ".status.json")
    monkeypatch.setattr("marquee_ui.LAST_SEEN_FILE", tmp_path / ".last_seen.json")
    monkeypatch.setattr(MarqueeApp, "poll_live_streams_from_api", lambda self: {})
    # daemon_running also shells out via subprocess.run (pgrep); mock it directly
    # so patching subprocess.run to always raise below doesn't trip it up too —
    # this test is only about action_edit_list's error handling.
    monkeypatch.setattr(MarqueeApp, "daemon_running", lambda self: False)

    from contextlib import contextmanager

    @contextmanager
    def fake_suspend(self):
        yield

    def raise_not_found(*a, **kw):
        raise FileNotFoundError("nonexistent-editor-binary")

    monkeypatch.setattr(MarqueeApp, "suspend", fake_suspend)
    monkeypatch.setattr("subprocess.run", raise_not_found)

    app = MarqueeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("e")  # should not crash
        assert len(app.entries) == 1  # reload still happened, list unchanged


@pytest.mark.asyncio
async def test_i_shows_overlay_with_title_and_bio(tmp_path, monkeypatch):
    streamers_file = tmp_path / "streamers.txt"
    streamers_file.write_text("alpha\n")
    monkeypatch.setattr("marquee_ui.STREAMERS_FILE", streamers_file)
    monkeypatch.setattr("marquee_ui.STATUS_FILE", tmp_path / ".status.json")
    monkeypatch.setattr("marquee_ui.LAST_SEEN_FILE", tmp_path / ".last_seen.json")
    monkeypatch.setattr(MarqueeApp, "poll_live_streams_from_api", lambda self: {})
    monkeypatch.setattr(MarqueeApp, "_fetch_channel_bio", lambda self, streamer: "A cool streamer bio")

    app = MarqueeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.current_stream = "alpha"
        app.live_streams = {"alpha": {"title": "Playing games", "game": "g", "viewers": 1, "started_at": None}}
        await pilot.press("i")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert isinstance(app.screen, InfoModal)
        title_widget = app.screen.query_one("#info-title", Static)
        bio_widget = app.screen.query_one("#bio", Static)
        assert "Playing games" in title_widget.content
        assert "A cool streamer bio" in bio_widget.content
        # Regression: width:auto on the panel Vertical previously collapsed
        # every child to a 0x0 box (blank popup, no visible text at all).
        assert title_widget.size.width > 0
        assert bio_widget.size.width > 0
        await pilot.press("i")
        await pilot.pause()
        assert not isinstance(app.screen, InfoModal)
        frame2 = app.query_one("#frame")
        assert "PRIORITY LIST" in frame2.content


@pytest.mark.asyncio
async def test_i_does_nothing_when_no_current_stream(tmp_path, monkeypatch):
    streamers_file = tmp_path / "streamers.txt"
    streamers_file.write_text("alpha\n")
    monkeypatch.setattr("marquee_ui.STREAMERS_FILE", streamers_file)
    monkeypatch.setattr("marquee_ui.STATUS_FILE", tmp_path / ".status.json")
    monkeypatch.setattr("marquee_ui.LAST_SEEN_FILE", tmp_path / ".last_seen.json")
    monkeypatch.setattr(MarqueeApp, "poll_live_streams_from_api", lambda self: {})

    app = MarqueeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.current_stream is None
        await pilot.press("i")
        assert not isinstance(app.screen, InfoModal)


@pytest.mark.asyncio
async def test_overlay_blocks_navigation_and_launch(tmp_path, monkeypatch):
    streamers_file = tmp_path / "streamers.txt"
    streamers_file.write_text("alpha\nbeta\n")
    control_file = tmp_path / ".control"
    monkeypatch.setattr("marquee_ui.STREAMERS_FILE", streamers_file)
    monkeypatch.setattr("marquee_ui.STATUS_FILE", tmp_path / ".status.json")
    monkeypatch.setattr("marquee_ui.LAST_SEEN_FILE", tmp_path / ".last_seen.json")
    monkeypatch.setattr("marquee_ui.CONTROL_FILE", control_file)
    monkeypatch.setattr(MarqueeApp, "poll_live_streams_from_api", lambda self: {})
    monkeypatch.setattr(MarqueeApp, "_fetch_channel_bio", lambda self, streamer: "bio")

    app = MarqueeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.current_stream = "alpha"
        app.live_streams = {"alpha": {"title": "t", "game": "g", "viewers": 1, "started_at": None}}
        await pilot.press("i")
        assert isinstance(app.screen, InfoModal)
        starting_index = app.nav.index

        await pilot.press("j")  # should be swallowed by the modal, not reach the list
        assert app.nav.index == starting_index

        await pilot.press("enter")  # should be swallowed, not launch a stream
        assert not control_file.exists()

        await pilot.press("i")  # this should still close it
        await pilot.pause()
        assert not isinstance(app.screen, InfoModal)


@pytest.mark.asyncio
async def test_quit_instant_when_daemon_not_running(tmp_path, monkeypatch):
    streamers_file = tmp_path / "streamers.txt"
    streamers_file.write_text("alpha\n")
    monkeypatch.setattr("marquee_ui.STREAMERS_FILE", streamers_file)
    monkeypatch.setattr("marquee_ui.STATUS_FILE", tmp_path / ".status.json")
    monkeypatch.setattr("marquee_ui.LAST_SEEN_FILE", tmp_path / ".last_seen.json")
    monkeypatch.setattr(MarqueeApp, "poll_live_streams_from_api", lambda self: {})
    monkeypatch.setattr(MarqueeApp, "daemon_running", lambda self: False)

    exited = {"called": False}
    monkeypatch.setattr(MarqueeApp, "exit", lambda self, *a, **kw: exited.__setitem__("called", True))
    run_calls = []
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: run_calls.append(a) or mock.Mock(returncode=1))

    app = MarqueeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()
        # No daemon to optionally keep running — no dialog, quit immediately.
        assert not isinstance(app.screen, QuitConfirmModal)
        assert exited["called"] is True
        assert any(call[0][:2] == ["pkill", "-x"] and "chatterino" in call[0] for call in run_calls)


@pytest.mark.asyncio
async def test_quit_menu_navigation_and_escape_cancels(tmp_path, monkeypatch):
    streamers_file = tmp_path / "streamers.txt"
    streamers_file.write_text("alpha\n")
    monkeypatch.setattr("marquee_ui.STREAMERS_FILE", streamers_file)
    monkeypatch.setattr("marquee_ui.STATUS_FILE", tmp_path / ".status.json")
    monkeypatch.setattr("marquee_ui.LAST_SEEN_FILE", tmp_path / ".last_seen.json")
    monkeypatch.setattr(MarqueeApp, "poll_live_streams_from_api", lambda self: {})
    monkeypatch.setattr(MarqueeApp, "daemon_running", lambda self: True)

    exited = {"called": False}
    monkeypatch.setattr(MarqueeApp, "exit", lambda self, *a, **kw: exited.__setitem__("called", True))
    stop_calls = {"count": 0}
    monkeypatch.setattr(MarqueeApp, "stop_service", lambda self: stop_calls.__setitem__("count", stop_calls["count"] + 1))
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: mock.Mock(returncode=1))

    app = MarqueeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()
        assert exited["called"] is False  # just opens the menu
        assert isinstance(app.screen, QuitConfirmModal)
        assert app.screen.index == 0  # defaults to "stop"
        # Regression: width:auto on the panel Vertical previously collapsed
        # every child to a 0x0 box (blank popup, no visible text at all).
        for static in app.screen.query(Static):
            assert static.size.width > 0

        await pilot.press("j")  # navigate to "keep"
        assert app.screen.index == 1
        await pilot.press("k")  # navigate back to "stop"
        assert app.screen.index == 0

        await pilot.press("escape")  # cancel — should not exit
        await pilot.pause()
        assert exited["called"] is False
        assert stop_calls["count"] == 0
        assert not isinstance(app.screen, QuitConfirmModal)


@pytest.mark.asyncio
async def test_quit_menu_stop_option_stops_daemon(tmp_path, monkeypatch):
    streamers_file = tmp_path / "streamers.txt"
    streamers_file.write_text("alpha\n")
    monkeypatch.setattr("marquee_ui.STREAMERS_FILE", streamers_file)
    monkeypatch.setattr("marquee_ui.STATUS_FILE", tmp_path / ".status.json")
    monkeypatch.setattr("marquee_ui.LAST_SEEN_FILE", tmp_path / ".last_seen.json")
    monkeypatch.setattr(MarqueeApp, "poll_live_streams_from_api", lambda self: {})
    monkeypatch.setattr(MarqueeApp, "daemon_running", lambda self: True)

    exited = {"called": False}
    monkeypatch.setattr(MarqueeApp, "exit", lambda self, *a, **kw: exited.__setitem__("called", True))
    stop_calls = {"count": 0}
    monkeypatch.setattr(MarqueeApp, "stop_service", lambda self: stop_calls.__setitem__("count", stop_calls["count"] + 1))

    app = MarqueeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()
        await pilot.press("enter")  # default selection is "stop"
        await pilot.pause()
        assert stop_calls["count"] == 1
        assert exited["called"] is True


@pytest.mark.asyncio
async def test_quit_menu_keep_option_leaves_daemon_running(tmp_path, monkeypatch):
    streamers_file = tmp_path / "streamers.txt"
    streamers_file.write_text("alpha\n")
    monkeypatch.setattr("marquee_ui.STREAMERS_FILE", streamers_file)
    monkeypatch.setattr("marquee_ui.STATUS_FILE", tmp_path / ".status.json")
    monkeypatch.setattr("marquee_ui.LAST_SEEN_FILE", tmp_path / ".last_seen.json")
    monkeypatch.setattr(MarqueeApp, "poll_live_streams_from_api", lambda self: {})
    monkeypatch.setattr(MarqueeApp, "daemon_running", lambda self: True)

    exited = {"called": False}
    monkeypatch.setattr(MarqueeApp, "exit", lambda self, *a, **kw: exited.__setitem__("called", True))
    stop_calls = {"count": 0}
    monkeypatch.setattr(MarqueeApp, "stop_service", lambda self: stop_calls.__setitem__("count", stop_calls["count"] + 1))
    run_calls = []
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: run_calls.append(a) or mock.Mock(returncode=1))

    app = MarqueeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()
        await pilot.press("j")  # move to "keep"
        await pilot.press("enter")
        await pilot.pause()
        assert stop_calls["count"] == 0  # daemon left running
        assert exited["called"] is True
        # Daemon (and its stream) stay up, so chatterino — its companion chat
        # window — must be left running too, not killed.
        assert not any(call[0][:2] == ["pkill", "-x"] and "chatterino" in call[0] for call in run_calls)


@pytest.mark.asyncio
async def test_start_service_noop_when_already_running(tmp_path, monkeypatch):
    streamers_file = tmp_path / "streamers.txt"
    streamers_file.write_text("alpha\n")
    monkeypatch.setattr("marquee_ui.STREAMERS_FILE", streamers_file)
    monkeypatch.setattr("marquee_ui.STATUS_FILE", tmp_path / ".status.json")
    monkeypatch.setattr("marquee_ui.LAST_SEEN_FILE", tmp_path / ".last_seen.json")
    monkeypatch.setattr(MarqueeApp, "poll_live_streams_from_api", lambda self: {})
    monkeypatch.setattr(MarqueeApp, "daemon_running", lambda self: True)

    start_calls = {"count": 0}
    monkeypatch.setattr(MarqueeApp, "start_service", lambda self: start_calls.__setitem__("count", start_calls["count"] + 1))

    app = MarqueeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("s")
        assert start_calls["count"] == 0  # already running, should not call start_service again


@pytest.mark.asyncio
async def test_footer_key_highlight_set_and_cleared(tmp_path, monkeypatch):
    from rich.cells import cell_len

    streamers_file = tmp_path / "streamers.txt"
    streamers_file.write_text("alpha\nbeta\n")
    monkeypatch.setattr("marquee_ui.STREAMERS_FILE", streamers_file)
    monkeypatch.setattr("marquee_ui.STATUS_FILE", tmp_path / ".status.json")
    monkeypatch.setattr("marquee_ui.LAST_SEEN_FILE", tmp_path / ".last_seen.json")
    monkeypatch.setattr(MarqueeApp, "poll_live_streams_from_api", lambda self: {})
    monkeypatch.setattr(MarqueeApp, "daemon_running", lambda self: True)

    def footer_highlighted_text(app):
        footer = [l for l in app.query_one("#frame").content.split("\n") if "uit" in l.plain][0]
        from marquee_ui import HIGHLIGHT_STYLE
        spans = [s for s in footer.spans if str(s.style) == HIGHLIGHT_STYLE]
        if not spans:
            return None
        span = spans[0]
        return footer.plain[span.start:span.end].strip()

    app = MarqueeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.last_footer_key is None
        assert footer_highlighted_text(app) is None

        await pilot.press("s")
        assert app.last_footer_key == "s"
        assert footer_highlighted_text(app) == "(S)tart"

        # Any other tracked key re-highlights its own segment.
        await pilot.press("x")
        assert app.last_footer_key == "x"
        assert footer_highlighted_text(app) == "(X)Stop"

        # Arrow/j/k navigation clears it entirely.
        await pilot.press("j")
        assert app.last_footer_key is None
        assert footer_highlighted_text(app) is None

        await pilot.press("i")  # no current stream — action no-ops but key still registers
        assert app.last_footer_key == "i"
        assert footer_highlighted_text(app) == "(I)nfo"

        await pilot.press("up")
        assert app.last_footer_key is None


@pytest.mark.asyncio
async def test_stop_service_kills_mpv_and_chatterino(tmp_path, monkeypatch):
    streamers_file = tmp_path / "streamers.txt"
    streamers_file.write_text("alpha\n")
    monkeypatch.setattr("marquee_ui.STREAMERS_FILE", streamers_file)
    monkeypatch.setattr("marquee_ui.STATUS_FILE", tmp_path / ".status.json")
    monkeypatch.setattr("marquee_ui.LAST_SEEN_FILE", tmp_path / ".last_seen.json")
    monkeypatch.setattr(MarqueeApp, "poll_live_streams_from_api", lambda self: {})
    monkeypatch.setattr(MarqueeApp, "daemon_running", lambda self: True)

    run_calls = []
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: run_calls.append(a) or mock.Mock(returncode=1))

    app = MarqueeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("x")

        commands = [call[0] for call in run_calls]
        assert any("mpv" in c for c in commands)
        assert any(c[:2] == ["pkill", "-x"] and "chatterino" in c for c in commands)


@pytest.mark.asyncio
async def test_typing_state_renders_in_header_box(tmp_path, monkeypatch):
    streamers_file = tmp_path / "streamers.txt"
    streamers_file.write_text("alpha\n")
    monkeypatch.setattr("marquee_ui.STREAMERS_FILE", streamers_file)
    monkeypatch.setattr("marquee_ui.STATUS_FILE", tmp_path / ".status.json")
    monkeypatch.setattr("marquee_ui.LAST_SEEN_FILE", tmp_path / ".last_seen.json")
    monkeypatch.setattr(MarqueeApp, "poll_live_streams_from_api", lambda self: {})

    app = MarqueeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("slash")
        for ch in "gronk":
            await pilot.press(ch)
        frame = app.query_one("#frame")
        assert "Watch streamer: gronk" in frame.content


@pytest.mark.asyncio
async def test_mode_select_state_renders_prompt_in_header_box(tmp_path, monkeypatch):
    streamers_file = tmp_path / "streamers.txt"
    streamers_file.write_text("alpha\n")
    monkeypatch.setattr("marquee_ui.STREAMERS_FILE", streamers_file)
    monkeypatch.setattr("marquee_ui.STATUS_FILE", tmp_path / ".status.json")
    monkeypatch.setattr("marquee_ui.LAST_SEEN_FILE", tmp_path / ".last_seen.json")
    monkeypatch.setattr(MarqueeApp, "poll_live_streams_from_api", lambda self: {})

    app = MarqueeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("slash")
        for ch in "gronk":
            await pilot.press(ch)
        await pilot.press("enter")
        frame = app.query_one("#frame")
        assert "gronk" in frame.content
        assert "Override" in frame.content
        assert "One-Shot" in frame.content


@pytest.mark.asyncio
async def test_oneshot_does_not_relabel_now_watching_box(tmp_path, monkeypatch):
    streamers_file = tmp_path / "streamers.txt"
    streamers_file.write_text("alpha\n")
    monkeypatch.setattr("marquee_ui.STREAMERS_FILE", streamers_file)
    monkeypatch.setattr("marquee_ui.STATUS_FILE", tmp_path / ".status.json")
    monkeypatch.setattr("marquee_ui.LAST_SEEN_FILE", tmp_path / ".last_seen.json")
    monkeypatch.setattr("marquee_ui.SCRIPT_DIR", tmp_path)
    monkeypatch.setattr(MarqueeApp, "poll_live_streams_from_api", lambda self: {})

    fake_process = mock.Mock()
    fake_process.poll.return_value = None
    monkeypatch.setattr("marquee_ui.subprocess.Popen", lambda *a, **kw: fake_process)
    monkeypatch.setattr("marquee_ui.subprocess.run", lambda *a, **kw: mock.Mock(returncode=1))

    app = MarqueeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("slash")
        for ch in "gronk":
            await pilot.press(ch)
        await pilot.press("enter")
        await pilot.press("1")  # One-Shot
        assert app.ad_hoc_mode is None  # must NOT be set to "oneshot"
        frame = app.query_one("#frame")
        # The footer always shows a "(/)Ad-hoc" keybinding hint, so check the
        # NOW WATCHING border label specifically rather than the whole frame.
        assert "(ad-hoc" not in frame.content.plain.lower()
