from rich.cells import cell_len

from marquee_render import (
    HeaderData, RowData, render_header, render_row_collapsed,
    render_row_expanded_detail, header_border_label,
)


def test_render_header_idle():
    header = HeaderData(active=False)
    lines = render_header(header, 40)
    assert lines[0] == "No stream active".ljust(40)
    assert lines[1] == " " * 40
    assert lines[2] == " " * 40


def test_render_header_live_alignment():
    header = HeaderData(
        active=True, name="Jerma", is_live=True, viewers=12400,
        game="Just Chatting", started_at="2026-08-13T10:00:00Z",
        title="doing bit stuff",
    )
    lines = render_header(header, 40)
    assert len(lines) == 3
    assert all(cell_len(l) == 40 for l in lines)
    assert lines[0].startswith("\U0001F7E2 Jerma")
    assert "12k viewers" in lines[0]
    assert lines[1].startswith("Just Chatting")
    assert "live" in lines[1]


def test_render_header_title_truncates_hard():
    header = HeaderData(active=True, name="X", title="a" * 100)
    lines = render_header(header, 20)
    assert cell_len(lines[2]) == 20
    assert lines[2].endswith("-")  # truncate_text's dash marker


def test_header_border_label_normal():
    assert header_border_label(None) == "NOW WATCHING"


def test_header_border_label_ad_hoc():
    assert header_border_label("override") == "NOW WATCHING (ad-hoc · override)"


def test_render_row_collapsed_live():
    row = RowData(name="Jerma", is_live=True, viewers=12400, game="Just Chatting")
    line = render_row_collapsed(row, 50)
    assert cell_len(line) == 50
    assert line.startswith("\U0001F7E2 Jerma")
    assert "12k" in line
    assert "Just Chatting" in line


def test_render_row_collapsed_offline_no_viewers_or_game():
    row = RowData(name="Northernlion", is_live=False)
    line = render_row_collapsed(row, 50)
    assert cell_len(line) == 50
    assert line.startswith("\U0001F534 Northernlion")
    assert "None" not in line


def test_render_row_expanded_detail_live():
    row = RowData(
        name="Jerma", is_live=True, title="doing bit stuff",
        started_at="2026-08-13T10:00:00Z",
    )
    detail = render_row_expanded_detail(row, 60)
    assert cell_len(detail) == 60
    assert '"doing bit stuff"' in detail
    assert "live" in detail


def test_render_row_expanded_detail_offline_unknown():
    row = RowData(name="Bob", is_live=False, last_seen=None)
    detail = render_row_expanded_detail(row, 60)
    assert "last live: unknown" in detail


def test_render_row_expanded_detail_offline_with_last_seen():
    row = RowData(name="Bob", is_live=False, last_seen="2026-08-10T12:00:00Z")
    detail = render_row_expanded_detail(row, 60)
    assert "last live:" in detail


def test_render_header_narrow_width_emoji_alignment():
    """Stress test: emoji alignment when width forces aggressive left truncation.

    With a 25-cell width, long name/game/viewers force the left side to truncate
    significantly. Verify all lines are exactly 25 cells and emoji is preserved.
    """
    header = HeaderData(
        active=True, name="VeryLongStreamerNameHere", is_live=True, viewers=999999,
        game="ReallyLongGameNameTooLong", started_at="2026-08-13T10:00:00Z",
        title="a" * 50,
    )
    lines = render_header(header, 25)
    assert len(lines) == 3
    assert all(cell_len(l) == 25 for l in lines)
    # Line 1 should have emoji at the very start, right before the (truncated) name
    assert lines[0].startswith("\U0001F7E2 ")
