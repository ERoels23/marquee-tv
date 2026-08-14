from rich.cells import cell_len

from marquee_render import (
    HeaderData, RowData, render_header, render_row_collapsed,
    render_row_expanded_detail, header_border_label, STATUS_DOT,
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
    assert lines[0].startswith(f"{STATUS_DOT} Jerma")
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
    row = RowData(
        name="Jerma", is_live=True, viewers=12400, game="Just Chatting",
        started_at="2026-08-13T10:00:00Z",
    )
    line = render_row_collapsed(row, 60)
    assert cell_len(line) == 60
    assert line.startswith(f"{STATUS_DOT} Jerma")
    assert "12k" in line
    assert "Just Chatting" in line
    assert "live" in line  # uptime now shown on the collapsed line, not just detail


def test_render_row_collapsed_offline_no_viewers_or_game():
    row = RowData(name="Northernlion", is_live=False)
    line = render_row_collapsed(row, 50)
    assert cell_len(line) == 50
    assert line.startswith(f"{STATUS_DOT} Northernlion")
    assert "None" not in line


def test_render_row_collapsed_uptime_columns_align_across_rows(monkeypatch):
    # Different digit counts (e.g. "5h01m" vs "12h34m") used to shift where
    # the uptime text started, since the whole "uptime viewers" blob was just
    # right-justified as one unit. Fixed-width columns keep it aligned.
    monkeypatch.setattr("marquee_render.format_uptime", lambda started_at: {
        "a": "5h01m", "b": "12h34m",
    }[started_at])
    row_a = RowData(name="A", is_live=True, viewers=5, game="g", started_at="a")
    row_b = RowData(name="B", is_live=True, viewers=12345, game="g", started_at="b")

    line_a = render_row_collapsed(row_a, 60, highlighted=False)
    line_b = render_row_collapsed(row_b, 60, highlighted=False)

    assert "live 5h01m" in line_a
    assert "live 12h34m" in line_b
    assert line_a.index("live") == line_b.index("live")


def test_render_row_collapsed_category_not_truncated():
    long_game = "A" * 60
    row = RowData(name="X", is_live=True, viewers=1, game=long_game, started_at=None)
    line = render_row_collapsed(row, 120)
    assert long_game in line


def test_render_row_collapsed_category_shifted_right():
    row = RowData(name="X", is_live=True, viewers=1, game="Category", started_at=None)
    line = render_row_collapsed(row, 60)
    assert "    Category" in line  # 4-space gap (shifted 2 cells right of the old 2-space gap)


def test_render_row_collapsed_highlighted_shows_username_in_parens():
    row = RowData(name="Apollo", is_live=False, username="dumbdog")
    line = render_row_collapsed(row, 50, highlighted=True)
    assert "Apollo (dumbdog)" in line


def test_render_row_collapsed_highlighted_omits_parens_when_no_nickname():
    row = RowData(name="dumbdog", is_live=False, username="dumbdog")
    line = render_row_collapsed(row, 50, highlighted=True)
    assert "(dumbdog)" not in line


def test_render_row_collapsed_not_highlighted_omits_username_paren():
    row = RowData(name="Apollo", is_live=False, username="dumbdog")
    line = render_row_collapsed(row, 50, highlighted=False)
    assert "(dumbdog)" not in line


def test_render_row_expanded_detail_live():
    row = RowData(
        name="Jerma", is_live=True, title="doing bit stuff",
        started_at="2026-08-13T10:00:00Z",
    )
    detail = render_row_expanded_detail(row, 60)
    assert cell_len(detail) == 60
    assert "doing bit stuff" in detail
    assert '"' not in detail  # quotes removed — unnecessary width
    # uptime moved up to the collapsed line — no longer duplicated here, and
    # this is also what used to get truncated away by a very long title.
    assert "live" not in detail


def test_render_row_expanded_detail_offline_unknown():
    row = RowData(name="Bob", is_live=False, last_seen=None)
    detail = render_row_expanded_detail(row, 60)
    assert "last live: unknown" in detail


def test_render_row_expanded_detail_offline_with_last_seen():
    row = RowData(name="Bob", is_live=False, last_seen={"at": "2026-08-10T12:00:00Z", "game": None, "title": None})
    detail = render_row_expanded_detail(row, 60)
    assert "last live:" in detail


def test_render_row_expanded_detail_offline_shows_game_and_title_pipe_separated():
    row = RowData(name="Bob", is_live=False, last_seen={
        "at": "2026-08-10T12:00:00Z", "game": "Machine Party", "title": "this is the stream title",
    })
    detail = render_row_expanded_detail(row, 80)
    assert "last live:" in detail
    assert "Machine Party" in detail
    assert "this is the stream title" in detail
    assert "last live:" in detail.split(" | ")[0]
    assert detail.split(" | ")[1].strip() == "Machine Party"
    assert detail.split(" | ")[2].strip() == "this is the stream title"


def test_render_row_expanded_detail_offline_backfilled_title_only_no_game():
    # VOD backfill has no category — only "last live" + title should show.
    row = RowData(name="Bob", is_live=False, last_seen={
        "at": "2026-08-10T12:00:00Z", "game": None, "title": "Some VOD title",
    })
    detail = render_row_expanded_detail(row, 80)
    parts = detail.strip().split(" | ")
    assert len(parts) == 2
    assert "Some VOD title" in parts[1]


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
    # Line 1 should have the status dot at the very start, right before the (truncated) name
    assert lines[0].startswith(f"{STATUS_DOT} ")
