import json
from pathlib import Path

import pytest

from marquee_ui import MarqueeApp


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
