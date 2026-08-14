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
