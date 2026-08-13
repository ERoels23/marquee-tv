from datetime import datetime, timezone
from ui_format import format_viewers, truncate_text, format_uptime, format_last_seen, build_mpv_title


def test_format_viewers_small():
    assert format_viewers(842) == "842"


def test_format_viewers_thousands():
    assert format_viewers(12400) == "12k"


def test_format_viewers_millions():
    assert format_viewers(1_500_000) == "1.5M"


def test_truncate_text_no_op():
    assert truncate_text("short", 20) == "short"


def test_truncate_text_dash():
    assert truncate_text("a very long title here", 10) == "a very lo-"
    assert len(truncate_text("a very long title here", 10)) == 10


def test_format_uptime():
    now = datetime(2026, 8, 13, 12, 14, 0, tzinfo=timezone.utc)
    assert format_uptime("2026-08-13T10:00:00Z", now=now) == "2h14m"


def test_format_last_seen_minutes():
    now = datetime(2026, 8, 13, 12, 30, 0, tzinfo=timezone.utc)
    assert format_last_seen("2026-08-13T12:00:00Z", now=now) == "30m ago"


def test_format_last_seen_days():
    now = datetime(2026, 8, 13, 12, 0, 0, tzinfo=timezone.utc)
    assert format_last_seen("2026-08-10T12:00:00Z", now=now) == "3d ago"


def test_format_last_seen_unknown():
    assert format_last_seen(None) == "unknown"


def test_build_mpv_title():
    assert build_mpv_title("Jerma", "Just Chatting", "doing bits") == "Jerma ::: Just Chatting ::: doing bits"


def test_truncate_text_handles_wide_glyphs():
    from rich.cells import cell_len
    result = truncate_text("hi 😀😀😀 title", 10)
    assert cell_len(result) == 10
    assert result.endswith("-")


def test_format_last_seen_just_now():
    now = datetime(2026, 8, 13, 12, 0, 30, tzinfo=timezone.utc)
    assert format_last_seen("2026-08-13T12:00:00Z", now=now) == "just now"
