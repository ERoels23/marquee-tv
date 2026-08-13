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
