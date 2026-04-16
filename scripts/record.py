"""录制入口脚本。"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.annotation import (
    HeuristicFlowAnnotator,
    annotate_recording as annotate_saved_flow,
)
from scripts.capture import ScreenCapture
from scripts.focused_text import try_get_focused_text_value
from scripts.recorder import Recorder, RecordingError
from scripts.schema import CURRENT_FLOW_SCHEMA_VERSION, FlowAnnotation
from scripts.storage import Storage
from scripts.window_context import try_get_window_context


class ForegroundAppTracker:
    """缓存当前前台 app 名称，避免在每个事件里都跑 AppleScript。"""

    def __init__(self, provider: Callable[[], str | None]) -> None:
        self._provider = provider
        self._current_app: str | None = None

    def refresh(self) -> str | None:
        """同步刷新并返回当前前台 app。"""
        self._current_app = self._provider()
        return self._current_app

    def current_app(self) -> str | None:
        """返回最近一次刷新得到的前台 app。"""
        return self._current_app


class SwiftRecordingOverlay:
    """通过 Swift/AppKit helper 驱动的录制控制浮窗。"""

    def __init__(
        self,
        *,
        title: str = "AppAgent-Claw Recorder",
        helper_path: str | Path | None = None,
        swift_binary: str = "swift",
        popen_factory: Callable[..., Any] = subprocess.Popen,
    ) -> None:
        self._title = title
        self._helper_path = (
            Path(helper_path)
            if helper_path is not None
            else Path(__file__).with_name("record_overlay.swift")
        )
        self._swift_binary = swift_binary
        self._popen_factory = popen_factory
        self._on_start: Callable[[], None] = lambda: None
        self._on_cancel: Callable[[], None] = lambda: None
        self._process: Any = None
        self._closed = False
        self._command_lock = threading.Lock()
        self._last_error: str | None = None

    def show_ready(
        self,
        *,
        on_start: Callable[[], None],
        on_cancel: Callable[[], None],
    ) -> None:
        """显示 ready 态浮窗。"""
        self._on_start = on_start
        self._on_cancel = on_cancel

    def show_recording_hud(self, text: str) -> None:
        """切换成录制中状态条。"""
        self._send_command(f"recording\t{text}")

    def set_status(self, text: str) -> None:
        """更新状态文案。"""
        self._send_command(f"status\t{text}")

    def hide_hud(self) -> None:
        """临时隐藏状态条，避免污染截图。"""
        self._send_command("hide")

    def show_hud(self) -> None:
        """恢复显示状态条。"""
        self._send_command("show")

    def schedule(self, delay_ms: int, callback: Callable[[], None]) -> None:
        """在 UI 线程中安排下一次轮询。"""
        timer = threading.Timer(delay_ms / 1000, callback)
        timer.daemon = True
        timer.start()

    def close(self) -> None:
        """关闭浮窗。"""
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

    def run(self) -> None:
        """阻塞运行浮窗事件循环。"""
        if not self._helper_path.exists():
            raise RuntimeError(f"overlay helper not found: {self._helper_path}")
        self._process = self._popen_factory(
            [
                self._swift_binary,
                str(self._helper_path),
                self._title,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout = getattr(self._process, "stdout", None)
        stderr = getattr(self._process, "stderr", None)
        if stdout is None:
            raise RuntimeError("overlay helper stdout is unavailable")
        try:
            for raw_line in stdout:
                line = raw_line.strip()
                if line == "start":
                    self._on_start()
                elif line == "cancel":
                    self._on_cancel()
                elif line == "closed":
                    break
        finally:
            if stderr is not None:
                error_output = stderr.read().strip()
                if error_output:
                    self._last_error = error_output
            wait = getattr(self._process, "wait", None)
            if callable(wait):
                try:
                    wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
        returncode = getattr(self._process, "returncode", None)
        if returncode is None:
            poll = getattr(self._process, "poll", None)
            if callable(poll):
                returncode = poll()
        if (returncode or 0) != 0 and not self._closed:
            detail = self._last_error or f"overlay helper exited with status {returncode}"
            raise RuntimeError(detail)

    def _send_command(self, command: str) -> None:
        if self._process is None or self._closed:
            return
        stdin = getattr(self._process, "stdin", None)
        if stdin is None:
            return
        with self._command_lock:
            try:
                stdin.write(f"{command}\n")
                stdin.flush()
            except (BrokenPipeError, ValueError):
                return


class InteractiveRecordingSession:
    """单命令浮窗录制会话控制器。"""

    def __init__(
        self,
        *,
        name: str,
        storage: Storage,
        overlay: Any,
        capture_factory: Callable[[], ScreenCapture] = ScreenCapture,
        recorder_factory: Callable[..., Recorder] = Recorder,
        foreground_app_provider: Callable[[], str | None] = None,
        session_id_factory: Callable[[], str] = None,
        poll_interval_ms: int = 200,
        auto_annotate: bool = True,
        annotator_factory: Callable[[], Any] = HeuristicFlowAnnotator,
    ) -> None:
        self._name = name
        self._storage = storage
        self._overlay = overlay
        self._capture_factory = capture_factory
        self._recorder_factory = recorder_factory
        self._foreground_app_provider = foreground_app_provider or get_foreground_app_name
        self._session_id_factory = session_id_factory or _new_session_id
        self._poll_interval_ms = poll_interval_ms
        self._auto_annotate = auto_annotate
        self._annotator_factory = annotator_factory

        self._session_id = self._session_id_factory()
        self._session_dir = self._storage.create_recording_session(name, self._session_id)
        self._tracker = ForegroundAppTracker(self._foreground_app_provider)
        self._controller_app_names = {
            app_name
            for app_name in {
                self._foreground_app_provider(),
                "Python",
                "Python Launcher",
            }
            if app_name
        }
        self._recorder: Recorder | None = None
        self._state = "ready"
        self._payload: dict[str, Any] | None = None
        self._exit_code = 1
        self._owns_active_session = False

    def run(self) -> tuple[dict[str, Any], int]:
        """运行完整的浮窗录制流程。"""
        active_session = self._claim_active_session()
        if active_session is not None:
            self._storage.delete_recording_session(self._session_dir)
            return (
                _error_payload(
                    error_code="active_session_exists",
                    error_message="another recording session is already active",
                    session_id=active_session.get("session_id"),
                ),
                1,
            )
        try:
            self._overlay.show_ready(
                on_start=self._handle_start,
                on_cancel=self._handle_cancel,
            )
            self._overlay.run()
        except Exception as exc:  # noqa: BLE001
            self._storage.delete_recording_session(self._session_dir)
            self._payload = _error_payload(
                error_code="overlay_failed",
                error_message=str(exc),
                session_id=self._session_id,
            )
            self._exit_code = 1
        finally:
            self._release_active_session()
        if self._payload is None:
            self._storage.delete_recording_session(self._session_dir)
            self._payload = _error_payload(
                error_code="unexpected_exit",
                error_message="recording UI exited without a result",
                session_id=self._session_id,
            )
            self._exit_code = 1
        return self._payload, self._exit_code

    def _handle_start(self) -> None:
        if self._state != "ready":
            return
        self._state = "arming"
        self._write_active_session(status="arming")
        controller_app = self._foreground_app_provider()
        if controller_app:
            self._controller_app_names.add(controller_app)
        self._tracker.refresh()
        self._overlay.show_recording_hud("录制中，ESC 停止")
        try:
            self._recorder = self._recorder_factory(
                storage=self._storage,
                capture=self._capture_factory(),
                session_dir=self._session_dir,
                flow_name=self._name,
                session_id=self._session_id,
                foreground_app_getter=self._tracker.current_app,
                foreground_app_refresher=self._tracker.refresh,
                window_context_getter=try_get_window_context,
                focused_text_getter=try_get_focused_text_value,
                controller_app_names=self._controller_app_names,
                before_click_capture=self._overlay.hide_hud,
                after_click_capture=self._overlay.show_hud,
            )
            self._recorder.start()
        except Exception as exc:  # noqa: BLE001
            self._storage.delete_recording_session(self._session_dir)
            self._payload = _error_payload(
                error_code="start_failed",
                error_message=str(exc),
                session_id=self._session_id,
            )
            self._exit_code = 1
            self._overlay.close()
            return
        self._state = "recording"
        self._write_active_session(status="recording")
        self._overlay.schedule(self._poll_interval_ms, self._tick)

    def _handle_cancel(self) -> None:
        if self._state == "completed":
            return
        if self._recorder is not None:
            self._recorder.request_stop()
        self._storage.delete_recording_session(self._session_dir)
        self._payload = {
            "status": "cancelled",
            "session_id": self._session_id,
            "reason": "closed_before_completion",
        }
        self._exit_code = 0
        self._state = "completed"
        self._overlay.close()

    def _tick(self) -> None:
        if self._state != "recording" or self._recorder is None:
            return
        self._tracker.refresh()
        if self._recorder.target_app:
            self._overlay.set_status(f"录制中：{self._recorder.target_app} · ESC 停止")
        if self._recorder.stop_requested:
            self._finalize()
            return
        self._overlay.schedule(self._poll_interval_ms, self._tick)

    def _finalize(self) -> None:
        if self._state == "completed" or self._recorder is None:
            return
        self._state = "finalizing"
        self._write_active_session(status="finalizing")
        try:
            result = self._recorder.finalize()
        except RecordingError as exc:
            self._storage.delete_recording_session(self._session_dir)
            self._payload = {
                "status": "failed",
                "error_code": "recording_failed",
                "error_message": str(exc),
                "session_id": self._session_id,
            }
            self._exit_code = 1
            self._state = "completed"
            self._overlay.close()
            return

        if result.step_count == 0 or result.flow.app_context is None:
            reason = (
                "cancelled_before_target_app_locked"
                if result.flow.app_context is None
                else "no_recorded_steps"
            )
            self._storage.delete_recording_session(result.session_dir)
            self._payload = {
                "status": "cancelled",
                "session_id": self._session_id,
                "reason": reason,
            }
            self._exit_code = 0
            self._state = "completed"
            self._overlay.close()
            return

        recording_dir = self._storage.promote_recording_session(result.session_dir, self._name)
        annotation_status = "skipped"
        annotation_error_message: str | None = None
        if self._auto_annotate:
            try:
                annotation_result = annotate_saved_flow(
                    target=str(recording_dir),
                    storage=self._storage,
                    annotator=self._annotator_factory(),
                    source="agent",
                )
                annotation_status = annotation_result.flow.annotation.status
                annotation_error_message = annotation_result.error_message
            except Exception as exc:  # noqa: BLE001
                annotation_status = "failed"
                annotation_error_message = str(exc)
                self._mark_annotation_failed(recording_dir, error_message=str(exc))
        self._payload = {
            "status": "completed",
            "session_id": self._session_id,
            "recording_dir": str(recording_dir),
            "flow_path": str(recording_dir / "flow.json"),
            "step_count": result.step_count,
            "foreground_app": result.flow.app_context.foreground_app,
            "annotation_status": annotation_status,
        }
        if annotation_error_message is not None:
            self._payload["annotation_error_message"] = annotation_error_message
        self._exit_code = 0
        self._state = "completed"
        self._overlay.close()

    def _mark_annotation_failed(
        self,
        recording_dir: Path,
        *,
        error_message: str,
    ) -> None:
        """在自动标注崩溃时尽量把失败状态写回 ``flow.json``。"""
        try:
            flow = self._storage.load_flow(recording_dir)
            flow = replace(
                flow,
                schema_version=CURRENT_FLOW_SCHEMA_VERSION,
                annotation=FlowAnnotation(
                    status="failed",
                    source="agent",
                    analyzed_at=_utc_now_iso(),
                    error_message=error_message,
                ),
            )
            self._storage.save_flow(recording_dir, flow)
        except Exception:  # noqa: BLE001
            return None

    def _claim_active_session(self) -> dict[str, Any] | None:
        active_session = self._storage.load_active_session()
        if active_session is not None:
            return active_session
        self._owns_active_session = True
        self._write_active_session(status="ready")
        return None

    def _release_active_session(self) -> None:
        if not self._owns_active_session:
            return
        active_session = self._storage.load_active_session()
        if active_session is None or active_session.get("session_id") == self._session_id:
            self._storage.clear_active_session()
        self._owns_active_session = False

    def _write_active_session(self, *, status: str) -> None:
        if not self._owns_active_session:
            return
        self._storage.write_active_session(
            {
                "session_id": self._session_id,
                "name": self._name,
                "pid": os.getpid(),
                "status": status,
                "session_dir": str(self._session_dir),
            }
        )


def main(argv: list[str] | None = None) -> int:
    """启动录制 CLI。"""
    args = _build_parser().parse_args(argv)
    if args.command == "start":
        payload, exit_code = start_recording(
            name=args.name,
            data_root=args.data_root,
            auto_annotate=not args.skip_annotation,
        )
    else:
        payload, exit_code = annotate_recording(
            target=args.target,
            data_root=args.data_root,
        )
    print(json.dumps(payload, ensure_ascii=False))
    return exit_code


def start_recording(
    *,
    name: str,
    data_root: str = "data",
    storage: Storage | None = None,
    overlay_factory: Callable[[], Any] = SwiftRecordingOverlay,
    capture_factory: Callable[[], ScreenCapture] = ScreenCapture,
    recorder_factory: Callable[..., Recorder] = Recorder,
    foreground_app_provider: Callable[[], str | None] = None,
    session_id_factory: Callable[[], str] = None,
    auto_annotate: bool = True,
    annotator_factory: Callable[[], Any] = HeuristicFlowAnnotator,
) -> tuple[dict[str, Any], int]:
    """启动单命令浮窗录制流程，并在结束时返回最终 JSON。"""
    storage = storage or Storage(data_root)
    session = InteractiveRecordingSession(
        name=name,
        storage=storage,
        overlay=overlay_factory(),
        capture_factory=capture_factory,
        recorder_factory=recorder_factory,
        foreground_app_provider=foreground_app_provider or get_foreground_app_name,
        session_id_factory=session_id_factory or _new_session_id,
        auto_annotate=auto_annotate,
        annotator_factory=annotator_factory,
    )
    return session.run()


def annotate_recording(
    *,
    target: str,
    data_root: str = "data",
    storage: Storage | None = None,
    annotator_factory: Callable[[], Any] = HeuristicFlowAnnotator,
) -> tuple[dict[str, Any], int]:
    """手动对已保存录制重跑一次标注。"""
    storage = storage or Storage(data_root)
    try:
        annotation_result = annotate_saved_flow(
            target=target,
            storage=storage,
            annotator=annotator_factory(),
            source="manual",
        )
    except Exception as exc:  # noqa: BLE001
        return (
            _error_payload(
                error_code="annotation_failed",
                error_message=str(exc),
                target=target,
            ),
            1,
        )

    payload = {
        "status": "completed" if annotation_result.success else "failed",
        "target": target,
        "recording_dir": str(annotation_result.recording_dir),
        "flow_path": str(annotation_result.flow_path),
        "flow_name": annotation_result.flow.name,
        "annotation_status": annotation_result.flow.annotation.status,
    }
    if annotation_result.error_message is not None:
        payload["annotation_error_message"] = annotation_result.error_message
    return payload, 0 if annotation_result.success else 1


def get_foreground_app_name(
    *,
    subprocess_module: Any = subprocess,
) -> str | None:
    """查询当前 frontmost app 名称。"""
    script = (
        'tell application "System Events" to get name of first application process '
        "whose frontmost is true"
    )
    result = subprocess_module.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    name = result.stdout.strip()
    return name or None


def _build_parser() -> argparse.ArgumentParser:
    """构造录制 CLI 参数解析器。"""
    parser = argparse.ArgumentParser(description="Record macOS foreground app workflows")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start_parser = subparsers.add_parser("start")
    start_parser.add_argument("--name", default="flow")
    start_parser.add_argument("--data-root", default="data")
    start_parser.add_argument(
        "--skip-annotation",
        action="store_true",
        help="skip post-recording flow annotation and text-slot analysis",
    )

    annotate_parser = subparsers.add_parser("annotate")
    annotate_parser.add_argument("target")
    annotate_parser.add_argument("--data-root", default="data")
    return parser


def _error_payload(
    *,
    error_code: str,
    error_message: str,
    **details: Any,
) -> dict[str, Any]:
    """构造统一错误 JSON。"""
    payload = {
        "status": "error",
        "error_code": error_code,
        "error_message": error_message,
    }
    payload.update(details)
    return payload


def _new_session_id() -> str:
    """生成录制 session_id。"""
    return uuid.uuid4().hex[:12]


def _utc_now_iso() -> str:
    """生成当前 UTC ISO 时间戳。"""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
