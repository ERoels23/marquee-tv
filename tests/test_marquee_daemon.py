from marquee_daemon import parse_control_command


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
