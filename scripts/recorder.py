"""录制会话编排逻辑。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from scripts.capture import CapturedClick, MonitorSnapshot, ScreenCapture
from scripts.focused_text import wait_for_focused_text_change
from scripts.schema import (
    AppContext,
    FlowDefinition,
    FlowStep,
    Locator,
    RetryPolicy,
    Timing,
    Validation,
    WindowContext,
)
from scripts.storage import Storage


@dataclass(slots=True)
class RecordingResult:
    """一次录制会话的最终结果。"""

    session_id: str
    session_dir: Path
    flow_path: Path
    step_count: int
    flow: FlowDefinition


@dataclass(slots=True)
class _TextBuffer:
    """连续文本输入的暂存区。"""

    chunks: list[str]
    last_timestamp: float
    window_context: WindowContext | None = None
    focused_text_before: str | None = None
    last_observed_focused_text: str | None = None
    composition_pending: bool = False


@dataclass(slots=True)
class _PendingMousePress:
    """鼠标按下后，等待松开时再定性的临时状态。"""

    button: str
    x: int
    y: int
    pressed_at: float
    snapshot: MonitorSnapshot


class RecordingError(RuntimeError):
    """录制中断或失败时抛出的错误。"""


class Recorder:
    """采集用户输入事件并构建流程步骤。"""

    def __init__(
        self,
        *,
        storage: Storage,
        capture: ScreenCapture,
        session_dir: str | Path,
        flow_name: str,
        session_id: str,
        foreground_app: str | None = None,
        foreground_app_getter: Callable[[], str | None] | None = None,
        foreground_app_refresher: Callable[[], str | None] | None = None,
        window_context_getter: Callable[[str], WindowContext | None] | None = None,
        focused_text_getter: Callable[[str], str | None] | None = None,
        controller_app_names: set[str] | None = None,
        stop_key_name: str = "escape",
        before_click_capture: Callable[[], None] | None = None,
        after_click_capture: Callable[[], None] | None = None,
        created_at: str | None = None,
        keyboard_listener_factory: Callable[..., Any] | None = None,
        mouse_listener_factory: Callable[..., Any] | None = None,
        time_source: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        wait_threshold_seconds: float = 0.8,
        double_click_interval_seconds: float = 0.35,
        double_click_distance_px: int = 4,
        long_press_threshold_seconds: float = 0.5,
        long_press_distance_px: int = 8,
    ) -> None:
        """初始化录制依赖、监听器与聚合规则。"""
        self._storage = storage
        self._capture = capture
        self._session_dir = Path(session_dir)
        self._flow_name = flow_name
        self._session_id = session_id
        self._foreground_app = foreground_app
        self._foreground_app_getter = foreground_app_getter or (lambda: None)
        self._foreground_app_refresher = foreground_app_refresher
        self._window_context_getter = window_context_getter
        self._focused_text_getter = focused_text_getter
        self._controller_app_names = {name for name in (controller_app_names or set()) if name}
        self._stop_key_name = stop_key_name
        self._before_click_capture = before_click_capture
        self._after_click_capture = after_click_capture
        self._created_at = created_at or _utc_now_iso()
        self._keyboard_listener_factory = (
            keyboard_listener_factory or _default_keyboard_listener_factory
        )
        self._mouse_listener_factory = (
            mouse_listener_factory or _default_mouse_listener_factory
        )
        self._time_source = time_source
        self._sleep = sleep
        self._wait_threshold_seconds = wait_threshold_seconds
        self._double_click_interval_seconds = double_click_interval_seconds
        self._double_click_distance_px = double_click_distance_px
        self._long_press_threshold_seconds = long_press_threshold_seconds
        self._long_press_distance_px = long_press_distance_px
        self._ime_commit_delay_seconds = 0.05
        self._ime_commit_poll_timeout_seconds = 0.35
        self._ime_commit_poll_interval_seconds = 0.05

        self._steps: list[FlowStep] = []
        self._text_buffer: _TextBuffer | None = None
        self._pending_mouse_press: _PendingMousePress | None = None
        self._active_modifiers: set[str] = set()
        self._last_committed_at: float | None = None
        self._last_click_meta: dict[str, Any] | None = None
        self._last_window_context: WindowContext | None = None
        self._stable_window_context: WindowContext | None = None
        self._pending_text_commit_key: str | None = None
        self._pending_text_commit_released_at: float | None = None
        self._target_app = foreground_app
        self._stop_requested = False
        self._started = False
        self._keyboard_listener: Any = None
        self._mouse_listener: Any = None
        self._fatal_error: Exception | None = None
        self._window_context_position_tolerance_px = 80
        self._window_context_size_tolerance_px = 80
        self._window_context_area_shrink_ratio = 0.7

    @property
    def stop_requested(self) -> bool:
        """返回是否已经收到停止请求。"""
        return self._stop_requested

    @property
    def target_app(self) -> str | None:
        """返回本次录制锁定的目标 app。"""
        return self._target_app

    @property
    def step_count(self) -> int:
        """返回当前已生成的步骤数量。"""
        return len(self._steps)

    def start(self) -> None:
        """启动录制监听。"""
        if self._started:
            return
        self._keyboard_listener = self._keyboard_listener_factory(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self._mouse_listener = self._mouse_listener_factory(
            on_click=self._on_click,
            on_scroll=self._on_scroll,
        )
        self._keyboard_listener.start()
        self._mouse_listener.start()
        self._started = True

    def stop(self) -> None:
        """请求停止录制监听。"""
        self._stop_requested = True

    def request_stop(self) -> None:
        """请求停止录制；供信号处理或外部调用。"""
        self.stop()

    def wait_until_stopped(self) -> None:
        """阻塞等待，直到录制被请求停止或发生致命错误。"""
        while not self._stop_requested and self._fatal_error is None:
            self._sleep(0.05)

    def finalize(self) -> RecordingResult:
        """停止监听、刷新缓冲并落盘 ``flow.json``。"""
        self._stop_listeners()
        if self._fatal_error is not None:
            raise RecordingError(str(self._fatal_error)) from self._fatal_error

        self._resolve_pending_text_commit(fallback=True)
        self._flush_text_buffer()
        self._discard_pending_mouse_press()
        flow = FlowDefinition(
            name=self._flow_name,
            created_at=self._created_at,
            app_context=(
                AppContext(foreground_app=self._foreground_app)
                if self._foreground_app is not None
                else None
            ),
            steps=self._steps,
        )
        flow_path = self._storage.save_flow(self._session_dir, flow)
        return RecordingResult(
            session_id=self._session_id,
            session_dir=self._session_dir,
            flow_path=flow_path,
            step_count=len(self._steps),
            flow=flow,
        )

    def handle_click(
        self,
        button: Any,
        *,
        x: int,
        y: int,
        event_time: float | None = None,
        pressed: bool = True,
    ) -> None:
        """处理点击事件；主要供测试与 listener 回调复用。"""
        if self._fatal_error is not None or self._stop_requested:
            return
        try:
            event_at = event_time or self._time_source()
            if not self._should_record_event("mouse"):
                if not pressed:
                    self._discard_pending_mouse_press()
                return
            self._handle_click_event(
                button=button,
                x=x,
                y=y,
                pressed=pressed,
                event_time=event_at,
            )
        except Exception as exc:  # noqa: BLE001
            self._handle_fatal_error(exc)

    def handle_scroll(
        self,
        *,
        x: int,
        y: int,
        dx: int,
        dy: int,
        event_time: float | None = None,
    ) -> None:
        """处理滚轮事件。"""
        if self._fatal_error is not None or self._stop_requested:
            return
        try:
            if not self._should_record_event("scroll"):
                return
            self._flush_text_buffer()
            event_at = event_time or self._time_source()
            self._insert_wait_if_needed(event_at)
            target = self._build_target_for_point(x, y)
            step = FlowStep(
                id=self._next_step_id(),
                action="scroll",
                target=target,
                scroll_x=dx,
                scroll_y=dy,
                window_context=self._read_window_context(),
            )
            step.validate()
            self._append_step(step, committed_at=event_at)
        except Exception as exc:  # noqa: BLE001
            self._handle_fatal_error(exc)

    def handle_key_press(self, key: Any, *, event_time: float | None = None) -> None:
        """处理键盘按下事件。"""
        if self._fatal_error is not None:
            return
        try:
            key_name = _normalize_key_name(key)
            if key_name == self._stop_key_name:
                self.request_stop()
                return
            if self._stop_requested:
                return
            if not self._should_record_event("keyboard"):
                return

            event_at = event_time or self._time_source()
            modifier = _normalize_modifier(key)
            if modifier is not None:
                self._active_modifiers.add(modifier)
                return

            if self._pending_text_commit_key is not None:
                self._resolve_pending_text_commit(fallback=True)

            char = _extract_printable_char(key)
            if (
                key_name is not None
                and self._should_capture_ime_digit_commit(key_name)
            ):
                self._pending_text_commit_key = key_name
                self._pending_text_commit_released_at = None
                return

            if char is not None and self._should_treat_printable_char_as_text():
                self._start_or_extend_text_buffer(char, event_at)
                return

            if (
                key_name in {"space", "enter"}
                and not self._active_modifiers
                and self._text_buffer is not None
            ):
                self._pending_text_commit_key = key_name
                self._pending_text_commit_released_at = None
                return

            self._flush_text_buffer()
            self._insert_wait_if_needed(event_at)
            if key_name is None:
                return
            if self._active_modifiers:
                self._record_hotkey_step(
                    key_name,
                    event_at,
                    modifiers=self._ordered_modifiers(),
                )
                return
            self._record_hotkey_step(key_name, event_at)
        except Exception as exc:  # noqa: BLE001
            self._handle_fatal_error(exc)

    def handle_key_release(self, key: Any, *, event_time: float | None = None) -> None:
        """处理键盘释放事件。"""
        modifier = _normalize_modifier(key)
        if modifier is None:
            key_name = _normalize_key_name(key)
            if (
                key_name in {"space", "enter", "1", "2", "3", "4", "5", "6", "7", "8", "9"}
                and key_name == self._pending_text_commit_key
                and self._text_buffer is not None
            ):
                event_at = event_time or self._time_source()
                self._pending_text_commit_released_at = event_at
                self._sleep(self._ime_commit_delay_seconds)
                committed = self._sync_text_buffer_from_focused_value(
                    self._text_buffer,
                    event_time=event_at,
                    wait_for_change=True,
                )
                if committed:
                    self._clear_pending_text_commit()
                return
            return
        self._active_modifiers.discard(modifier)

    def _handle_click_event(
        self,
        *,
        button: Any,
        x: int,
        y: int,
        pressed: bool,
        event_time: float,
    ) -> None:
        """按下时缓存素材，松开时再落成点击类步骤。"""
        button_name = _normalize_mouse_button(button)
        if button_name is None:
            return
        if pressed:
            self._begin_mouse_press(button_name=button_name, x=x, y=y, event_time=event_time)
            return
        self._complete_mouse_press(
            button_name=button_name,
            x=x,
            y=y,
            event_time=event_time,
        )

    def _begin_mouse_press(
        self,
        *,
        button_name: str,
        x: int,
        y: int,
        event_time: float,
    ) -> None:
        """记录鼠标按下时的监视器快照，松开时再按最终点击点裁图。"""
        self._flush_text_buffer()
        snapshot = self._capture_monitor_snapshot(x, y)
        self._pending_mouse_press = _PendingMousePress(
            button=button_name,
            x=x,
            y=y,
            pressed_at=event_time,
            snapshot=snapshot,
        )

    def _complete_mouse_press(
        self,
        *,
        button_name: str,
        x: int,
        y: int,
        event_time: float,
    ) -> None:
        """在鼠标松开时把缓存的按下事件归类为最终动作。"""
        pending = self._pending_mouse_press
        if pending is None or pending.button != button_name:
            return
        self._pending_mouse_press = None
        action, hold_duration_ms = self._classify_mouse_action(
            pending,
            x=x,
            y=y,
            event_time=event_time,
        )
        if action == "click" and self._can_merge_double_click(x, y, event_time):
            captured = self._captured_click_from_pending(pending, x, y)
            window_context = self._read_window_context(target_point=(x, y))
            self._merge_last_click_to_double_click(
                captured=captured,
                window_context=window_context,
                event_time=event_time,
            )
            return
        self._insert_wait_if_needed(pending.pressed_at)
        captured = self._captured_click_from_pending(pending, x, y)
        window_context = self._read_window_context(target_point=(x, y))
        step = self._build_click_step(
            step_id=self._next_step_id(),
            action=action,
            captured=captured,
            window_context=window_context,
            hold_duration_ms=hold_duration_ms,
        )
        step.validate()
        self._append_step(step, committed_at=event_time)
        if action == "click":
            self._last_click_meta = {
                "step_index": len(self._steps) - 1,
                "x": x,
                "y": y,
                "timestamp": event_time,
            }
        else:
            self._last_click_meta = None

    def _merge_last_click_to_double_click(
        self,
        *,
        captured: CapturedClick,
        window_context: WindowContext | None,
        event_time: float,
    ) -> None:
        """把上一条 click 合并为 double_click。"""
        if self._last_click_meta is None:
            raise RuntimeError("double click merge requires previous click metadata")
        step = self._steps[self._last_click_meta["step_index"]]
        updated = self._build_click_step(
            step_id=step.id,
            action="double_click",
            captured=captured,
            window_context=window_context,
        )
        updated.validate()
        self._steps[self._last_click_meta["step_index"]] = updated
        self._last_committed_at = event_time
        if updated.window_context is not None:
            self._last_window_context = updated.window_context
            self._stable_window_context = self._copy_window_context(updated.window_context)
        self._last_click_meta = None

    def _capture_monitor_snapshot(self, x: int, y: int) -> MonitorSnapshot:
        """在截图前后协调 HUD 可见性，并缓存整张监视器图。"""
        if self._before_click_capture is not None:
            self._before_click_capture()
        try:
            return self._capture.capture_monitor_snapshot(x, y)
        finally:
            if self._after_click_capture is not None:
                self._after_click_capture()

    def _captured_click_from_pending(
        self,
        pending: _PendingMousePress,
        x: int,
        y: int,
    ) -> CapturedClick:
        """优先使用按下时缓存的整张监视器图，在松开点重新裁切步骤素材。"""
        try:
            return self._capture.capture_click_from_snapshot(pending.snapshot, x, y)
        except ValueError:
            return self._capture_click_direct(x, y)

    def _capture_click_direct(self, x: int, y: int) -> CapturedClick:
        """在快照无法复用时，直接按最终点击点重新抓取一次素材。"""
        if self._before_click_capture is not None:
            self._before_click_capture()
        try:
            return self._capture.capture_click(x, y)
        finally:
            if self._after_click_capture is not None:
                self._after_click_capture()

    def _classify_mouse_action(
        self,
        pending: _PendingMousePress,
        *,
        x: int,
        y: int,
        event_time: float,
    ) -> tuple[str, int | None]:
        """根据按住时长和位移判断最终动作类型。"""
        held_seconds = max(event_time - pending.pressed_at, 0.0)
        hold_duration_ms = max(int(round(held_seconds * 1000)), 0)
        moved_x = abs(x - pending.x)
        moved_y = abs(y - pending.y)
        is_stationary_hold = (
            moved_x <= self._long_press_distance_px
            and moved_y <= self._long_press_distance_px
        )
        if (
            is_stationary_hold
            and held_seconds >= self._long_press_threshold_seconds
        ):
            if pending.button == "left":
                return "long_press", hold_duration_ms
            return "right_long_press", hold_duration_ms
        if pending.button == "left":
            return "click", None
        return "right_click", None

    def _can_merge_double_click(self, x: int, y: int, event_time: float) -> bool:
        """判断当前左键点击是否应与上一条 click 合并。"""
        if self._last_click_meta is None:
            return False
        delta_seconds = event_time - self._last_click_meta["timestamp"]
        if delta_seconds > self._double_click_interval_seconds:
            return False
        x_delta = abs(x - self._last_click_meta["x"])
        y_delta = abs(y - self._last_click_meta["y"])
        return (
            x_delta <= self._double_click_distance_px
            and y_delta <= self._double_click_distance_px
        )

    def _build_click_step(
        self,
        *,
        step_id: str,
        action: str,
        captured: CapturedClick,
        window_context: WindowContext | None,
        hold_duration_ms: int | None = None,
    ) -> FlowStep:
        """根据截图结果构造点击类步骤。"""
        anchor_image, context_image = self._storage.save_step_images(
            self._session_dir,
            step_id,
            anchor_image=captured.anchor_image,
            context_image=captured.context_image,
        )
        return FlowStep(
            id=step_id,
            action=action,
            monitor=captured.monitor,
            target=captured.target,
            locator=Locator(
                anchor_image=anchor_image,
                context_image=context_image,
                search_region=captured.search_region,
                match_threshold=0.92,
            ),
            timing=Timing(pre_delay_ms=0, post_delay_ms=800),
            retry=RetryPolicy(max_attempts=2, fallback_to_relative=True),
            validation=Validation(mode="none"),
            window_context=window_context,
            hold_duration_ms=hold_duration_ms,
        )

    def _build_target_for_point(self, x: int, y: int):
        """尽量为滚动等动作补充目标坐标。"""
        try:
            _monitor, target = self._capture.describe_point(x, y)
        except ValueError:
            return None
        return target

    def _start_or_extend_text_buffer(self, text: str, event_time: float) -> None:
        """聚合连续可打印字符。"""
        if self._text_buffer is None:
            self._insert_wait_if_needed(event_time)
            focused_text_before = self._read_focused_text_value()
            self._text_buffer = _TextBuffer(
                chunks=[text],
                last_timestamp=event_time,
                window_context=self._read_window_context(),
                focused_text_before=focused_text_before,
                last_observed_focused_text=focused_text_before,
            )
        else:
            self._text_buffer.chunks.append(text)
            self._text_buffer.last_timestamp = event_time
        window_context = self._read_window_context()
        if window_context is not None:
            self._text_buffer.window_context = window_context
        self._refresh_text_buffer_composition_state(self._text_buffer)

    def _flush_text_buffer(
        self,
        *,
        resolve_pending_commit: bool = True,
        sync_focused_text: bool = True,
    ) -> None:
        """把缓冲文本落成一个 ``type_text`` 步骤。"""
        if self._text_buffer is None:
            self._clear_pending_text_commit()
            return
        if resolve_pending_commit:
            self._resolve_pending_text_commit(fallback=True)
            if self._text_buffer is None:
                return
        if sync_focused_text:
            self._sync_text_buffer_from_focused_value(
                self._text_buffer,
                event_time=self._text_buffer.last_timestamp,
            )
        resolved_text = "".join(self._text_buffer.chunks)
        step = FlowStep(
            id=self._next_step_id(),
            action="type_text",
            text=resolved_text,
            window_context=self._text_buffer.window_context,
        )
        step.validate()
        self._append_step(step, committed_at=self._text_buffer.last_timestamp)
        self._text_buffer = None
        self._clear_pending_text_commit()

    def _insert_wait_if_needed(self, event_time: float) -> None:
        """在动作之间空闲过长时自动插入 wait 步骤。"""
        if self._last_committed_at is None:
            return
        idle_seconds = event_time - self._last_committed_at
        if idle_seconds <= self._wait_threshold_seconds:
            return
        wait_step = FlowStep(
            id=self._next_step_id(),
            action="wait",
            seconds=round(idle_seconds, 2),
            window_context=self._copy_window_context(self._last_window_context),
        )
        wait_step.validate()
        self._append_step(wait_step, committed_at=event_time)
        self._last_click_meta = None

    def _record_hotkey_step(
        self,
        key_name: str,
        event_time: float,
        *,
        modifiers: list[str] | None = None,
    ) -> None:
        """记录一条 hotkey 步骤。"""
        if modifiers:
            step = FlowStep(
                id=self._next_step_id(),
                action="hotkey",
                keys=[*modifiers, key_name],
                window_context=self._read_window_context(),
            )
        else:
            step = FlowStep(
                id=self._next_step_id(),
                action="hotkey",
                key=key_name,
                window_context=self._read_window_context(),
            )
        step.validate()
        self._append_step(step, committed_at=event_time)

    def _append_step(self, step: FlowStep, *, committed_at: float) -> None:
        """把步骤写入内存列表并刷新时间游标。"""
        self._steps.append(step)
        self._last_committed_at = committed_at
        if step.window_context is not None:
            self._last_window_context = step.window_context
            self._stable_window_context = self._copy_window_context(step.window_context)
        if step.action != "click":
            self._last_click_meta = None

    def _discard_pending_mouse_press(self) -> None:
        """清理尚未等到松开的鼠标按下状态。"""
        self._pending_mouse_press = None

    def _next_step_id(self) -> str:
        """按协议生成零填充 step ID。"""
        return f"step_{len(self._steps) + 1:04d}"

    def _should_record_event(self, event_kind: str) -> bool:
        """根据当前前台 app 与 armed 状态判断是否应记录该事件。"""
        app_name = self._resolve_foreground_app_name()
        if not app_name:
            return False
        if self._target_app is None:
            if self._is_controller_app(app_name):
                return False
            self._target_app = app_name
            self._foreground_app = app_name
            return event_kind != "mouse"
        return app_name == self._target_app

    def _resolve_foreground_app_name(self) -> str | None:
        """优先读取缓存前台 app，需要时再触发一次同步刷新。"""
        app_name = self._foreground_app_getter()
        needs_refresh = (
            app_name is None
            or (self._target_app is None and self._is_controller_app(app_name))
            or (self._target_app is not None and app_name != self._target_app)
        )
        if needs_refresh and self._foreground_app_refresher is not None:
            refreshed = self._foreground_app_refresher()
            if refreshed is not None:
                app_name = refreshed
        return app_name

    def _is_controller_app(self, app_name: str | None) -> bool:
        """判断 app 是否属于录制控制层。"""
        if app_name is None:
            return False
        return app_name in self._controller_app_names

    def _stop_listeners(self) -> None:
        """停止 listener，并尽量等待线程退出。"""
        for listener in (self._keyboard_listener, self._mouse_listener):
            if listener is None:
                continue
            listener.stop()
            join = getattr(listener, "join", None)
            if callable(join):
                join(timeout=1)
        self._keyboard_listener = None
        self._mouse_listener = None
        self._started = False

    def _read_window_context(
        self,
        target_point: tuple[int, int] | None = None,
    ) -> WindowContext | None:
        """尽量读取当前目标 app 的窗口上下文；失败时降级为 ``None``。"""
        if self._window_context_getter is None:
            return None
        app_name = self._target_app or self._foreground_app
        if app_name is None:
            return None
        previous_stable = self._copy_window_context(self._stable_window_context)
        try:
            window_context = self._window_context_getter(app_name)
        except Exception:  # noqa: BLE001
            return self._fallback_window_context_for_point(
                app_name,
                target_point=target_point,
                stable_context=previous_stable,
            )
        stabilized = self._stabilize_window_context(window_context, app_name=app_name)
        if target_point is not None:
            x, y = target_point
            if self._window_context_contains_point(stabilized, x, y):
                return self._copy_window_context(stabilized)
            fallback = self._fallback_window_context_for_point(
                app_name,
                target_point=target_point,
                stable_context=previous_stable,
            )
            if fallback is not None:
                self._stable_window_context = self._copy_window_context(fallback)
            return fallback
        return self._copy_window_context(stabilized)

    def _fallback_window_context(self, app_name: str) -> WindowContext | None:
        """在实时读取失败时尽量沿用上一条稳定窗口上下文。"""
        if self._stable_window_context is None:
            return None
        if self._stable_window_context.app_name != app_name:
            return None
        return self._copy_window_context(self._stable_window_context)

    def _fallback_window_context_for_point(
        self,
        app_name: str,
        *,
        target_point: tuple[int, int] | None,
        stable_context: WindowContext | None = None,
    ) -> WindowContext | None:
        """在读取不可信时，仅回退到包含目标点的稳定窗口上下文。"""
        fallback = stable_context or self._fallback_window_context(app_name)
        if fallback is None:
            return None
        if fallback.app_name != app_name:
            return None
        if target_point is None:
            return self._copy_window_context(fallback)
        x, y = target_point
        if not self._window_context_contains_point(fallback, x, y):
            return None
        return self._copy_window_context(fallback)

    def _stabilize_window_context(
        self,
        window_context: WindowContext | None,
        *,
        app_name: str,
    ) -> WindowContext | None:
        """过滤同一应用里明显异常的临时窗口几何跳变。"""
        if window_context is None:
            return self._fallback_window_context(app_name)

        current = self._copy_window_context(window_context)
        if current is None:
            return self._fallback_window_context(app_name)

        previous = self._stable_window_context
        if previous is None or previous.app_name != current.app_name:
            self._stable_window_context = self._copy_window_context(current)
            return current

        if self._should_reuse_stable_window_context(previous, current):
            return self._copy_window_context(previous)

        if (
            not current.window_title
            and previous.window_title
            and self._has_similar_window_geometry(previous, current)
        ):
            current.window_title = previous.window_title

        self._stable_window_context = self._copy_window_context(current)
        return current

    def _should_reuse_stable_window_context(
        self,
        previous: WindowContext,
        current: WindowContext,
    ) -> bool:
        """判断当前窗口是否像同一 app 的临时小窗，应沿用旧值。"""
        if previous.app_name != current.app_name:
            return False
        if not self._is_nearby_origin(previous, current):
            return False
        return self._window_area(current) < (
            self._window_area(previous) * self._window_context_area_shrink_ratio
        )

    def _is_nearby_origin(self, previous: WindowContext, current: WindowContext) -> bool:
        """判断两个窗口左上角是否足够接近。"""
        return (
            abs(previous.x - current.x) <= self._window_context_position_tolerance_px
            and abs(previous.y - current.y) <= self._window_context_position_tolerance_px
        )

    def _has_similar_window_geometry(
        self,
        previous: WindowContext,
        current: WindowContext,
    ) -> bool:
        """判断两次读取的窗口几何是否基本一致。"""
        return (
            self._is_nearby_origin(previous, current)
            and abs(previous.width - current.width) <= self._window_context_size_tolerance_px
            and abs(previous.height - current.height) <= self._window_context_size_tolerance_px
        )

    @staticmethod
    def _window_context_contains_point(
        window_context: WindowContext | None,
        x: int,
        y: int,
    ) -> bool:
        """判断点击点是否位于窗口几何范围内。"""
        if window_context is None:
            return False
        return (
            window_context.x <= x <= window_context.x + window_context.width
            and window_context.y <= y <= window_context.y + window_context.height
        )

    @staticmethod
    def _window_area(window_context: WindowContext) -> int:
        """计算窗口面积，用于识别异常缩小。"""
        return window_context.width * window_context.height

    def _read_focused_text_value(self) -> str | None:
        """读取当前聚焦输入控件的文本值；失败时返回 ``None``。"""
        if self._focused_text_getter is None:
            return None
        app_name = self._target_app or self._foreground_app
        if app_name is None:
            return None
        try:
            return self._focused_text_getter(app_name)
        except Exception:  # noqa: BLE001
            return None

    def _wait_for_focused_text_change(self, previous_text: str | None) -> str | None:
        """短时轮询聚焦输入框，等待输入法提交后的文本变化。"""
        if self._focused_text_getter is None:
            return None
        app_name = self._target_app or self._foreground_app
        if app_name is None:
            return None
        return wait_for_focused_text_change(
            app_name,
            previous_text=previous_text,
            focused_text_getter=self._focused_text_getter,
            timeout_seconds=self._ime_commit_poll_timeout_seconds,
            poll_interval_seconds=self._ime_commit_poll_interval_seconds,
            sleep=self._sleep,
        )

    def _read_latest_focused_text(
        self,
        buffer: _TextBuffer,
        *,
        wait_for_change: bool = False,
    ) -> tuple[str | None, bool]:
        """读取最新的聚焦文本，并在需要时短轮询一次变化。"""
        focused_text_after = None
        observed_change = False
        if wait_for_change:
            focused_text_after = self._wait_for_focused_text_change(
                buffer.last_observed_focused_text
            )
            observed_change = focused_text_after is not None
        if focused_text_after is None:
            focused_text_after = self._read_focused_text_value()
        if focused_text_after is not None:
            buffer.last_observed_focused_text = focused_text_after
        return focused_text_after, observed_change

    def _resolve_focused_text_delta(
        self,
        buffer: _TextBuffer,
        *,
        wait_for_change: bool = False,
    ) -> tuple[str | None, bool]:
        """用聚焦输入框的最终值覆盖输入法过程键序。"""
        if buffer.focused_text_before is None:
            return None, False
        focused_text_after, observed_change = self._read_latest_focused_text(
            buffer,
            wait_for_change=wait_for_change,
        )
        if focused_text_after is None:
            return None, observed_change
        return _string_delta(buffer.focused_text_before, focused_text_after), observed_change

    def _refresh_text_buffer_composition_state(self, buffer: _TextBuffer) -> None:
        """根据当前 focused text 更新是否处于输入法待提交状态。"""
        current_text = "".join(buffer.chunks)
        committed_text, _observed_change = self._resolve_focused_text_delta(buffer)
        if committed_text in {None, ""}:
            buffer.composition_pending = True
            return
        if committed_text != current_text:
            buffer.composition_pending = True
            buffer.chunks = [committed_text]
            return
        if not buffer.composition_pending:
            buffer.composition_pending = False

    def _sync_text_buffer_from_focused_value(
        self,
        buffer: _TextBuffer,
        *,
        event_time: float,
        wait_for_change: bool = False,
    ) -> bool:
        """尝试把当前输入缓冲同步成控件里的最终文本。"""
        current_text = "".join(buffer.chunks)
        committed_text, _observed_change = self._resolve_focused_text_delta(
            buffer,
            wait_for_change=wait_for_change,
        )
        if committed_text in {None, ""}:
            return False
        buffer.chunks = [committed_text]
        buffer.last_timestamp = event_time
        latest_window_context = self._read_window_context()
        if latest_window_context is not None:
            buffer.window_context = latest_window_context
        if committed_text != current_text:
            buffer.composition_pending = False
            return True
        return False

    def _should_capture_ime_digit_commit(self, key_name: str) -> bool:
        """把数字键视作可能的候选确认；失败时会回退成普通数字输入。"""
        return (
            key_name in {"1", "2", "3", "4", "5", "6", "7", "8", "9"}
            and not self._active_modifiers
            and self._text_buffer is not None
        )

    def _should_treat_printable_char_as_text(self) -> bool:
        """判断当前可打印字符是否应归入文本输入而非 hotkey。"""
        return not self._active_modifiers or self._active_modifiers == {"shift"}

    def _resolve_pending_text_commit(self, *, fallback: bool) -> None:
        """在 flush 边界前决定确认键是输入法选词还是普通按键。"""
        if self._pending_text_commit_key is None:
            return
        buffer = self._text_buffer
        if buffer is None:
            self._clear_pending_text_commit()
            return

        event_at = self._pending_text_commit_released_at or buffer.last_timestamp
        committed = self._sync_text_buffer_from_focused_value(
            buffer,
            event_time=event_at,
        )
        if committed:
            self._clear_pending_text_commit()
            return
        if not fallback:
            return

        key_name = self._pending_text_commit_key
        self._clear_pending_text_commit()
        if key_name == "enter":
            self._flush_text_buffer(
                resolve_pending_commit=False,
                sync_focused_text=False,
            )
            self._insert_wait_if_needed(event_at)
            self._record_hotkey_step(key_name, event_at)
            return

        buffer.chunks.append(" " if key_name == "space" else key_name)
        buffer.last_timestamp = event_at
        latest_window_context = self._read_window_context()
        if latest_window_context is not None:
            buffer.window_context = latest_window_context
        buffer.composition_pending = False

    def _clear_pending_text_commit(self) -> None:
        """清理尚未定性的候选确认键状态。"""
        self._pending_text_commit_key = None
        self._pending_text_commit_released_at = None

    @staticmethod
    def _copy_window_context(window_context: WindowContext | None) -> WindowContext | None:
        """复制窗口上下文，避免后续修改污染已录制步骤。"""
        if window_context is None:
            return None
        return WindowContext(
            app_name=window_context.app_name,
            window_title=window_context.window_title,
            x=window_context.x,
            y=window_context.y,
            width=window_context.width,
            height=window_context.height,
        )

    def _handle_fatal_error(self, exc: Exception) -> None:
        """记录致命错误并触发停止。"""
        self._fatal_error = exc
        self._stop_requested = True

    def _on_click(self, x: int, y: int, button: Any, pressed: bool) -> None:
        """鼠标 listener 回调。"""
        self.handle_click(
            button,
            x=x,
            y=y,
            pressed=pressed,
        )

    def _on_scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        """滚轮 listener 回调。"""
        self.handle_scroll(x=x, y=y, dx=dx, dy=dy)

    def _on_press(self, key: Any) -> None:
        """键盘按下 listener 回调。"""
        self.handle_key_press(key)

    def _on_release(self, key: Any) -> None:
        """键盘释放 listener 回调。"""
        self.handle_key_release(key)

    def _ordered_modifiers(self) -> list[str]:
        """按稳定顺序输出当前按住的修饰键。"""
        order = ["command", "control", "option", "shift"]
        return [item for item in order if item in self._active_modifiers]


def _default_keyboard_listener_factory(**kwargs: Any) -> Any:
    """延迟导入 pynput 键盘 listener。"""
    from pynput import keyboard

    return keyboard.Listener(**kwargs)


def _default_mouse_listener_factory(**kwargs: Any) -> Any:
    """延迟导入 pynput 鼠标 listener。"""
    from pynput import mouse

    return mouse.Listener(**kwargs)


def _normalize_mouse_button(button: Any) -> str | None:
    """把按钮对象或字符串映射为标准动作名。"""
    name = getattr(button, "name", None)
    if name is None and isinstance(button, str):
        name = button
    if name in {"left", "right"}:
        return name
    return None


def _normalize_modifier(key: Any) -> str | None:
    """把不同来源的修饰键名称归一化。"""
    key_name = _raw_key_name(key)
    mapping = {
        "alt": "option",
        "alt_gr": "option",
        "alt_l": "option",
        "alt_r": "option",
        "command": "command",
        "cmd": "command",
        "cmd_l": "command",
        "cmd_r": "command",
        "control": "control",
        "ctrl": "control",
        "ctrl_l": "control",
        "ctrl_r": "control",
        "option": "option",
        "shift": "shift",
        "shift_l": "shift",
        "shift_r": "shift",
    }
    if key_name is None:
        return None
    return mapping.get(key_name)


def _extract_printable_char(key: Any) -> str | None:
    """提取可直接并入 ``type_text`` 的字符。"""
    if isinstance(key, str) and len(key) == 1 and key.isprintable():
        return key
    char = getattr(key, "char", None)
    if isinstance(char, str) and len(char) == 1 and char.isprintable():
        return char
    return None


def _normalize_key_name(key: Any) -> str | None:
    """提取 hotkey 使用的标准 key 名称。"""
    if isinstance(key, str):
        return key if len(key) > 1 else key.lower()
    char = getattr(key, "char", None)
    if isinstance(char, str) and len(char) == 1:
        return char.lower()
    raw_name = _raw_key_name(key)
    if raw_name is None:
        return None
    if raw_name == "esc":
        return "escape"
    return raw_name


def _raw_key_name(key: Any) -> str | None:
    """从不同输入对象中提取未归一化的 key 名称。"""
    if isinstance(key, str):
        return key.lower()
    name = getattr(key, "name", None)
    if isinstance(name, str):
        return name.lower()
    rendered = str(key)
    if rendered.startswith("Key."):
        return rendered.split(".", maxsplit=1)[1].lower()
    return None


def _utc_now_iso() -> str:
    """生成当前 UTC ISO 时间戳。"""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _string_delta(before: str, after: str) -> str | None:
    """提取输入框值在一次输入后的新增或替换片段。"""
    if before == after:
        return None

    prefix = 0
    max_prefix = min(len(before), len(after))
    while prefix < max_prefix and before[prefix] == after[prefix]:
        prefix += 1

    suffix = 0
    max_suffix = min(len(before) - prefix, len(after) - prefix)
    while suffix < max_suffix and before[-(suffix + 1)] == after[-(suffix + 1)]:
        suffix += 1

    end_index = len(after) - suffix if suffix > 0 else len(after)
    return after[prefix:end_index]
