from priority_list import parse_streamers_file, usernames


def test_parse_basic(tmp_path):
    f = tmp_path / "streamers.txt"
    f.write_text("# comment\n\njerma985|Jerma\ncosmonaut_variety_hour\n")
    entries = parse_streamers_file(f)
    assert len(entries) == 2
    assert entries[0].username == "jerma985"
    assert entries[0].nickname == "Jerma"
    assert entries[0].display_name == "Jerma"
    assert entries[1].username == "cosmonaut_variety_hour"
    assert entries[1].nickname is None
    assert entries[1].display_name == "cosmonaut_variety_hour"


def test_parse_lowercases_username(tmp_path):
    f = tmp_path / "streamers.txt"
    f.write_text("NorthernLion|NL\n")
    entries = parse_streamers_file(f)
    assert entries[0].username == "northernlion"


def test_parse_empty_file(tmp_path):
    f = tmp_path / "streamers.txt"
    f.write_text("# only comments\n\n")
    assert parse_streamers_file(f) == []


def test_usernames_helper(tmp_path):
    f = tmp_path / "streamers.txt"
    f.write_text("a|A\nb\n")
    entries = parse_streamers_file(f)
    assert usernames(entries) == ["a", "b"]


def test_parse_whitespace_handling(tmp_path):
    f = tmp_path / "streamers.txt"
    f.write_text("  username  |  nickname  \n")
    entries = parse_streamers_file(f)
    assert entries[0].username == "username"
    assert entries[0].nickname == "nickname"


def test_parse_empty_nickname_becomes_none(tmp_path):
    f = tmp_path / "streamers.txt"
    f.write_text("username|  \n")
    entries = parse_streamers_file(f)
    assert entries[0].username == "username"
    assert entries[0].nickname is None


def test_parse_multiple_pipes(tmp_path):
    f = tmp_path / "streamers.txt"
    f.write_text("username|nick|name|extra\n")
    entries = parse_streamers_file(f)
    assert entries[0].username == "username"
    assert entries[0].nickname == "nick|name|extra"


def test_parse_separator_line(tmp_path):
    f = tmp_path / "streamers.txt"
    f.write_text("alpha\n---\nbeta\n")
    entries = parse_streamers_file(f)
    assert len(entries) == 3
    assert entries[0].is_separator is False
    assert entries[1].is_separator is True
    assert entries[2].is_separator is False


def test_separator_excluded_from_usernames(tmp_path):
    f = tmp_path / "streamers.txt"
    f.write_text("alpha\n---\nbeta\n---\ngamma\n")
    entries = parse_streamers_file(f)
    assert usernames(entries) == ["alpha", "beta", "gamma"]


def test_separator_with_surrounding_whitespace(tmp_path):
    f = tmp_path / "streamers.txt"
    f.write_text("alpha\n  ---  \nbeta\n")
    entries = parse_streamers_file(f)
    assert entries[1].is_separator is True


# ---------------------------------------------------------------------------
# Phase 3: time-based priority rules
# ---------------------------------------------------------------------------

import datetime
import pytest
from priority_list import _parse_hhmm, _in_window


def test_parse_hhmm_valid():
    assert _parse_hhmm("08:00") == datetime.time(8, 0)
    assert _parse_hhmm("23:59") == datetime.time(23, 59)
    assert _parse_hhmm("00:00") == datetime.time(0, 0)


@pytest.mark.parametrize("bad", ["25:00", "08:60", "8:00", "0800", "8am", "", "12:5"])
def test_parse_hhmm_invalid_returns_none(bad):
    assert _parse_hhmm(bad) is None


def test_in_window_same_day():
    s, e = datetime.time(8, 0), datetime.time(20, 0)
    assert _in_window(datetime.time(12, 0), s, e) is True
    assert _in_window(datetime.time(8, 0), s, e) is True
    assert _in_window(datetime.time(20, 0), s, e) is False
    assert _in_window(datetime.time(7, 59), s, e) is False


def test_in_window_wraps_midnight():
    s, e = datetime.time(20, 0), datetime.time(8, 0)
    assert _in_window(datetime.time(23, 0), s, e) is True
    assert _in_window(datetime.time(3, 0), s, e) is True
    assert _in_window(datetime.time(8, 0), s, e) is False
    assert _in_window(datetime.time(12, 0), s, e) is False


from priority_list import StreamerEntry, TimeRule, TimeBlock


def _members(*names):
    return [StreamerEntry(username=n) for n in names]


def test_timeblock_resolve_uses_matching_window():
    block = TimeBlock(
        members=_members("a", "b"),
        rules=[TimeRule(datetime.time(20, 0), datetime.time(8, 0), ["b", "a"])],
    )
    assert [e.username for e in block.resolve(datetime.time(22, 0))] == ["b", "a"]
    assert [e.username for e in block.resolve(datetime.time(12, 0))] == ["a", "b"]


def test_timeblock_resolve_appends_omitted_members_in_default_order():
    block = TimeBlock(
        members=_members("a", "b", "c"),
        rules=[TimeRule(datetime.time(20, 0), datetime.time(8, 0), ["c"])],
    )
    assert [e.username for e in block.resolve(datetime.time(22, 0))] == ["c", "a", "b"]


def test_timeblock_with_warning_always_returns_default_order():
    block = TimeBlock(
        members=_members("a", "b"),
        rules=[TimeRule(datetime.time(20, 0), datetime.time(8, 0), ["b", "a"])],
        warning="overlapping windows",
    )
    assert [e.username for e in block.resolve(datetime.time(22, 0))] == ["a", "b"]


def test_parse_valid_block(tmp_path):
    f = tmp_path / "streamers.txt"
    f.write_text(
        "northernlion|NL\n"
        "@block\n"
        "jerma985|Jerma\n"
        "cosmonaut_variety_hour|Cosmo\n"
        "@when 20:00-08:00: cosmonaut_variety_hour, jerma985\n"
        "@end\n"
        "shaun_vids\n"
    )
    entries = parse_streamers_file(f)
    assert len(entries) == 3
    assert entries[0].username == "northernlion"
    block = entries[1]
    assert isinstance(block, TimeBlock)
    assert block.warning is None
    assert [m.username for m in block.members] == ["jerma985", "cosmonaut_variety_hour"]
    assert block.members[0].nickname == "Jerma"
    assert len(block.rules) == 1
    assert block.rules[0].start == datetime.time(20, 0)
    assert block.rules[0].end == datetime.time(8, 0)
    assert block.rules[0].order == ["cosmonaut_variety_hour", "jerma985"]
    assert entries[2].username == "shaun_vids"


def test_parse_no_block_unchanged(tmp_path):
    f = tmp_path / "streamers.txt"
    f.write_text("a|A\n---\nb\n")
    entries = parse_streamers_file(f)
    assert [type(e).__name__ for e in entries] == ["StreamerEntry", "StreamerEntry", "StreamerEntry"]
    assert usernames(entries) == ["a", "b"]


def test_parse_block_allows_comments_and_blanks_inside(tmp_path):
    f = tmp_path / "streamers.txt"
    f.write_text(
        "@block\n# the pair\na\n\nb\n@when 08:00-20:00: a, b\n@end\n"
    )
    block = parse_streamers_file(f)[0]
    assert [m.username for m in block.members] == ["a", "b"]


def _block(text):
    from priority_list import _parse_block
    return _parse_block(("@block\n" + text + "@end\n").split("\n"), 1)[0]


def test_block_invalid_time_warns_and_keeps_members():
    b = _block("a\nb\n@when 25:00-08:00: a, b\n")
    assert b.warning is not None and "25:00" in b.warning
    assert [m.username for m in b.members] == ["a", "b"]
    assert [e.username for e in b.resolve(datetime.time(2, 0))] == ["a", "b"]


def test_block_zero_length_window_warns():
    b = _block("a\nb\n@when 08:00-08:00: a, b\n")
    assert b.warning is not None


def test_block_overlapping_windows_warn():
    b = _block("a\nb\n@when 08:00-21:00: a, b\n@when 20:00-08:00: b, a\n")
    assert b.warning is not None and "overlap" in b.warning.lower()


def test_block_touching_windows_are_ok():
    b = _block("a\nb\n@when 08:00-20:00: a, b\n@when 20:00-08:00: b, a\n")
    assert b.warning is None


def test_block_unknown_member_name_warns():
    b = _block("a\nb\n@when 08:00-20:00: a, zzz\n")
    assert b.warning is not None and "zzz" in b.warning


def test_block_duplicate_name_in_when_warns():
    b = _block("a\nb\n@when 08:00-20:00: a, a\n")
    assert b.warning is not None


def test_block_no_when_rules_warns():
    b = _block("a\nb\n")
    assert b.warning is not None and "no @when" in b.warning.lower()


def test_block_no_members_warns():
    b = _block("@when 08:00-20:00: a\n")
    assert b.warning is not None


from priority_list import resolve_entries


def test_resolve_entries_flattens_block_by_time(tmp_path):
    f = tmp_path / "streamers.txt"
    f.write_text(
        "top\n@block\na\nb\n@when 20:00-08:00: b, a\n@end\nbottom\n"
    )
    entries = parse_streamers_file(f)
    day = resolve_entries(entries, datetime.time(12, 0))
    assert [e.username for e in day] == ["top", "a", "b", "bottom"]
    night = resolve_entries(entries, datetime.time(23, 0))
    assert [e.username for e in night] == ["top", "b", "a", "bottom"]


def test_resolve_entries_no_block_is_identity(tmp_path):
    f = tmp_path / "streamers.txt"
    f.write_text("a\n---\nb\n")
    entries = parse_streamers_file(f)
    out = resolve_entries(entries, datetime.time(12, 0))
    assert usernames(out) == ["a", "b"]


def test_usernames_still_accepts_flat_list(tmp_path):
    f = tmp_path / "streamers.txt"
    f.write_text("a\nb\n")
    assert usernames(parse_streamers_file(f)) == ["a", "b"]
