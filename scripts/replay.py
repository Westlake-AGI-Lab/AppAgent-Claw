"""回放入口脚本。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import asdict, is_dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.capture import ScreenCapture
from scripts.executor import Executor
from scripts.focused_text import try_get_focused_text_value
from scripts.resolver import Resolver
from scripts.schema import AppContext, CLICK_ACTIONS, FlowDefinition, FlowStep, WindowContext
from scripts.storage import Storage


DEFAULT_VALIDATION_TIMEOUT_SECONDS = 2.0
VALIDATION_POLL_INTERVAL_SECONDS = 0.2
DEFAULT_TEXT_VALIDATION_TIMEOUT_SECONDS = 2.0
TEXT_VALIDATION_POLL_INTERVAL_SECONDS = 0.2
TEXT_VALIDATION_FALLBACK_WAIT_SECONDS = 1.0
REPLAY_OVERLAY_FINAL_STATUS_SECONDS = 0.6
REPLAY_OVERLAY_MAX_STATUS_LENGTH = 96


class NullReplayOverlay:
    """回放 HUD 的空实现。"""

    def start(self) -> None:
        return None

    def set_status(self, text: str) -> None:
        del text
        return None

    def hide(self) -> None:
        return None

    def show(self) -> None:
        return None

    def close(self) -> None:
        return None


class SwiftReplayOverlay:
    """通过 Swift/AppKit helper 驱动的回放状态浮窗。"""

    def __init__(
        self,
        *,
        title: str = "AppAgent-Claw Replay",
        helper_path: str | Path | None = None,
        swift_binary: str = "swift",
        popen_factory: Callable[..., Any] = subprocess.Popen,
    ) -> None:
        self._title = title
        self._helper_path = (
            Path(helper_path)
            if helper_path is not None
            else Path(__file__).with_name("replay_overlay.swift")
        )
        self._swift_binary = swift_binary
        self._popen_factory = popen_factory
        self._process: Any = None
        self._closed = False

    def start(self) -> None:
        """启动状态浮窗 helper。"""
        if self._process is not None or self._closed:
            return
        if not self._helper_path.exists():
            raise RuntimeError(f"overlay helper not found: {self._helper_path}")
        self._process = self._popen_factory(
            [self._swift_binary, str(self._helper_path), self._title],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )

    def set_status(self, text: str) -> None:
        """更新 HUD 文案。"""
        self._send_command(f"status\t{text}")

    def hide(self) -> None:
        """临时隐藏 HUD，避免污染截图。"""
        self._send_command("hide")

    def show(self) -> None:
        """恢复 HUD 显示。"""
        self._send_command("show")

    def close(self) -> None:
        """关闭状态浮窗。"""
        if self._closed:
            return
        self._send_command("close")
        self._closed = True
        if self._process is not None:
            wait = getattr(self._process, "wait", None)
            if callable(wait):
                try:
                    wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass

    def _send_command(self, command: str) -> None:
        if self._process is None or self._closed:
            return
        stdin = getattr(self._process, "stdin", None)
        if stdin is None:
            return
        try:
            stdin.write(f"{command}\n")
            stdin.flush()
        except (BrokenPipeError, ValueError):
            return


class ReplayRunner:
    """串联窗口准备、定位、执行和验证的回放控制器。"""

    def __init__(
        self,
        *,
        flow: FlowDefinition,
        flow_path: str | Path,
        recording_dir: str | Path,
        run_dir: str | Path,
        storage: Storage,
        executor: Executor,
        resolver: Resolver,
        overlay: Any,
        inputs: dict[str, str] | None = None,
        focused_text_getter: Callable[[str], str | None] | None = None,
        debug: bool = False,
        sleep: Callable[[float], None] = time.sleep,
        time_source: Callable[[], float] = time.monotonic,
    ) -> None:
        self._flow = flow
        self._flow_path = Path(flow_path)
        self._recording_dir = Path(recording_dir)
        self._run_dir = Path(run_dir)
        self._storage = storage
        self._executor = executor
        self._resolver = resolver
        self._overlay = overlay
        self._inputs = inputs or {}
        self._focused_text_getter = focused_text_getter
        self._debug = debug
        self._sleep = sleep
        self._time_source = time_source
        self._prepared_window_context: WindowContext | None = None
        self._overlay_started = False

    def run(self) -> tuple[dict[str, Any], int]:
        """执行整个 flow，并始终写出 ``run.json``。"""
        started_at = _utc_now_iso()
        step_results: list[dict[str, Any]] = []
        payload: dict[str, Any]
        exit_code = 0
        final_overlay_status = "回放结束"

        try:
            self._start_overlay()
            self._validate_inputs()
            for index, step in enumerate(self._flow.steps):
                step_result = self._run_step(index, step)
                step_results.append(step_result)
                if not step_result["success"]:
                    final_overlay_status = self._failure_overlay_status(
                        index=index,
                        step=step,
                    )
                    payload = self._build_payload(
                        status="failed",
                        started_at=started_at,
                        steps=step_results,
                        failed_step_id=step.id,
                        error_code=step_result.get("error_code"),
                        error_message=step_result.get("error_message"),
                    )
                    exit_code = 1
                    break
            else:
                final_overlay_status = self._completed_overlay_status()
                payload = self._build_payload(
                    status="completed",
                    started_at=started_at,
                    steps=step_results,
                )
        except KeyboardInterrupt:
            final_overlay_status = "回放已取消"
            payload = self._build_payload(
                status="cancelled",
                started_at=started_at,
                steps=step_results,
                error_code="interrupted",
                error_message="replay interrupted by user",
            )
            exit_code = 130
        except InvalidReplayInputsError as exc:
            final_overlay_status = "回放输入无效"
            payload = self._build_payload(
                status="error",
                started_at=started_at,
                steps=step_results,
                error_code="invalid_inputs",
                error_message=str(exc),
            )
            exit_code = 1
        except Exception as exc:  # noqa: BLE001
            final_overlay_status = "回放异常"
            payload = self._build_payload(
                status="error",
                started_at=started_at,
                steps=step_results,
                error_code="replay_failed",
                error_message=str(exc),
            )
            exit_code = 1
        finally:
            self._finish_overlay(final_overlay_status)

        self._storage.write_run_json(self._run_dir, payload)
        return payload, exit_code

    def _run_step(self, index: int, step: FlowStep) -> dict[str, Any]:
        """执行单个步骤，并在点击类步骤上处理 resolve / retry / validation。"""
        max_attempts = 1
        if step.action in CLICK_ACTIONS and step.retry is not None:
            max_attempts = step.retry.max_attempts

        step_result = {
            "step_id": step.id,
            "action": step.action,
            "success": False,
            "attempts": [],
        }

        for attempt_index in range(max_attempts):
            attempt_number = attempt_index + 1
            attempt_dir = self._run_dir / "steps" / step.id / f"attempt_{attempt_number}"
            attempt_result = {"attempt": attempt_number}
            self._overlay.set_status(
                self._step_overlay_status(
                    index=index,
                    step=step,
                    attempt_number=attempt_number,
                )
            )

            prepare_result = self._prepare_window_if_needed(
                step,
                force=attempt_number > 1,
            )
            if prepare_result is not None:
                attempt_result["window_preparation"] = _serialize_result(prepare_result)
                if not prepare_result.success:
                    attempt_result["success"] = False
                    step_result["attempts"].append(attempt_result)
                    step_result["error_code"] = (
                        prepare_result.error_code or "window_prepare_failed"
                    )
                    step_result["error_message"] = (
                        prepare_result.error_message or "failed to prepare window"
                    )
                    return step_result

            execution_step = step
            focused_text_before = None
            resolved_text = step.text or ""
            if step.action == "type_text":
                execution_step, text_resolution = self._resolve_type_text_step(step)
                resolved_text = execution_step.text or ""
                attempt_result["text_resolution"] = text_resolution
                focused_text_before = self._read_focused_text(step)
            if step.action in CLICK_ACTIONS:
                resolve_result = self._resolver.resolve(
                    step,
                    debug=self._debug,
                    debug_dir=attempt_dir,
                    save_failure_debug=attempt_number == max_attempts,
                )
                attempt_result["resolve"] = resolve_result.to_dict()
                if not resolve_result.success:
                    attempt_result["success"] = False
                    step_result["attempts"].append(attempt_result)
                    if (
                        attempt_number < max_attempts
                        and resolve_result.error_code == "target_not_found"
                    ):
                        continue
                    step_result["error_code"] = (
                        resolve_result.error_code or "resolve_failed"
                    )
                    step_result["error_message"] = (
                        resolve_result.error_message or "failed to resolve target"
                    )
                    return step_result
                execution_step = _step_with_resolved_target(
                    step,
                    x=resolve_result.x,
                    y=resolve_result.y,
                )

            execute_result = self._executor.run_step(execution_step)
            attempt_result["execute"] = _serialize_result(execute_result)
            if not execute_result.success:
                attempt_result["success"] = False
                step_result["attempts"].append(attempt_result)
                if attempt_number < max_attempts:
                    continue
                step_result["error_code"] = (
                    execute_result.error_code or "execution_failed"
                )
                step_result["error_message"] = (
                    execute_result.error_message or "failed to execute step"
                )
                return step_result

            if step.action == "type_text":
                text_validation_result = self._validate_type_text_step(
                    execution_step,
                    target_text=resolved_text,
                    focused_text_before=focused_text_before,
                )
                attempt_result["text_validation"] = text_validation_result
                if not text_validation_result["success"]:
                    attempt_result["success"] = False
                    step_result["attempts"].append(attempt_result)
                    step_result["error_code"] = (
                        text_validation_result.get("error_code")
                        or "text_validation_failed"
                    )
                    step_result["error_message"] = (
                        text_validation_result.get("error_message")
                        or "typed text did not appear before timeout"
                    )
                    return step_result

            validation_result = self._validate_step(
                index,
                step,
                attempt_dir=attempt_dir,
                save_failure_debug=attempt_number == max_attempts,
            )
            attempt_result["validation"] = validation_result
            if not validation_result["success"]:
                attempt_result["success"] = False
                step_result["attempts"].append(attempt_result)
                if (
                    attempt_number < max_attempts
                    and validation_result.get("error_code") == "validation_failed"
                ):
                    continue
                step_result["error_code"] = (
                    validation_result.get("error_code") or "validation_failed"
                )
                step_result["error_message"] = (
                    validation_result.get("error_message")
                    or "validation did not pass"
                )
                return step_result

            attempt_result["success"] = True
            step_result["attempts"].append(attempt_result)
            step_result["success"] = True
            return step_result

        step_result["error_code"] = "retry_exhausted"
        step_result["error_message"] = "step retries exhausted"
        return step_result

    def _start_overlay(self) -> None:
        """尝试启动回放 HUD；失败时降级为 no-op。"""
        if isinstance(self._overlay, NullReplayOverlay):
            self._overlay_started = False
            return
        try:
            self._overlay.start()
        except Exception:  # noqa: BLE001
            self._overlay = NullReplayOverlay()
            self._overlay_started = False
            return
        self._overlay_started = True
        if self._flow.steps:
            self._overlay.set_status(f"准备回放 {len(self._flow.steps)} 步")
        else:
            self._overlay.set_status("准备回放")

    def _finish_overlay(self, final_status: str) -> None:
        """显示最终状态并关闭 HUD。"""
        if not self._overlay_started:
            return
        self._overlay.set_status(_truncate_overlay_text(final_status))
        self._sleep(REPLAY_OVERLAY_FINAL_STATUS_SECONDS)
        self._overlay.close()

    def _step_overlay_status(
        self,
        *,
        index: int,
        step: FlowStep,
        attempt_number: int,
    ) -> str:
        """构造单步执行时的 HUD 文案。"""
        parts = [f"回放中 {index + 1}/{len(self._flow.steps)}", step.action]
        if attempt_number > 1:
            parts.append(f"第 {attempt_number} 次尝试")
        step_summary = _step_overlay_summary(step)
        if step_summary != step.action:
            parts.append(step_summary)
        return _truncate_overlay_text(" · ".join(parts))

    def _completed_overlay_status(self) -> str:
        """构造回放成功时的 HUD 文案。"""
        return f"回放完成 {len(self._flow.steps)}/{len(self._flow.steps)}"

    def _failure_overlay_status(
        self,
        *,
        index: int,
        step: FlowStep,
    ) -> str:
        """构造回放失败时的 HUD 文案。"""
        parts = [f"回放失败 {index + 1}/{len(self._flow.steps)}", step.action]
        step_summary = _step_overlay_summary(step)
        if step_summary != step.action:
            parts.append(step_summary)
        return _truncate_overlay_text(" · ".join(parts))

    def _validate_inputs(self) -> None:
        """确认运行时 inputs 只覆盖已声明的文本槽位。"""
        declared_ids = {flow_input.id for flow_input in self._flow.inputs}
        unknown_ids = sorted(set(self._inputs) - declared_ids)
        if unknown_ids:
            raise InvalidReplayInputsError(
                "unknown replay input ids: " + ", ".join(unknown_ids)
            )

        invalid_values = [
            key for key, value in self._inputs.items() if not isinstance(value, str)
        ]
        if invalid_values:
            raise InvalidReplayInputsError(
                "replay input values must be strings: " + ", ".join(sorted(invalid_values))
            )

    def _resolve_type_text_step(
        self,
        step: FlowStep,
    ) -> tuple[FlowStep, dict[str, Any]]:
        """解析 ``type_text`` 的最终输入文本。"""
        recorded_text = step.text or ""
        input_id = step.text_policy.input_id if step.text_policy is not None else None
        if (
            step.text_policy is None
            or step.text_policy.mode != "parameterized"
            or input_id is None
        ):
            return (
                step,
                {
                    "mode": "fixed",
                    "input_id": input_id,
                    "recorded_text": recorded_text,
                    "resolved_text": recorded_text,
                    "source": "recorded",
                },
            )

        if input_id in self._inputs:
            resolved_text = self._inputs[input_id]
            source = "input"
        else:
            resolved_text = recorded_text
            source = "recorded_fallback"
        return (
            replace(step, text=resolved_text),
            {
                "mode": "parameterized",
                "input_id": input_id,
                "recorded_text": recorded_text,
                "resolved_text": resolved_text,
                "source": source,
            },
        )

    def _prepare_window_if_needed(
        self,
        step: FlowStep,
        *,
        force: bool = False,
    ) -> Any | None:
        """在窗口上下文变化或重试时恢复窗口位置。"""
        window_context = step.window_context
        if window_context is None:
            return None
        if self._should_skip_window_preparation(step):
            return None
        if not force and self._prepared_window_context == window_context:
            return None
        result = self._executor.prepare_window(window_context)
        if result.success:
            self._prepared_window_context = window_context
        else:
            self._prepared_window_context = None
        return result

    def _should_skip_window_preparation(self, step: FlowStep) -> bool:
        """当录制的窗口上下文明显不包含目标点时，跳过窗口恢复。"""
        if step.action not in CLICK_ACTIONS:
            return False
        if step.window_context is None or step.target is None:
            return False
        return not _window_context_contains_target(step.window_context, step.target)

    def _validate_step(
        self,
        index: int,
        step: FlowStep,
        *,
        attempt_dir: Path,
        save_failure_debug: bool,
    ) -> dict[str, Any]:
        """执行动作后的基础验证。"""
        validation = step.validation
        if validation is None or validation.mode == "none":
            return {
                "success": True,
                "mode": "none",
            }

        try:
            image_path, search_region, threshold = self._validation_target(index, step)
        except ValueError as exc:
            return {
                "success": False,
                "mode": validation.mode,
                "error_code": "validation_target_missing",
                "error_message": str(exc),
            }

        timeout_seconds = (
            validation.timeout_seconds
            if validation.timeout_seconds is not None
            else DEFAULT_VALIDATION_TIMEOUT_SECONDS
        )
        deadline = self._time_source() + max(timeout_seconds, 0.0)
        polls = 0

        while True:
            polls += 1
            match_result = self._resolver.locate_image(
                image_path,
                search_region,
                threshold=threshold,
                debug=False,
                debug_dir=attempt_dir,
                capture_name=f"validation_{validation.mode}",
                match_name=f"validation_{validation.mode}_debug",
                save_on_failure=False,
            )
            success = (
                not match_result.matched
                if validation.mode == "anchor_absent"
                else match_result.matched
            )
            if success:
                if self._debug:
                    match_result = self._resolver.locate_image(
                        image_path,
                        search_region,
                        threshold=threshold,
                        debug=True,
                        debug_dir=attempt_dir,
                        capture_name=f"validation_{validation.mode}",
                        match_name=f"validation_{validation.mode}_debug",
                        save_on_failure=False,
                    )
                return {
                    "success": True,
                    "mode": validation.mode,
                    "polls": polls,
                    "timeout_seconds": timeout_seconds,
                    "match": match_result.to_dict(),
                }

            if self._time_source() >= deadline:
                if self._debug or save_failure_debug:
                    match_result = self._resolver.locate_image(
                        image_path,
                        search_region,
                        threshold=threshold,
                        debug=self._debug,
                        debug_dir=attempt_dir,
                        capture_name=f"validation_{validation.mode}",
                        match_name=f"validation_{validation.mode}_debug",
                        save_on_failure=save_failure_debug,
                    )
                return {
                    "success": False,
                    "mode": validation.mode,
                    "polls": polls,
                    "timeout_seconds": timeout_seconds,
                    "match": match_result.to_dict(),
                    "error_code": "validation_failed",
                    "error_message": f"{validation.mode} did not pass before timeout",
                }

            self._sleep(VALIDATION_POLL_INTERVAL_SECONDS)

    def _validate_type_text_step(
        self,
        step: FlowStep,
        *,
        target_text: str,
        focused_text_before: str | None,
    ) -> dict[str, Any]:
        """在 ``type_text`` 之后确认焦点输入控件已出现目标文本。"""
        if not target_text:
            return {
                "success": False,
                "mode": "focused_text",
                "error_code": "text_validation_failed",
                "error_message": "type_text step is missing text",
            }

        app_name = self._focused_text_app_name(step)
        if app_name is None or self._focused_text_getter is None:
            return self._fallback_text_validation("focused_text_unavailable")

        deadline = self._time_source() + DEFAULT_TEXT_VALIDATION_TIMEOUT_SECONDS
        polls = 0
        saw_focused_text = False
        latest_text: str | None = None

        while True:
            polls += 1
            latest_text = self._read_focused_text(step)
            if latest_text is None:
                if not saw_focused_text:
                    return self._fallback_text_validation(
                        "focused_text_unavailable",
                        polls=polls,
                        before_text=focused_text_before,
                    )
            else:
                saw_focused_text = True
                if _focused_text_contains_target(
                    before_text=focused_text_before,
                    current_text=latest_text,
                    target_text=target_text,
                ):
                    return {
                        "success": True,
                        "mode": "focused_text",
                        "polls": polls,
                        "timeout_seconds": DEFAULT_TEXT_VALIDATION_TIMEOUT_SECONDS,
                        "before_text": focused_text_before,
                        "observed_text": latest_text,
                        "target_text": target_text,
                    }

            if self._time_source() >= deadline:
                return {
                    "success": False,
                    "mode": "focused_text",
                    "polls": polls,
                    "timeout_seconds": DEFAULT_TEXT_VALIDATION_TIMEOUT_SECONDS,
                    "before_text": focused_text_before,
                    "observed_text": latest_text,
                    "target_text": target_text,
                    "error_code": "text_validation_failed",
                    "error_message": "focused text did not include target text before timeout",
                }

            self._sleep(TEXT_VALIDATION_POLL_INTERVAL_SECONDS)

    def _fallback_text_validation(
        self,
        reason: str,
        *,
        polls: int = 0,
        before_text: str | None = None,
    ) -> dict[str, Any]:
        """在读取不到焦点文本时，降级为固定等待。"""
        self._sleep(TEXT_VALIDATION_FALLBACK_WAIT_SECONDS)
        return {
            "success": True,
            "mode": "fallback_wait",
            "polls": polls,
            "wait_seconds": TEXT_VALIDATION_FALLBACK_WAIT_SECONDS,
            "reason": reason,
            "before_text": before_text,
        }

    def _read_focused_text(self, step: FlowStep) -> str | None:
        """读取当前步骤所属应用的焦点输入文本；失败时返回 ``None``。"""
        if self._focused_text_getter is None:
            return None
        app_name = self._focused_text_app_name(step)
        if app_name is None:
            return None
        try:
            return self._focused_text_getter(app_name)
        except Exception:  # noqa: BLE001
            return None

    def _focused_text_app_name(self, step: FlowStep) -> str | None:
        """确定读取焦点文本时应该面向的目标应用。"""
        if step.window_context is not None:
            return step.window_context.app_name
        app_context: AppContext | None = self._flow.app_context
        if app_context is not None:
            return app_context.foreground_app
        return None

    def _validation_target(
        self,
        index: int,
        step: FlowStep,
    ) -> tuple[str, Any, float]:
        """根据 validation.mode 选择后验检查的图片与搜索区域。"""
        if step.validation is None or step.locator is None:
            raise ValueError("validation target requires locator metadata")
        if step.validation.mode == "anchor_absent":
            return (
                step.locator.anchor_image,
                step.locator.search_region,
                step.locator.match_threshold,
            )
        if step.validation.mode == "anchor_present":
            next_click_step = self._next_click_step(index + 1)
            if next_click_step.locator is None:
                raise ValueError("next click step is missing locator metadata")
            return (
                next_click_step.locator.anchor_image,
                next_click_step.locator.search_region,
                next_click_step.locator.match_threshold,
            )
        raise ValueError(f"unsupported validation mode: {step.validation.mode}")

    def _next_click_step(self, start_index: int) -> FlowStep:
        """找到当前步骤之后第一个点击类步骤。"""
        for candidate in self._flow.steps[start_index:]:
            if candidate.action in CLICK_ACTIONS:
                return candidate
        raise ValueError("anchor_present requires a later click step")

    def _build_payload(
        self,
        *,
        status: str,
        started_at: str,
        steps: list[dict[str, Any]],
        failed_step_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        """统一构造最终回放结果。"""
        payload = {
            "status": status,
            "flow_name": self._flow.name,
            "flow_path": str(self._flow_path),
            "recording_dir": str(self._recording_dir),
            "run_dir": str(self._run_dir),
            "started_at": started_at,
            "ended_at": _utc_now_iso(),
            "step_count": len(self._flow.steps),
            "steps": steps,
            "debug": self._debug,
        }
        if failed_step_id is not None:
            payload["failed_step_id"] = failed_step_id
        if error_code is not None:
            payload["error_code"] = error_code
        if error_message is not None:
            payload["error_message"] = error_message
        return payload


class RelativeOnlyResolver(Resolver):
    """跳过模板匹配，只使用相对坐标兜底。"""

    def template_strategies(self, step: FlowStep) -> list[Any]:
        return []


def replay_flow(
    *,
    target: str,
    data_root: str = "data",
    storage: Storage | None = None,
    capture_factory: Callable[[], ScreenCapture] = ScreenCapture,
    executor_factory: Callable[[], Executor] = Executor,
    resolver_factory: Callable[..., Resolver] = Resolver,
    overlay_factory: Callable[[], Any] = NullReplayOverlay,
    inputs: dict[str, str] | None = None,
    focused_text_getter: Callable[[str], str | None] | None = try_get_focused_text_value,
    debug: bool = False,
    relative_only: bool = False,
    sleep: Callable[[float], None] = time.sleep,
    time_source: Callable[[], float] = time.monotonic,
) -> tuple[dict[str, Any], int]:
    """回放指定 flow，并返回最终 JSON 结果。"""
    storage = storage or Storage(data_root)
    try:
        recording_dir, flow_path, flow = _resolve_target(storage, target)
    except Exception as exc:  # noqa: BLE001
        payload = {
            "status": "error",
            "error_code": "flow_not_found",
            "error_message": str(exc),
            "target": target,
        }
        return payload, 1

    run_dir = storage.create_run(flow.name)
    executor = executor_factory()
    capture = capture_factory()
    try:
        overlay = overlay_factory()
    except Exception:  # noqa: BLE001
        overlay = NullReplayOverlay()
    resolver_cls = RelativeOnlyResolver if relative_only else resolver_factory
    resolver = resolver_cls(
        flow_dir=recording_dir,
        capture=capture,
        before_capture=overlay.hide,
        after_capture=overlay.show,
    )
    runner = ReplayRunner(
        flow=flow,
        flow_path=flow_path,
        recording_dir=recording_dir,
        run_dir=run_dir,
        storage=storage,
        executor=executor,
        resolver=resolver,
        overlay=overlay,
        inputs=inputs,
        focused_text_getter=focused_text_getter,
        debug=debug,
        sleep=sleep,
        time_source=time_source,
    )
    return runner.run()


def main(argv: list[str] | None = None) -> int:
    """启动回放 CLI。"""
    args = _build_parser().parse_args(argv)
    try:
        inputs = _parse_inputs_json(args.inputs_json)
    except ValueError as exc:
        print(
            json.dumps(
                {
                    "status": "error",
                    "error_code": "invalid_inputs",
                    "error_message": str(exc),
                },
                ensure_ascii=False,
            )
        )
        return 1
    payload, exit_code = replay_flow(
        target=args.target,
        data_root=args.data_root,
        overlay_factory=SwiftReplayOverlay,
        inputs=inputs,
        debug=args.debug,
        relative_only=args.relative_only,
    )
    cli_payload = _build_cli_payload(
        payload,
        full_output=args.json or args.debug,
    )
    print(json.dumps(cli_payload, ensure_ascii=False))
    return exit_code


def _build_parser() -> argparse.ArgumentParser:
    """构造回放 CLI 参数解析器。"""
    parser = argparse.ArgumentParser(description="Replay macOS foreground app workflows")
    parser.add_argument("command", choices=["run"])
    parser.add_argument("target")
    parser.add_argument("--data-root", default="data")
    parser.add_argument(
        "--inputs-json",
        default=None,
        help="JSON object of replay-time text inputs keyed by flow input id",
    )
    parser.add_argument("--debug", action="store_true")
    parser.add_argument(
        "--relative-only",
        action="store_true",
        help="skip template matching and replay click-like steps using relative coordinates only",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the full replay payload, including per-step details",
    )
    return parser


def _build_cli_payload(
    payload: dict[str, Any],
    *,
    full_output: bool,
) -> dict[str, Any]:
    """根据 CLI 模式决定输出摘要还是完整 payload。"""
    if full_output or "steps" not in payload:
        return payload

    steps = payload.get("steps")
    if not isinstance(steps, list):
        return payload

    summary = {key: value for key, value in payload.items() if key != "steps"}
    summary["completed_step_count"] = sum(
        1
        for step in steps
        if isinstance(step, dict) and step.get("success") is True
    )
    summary["steps_omitted"] = True
    return summary


def _parse_inputs_json(raw_value: str | None) -> dict[str, str] | None:
    """解析 replay CLI 的 JSON 输入。"""
    if raw_value is None:
        return None
    try:
        payload = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"inputs-json must be valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("inputs-json must decode to a JSON object")
    normalized: dict[str, str] = {}
    for key, value in payload.items():
        if not isinstance(key, str) or not key:
            raise ValueError("inputs-json keys must be non-empty strings")
        if not isinstance(value, str):
            raise ValueError(f"inputs-json value for {key} must be a string")
        normalized[key] = value
    return normalized


def _resolve_target(
    storage: Storage,
    target: str,
) -> tuple[Path, Path, FlowDefinition]:
    """优先按路径，其次按录制名称解析目标 flow。"""
    target_path = storage.resolve_input_path(target)
    if target_path.exists():
        flow_path = target_path / "flow.json" if target_path.is_dir() else target_path
        recording_dir = flow_path.parent
        return recording_dir, flow_path, storage.load_flow(flow_path)

    normalized_target = storage._safe_name(target, fallback="flow")
    if not storage.recordings_dir.exists():
        raise FileNotFoundError(f"recordings directory does not exist: {storage.recordings_dir}")

    matches: list[tuple[Path, Path, FlowDefinition]] = []
    for recording_dir in sorted(storage.recordings_dir.iterdir(), reverse=True):
        if not recording_dir.is_dir():
            continue
        flow_path = recording_dir / "flow.json"
        if not flow_path.exists():
            continue
        try:
            flow = storage.load_flow(flow_path)
        except Exception:  # noqa: BLE001
            continue
        if storage._safe_name(flow.name, fallback="flow") == normalized_target:
            matches.append((recording_dir, flow_path, flow))

    if not matches:
        raise FileNotFoundError(f'no recording found for "{target}"')
    return matches[0]


def _serialize_result(result: Any) -> dict[str, Any]:
    """把结果对象归一化为 JSON 兼容字典。"""
    to_dict = getattr(result, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    if is_dataclass(result):
        return asdict(result)
    if isinstance(result, dict):
        return result
    raise TypeError(f"unsupported result type: {type(result)!r}")


def _step_with_resolved_target(step: FlowStep, *, x: int | None, y: int | None) -> FlowStep:
    """基于 resolver 的坐标创建一次临时执行步骤。"""
    if x is None or y is None:
        raise ValueError("resolved click step requires absolute coordinates")
    payload = step.to_dict()
    target_payload = payload.get("target", {})
    target_payload["abs_x"] = x
    target_payload["abs_y"] = y
    payload["target"] = target_payload
    return FlowStep.from_dict(payload)


def _window_context_contains_target(window_context: WindowContext, target: Any) -> bool:
    """判断录制目标点是否落在窗口几何范围内。"""
    return (
        window_context.x <= target.abs_x <= window_context.x + window_context.width
        and window_context.y <= target.abs_y <= window_context.y + window_context.height
    )


def _focused_text_contains_target(
    *,
    before_text: str | None,
    current_text: str,
    target_text: str,
) -> bool:
    """判断输入框文本是否已经体现出本次输入。"""
    if target_text not in current_text:
        return False
    if before_text is None:
        return True
    return current_text != before_text or target_text not in before_text


def _utc_now_iso() -> str:
    """生成当前 UTC ISO 时间戳。"""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _step_overlay_summary(step: FlowStep) -> str:
    """为 HUD 提取单步摘要。"""
    for candidate in (step.description, step.action, step.id):
        if candidate is None:
            continue
        normalized = " ".join(str(candidate).split())
        if normalized:
            return normalized
    return "step"


def _truncate_overlay_text(text: str) -> str:
    """限制 HUD 文案长度，避免浮窗过宽。"""
    normalized = " ".join(text.split())
    if len(normalized) <= REPLAY_OVERLAY_MAX_STATUS_LENGTH:
        return normalized
    return f"{normalized[: REPLAY_OVERLAY_MAX_STATUS_LENGTH - 3]}..."


class InvalidReplayInputsError(ValueError):
    """回放时的动态文本输入不合法。"""


if __name__ == "__main__":
    raise SystemExit(main())
