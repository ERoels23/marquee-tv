from marquee_model import ListNavigator, AdHocFlow, AdHocFlowState, AdHocMode, QuitConfirm


def test_navigator_wraps_down():
    nav = ListNavigator(3)
    assert nav.index == 0
    nav.move_down()
    nav.move_down()
    nav.move_down()
    assert nav.index == 0  # wrapped back around


def test_navigator_wraps_up():
    nav = ListNavigator(3)
    nav.move_up()
    assert nav.index == 2  # wrapped to the end


def test_navigator_empty_list_noop():
    nav = ListNavigator(0)
    assert nav.index == -1
    nav.move_down()
    nav.move_up()
    assert nav.index == -1


def test_navigator_set_count_clamps_index():
    nav = ListNavigator(5)
    nav.index = 4
    nav.set_count(2)
    assert nav.index == 1


def test_adhoc_flow_full_sequence():
    flow = AdHocFlow()
    flow.start()
    flow.type_char("j")
    flow.type_char("e")
    flow.type_char("r")
    flow.backspace()
    flow.type_char("r")
    flow.type_char("m")
    flow.type_char("a")
    assert flow.buffer == "jerma"
    assert flow.submit_name() is True
    assert flow.pending_name == "jerma"
    result = flow.choose_mode(AdHocMode.OVERRIDE)
    assert result == ("jerma", AdHocMode.OVERRIDE)


def test_adhoc_flow_empty_submit_rejected():
    flow = AdHocFlow()
    flow.start()
    assert flow.submit_name() is False


def test_adhoc_flow_choose_mode_without_submit_returns_none():
    flow = AdHocFlow()
    flow.start()
    flow.type_char("x")
    assert flow.choose_mode(AdHocMode.TEMPORARY) is None


def test_adhoc_flow_cancel_resets():
    flow = AdHocFlow()
    flow.start()
    flow.type_char("x")
    flow.cancel()
    assert flow.buffer == ""
    assert flow.state == AdHocFlowState.IDLE


def test_quit_confirm_requires_arm_then_confirm():
    qc = QuitConfirm()
    assert qc.confirm() is False  # not armed yet
    qc.request()
    assert qc.confirm() is True
    assert qc.confirm() is False  # disarmed after confirming


def test_quit_confirm_cancel():
    qc = QuitConfirm()
    qc.request()
    qc.cancel()
    assert qc.confirm() is False


def test_navigator_single_item_wraps():
    nav = ListNavigator(1)
    assert nav.index == 0
    nav.move_down()
    assert nav.index == 0
    nav.move_up()
    assert nav.index == 0


def test_navigator_set_count_empty_to_filled():
    nav = ListNavigator(0)
    assert nav.index == -1
    nav.set_count(3)
    assert nav.index == 0


def test_adhoc_flow_type_char_when_not_typing():
    flow = AdHocFlow()
    flow.type_char("x")  # not started yet
    assert flow.buffer == ""
    flow.start()
    flow.type_char("a")
    flow.submit_name()
    flow.type_char("b")  # in MODE_SELECT state
    assert flow.buffer == "a"  # not appended


def test_adhoc_flow_backspace_when_not_typing():
    flow = AdHocFlow()
    flow.start()
    flow.type_char("hello")
    flow.submit_name()
    flow.backspace()  # in MODE_SELECT state
    assert flow.buffer == "hello"  # not removed


def test_adhoc_flow_backspace_empty_buffer():
    flow = AdHocFlow()
    flow.start()
    flow.backspace()  # empty buffer
    assert flow.buffer == ""


def test_adhoc_flow_cancel_idempotent():
    flow = AdHocFlow()
    flow.start()
    flow.type_char("test")
    flow.cancel()
    assert flow.buffer == ""
    assert flow.state == AdHocFlowState.IDLE
    flow.cancel()  # cancel again
    assert flow.buffer == ""
    assert flow.state == AdHocFlowState.IDLE


def test_adhoc_flow_submit_after_cancel():
    flow = AdHocFlow()
    flow.start()
    flow.type_char("first")
    flow.cancel()
    assert flow.state == AdHocFlowState.IDLE
    flow.start()
    flow.type_char("second")
    assert flow.submit_name() is True
    assert flow.pending_name == "second"
