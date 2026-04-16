"""桌面动作执行原语。"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

import pyautogui

from scripts.schema import FlowStep, WindowContext
from scripts.window_context import get_window_context as read_window_context


@dataclass(slots=True)
class ActionResult:
    """统一封装动作执行结果。"""

    success: bool
    action: str
    step_id: str | None
    started_at: str
    ended_at: str
    duration_ms: int
    details: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """将执行结果转换为可序列化字典。"""
        return {
            "success": self.success,
            "action": self.action,
            "step_id": self.step_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_ms": self.duration_ms,
            "details": self.details,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }


class WindowContextUnavailableError(RuntimeError):
    """读取窗口信息失败。"""


class WindowRestoreError(RuntimeError):
    """恢复窗口位置或大小失败。"""


class Executor:
    """执行点击、输入、热键、等待以及窗口辅助动作。"""

    def __init__(
        self,
        pyautogui_module: Any = pyautogui,
        subprocess_module: Any = subprocess,
        sleep: Callable[[float], None] = time.sleep,
        clipboard_reader: Callable[[], str] | None = None,
        clipboard_writer: Callable[[str], None] | None = None,
    ) -> None:
        """初始化执行器并注入可替换的外部依赖。"""
        self._pyautogui = pyautogui_module
        self._subprocess = subprocess_module
        self._sleep = sleep
        self._clipboard_reader = clipboard_reader or self._read_clipboard
        self._clipboard_writer = clipboard_writer or self._write_to_clipboard
        self._clipboard_settle_seconds = 0.05
        self._pasted_text_settle_seconds = 1.0
        if hasattr(self._pyautogui, "FAILSAFE"):
            self._pyautogui.FAILSAFE = True

    def run_step(self, step: FlowStep) -> ActionResult:
        """根据步骤动作类型分发到对应执行原语。"""
        try:
            step.validate()
        except ValueError as exc:
            return self._failure(
                action=step.action,
                step_id=step.id,
                error_code="invalid_step",
                error_message=str(exc),
            )

        if step.action == "move":
            return self.move(
                x=step.target.abs_x,
                y=step.target.abs_y,
                pre_delay_ms=step.timing.pre_delay_ms,
                post_delay_ms=step.timing.post_delay_ms,
                step_id=step.id,
            )
        if step.action == "click":
            return self.click(
                x=step.target.abs_x,
                y=step.target.abs_y,
                pre_delay_ms=step.timing.pre_delay_ms,
                post_delay_ms=step.timing.post_delay_ms,
                step_id=step.id,
            )
        if step.action == "double_click":
            return self.double_click(
                x=step.target.abs_x,
                y=step.target.abs_y,
                pre_delay_ms=step.timing.pre_delay_ms,
                post_delay_ms=step.timing.post_delay_ms,
                step_id=step.id,
            )
        if step.action == "right_click":
            return self.right_click(
                x=step.target.abs_x,
                y=step.target.abs_y,
                pre_delay_ms=step.timing.pre_delay_ms,
                post_delay_ms=step.timing.post_delay_ms,
                step_id=step.id,
            )
        if step.action == "long_press":
            return self.long_press(
                x=step.target.abs_x,
                y=step.target.abs_y,
                hold_duration_ms=step.hold_duration_ms or 0,
                pre_delay_ms=step.timing.pre_delay_ms,
                post_delay_ms=step.timing.post_delay_ms,
                step_id=step.id,
            )
        if step.action == "right_long_press":
            return self.right_long_press(
                x=step.target.abs_x,
                y=step.target.abs_y,
                hold_duration_ms=step.hold_duration_ms or 0,
                pre_delay_ms=step.timing.pre_delay_ms,
                post_delay_ms=step.timing.post_delay_ms,
                step_id=step.id,
            )
        if step.action == "scroll":
            target = None
            if step.target is not None:
                target = (step.target.abs_x, step.target.abs_y)
            return self.scroll(
                scroll_x=step.scroll_x or 0,
                scroll_y=step.scroll_y or 0,
                target=target,
                pre_delay_ms=step.timing.pre_delay_ms,
                post_delay_ms=step.timing.post_delay_ms,
                step_id=step.id,
            )
        if step.action == "type_text":
            return self.type_text(
                text=step.text or "",
                pre_delay_ms=step.timing.pre_delay_ms,
                post_delay_ms=step.timing.post_delay_ms,
                step_id=step.id,
            )
        if step.action == "hotkey":
            return self.hotkey(
                keys=step.keys,
                key=step.key,
                pre_delay_ms=step.timing.pre_delay_ms,
                post_delay_ms=step.timing.post_delay_ms,
                step_id=step.id,
            )
        if step.action == "wait":
            return self.wait(
                seconds=step.seconds or 0.0,
                pre_delay_ms=step.timing.pre_delay_ms,
                post_delay_ms=step.timing.post_delay_ms,
                step_id=step.id,
            )
        return self._failure(
            action=step.action,
            step_id=step.id,
            error_code="unsupported_action",
            error_message=f"Unsupported action: {step.action}",
        )

    def move(
        self,
        x: int,
        y: int,
        *,
        pre_delay_ms: int = 0,
        post_delay_ms: int = 0,
        step_id: str | None = None,
    ) -> ActionResult:
        """移动鼠标到指定坐标。"""
        return self._execute(
            action="move",
            step_id=step_id,
            pre_delay_ms=pre_delay_ms,
            post_delay_ms=post_delay_ms,
            callback=lambda: self._move_details(x, y),
        )

    def click(
        self,
        x: int,
        y: int,
        *,
        pre_delay_ms: int = 0,
        post_delay_ms: int = 0,
        step_id: str | None = None,
    ) -> ActionResult:
        """在指定坐标执行单击。"""
        return self._execute(
            action="click",
            step_id=step_id,
            pre_delay_ms=pre_delay_ms,
            post_delay_ms=post_delay_ms,
            callback=lambda: self._click_details(x, y),
        )

    def double_click(
        self,
        x: int,
        y: int,
        *,
        pre_delay_ms: int = 0,
        post_delay_ms: int = 0,
        step_id: str | None = None,
    ) -> ActionResult:
        """在指定坐标执行双击。"""
        return self._execute(
            action="double_click",
            step_id=step_id,
            pre_delay_ms=pre_delay_ms,
            post_delay_ms=post_delay_ms,
            callback=lambda: self._double_click_details(x, y),
        )

    def right_click(
        self,
        x: int,
        y: int,
        *,
        pre_delay_ms: int = 0,
        post_delay_ms: int = 0,
        step_id: str | None = None,
    ) -> ActionResult:
        """在指定坐标执行右键点击。"""
        return self._execute(
            action="right_click",
            step_id=step_id,
            pre_delay_ms=pre_delay_ms,
            post_delay_ms=post_delay_ms,
            callback=lambda: self._right_click_details(x, y),
        )

    def long_press(
        self,
        x: int,
        y: int,
        *,
        hold_duration_ms: int,
        pre_delay_ms: int = 0,
        post_delay_ms: int = 0,
        step_id: str | None = None,
    ) -> ActionResult:
        """在指定坐标执行左键长按。"""
        return self._execute(
            action="long_press",
            step_id=step_id,
            pre_delay_ms=pre_delay_ms,
            post_delay_ms=post_delay_ms,
            callback=lambda: self._long_press_details(
                x,
                y,
                hold_duration_ms=hold_duration_ms,
                button="left",
            ),
        )

    def right_long_press(
        self,
        x: int,
        y: int,
        *,
        hold_duration_ms: int,
        pre_delay_ms: int = 0,
        post_delay_ms: int = 0,
        step_id: str | None = None,
    ) -> ActionResult:
        """在指定坐标执行右键长按。"""
        return self._execute(
            action="right_long_press",
            step_id=step_id,
            pre_delay_ms=pre_delay_ms,
            post_delay_ms=post_delay_ms,
            callback=lambda: self._long_press_details(
                x,
                y,
                hold_duration_ms=hold_duration_ms,
                button="right",
            ),
        )

    def scroll(
        self,
        scroll_x: int = 0,
        scroll_y: int = 0,
        *,
        target: tuple[int, int] | None = None,
        pre_delay_ms: int = 0,
        post_delay_ms: int = 0,
        step_id: str | None = None,
    ) -> ActionResult:
        """执行纵向或横向滚动，可选先移动到目标坐标。"""
        return self._execute(
            action="scroll",
            step_id=step_id,
            pre_delay_ms=pre_delay_ms,
            post_delay_ms=post_delay_ms,
            callback=lambda: self._scroll_details(scroll_x, scroll_y, target),
        )

    def type_text(
        self,
        text: str,
        *,
        pre_delay_ms: int = 0,
        post_delay_ms: int = 0,
        step_id: str | None = None,
    ) -> ActionResult:
        """输入一段文本。"""
        return self._execute(
            action="type_text",
            step_id=step_id,
            pre_delay_ms=pre_delay_ms,
            post_delay_ms=post_delay_ms,
            callback=lambda: self._type_text_details(text),
        )

    def hotkey(
        self,
        *,
        keys: list[str] | None = None,
        key: str | None = None,
        pre_delay_ms: int = 0,
        post_delay_ms: int = 0,
        step_id: str | None = None,
    ) -> ActionResult:
        """执行单键或组合热键。"""
        return self._execute(
            action="hotkey",
            step_id=step_id,
            pre_delay_ms=pre_delay_ms,
            post_delay_ms=post_delay_ms,
            callback=lambda: self._hotkey_details(keys=keys, key=key),
        )

    def wait(
        self,
        seconds: float,
        *,
        pre_delay_ms: int = 0,
        post_delay_ms: int = 0,
        step_id: str | None = None,
    ) -> ActionResult:
        """等待指定秒数。"""
        return self._execute(
            action="wait",
            step_id=step_id,
            pre_delay_ms=pre_delay_ms,
            post_delay_ms=post_delay_ms,
            callback=lambda: self._wait_details(seconds),
        )

    def open_app(self, app_name: str) -> ActionResult:
        """通过 macOS `open -a` 打开应用。"""
        return self._execute(
            action="open_app",
            step_id=None,
            callback=lambda: self._open_app_details(app_name),
        )

    def setup_window(
        self,
        app_name: str,
        *,
        x: int,
        y: int,
        width: int,
        height: int,
        window_title: str | None = None,
    ) -> ActionResult:
        """将窗口恢复到录制时的几何位置与尺寸。"""
        return self._execute(
            action="setup_window",
            step_id=None,
            callback=lambda: self._setup_window_details(
                app_name=app_name,
                x=x,
                y=y,
                width=width,
                height=height,
                window_title=window_title,
            ),
            default_error_code="window_restore_failed",
        )

    def prepare_window(self, window_context: WindowContext) -> ActionResult:
        """根据录制步骤里的窗口上下文激活并恢复目标窗口。"""
        return self._execute(
            action="prepare_window",
            step_id=None,
            callback=lambda: self._prepare_window_details(window_context),
            default_error_code="window_restore_failed",
        )

    def get_window_context(self, app_name: str) -> ActionResult:
        """读取指定应用首个窗口的标题、位置与尺寸。"""
        return self._execute(
            action="get_window_context",
            step_id=None,
            callback=lambda: self._window_context_details(app_name),
            default_error_code="window_info_unavailable",
        )

    def get_window_bounds(self, app_name: str) -> ActionResult:
        """读取指定应用首个窗口的位置与尺寸，并附带窗口标题。"""
        return self._execute(
            action="get_window_bounds",
            step_id=None,
            callback=lambda: self._window_bounds_details(app_name),
            default_error_code="window_info_unavailable",
        )

    def _execute(
        self,
        *,
        action: str,
        step_id: str | None,
        callback: Callable[[], dict[str, Any]],
        pre_delay_ms: int = 0,
        post_delay_ms: int = 0,
        default_error_code: str = "execution_failed",
    ) -> ActionResult:
        """统一包裹执行前后延迟、计时与异常处理。"""
        started_at = self._now_iso()
        start_perf = time.perf_counter()
        try:
            self._apply_delay_ms(pre_delay_ms)
            details = callback()
            self._apply_delay_ms(post_delay_ms)
        except WindowContextUnavailableError as exc:
            return self._failure(
                action=action,
                step_id=step_id,
                error_code="window_info_unavailable",
                error_message=str(exc),
                started_at=started_at,
                start_perf=start_perf,
            )
        except WindowRestoreError as exc:
            return self._failure(
                action=action,
                step_id=step_id,
                error_code="window_restore_failed",
                error_message=str(exc),
                started_at=started_at,
                start_perf=start_perf,
            )
        except Exception as exc:
            return self._failure(
                action=action,
                step_id=step_id,
                error_code=default_error_code,
                error_message=str(exc),
                started_at=started_at,
                start_perf=start_perf,
            )
        return self._success(
            action=action,
            step_id=step_id,
            details=details,
            started_at=started_at,
            start_perf=start_perf,
        )

    def _move_details(self, x: int, y: int) -> dict[str, Any]:
        """执行鼠标移动并返回结果细节。"""
        self._pyautogui.moveTo(x, y, duration=0.3)
        return {"x": x, "y": y}

    def _click_details(self, x: int, y: int) -> dict[str, Any]:
        """执行单击并返回结果细节。"""
        self._pyautogui.click(x, y, duration=0.2)
        return {"x": x, "y": y}

    def _double_click_details(self, x: int, y: int) -> dict[str, Any]:
        """执行双击并返回结果细节。"""
        self._pyautogui.doubleClick(x, y, interval=0.1)
        return {"x": x, "y": y}

    def _right_click_details(self, x: int, y: int) -> dict[str, Any]:
        """执行右键点击并返回结果细节。"""
        self._pyautogui.rightClick(x, y)
        return {"x": x, "y": y}

    def _long_press_details(
        self,
        x: int,
        y: int,
        *,
        hold_duration_ms: int,
        button: str,
    ) -> dict[str, Any]:
        """执行长按并返回按住时长与按钮类型。"""
        self._pyautogui.mouseDown(x, y, button=button)
        self._sleep(hold_duration_ms / 1000)
        self._pyautogui.mouseUp(x, y, button=button)
        return {
            "x": x,
            "y": y,
            "button": button,
            "hold_duration_ms": hold_duration_ms,
        }

    def _scroll_details(
        self,
        scroll_x: int,
        scroll_y: int,
        target: tuple[int, int] | None,
    ) -> dict[str, Any]:
        """执行滚动并返回滚动方向、距离与目标坐标。"""
        if scroll_x == 0 and scroll_y == 0:
            raise ValueError("scroll requires a non-zero scroll_x or scroll_y")
        if target is not None:
            self._pyautogui.moveTo(target[0], target[1], duration=0.3)
        if scroll_y:
            self._pyautogui.scroll(scroll_y)
        if scroll_x:
            if not hasattr(self._pyautogui, "hscroll"):
                raise RuntimeError("horizontal scroll is not supported")
            self._pyautogui.hscroll(scroll_x)
        return {"scroll_x": scroll_x, "scroll_y": scroll_y, "target": target}

    def _type_text_details(self, text: str) -> dict[str, Any]:
        """执行文本输入并返回输入内容。"""
        details: dict[str, Any] = {
            "text": text,
            "method": "paste",
            "clipboard_restored": False,
        }
        original_clipboard: str | None = None
        can_restore_clipboard = False

        try:
            original_clipboard = self._clipboard_reader()
            can_restore_clipboard = True
        except Exception as exc:  # noqa: BLE001
            details["clipboard_restore_error"] = (
                f"failed to read original clipboard: {exc}"
            )

        self._clipboard_writer(text)
        self._sleep(self._clipboard_settle_seconds)
        self._pyautogui.hotkey("command", "v")
        self._sleep(self._pasted_text_settle_seconds)

        if can_restore_clipboard and original_clipboard is not None:
            try:
                self._clipboard_writer(original_clipboard)
                details["clipboard_restored"] = True
            except Exception as exc:  # noqa: BLE001
                details["clipboard_restore_error"] = (
                    f"failed to restore clipboard: {exc}"
                )

        return details

    def _hotkey_details(
        self,
        *,
        keys: list[str] | None,
        key: str | None,
    ) -> dict[str, Any]:
        """执行单键或组合热键并返回实际按键序列。"""
        if keys:
            if len(keys) == 1:
                self._pyautogui.press(keys[0])
            else:
                self._pyautogui.hotkey(*keys)
            return {"keys": keys}
        if key:
            self._pyautogui.press(key)
            return {"keys": [key]}
        raise ValueError("hotkey requires key or keys")

    def _wait_details(self, seconds: float) -> dict[str, Any]:
        """执行等待并返回等待秒数。"""
        self._sleep(seconds)
        return {"seconds": seconds}

    def _open_app_details(self, app_name: str) -> dict[str, Any]:
        """调用系统命令打开应用并返回应用名。"""
        result = self._subprocess.run(
            ["open", "-a", app_name],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"failed to open {app_name}")
        return {"app_name": app_name}

    def _setup_window_details(
        self,
        app_name: str,
        x: int,
        y: int,
        width: int,
        height: int,
        window_title: str | None,
    ) -> dict[str, Any]:
        """通过 AppleScript 设置窗口位置与大小。"""
        window_context = WindowContext(
            app_name=app_name,
            window_title=window_title,
            x=x,
            y=y,
            width=width,
            height=height,
        )
        window_context.validate()
        self._open_app_details(app_name)
        current_context = self._read_window_context_or_raise(app_name)
        escaped_name = _escape_applescript_string(app_name)
        script = f'''
        tell application "System Events"
            tell process "{escaped_name}"
                set position of window 1 to {{{x}, {y}}}
                set size of window 1 to {{{width}, {height}}}
            end tell
        end tell
        '''
        result = self._subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise WindowRestoreError(
                result.stderr.strip() or "failed to restore window geometry"
            )
        details = {
            "app_name": app_name,
            "window_title": current_context.window_title,
            "x": x,
            "y": y,
            "width": width,
            "height": height,
        }
        if window_title is not None:
            details["expected_window_title"] = window_title
            details["window_title_matched"] = current_context.window_title == window_title
        return details

    def _prepare_window_details(self, window_context: WindowContext) -> dict[str, Any]:
        """用录制好的窗口上下文激活并复位窗口。"""
        window_context.validate()
        return self._setup_window_details(
            app_name=window_context.app_name,
            x=window_context.x,
            y=window_context.y,
            width=window_context.width,
            height=window_context.height,
            window_title=window_context.window_title,
        )

    def _window_context_details(self, app_name: str) -> dict[str, Any]:
        """读取窗口标题与几何信息。"""
        return self._read_window_context_or_raise(app_name).to_dict()

    def _window_bounds_details(self, app_name: str) -> dict[str, Any]:
        """通过 AppleScript 查询窗口位置与大小。"""
        return self._window_context_details(app_name)

    def _read_window_context_or_raise(self, app_name: str) -> WindowContext:
        """读取窗口上下文，并把底层异常转换成结构化错误。"""
        try:
            return read_window_context(
                app_name,
                subprocess_module=self._subprocess,
            )
        except RuntimeError as exc:
            raise WindowContextUnavailableError(str(exc)) from exc

    def _apply_delay_ms(self, delay_ms: int) -> None:
        """按毫秒应用执行前后延迟。"""
        if delay_ms > 0:
            self._sleep(delay_ms / 1000)

    def _write_to_clipboard(self, text: str) -> None:
        """通过 ``pbcopy`` 写入剪贴板，供 ASCII 文本粘贴。"""
        result = self._subprocess.run(
            ["pbcopy"],
            input=text,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "failed to write clipboard")

    def _read_clipboard(self) -> str:
        """通过 ``pbpaste`` 读取当前剪贴板文本内容。"""
        result = self._subprocess.run(
            ["pbpaste"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "failed to read clipboard")
        return result.stdout

    @staticmethod
    def _now_iso() -> str:
        """生成当前 UTC 时间的 ISO 字符串。"""
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def _success(
        self,
        *,
        action: str,
        step_id: str | None,
        details: dict[str, Any],
        started_at: str,
        start_perf: float,
    ) -> ActionResult:
        """构造成功的动作执行结果。"""
        ended_at = self._now_iso()
        duration_ms = int((time.perf_counter() - start_perf) * 1000)
        return ActionResult(
            success=True,
            action=action,
            step_id=step_id,
            started_at=started_at,
            ended_at=ended_at,
            duration_ms=duration_ms,
            details=details,
        )

    def _failure(
        self,
        *,
        action: str,
        step_id: str | None,
        error_code: str,
        error_message: str,
        started_at: str | None = None,
        start_perf: float | None = None,
    ) -> ActionResult:
        """构造失败的动作执行结果。"""
        ended_at = self._now_iso()
        duration_ms = 0
        if start_perf is not None:
            duration_ms = int((time.perf_counter() - start_perf) * 1000)
        return ActionResult(
            success=False,
            action=action,
            step_id=step_id,
            started_at=started_at or ended_at,
            ended_at=ended_at,
            duration_ms=duration_ms,
            error_code=error_code,
            error_message=error_message,
        )


def _escape_applescript_string(value: str) -> str:
    """对 AppleScript 字符串字面量做最小转义。"""
    return value.replace("\\", "\\\\").replace('"', '\\"')
