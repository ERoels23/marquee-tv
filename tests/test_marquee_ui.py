import json
from pathlib import Path
from unittest import mock

import pytest

from marquee_ui import MarqueeApp
from marquee_model import AdHocFlowState


@pytest.mark.asyncio
async def test_app_boots_and_shows_idle_header(tmp_path, monkeypatch):
    streamers_file = tmp_path / "streamers.txt"
    streamers_file.write_text("teststreamer|Test\n")
    monkeypatch.setattr("marquee_ui.STREAMERS_FILE", streamers_file)
    monkeypatch.setattr("marquee_ui.STATUS_FILE", tmp_path / ".status.json")
    monkeypatch.setattr("marquee_ui.LAST_SEEN_FILE", tmp_path / ".last_seen.json")
    monkeypatch.setattr(MarqueeApp, "poll_live_streams_from_api", lambda self: {})

    app = MarqueeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        frame = app.query_one("#frame")
        assert "No stream active" in frame.content
        assert "Test" in frame.content  # nickname from streamers.txt shows in the list


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

    app = MarqueeApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.nav.index = 1  # highlight "beta"
        await pilot.press("enter")
        assert control_file.read_text() == "switch:beta"


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
        frame = app.query_one("#frame")
        assert "Playing games" in frame.content
        assert "A cool streamer bio" in frame.content
        await pilot.press("i")
        frame2 = app.query_one("#frame")
        assert app.info_visible is False
        # overlay closed, back to normal view — bio text and overlay label are gone,
        # even though the header legitimately still shows the (truncated) title for
        # the currently-watched stream, so we can't assert "Playing games" is absent.
        assert "A cool streamer bio" not in frame2.content
        assert "Channel bio:" not in frame2.content
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
        assert app.info_visible is False


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
        assert app.info_visible is True
        starting_index = app.nav.index

        await pilot.press("j")  # should be swallowed
        assert app.nav.index == starting_index

        await pilot.press("enter")  # should be swallowed
        assert not control_file.exists()

        await pilot.press("i")  # this should still close it
        assert app.info_visible is False
