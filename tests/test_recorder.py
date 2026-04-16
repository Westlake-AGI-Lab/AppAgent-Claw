from pathlib import Path

from PIL import Image

from scripts.capture import CapturedClick, MonitorSnapshot
from scripts.recorder import Recorder, RecordingError
from scripts.schema import MonitorInfo, SearchRegion, Target, WindowContext
from scripts.storage import Storage


class ForegroundContext:
    def __init__(self, current_app: str | None) -> None:
        self.current_app = current_app
        self.refresh_calls = 0

    def get(self) -> str | None:
        return self.current_app

    def refresh(self) -> str | None:
        self.refresh_calls += 1
        return self.current_app


class FakeCapture:
    def __init__(self) -> None:
        self.snapshot_calls: list[tuple[int, int]] = []
        self.click_from_snapshot_calls: list[tuple[int, int]] = []
        self.click_calls: list[tuple[int, int]] = []
        self.describe_calls: list[tuple[int, int]] = []
        self.raise_on_click = False

    def capture_monitor_snapshot(self, x: int, y: int) -> MonitorSnapshot:
        if self.raise_on_click:
            raise RuntimeError("capture failed")
        self.snapshot_calls.append((x, y))
        return MonitorSnapshot(
            monitor=MonitorInfo(id=1, left=0, top=0, width=1440, height=900),
            monitor_image=Image.new("RGB", (1440, 900), color="white"),
        )

    def capture_click_from_snapshot(
        self,
        snapshot: MonitorSnapshot,
        x: int,
        y: int,
    ) -> CapturedClick:
        self.click_from_snapshot_calls.append((x, y))
        return self._captured_click(x, y)

    def capture_click(self, x: int, y: int) -> CapturedClick:
        if self.raise_on_click:
            raise RuntimeError("capture failed")
        self.click_calls.append((x, y))
        return self._captured_click(x, y)

    def _captured_click(self, x: int, y: int) -> CapturedClick:
        return CapturedClick(
            monitor=MonitorInfo(id=1, left=0, top=0, width=1440, height=900),
            target=Target(abs_x=x, abs_y=y, rel_x=x / 1440, rel_y=y / 900),
            search_region=SearchRegion(left=10, top=20, width=300, height=220),
            anchor_image=Image.new("RGB", (96, 96), color="red"),
            context_image=Image.new("RGB", (240, 160), color="blue"),
        )

    def describe_point(self, x: int, y: int) -> tuple[MonitorInfo, Target]:
        self.describe_calls.append((x, y))
        return (
            MonitorInfo(id=1, left=0, top=0, width=1440, height=900),
            Target(abs_x=x, abs_y=y, rel_x=x / 1440, rel_y=y / 900),
        )


def make_focused_text_getter(values: list[str | None]):
    iterator = iter(values)
    last_value = values[-1] if values else None

    def getter(app_name: str) -> str | None:
        nonlocal last_value
        try:
            last_value = next(iterator)
        except StopIteration:
            pass
        return last_value

    return getter


def build_recorder(
    tmp_path: Path,
    *,
    capture: FakeCapture | None = None,
    foreground_context: ForegroundContext | None = None,
    foreground_app: str | None = None,
    controller_app_names: set[str] | None = None,
    hud_events: list[str] | None = None,
    window_context_getter=None,  # noqa: ANN001
    focused_text_getter=None,  # noqa: ANN001
    sleep=None,  # noqa: ANN001
) -> Recorder:
    storage = Storage(tmp_path / "data")
    session_dir = storage.create_recording_session("phase-two", "sess1")
    context = foreground_context or ForegroundContext(foreground_app or "TextEdit")
    events = hud_events if hud_events is not None else []
    if window_context_getter is None:
        window_context_getter = lambda app_name: WindowContext(  # noqa: E731
            app_name=app_name,
            window_title=f"{app_name} Main",
            x=10,
            y=20,
            width=1280,
            height=720,
        )
    return Recorder(
        storage=storage,
        capture=capture or FakeCapture(),
        session_dir=session_dir,
        flow_name="phase-two",
        session_id="sess1",
        foreground_app=foreground_app,
        foreground_app_getter=context.get,
        foreground_app_refresher=context.refresh,
        window_context_getter=window_context_getter,
        focused_text_getter=focused_text_getter,
        controller_app_names=controller_app_names or {"Python"},
        before_click_capture=lambda: events.append("hide"),
        after_click_capture=lambda: events.append("show"),
        sleep=sleep or (lambda _: None),
    )


def emit_click(
    recorder: Recorder,
    button: str,
    *,
    x: int,
    y: int,
    press_time: float,
    release_time: float | None = None,
    release_x: int | None = None,
    release_y: int | None = None,
) -> None:
    recorder.handle_click(button, x=x, y=y, event_time=press_time, pressed=True)
    recorder.handle_click(
        button,
        x=release_x if release_x is not None else x,
        y=release_y if release_y is not None else y,
        event_time=release_time if release_time is not None else press_time + 0.05,
        pressed=False,
    )


def test_recorder_locks_first_non_controller_app_and_discards_first_click(tmp_path) -> None:
    context = ForegroundContext("Python")
    capture = FakeCapture()
    recorder = build_recorder(
        tmp_path,
        capture=capture,
        foreground_context=context,
        controller_app_names={"Python"},
    )

    emit_click(recorder, "left", x=100, y=120, press_time=1.0)
    assert recorder.target_app is None

    context.current_app = "TextEdit"
    emit_click(recorder, "left", x=100, y=120, press_time=1.2)
    assert recorder.target_app == "TextEdit"
    assert capture.snapshot_calls == []

    emit_click(recorder, "left", x=140, y=160, press_time=1.4)
    result = recorder.finalize()

    assert result.flow.app_context is not None
    assert result.flow.app_context.foreground_app == "TextEdit"
    assert [step.action for step in result.flow.steps] == ["click"]
    assert capture.snapshot_calls == [(140, 160)]
    assert capture.click_from_snapshot_calls == [(140, 160)]
    assert result.flow.steps[0].window_context is not None
    assert result.flow.steps[0].window_context.app_name == "TextEdit"


def test_recorder_ignores_events_from_non_target_apps(tmp_path) -> None:
    context = ForegroundContext("TextEdit")
    recorder = build_recorder(tmp_path, foreground_context=context)

    recorder.handle_key_press("a", event_time=1.0)
    context.current_app = "OpenClaw"
    recorder.handle_key_press("b", event_time=1.1)
    result = recorder.finalize()

    assert result.flow.app_context is not None
    assert result.flow.app_context.foreground_app == "TextEdit"
    assert len(result.flow.steps) == 1
    assert result.flow.steps[0].action == "type_text"
    assert result.flow.steps[0].text == "a"
    assert result.flow.steps[0].window_context is not None


def test_recorder_treats_escape_as_stop_and_does_not_record_it(tmp_path) -> None:
    context = ForegroundContext("TextEdit")
    recorder = build_recorder(tmp_path, foreground_context=context)

    recorder.handle_key_press("a", event_time=1.0)
    recorder.handle_key_press("escape", event_time=1.1)
    result = recorder.finalize()

    assert recorder.stop_requested is True
    assert len(result.flow.steps) == 1
    assert result.flow.steps[0].action == "type_text"
    assert result.flow.steps[0].text == "a"
    assert result.flow.steps[0].window_context is not None


def test_recorder_hides_and_restores_hud_around_click_capture(tmp_path) -> None:
    context = ForegroundContext("TextEdit")
    capture = FakeCapture()
    hud_events: list[str] = []
    recorder = build_recorder(
        tmp_path,
        capture=capture,
        foreground_context=context,
        foreground_app="TextEdit",
        hud_events=hud_events,
    )

    emit_click(recorder, "left", x=100, y=120, press_time=1.0)
    result = recorder.finalize()

    assert [step.action for step in result.flow.steps] == ["click"]
    assert hud_events == ["hide", "show"]
    assert capture.snapshot_calls == [(100, 120)]
    assert capture.click_from_snapshot_calls == [(100, 120)]
    assert result.flow.steps[0].window_context is not None


def test_recorder_crops_click_assets_from_release_position(tmp_path) -> None:
    context = ForegroundContext("TextEdit")
    capture = FakeCapture()
    recorder = build_recorder(
        tmp_path,
        capture=capture,
        foreground_context=context,
        foreground_app="TextEdit",
    )

    emit_click(
        recorder,
        "left",
        x=100,
        y=120,
        release_x=112,
        release_y=136,
        press_time=1.0,
    )
    result = recorder.finalize()

    assert capture.snapshot_calls == [(100, 120)]
    assert capture.click_from_snapshot_calls == [(112, 136)]
    assert result.flow.steps[0].target.abs_x == 112
    assert result.flow.steps[0].target.abs_y == 136


def test_recorder_merges_double_clicks_after_target_lock(tmp_path) -> None:
    context = ForegroundContext("TextEdit")
    capture = FakeCapture()
    recorder = build_recorder(
        tmp_path,
        capture=capture,
        foreground_context=context,
        foreground_app="TextEdit",
    )

    emit_click(recorder, "left", x=100, y=120, press_time=1.0)
    emit_click(recorder, "left", x=103, y=123, press_time=1.2)
    result = recorder.finalize()

    assert len(result.flow.steps) == 1
    assert result.flow.steps[0].action == "double_click"
    assert capture.snapshot_calls == [(100, 120), (103, 123)]
    assert capture.click_from_snapshot_calls == [(100, 120), (103, 123)]
    assert result.flow.steps[0].window_context is not None


def test_recorder_inserts_wait_and_records_scroll_after_app_lock(tmp_path) -> None:
    context = ForegroundContext("TextEdit")
    recorder = build_recorder(tmp_path, foreground_context=context)

    recorder.handle_key_press("h", event_time=1.0)
    recorder.handle_scroll(x=200, y=210, dx=0, dy=-4, event_time=2.3)
    result = recorder.finalize()

    assert [step.action for step in result.flow.steps] == ["type_text", "wait", "scroll"]
    assert result.flow.steps[1].seconds == 1.3
    assert result.flow.steps[0].window_context is not None
    assert result.flow.steps[1].window_context == result.flow.steps[0].window_context
    assert result.flow.steps[2].target is not None
    assert result.flow.steps[2].target.abs_x == 200
    assert result.flow.steps[2].scroll_y == -4
    assert result.flow.steps[2].window_context == result.flow.steps[0].window_context


def test_recorder_raises_when_click_capture_fails(tmp_path) -> None:
    context = ForegroundContext("TextEdit")
    capture = FakeCapture()
    capture.raise_on_click = True
    recorder = build_recorder(
        tmp_path,
        capture=capture,
        foreground_context=context,
        foreground_app="TextEdit",
    )

    recorder.handle_click("left", x=100, y=120, event_time=1.0)

    try:
        recorder.finalize()
    except RecordingError as exc:
        assert "capture failed" in str(exc)
    else:
        raise AssertionError("expected recorder.finalize to raise RecordingError")


def test_recorder_records_left_long_press_after_hold_threshold(tmp_path) -> None:
    context = ForegroundContext("TextEdit")
    capture = FakeCapture()
    recorder = build_recorder(
        tmp_path,
        capture=capture,
        foreground_context=context,
        foreground_app="TextEdit",
    )

    emit_click(
        recorder,
        "left",
        x=100,
        y=120,
        press_time=1.0,
        release_time=1.7,
    )
    result = recorder.finalize()

    assert [step.action for step in result.flow.steps] == ["long_press"]
    assert result.flow.steps[0].hold_duration_ms == 700
    assert capture.snapshot_calls == [(100, 120)]
    assert capture.click_from_snapshot_calls == [(100, 120)]


def test_recorder_records_right_long_press_after_hold_threshold(tmp_path) -> None:
    context = ForegroundContext("TextEdit")
    recorder = build_recorder(
        tmp_path,
        foreground_context=context,
        foreground_app="TextEdit",
    )

    emit_click(
        recorder,
        "right",
        x=140,
        y=160,
        press_time=1.0,
        release_time=1.6,
    )
    result = recorder.finalize()

    assert [step.action for step in result.flow.steps] == ["right_long_press"]
    assert result.flow.steps[0].hold_duration_ms == 600


def test_recorder_long_press_uses_release_time_for_following_wait(tmp_path) -> None:
    context = ForegroundContext("TextEdit")
    recorder = build_recorder(
        tmp_path,
        foreground_context=context,
        foreground_app="TextEdit",
    )

    emit_click(
        recorder,
        "left",
        x=100,
        y=120,
        press_time=1.0,
        release_time=1.8,
    )
    recorder.handle_key_press("h", event_time=2.9)
    result = recorder.finalize()

    assert [step.action for step in result.flow.steps] == ["long_press", "wait", "type_text"]
    assert result.flow.steps[1].seconds == 1.1


def test_recorder_does_not_promote_moved_hold_to_long_press(tmp_path) -> None:
    context = ForegroundContext("TextEdit")
    recorder = build_recorder(
        tmp_path,
        foreground_context=context,
        foreground_app="TextEdit",
    )

    emit_click(
        recorder,
        "left",
        x=100,
        y=120,
        press_time=1.0,
        release_time=1.7,
        release_x=120,
        release_y=150,
    )
    result = recorder.finalize()

    assert [step.action for step in result.flow.steps] == ["click"]
    assert result.flow.steps[0].hold_duration_ms is None


def test_recorder_keeps_recording_when_window_context_is_unavailable(tmp_path) -> None:
    context = ForegroundContext("TextEdit")
    recorder = build_recorder(
        tmp_path,
        foreground_context=context,
        window_context_getter=lambda app_name: None,
    )

    recorder.handle_key_press("a", event_time=1.0)
    result = recorder.finalize()

    assert len(result.flow.steps) == 1
    assert result.flow.steps[0].action == "type_text"
    assert result.flow.steps[0].window_context is None


def test_recorder_resolves_ime_text_to_committed_characters(tmp_path) -> None:
    context = ForegroundContext("TextEdit")
    recorder = build_recorder(
        tmp_path,
        foreground_context=context,
        focused_text_getter=make_focused_text_getter([""] * 11 + ["周杰伦"]),
    )

    for char in "zhoujielun":
        recorder.handle_key_press(char, event_time=1.0)
    recorder.handle_key_press("space", event_time=1.1)
    recorder.handle_key_release("space", event_time=1.2)
    result = recorder.finalize()

    assert [step.action for step in result.flow.steps] == ["type_text"]
    assert result.flow.steps[0].text == "周杰伦"


def test_recorder_resolves_ime_text_when_enter_finishes_input(tmp_path) -> None:
    context = ForegroundContext("TextEdit")
    recorder = build_recorder(
        tmp_path,
        foreground_context=context,
        focused_text_getter=make_focused_text_getter([""] * 11 + ["周杰伦"]),
    )

    for char in "zhoujielun":
        recorder.handle_key_press(char, event_time=1.0)
    recorder.handle_key_press("enter", event_time=1.1)
    recorder.handle_key_release("enter", event_time=1.2)
    result = recorder.finalize()

    assert [step.action for step in result.flow.steps] == ["type_text"]
    assert result.flow.steps[0].text == "周杰伦"


def test_recorder_records_chinese_candidate_selected_by_digit_key(tmp_path) -> None:
    context = ForegroundContext("TextEdit")
    recorder = build_recorder(
        tmp_path,
        foreground_context=context,
        focused_text_getter=make_focused_text_getter([""] * 6 + ["你好"]),
    )

    for char in "nihao":
        recorder.handle_key_press(char, event_time=1.0)
    recorder.handle_key_press("1", event_time=1.1)
    recorder.handle_key_release("1", event_time=1.2)
    result = recorder.finalize()

    assert [step.action for step in result.flow.steps] == ["type_text"]
    assert result.flow.steps[0].text == "你好"


def test_recorder_falls_back_to_raw_text_when_focused_text_is_unavailable(tmp_path) -> None:
    context = ForegroundContext("TextEdit")
    recorder = build_recorder(
        tmp_path,
        foreground_context=context,
        focused_text_getter=lambda app_name: None,
    )

    for char in "zhoujielun":
        recorder.handle_key_press(char, event_time=1.0)
    recorder.handle_key_press("space", event_time=1.1)
    recorder.handle_key_release("space", event_time=1.2)
    result = recorder.finalize()

    assert [step.action for step in result.flow.steps] == ["type_text"]
    assert result.flow.steps[0].text == "zhoujielun "


def test_recorder_keeps_enter_when_only_previous_space_changed_text(tmp_path) -> None:
    context = ForegroundContext("TextEdit")
    recorder = build_recorder(
        tmp_path,
        foreground_context=context,
        focused_text_getter=make_focused_text_getter([""] * 15 + ["linbo "] * 12),
    )

    for char in "linbo":
        recorder.handle_key_press(char, event_time=1.0)
    recorder.handle_key_press("space", event_time=1.1)
    recorder.handle_key_release("space", event_time=1.2)
    recorder.handle_key_press("enter", event_time=1.3)
    recorder.handle_key_release("enter", event_time=1.4)
    result = recorder.finalize()

    assert [step.action for step in result.flow.steps] == ["type_text", "hotkey"]
    assert result.flow.steps[0].text == "linbo "
    assert result.flow.steps[1].key == "enter"


def test_recorder_keeps_enter_when_focused_text_only_catches_up_to_buffer(tmp_path) -> None:
    context = ForegroundContext("TextEdit")
    recorder = build_recorder(
        tmp_path,
        foreground_context=context,
        focused_text_getter=make_focused_text_getter([""] * 5 + ["hello"] * 10),
    )

    for index, char in enumerate("hello", start=1):
        recorder.handle_key_press(char, event_time=1.0 + (index * 0.1))
    recorder.handle_key_press("enter", event_time=1.7)
    recorder.handle_key_release("enter", event_time=1.8)
    result = recorder.finalize()

    assert [step.action for step in result.flow.steps] == ["type_text", "hotkey"]
    assert result.flow.steps[0].text == "hello"
    assert result.flow.steps[1].key == "enter"


def test_recorder_keeps_shift_printable_characters_inside_type_text(tmp_path) -> None:
    context = ForegroundContext("TextEdit")
    recorder = build_recorder(tmp_path, foreground_context=context)

    recorder.handle_key_press("shift", event_time=1.0)
    recorder.handle_key_press("H", event_time=1.1)
    recorder.handle_key_release("H", event_time=1.15)
    recorder.handle_key_release("shift", event_time=1.2)
    for index, char in enumerate("ello", start=1):
        recorder.handle_key_press(char, event_time=1.2 + (index * 0.1))
    recorder.handle_key_press("space", event_time=1.7)
    recorder.handle_key_release("space", event_time=1.75)
    recorder.handle_key_press("shift", event_time=1.8)
    recorder.handle_key_press("W", event_time=1.9)
    recorder.handle_key_release("W", event_time=1.95)
    recorder.handle_key_release("shift", event_time=2.0)
    for index, char in enumerate("orld!", start=1):
        recorder.handle_key_press(char, event_time=2.0 + (index * 0.1))
    result = recorder.finalize()

    assert [step.action for step in result.flow.steps] == ["type_text"]
    assert result.flow.steps[0].text == "Hello World!"


def test_recorder_keeps_plain_numeric_input_as_text(tmp_path) -> None:
    context = ForegroundContext("TextEdit")
    recorder = build_recorder(tmp_path, foreground_context=context)

    recorder.handle_key_press("1", event_time=1.0)
    recorder.handle_key_press("2", event_time=1.1)
    recorder.handle_key_press("3", event_time=1.2)
    result = recorder.finalize()

    assert [step.action for step in result.flow.steps] == ["type_text"]
    assert result.flow.steps[0].text == "123"


def test_recorder_flush_boundary_prefers_final_committed_text(tmp_path) -> None:
    context = ForegroundContext("TextEdit")
    focused_values = [""] * 20 + ["周杰伦"]
    recorder = build_recorder(
        tmp_path,
        foreground_context=context,
        foreground_app="TextEdit",
        focused_text_getter=make_focused_text_getter(focused_values),
    )

    for char in "zhoujielun":
        recorder.handle_key_press(char, event_time=1.0)
    recorder.handle_key_press("enter", event_time=1.1)
    recorder.handle_key_release("enter", event_time=1.2)
    emit_click(recorder, "left", x=140, y=160, press_time=1.4)
    result = recorder.finalize()

    assert [step.action for step in result.flow.steps] == ["type_text", "click"]
    assert result.flow.steps[0].text == "周杰伦"


def test_recorder_reuses_stable_window_context_for_same_app_transient_small_window(
    tmp_path,
) -> None:
    context = ForegroundContext("WeChat")
    window_contexts = iter(
        [
            WindowContext(
                app_name="WeChat",
                window_title="微信",
                x=122,
                y=120,
                width=909,
                height=830,
            ),
            WindowContext(
                app_name="WeChat",
                window_title=None,
                x=170,
                y=155,
                width=368,
                height=366,
            ),
        ]
    )
    recorder = build_recorder(
        tmp_path,
        foreground_context=context,
        foreground_app="WeChat",
        window_context_getter=lambda app_name: next(window_contexts),
    )

    emit_click(recorder, "left", x=140, y=160, press_time=1.0)
    emit_click(recorder, "right", x=140, y=200, press_time=1.2)
    result = recorder.finalize()

    assert [step.action for step in result.flow.steps] == ["click", "right_click"]
    assert result.flow.steps[0].window_context is not None
    assert result.flow.steps[1].window_context is not None
    assert result.flow.steps[0].window_context.width == 909
    assert result.flow.steps[1].window_context.width == 909
    assert result.flow.steps[1].window_context.height == 830
    assert result.flow.steps[1].window_context.window_title == "微信"


def test_recorder_carries_forward_title_when_geometry_is_stable(tmp_path) -> None:
    context = ForegroundContext("WeChat")
    window_contexts = iter(
        [
            WindowContext(
                app_name="WeChat",
                window_title="微信",
                x=122,
                y=120,
                width=909,
                height=830,
            ),
            WindowContext(
                app_name="WeChat",
                window_title=None,
                x=126,
                y=118,
                width=912,
                height=828,
            ),
        ]
    )
    recorder = build_recorder(
        tmp_path,
        foreground_context=context,
        foreground_app="WeChat",
        window_context_getter=lambda app_name: next(window_contexts),
    )

    emit_click(recorder, "left", x=140, y=160, press_time=1.0)
    emit_click(recorder, "right", x=180, y=200, press_time=1.2)
    result = recorder.finalize()

    assert result.flow.steps[1].window_context is not None
    assert result.flow.steps[1].window_context.window_title == "微信"
    assert result.flow.steps[1].window_context.x == 126
    assert result.flow.steps[1].window_context.y == 118
    assert result.flow.steps[1].window_context.width == 912
    assert result.flow.steps[1].window_context.height == 828


def test_recorder_falls_back_to_previous_window_when_current_window_excludes_click_point(
    tmp_path,
) -> None:
    context = ForegroundContext("WeChat")
    window_contexts = iter(
        [
            WindowContext(
                app_name="WeChat",
                window_title="微信",
                x=108,
                y=76,
                width=932,
                height=942,
            ),
            WindowContext(
                app_name="WeChat",
                window_title="朋友圈",
                x=347,
                y=68,
                width=560,
                height=773,
            ),
        ]
    )
    recorder = build_recorder(
        tmp_path,
        foreground_context=context,
        foreground_app="WeChat",
        window_context_getter=lambda app_name: next(window_contexts),
    )

    emit_click(recorder, "left", x=138, y=528, press_time=1.0)
    emit_click(recorder, "left", x=139, y=352, press_time=1.2)
    result = recorder.finalize()

    assert result.flow.steps[0].window_context is not None
    assert result.flow.steps[1].window_context is not None
    assert result.flow.steps[1].window_context.window_title == "微信"
    assert result.flow.steps[1].window_context.x == 108
    assert result.flow.steps[1].window_context.width == 932


def test_recorder_accepts_real_window_change_for_same_app(tmp_path) -> None:
    context = ForegroundContext("WeChat")
    window_contexts = iter(
        [
            WindowContext(
                app_name="WeChat",
                window_title="微信",
                x=122,
                y=120,
                width=909,
                height=830,
            ),
            WindowContext(
                app_name="WeChat",
                window_title="文件传输助手",
                x=420,
                y=200,
                width=960,
                height=866,
            ),
        ]
    )
    recorder = build_recorder(
        tmp_path,
        foreground_context=context,
        foreground_app="WeChat",
        window_context_getter=lambda app_name: next(window_contexts),
    )

    emit_click(recorder, "left", x=140, y=160, press_time=1.0)
    emit_click(recorder, "right", x=500, y=300, press_time=1.2)
    result = recorder.finalize()

    assert result.flow.steps[1].window_context is not None
    assert result.flow.steps[1].window_context.window_title == "文件传输助手"
    assert result.flow.steps[1].window_context.x == 420
    assert result.flow.steps[1].window_context.width == 960
