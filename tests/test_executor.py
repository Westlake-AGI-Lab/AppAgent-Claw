from dataclasses import dataclass

from scripts.executor import Executor
from scripts.schema import (
    FlowStep,
    Locator,
    MonitorInfo,
    RetryPolicy,
    SearchRegion,
    Target,
    Timing,
    Validation,
    WindowContext,
)


class FakePyAutoGUI:
    def __init__(self) -> None:
        self.FAILSAFE = False
        self.calls: list[tuple] = []

    def moveTo(self, x: int, y: int, duration: float = 0.0) -> None:
        self.calls.append(("moveTo", x, y, duration))

    def click(self, x: int, y: int, duration: float = 0.0) -> None:
        self.calls.append(("click", x, y, duration))

    def doubleClick(self, x: int, y: int, interval: float = 0.0) -> None:
        self.calls.append(("doubleClick", x, y, interval))

    def rightClick(self, x: int, y: int) -> None:
        self.calls.append(("rightClick", x, y))

    def mouseDown(self, x: int, y: int, button: str = "left") -> None:
        self.calls.append(("mouseDown", x, y, button))

    def mouseUp(self, x: int, y: int, button: str = "left") -> None:
        self.calls.append(("mouseUp", x, y, button))

    def scroll(self, amount: int) -> None:
        self.calls.append(("scroll", amount))

    def hscroll(self, amount: int) -> None:
        self.calls.append(("hscroll", amount))

    def write(self, text: str, interval: float = 0.0) -> None:
        self.calls.append(("write", text, interval))

    def press(self, key: str) -> None:
        self.calls.append(("press", key))

    def hotkey(self, *keys: str) -> None:
        self.calls.append(("hotkey", *keys))


@dataclass
class CompletedProcess:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class FakeSubprocess:
    def __init__(self, results: list[CompletedProcess] | None = None) -> None:
        self.results = results or []
        self.calls: list[dict[str, object]] = []

    def run(self, args, **kwargs):  # noqa: ANN001
        self.calls.append({"args": tuple(args), **kwargs})
        if self.results:
            return self.results.pop(0)
        return CompletedProcess(returncode=0)


def build_click_step() -> FlowStep:
    return FlowStep(
        id="step_0001",
        action="click",
        monitor=MonitorInfo(id=1, left=0, top=0, width=1440, height=900),
        target=Target(abs_x=100, abs_y=200, rel_x=0.1, rel_y=0.2),
        locator=Locator(
            anchor_image="assets/step_0001/anchor.png",
            context_image="assets/step_0001/context.png",
            search_region=SearchRegion(left=10, top=20, width=30, height=40),
        ),
        timing=Timing(pre_delay_ms=100, post_delay_ms=200),
        retry=RetryPolicy(max_attempts=2),
        validation=Validation(mode="none"),
    )


def build_long_press_step(*, action: str = "long_press") -> FlowStep:
    step = build_click_step()
    step.action = action
    step.hold_duration_ms = 750
    return step


def test_executor_dispatches_click_and_applies_timing() -> None:
    fake_gui = FakePyAutoGUI()
    fake_sleep_calls: list[float] = []
    executor = Executor(
        pyautogui_module=fake_gui,
        sleep=fake_sleep_calls.append,
    )

    result = executor.run_step(build_click_step())

    assert result.success is True
    assert fake_gui.FAILSAFE is True
    assert ("click", 100, 200, 0.2) in fake_gui.calls
    assert fake_sleep_calls == [0.1, 0.2]


def test_executor_dispatches_long_press_and_applies_hold_duration() -> None:
    fake_gui = FakePyAutoGUI()
    fake_sleep_calls: list[float] = []
    executor = Executor(
        pyautogui_module=fake_gui,
        sleep=fake_sleep_calls.append,
    )

    result = executor.run_step(build_long_press_step())

    assert result.success is True
    assert ("mouseDown", 100, 200, "left") in fake_gui.calls
    assert ("mouseUp", 100, 200, "left") in fake_gui.calls
    assert result.details["hold_duration_ms"] == 750
    assert fake_sleep_calls == [0.1, 0.75, 0.2]


def test_executor_dispatches_right_long_press() -> None:
    fake_gui = FakePyAutoGUI()
    fake_sleep_calls: list[float] = []
    executor = Executor(
        pyautogui_module=fake_gui,
        sleep=fake_sleep_calls.append,
    )

    result = executor.run_step(build_long_press_step(action="right_long_press"))

    assert result.success is True
    assert ("mouseDown", 100, 200, "right") in fake_gui.calls
    assert ("mouseUp", 100, 200, "right") in fake_gui.calls
    assert result.details["button"] == "right"
    assert fake_sleep_calls == [0.1, 0.75, 0.2]


def test_executor_returns_structured_failure_for_invalid_step() -> None:
    executor = Executor(pyautogui_module=FakePyAutoGUI(), sleep=lambda _: None)
    step = FlowStep(id="step_0001", action="unknown")

    result = executor.run_step(step)

    assert result.success is False
    assert result.error_code == "invalid_step"


def test_executor_supports_scroll_and_hotkey() -> None:
    fake_gui = FakePyAutoGUI()
    executor = Executor(pyautogui_module=fake_gui, sleep=lambda _: None)

    scroll_result = executor.scroll(scroll_x=5, scroll_y=-10, target=(50, 60))
    hotkey_result = executor.hotkey(keys=["command", "k"])

    assert scroll_result.success is True
    assert hotkey_result.success is True
    assert fake_gui.calls[0] == ("moveTo", 50, 60, 0.3)
    assert ("scroll", -10) in fake_gui.calls
    assert ("hscroll", 5) in fake_gui.calls
    assert ("hotkey", "command", "k") in fake_gui.calls


def test_executor_pastes_ascii_text_via_clipboard() -> None:
    fake_gui = FakePyAutoGUI()
    clipboard_reads = ["original clipboard"]
    clipboard_writes: list[str] = []
    sleep_calls: list[float] = []
    executor = Executor(
        pyautogui_module=fake_gui,
        sleep=sleep_calls.append,
        clipboard_reader=lambda: clipboard_reads.pop(0),
        clipboard_writer=clipboard_writes.append,
    )

    result = executor.type_text("hello123")

    assert result.success is True
    assert result.details == {
        "text": "hello123",
        "method": "paste",
        "clipboard_restored": True,
    }
    assert clipboard_writes == ["hello123", "original clipboard"]
    assert ("hotkey", "command", "v") in fake_gui.calls
    assert all(call[0] != "write" for call in fake_gui.calls)
    assert sleep_calls == [0.05, 1.0]


def test_executor_pastes_non_ascii_text_via_clipboard() -> None:
    fake_gui = FakePyAutoGUI()
    clipboard_reads = ["初始剪贴板"]
    clipboard_writes: list[str] = []
    sleep_calls: list[float] = []
    executor = Executor(
        pyautogui_module=fake_gui,
        sleep=sleep_calls.append,
        clipboard_reader=lambda: clipboard_reads.pop(0),
        clipboard_writer=clipboard_writes.append,
    )

    result = executor.type_text("周杰伦")

    assert result.success is True
    assert result.details == {
        "text": "周杰伦",
        "method": "paste",
        "clipboard_restored": True,
    }
    assert clipboard_writes == ["周杰伦", "初始剪贴板"]
    assert ("hotkey", "command", "v") in fake_gui.calls
    assert all(call[0] != "write" for call in fake_gui.calls)
    assert sleep_calls == [0.05, 1.0]


def test_executor_reports_clipboard_restore_failure_without_failing_step() -> None:
    fake_gui = FakePyAutoGUI()
    writes: list[str] = []

    def clipboard_writer(text: str) -> None:
        writes.append(text)
        if text == "original clipboard":
            raise RuntimeError("restore failed")

    executor = Executor(
        pyautogui_module=fake_gui,
        sleep=lambda _: None,
        clipboard_reader=lambda: "original clipboard",
        clipboard_writer=clipboard_writer,
    )

    result = executor.type_text("hello")

    assert result.success is True
    assert result.details["method"] == "paste"
    assert result.details["clipboard_restored"] is False
    assert "restore failed" in result.details["clipboard_restore_error"]
    assert writes == ["hello", "original clipboard"]


def test_executor_open_app_uses_subprocess_result() -> None:
    fake_subprocess = FakeSubprocess(results=[CompletedProcess(returncode=0)])
    executor = Executor(
        pyautogui_module=FakePyAutoGUI(),
        subprocess_module=fake_subprocess,
        sleep=lambda _: None,
    )

    result = executor.open_app("TextEdit")

    assert result.success is True
    assert fake_subprocess.calls == [
        {
            "args": ("open", "-a", "TextEdit"),
            "capture_output": True,
            "text": True,
        }
    ]


def test_executor_parses_window_bounds_without_spaces() -> None:
    fake_subprocess = FakeSubprocess(
        results=[
            CompletedProcess(returncode=0, stdout="\n0,25\n1280,720\n"),
        ]
    )
    executor = Executor(
        pyautogui_module=FakePyAutoGUI(),
        subprocess_module=fake_subprocess,
        sleep=lambda _: None,
    )

    result = executor.get_window_bounds("TextEdit")

    assert result.success is True
    assert result.details["x"] == 0
    assert result.details["y"] == 25
    assert result.details["width"] == 1280
    assert result.details["height"] == 720
    assert result.details.get("window_title") is None


def test_executor_reads_window_context_with_title() -> None:
    fake_subprocess = FakeSubprocess(
        results=[
            CompletedProcess(returncode=0, stdout="Untitled\n10,30\n1440,900\n"),
        ]
    )
    executor = Executor(
        pyautogui_module=FakePyAutoGUI(),
        subprocess_module=fake_subprocess,
        sleep=lambda _: None,
    )

    result = executor.get_window_context("TextEdit")

    assert result.success is True
    assert result.details == {
        "app_name": "TextEdit",
        "window_title": "Untitled",
        "x": 10,
        "y": 30,
        "width": 1440,
        "height": 900,
    }


def test_executor_setup_window_uses_recorded_geometry() -> None:
    fake_subprocess = FakeSubprocess(
        results=[
            CompletedProcess(returncode=0),
            CompletedProcess(returncode=0, stdout="Main\n10,30\n1440,900\n"),
            CompletedProcess(returncode=0),
        ]
    )
    executor = Executor(
        pyautogui_module=FakePyAutoGUI(),
        subprocess_module=fake_subprocess,
        sleep=lambda _: None,
    )

    result = executor.setup_window(
        "TextEdit",
        x=20,
        y=40,
        width=1200,
        height=800,
        window_title="Main",
    )

    assert result.success is True
    assert fake_subprocess.calls[0]["args"] == ("open", "-a", "TextEdit")
    assert fake_subprocess.calls[1]["args"][0] == "osascript"
    assert fake_subprocess.calls[2]["args"][0] == "osascript"
    assert result.details["x"] == 20
    assert result.details["y"] == 40
    assert result.details["width"] == 1200
    assert result.details["height"] == 800
    assert result.details["window_title"] == "Main"


def test_executor_restores_window_even_when_title_mismatch() -> None:
    fake_subprocess = FakeSubprocess(
        results=[
            CompletedProcess(returncode=0),
            CompletedProcess(returncode=0, stdout="Other\n10,30\n1440,900\n"),
            CompletedProcess(returncode=0),
        ]
    )
    executor = Executor(
        pyautogui_module=FakePyAutoGUI(),
        subprocess_module=fake_subprocess,
        sleep=lambda _: None,
    )

    result = executor.setup_window(
        "TextEdit",
        x=20,
        y=40,
        width=1200,
        height=800,
        window_title="Main",
    )

    assert result.success is True
    assert result.details["window_title"] == "Other"
    assert result.details["expected_window_title"] == "Main"
    assert result.details["window_title_matched"] is False


def test_executor_prepare_window_uses_window_context() -> None:
    fake_subprocess = FakeSubprocess(
        results=[
            CompletedProcess(returncode=0),
            CompletedProcess(returncode=0, stdout="Doc\n50,60\n900,700\n"),
            CompletedProcess(returncode=0),
        ]
    )
    executor = Executor(
        pyautogui_module=FakePyAutoGUI(),
        subprocess_module=fake_subprocess,
        sleep=lambda _: None,
    )

    result = executor.prepare_window(
        WindowContext(
            app_name="TextEdit",
            window_title="Doc",
            x=100,
            y=120,
            width=1000,
            height=750,
        )
    )

    assert result.success is True
    assert result.details["app_name"] == "TextEdit"
    assert result.details["expected_window_title"] == "Doc"
    assert result.details["window_title_matched"] is True
