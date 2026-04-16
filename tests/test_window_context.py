from dataclasses import dataclass

from scripts.window_context import get_window_context, try_get_window_context


@dataclass
class CompletedProcess:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class FakeSubprocess:
    def __init__(self, results: list[CompletedProcess]) -> None:
        self.results = results
        self.calls: list[tuple[str, ...]] = []

    def run(self, args, capture_output: bool, text: bool):  # noqa: ANN001
        self.calls.append(tuple(args))
        if not self.results:
            raise AssertionError("unexpected subprocess.run call")
        return self.results.pop(0)


def test_get_window_context_prefers_focused_window() -> None:
    fake_subprocess = FakeSubprocess(
        [CompletedProcess(returncode=0, stdout="Doc\n10,30\n1440,900\n")]
    )

    context = get_window_context("TextEdit", subprocess_module=fake_subprocess)

    assert context.app_name == "TextEdit"
    assert context.window_title == "Doc"
    assert context.x == 10
    assert context.y == 30
    assert context.width == 1440
    assert context.height == 900
    assert len(fake_subprocess.calls) == 1
    assert 'AXFocusedWindow' in fake_subprocess.calls[0][2]


def test_get_window_context_falls_back_to_main_window() -> None:
    fake_subprocess = FakeSubprocess(
        [
            CompletedProcess(returncode=1, stderr="focused unavailable"),
            CompletedProcess(returncode=0, stdout="Main\n20,40\n1280,800\n"),
        ]
    )

    context = get_window_context("WeChat", subprocess_module=fake_subprocess)

    assert context.window_title == "Main"
    assert len(fake_subprocess.calls) == 2
    assert 'AXFocusedWindow' in fake_subprocess.calls[0][2]
    assert 'AXMainWindow' in fake_subprocess.calls[1][2]


def test_get_window_context_falls_back_to_first_window() -> None:
    fake_subprocess = FakeSubprocess(
        [
            CompletedProcess(returncode=1, stderr="focused unavailable"),
            CompletedProcess(returncode=1, stderr="main unavailable"),
            CompletedProcess(returncode=0, stdout="Chat\n15,25\n900,700\n"),
        ]
    )

    context = get_window_context("WeChat", subprocess_module=fake_subprocess)

    assert context.window_title == "Chat"
    assert len(fake_subprocess.calls) == 3
    assert 'window 1' in fake_subprocess.calls[2][2]


def test_get_window_context_normalizes_empty_title_to_none() -> None:
    fake_subprocess = FakeSubprocess(
        [CompletedProcess(returncode=0, stdout="\n50,60\n700,500\n")]
    )

    context = get_window_context("Preview", subprocess_module=fake_subprocess)

    assert context.window_title is None
    assert context.width == 700
    assert context.height == 500


def test_try_get_window_context_returns_none_when_all_selectors_fail() -> None:
    fake_subprocess = FakeSubprocess(
        [
            CompletedProcess(returncode=1, stderr="focused unavailable"),
            CompletedProcess(returncode=1, stderr="main unavailable"),
            CompletedProcess(returncode=1, stderr="window not found"),
        ]
    )

    context = try_get_window_context("WeChat", subprocess_module=fake_subprocess)

    assert context is None
    assert len(fake_subprocess.calls) == 3
