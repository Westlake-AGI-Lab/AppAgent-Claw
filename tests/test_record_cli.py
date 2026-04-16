from pathlib import Path

from scripts.record import SwiftRecordingOverlay, annotate_recording, start_recording
from scripts.recorder import RecordingResult
from scripts.schema import AppContext, FlowDefinition, FlowStep, Timing, WindowContext
from scripts.storage import Storage


class FakeOverlay:
    def __init__(self, script) -> None:  # noqa: ANN001
        self._script = script
        self.on_start = None
        self.on_cancel = None
        self.status_history: list[str] = []
        self.scheduled: list[callable] = []
        self.closed = False
        self.hide_calls = 0
        self.show_calls = 0

    def show_ready(self, *, on_start, on_cancel) -> None:
        self.on_start = on_start
        self.on_cancel = on_cancel

    def show_recording_hud(self, text: str) -> None:
        self.status_history.append(text)

    def set_status(self, text: str) -> None:
        self.status_history.append(text)

    def hide_hud(self) -> None:
        self.hide_calls += 1

    def show_hud(self) -> None:
        self.show_calls += 1

    def schedule(self, delay_ms: int, callback) -> None:  # noqa: ANN001
        del delay_ms
        self.scheduled.append(callback)

    def close(self) -> None:
        self.closed = True

    def run(self) -> None:
        self._script(self)


class FakeRecorder:
    last_instance = None

    def __init__(
        self,
        *,
        storage: Storage,
        session_dir: str | Path,
        flow_name: str,
        session_id: str,
        foreground_app_getter=None,  # noqa: ANN001
        foreground_app_refresher=None,  # noqa: ANN001
        controller_app_names=None,  # noqa: ANN001
        before_click_capture=None,  # noqa: ANN001
        after_click_capture=None,  # noqa: ANN001
        **_: object,
    ) -> None:
        self.storage = storage
        self.session_dir = Path(session_dir)
        self.flow_name = flow_name
        self.session_id = session_id
        self.foreground_app_getter = foreground_app_getter
        self.foreground_app_refresher = foreground_app_refresher
        self.controller_app_names = controller_app_names or set()
        self.before_click_capture = before_click_capture
        self.after_click_capture = after_click_capture
        self.target_app: str | None = None
        self.stop_requested = False
        self.started = False
        self.final_step_count = 1
        self.final_foreground_app = "TextEdit"
        FakeRecorder.last_instance = self

    def start(self) -> None:
        self.started = True

    def request_stop(self) -> None:
        self.stop_requested = True

    def finalize(self) -> RecordingResult:
        flow = FlowDefinition(
            name=self.flow_name,
            created_at="2026-03-20T00:00:00Z",
            app_context=(
                AppContext(foreground_app=self.final_foreground_app)
                if self.final_foreground_app is not None
                else None
            ),
            steps=[],
        )
        flow_path = self.storage.save_flow(self.session_dir, flow)
        return RecordingResult(
            session_id=self.session_id,
            session_dir=self.session_dir,
            flow_path=flow_path,
            step_count=self.final_step_count,
            flow=flow,
        )


class ExplodingOverlay:
    def show_ready(self, *, on_start, on_cancel) -> None:  # noqa: ANN001
        del on_start, on_cancel

    def run(self) -> None:
        raise RuntimeError("overlay boom")


class _FakeStdin:
    def __init__(self) -> None:
        self.writes: list[str] = []

    def write(self, text: str) -> None:
        self.writes.append(text)

    def flush(self) -> None:
        return None


class _FakeProcess:
    def __init__(self) -> None:
        self.stdin = _FakeStdin()
        self.wait_calls = 0

    def wait(self, timeout: float | None = None) -> None:
        del timeout
        self.wait_calls += 1


def test_start_recording_runs_single_command_flow_to_completion(tmp_path) -> None:
    storage = Storage(tmp_path / "data")

    def script(overlay: FakeOverlay) -> None:
        overlay.on_start()
        recorder = FakeRecorder.last_instance
        recorder.target_app = "TextEdit"
        recorder.stop_requested = True
        while overlay.scheduled and not overlay.closed:
            callback = overlay.scheduled.pop(0)
            callback()

    payload, exit_code = start_recording(
        name="demo-flow",
        storage=storage,
        overlay_factory=lambda: FakeOverlay(script),
        recorder_factory=FakeRecorder,
        foreground_app_provider=lambda: "OpenClaw",
        session_id_factory=lambda: "sess123",
    )

    assert exit_code == 0
    assert payload["status"] == "completed"
    assert payload["session_id"] == "sess123"
    assert payload["foreground_app"] == "TextEdit"
    assert payload["annotation_status"] == "completed"
    assert Path(payload["recording_dir"]).is_dir()
    assert Path(payload["flow_path"]).is_file()


def test_start_recording_returns_cancelled_when_window_closed_before_start(tmp_path) -> None:
    storage = Storage(tmp_path / "data")

    def script(overlay: FakeOverlay) -> None:
        overlay.on_cancel()

    payload, exit_code = start_recording(
        name="demo-flow",
        storage=storage,
        overlay_factory=lambda: FakeOverlay(script),
        recorder_factory=FakeRecorder,
        foreground_app_provider=lambda: "OpenClaw",
        session_id_factory=lambda: "sess123",
    )

    assert exit_code == 0
    assert payload["status"] == "cancelled"
    assert payload["reason"] == "closed_before_completion"


def test_start_recording_returns_cancelled_when_no_steps_recorded(tmp_path) -> None:
    storage = Storage(tmp_path / "data")

    def script(overlay: FakeOverlay) -> None:
        overlay.on_start()
        recorder = FakeRecorder.last_instance
        recorder.target_app = "TextEdit"
        recorder.final_step_count = 0
        recorder.stop_requested = True
        while overlay.scheduled and not overlay.closed:
            callback = overlay.scheduled.pop(0)
            callback()

    payload, exit_code = start_recording(
        name="demo-flow",
        storage=storage,
        overlay_factory=lambda: FakeOverlay(script),
        recorder_factory=FakeRecorder,
        foreground_app_provider=lambda: "OpenClaw",
        session_id_factory=lambda: "sess123",
    )

    assert exit_code == 0
    assert payload["status"] == "cancelled"
    assert payload["reason"] == "no_recorded_steps"


def test_start_recording_marks_initial_foreground_app_as_controller(tmp_path) -> None:
    storage = Storage(tmp_path / "data")

    def script(overlay: FakeOverlay) -> None:
        overlay.on_start()
        recorder = FakeRecorder.last_instance
        recorder.target_app = "TextEdit"
        recorder.stop_requested = True
        while overlay.scheduled and not overlay.closed:
            callback = overlay.scheduled.pop(0)
            callback()

    start_recording(
        name="demo-flow",
        storage=storage,
        overlay_factory=lambda: FakeOverlay(script),
        recorder_factory=FakeRecorder,
        foreground_app_provider=lambda: "OpenClaw",
        session_id_factory=lambda: "sess123",
    )

    assert "OpenClaw" in FakeRecorder.last_instance.controller_app_names


def test_start_recording_rejects_second_active_session(tmp_path) -> None:
    storage = Storage(tmp_path / "data")
    storage.write_active_session({"session_id": "other"})

    payload, exit_code = start_recording(
        name="demo-flow",
        storage=storage,
        overlay_factory=lambda: FakeOverlay(lambda overlay: overlay.on_cancel()),
        recorder_factory=FakeRecorder,
        foreground_app_provider=lambda: "OpenClaw",
        session_id_factory=lambda: "sess123",
    )

    assert exit_code == 1
    assert payload["status"] == "error"
    assert payload["error_code"] == "active_session_exists"
    assert payload["session_id"] == "other"
    assert list(storage.recording_sessions_dir.glob("sess123_*")) == []


def test_start_recording_clears_active_session_after_completion(tmp_path) -> None:
    storage = Storage(tmp_path / "data")

    def script(overlay: FakeOverlay) -> None:
        overlay.on_start()
        recorder = FakeRecorder.last_instance
        recorder.target_app = "TextEdit"
        recorder.stop_requested = True
        while overlay.scheduled and not overlay.closed:
            callback = overlay.scheduled.pop(0)
            callback()

    payload, exit_code = start_recording(
        name="demo-flow",
        storage=storage,
        overlay_factory=lambda: FakeOverlay(script),
        recorder_factory=FakeRecorder,
        foreground_app_provider=lambda: "OpenClaw",
        session_id_factory=lambda: "sess123",
    )

    assert exit_code == 0
    assert payload["status"] == "completed"
    assert storage.load_active_session() is None


def test_start_recording_surfaces_overlay_failures(tmp_path) -> None:
    storage = Storage(tmp_path / "data")
    payload, exit_code = start_recording(
        name="demo-flow",
        storage=storage,
        overlay_factory=ExplodingOverlay,
        recorder_factory=FakeRecorder,
        foreground_app_provider=lambda: "OpenClaw",
        session_id_factory=lambda: "sess123",
    )

    assert exit_code == 1
    assert payload["status"] == "error"
    assert payload["error_code"] == "overlay_failed"
    assert "overlay boom" in payload["error_message"]
    assert list(storage.recording_sessions_dir.glob("sess123_*")) == []


def test_start_recording_can_skip_annotation(tmp_path) -> None:
    storage = Storage(tmp_path / "data")

    def script(overlay: FakeOverlay) -> None:
        overlay.on_start()
        recorder = FakeRecorder.last_instance
        recorder.target_app = "TextEdit"
        recorder.stop_requested = True
        while overlay.scheduled and not overlay.closed:
            callback = overlay.scheduled.pop(0)
            callback()

    payload, exit_code = start_recording(
        name="demo-flow",
        storage=storage,
        overlay_factory=lambda: FakeOverlay(script),
        recorder_factory=FakeRecorder,
        foreground_app_provider=lambda: "OpenClaw",
        session_id_factory=lambda: "sess123",
        auto_annotate=False,
    )

    assert exit_code == 0
    assert payload["status"] == "completed"
    assert payload["annotation_status"] == "skipped"


def test_manual_annotate_updates_existing_flow(tmp_path) -> None:
    storage = Storage(tmp_path / "data")
    recording_dir = storage.create_recording("wechat-send")
    flow = FlowDefinition(
        name="wechat-send",
        created_at="2026-03-28T00:00:00Z",
        app_context=AppContext(foreground_app="WeChat"),
        steps=[
            FlowStep(
                id="step_0001",
                action="type_text",
                timing=Timing(),
                text="在吗",
                window_context=WindowContext(
                    app_name="WeChat",
                    window_title="Chat",
                    x=100,
                    y=120,
                    width=800,
                    height=700,
                ),
            ),
            FlowStep(
                id="step_0002",
                action="hotkey",
                timing=Timing(),
                key="enter",
            ),
        ],
    )
    storage.save_flow(recording_dir, flow)

    payload, exit_code = annotate_recording(
        target=str(recording_dir),
        storage=storage,
    )
    loaded = storage.load_flow(recording_dir)

    assert exit_code == 0
    assert payload["status"] == "completed"
    assert payload["annotation_status"] == "completed"
    assert loaded.description is not None
    assert loaded.inputs[0].id == "input_message_body_01"


def test_start_recording_keeps_success_when_annotation_factory_raises(tmp_path) -> None:
    storage = Storage(tmp_path / "data")

    def script(overlay: FakeOverlay) -> None:
        overlay.on_start()
        recorder = FakeRecorder.last_instance
        recorder.target_app = "TextEdit"
        recorder.stop_requested = True
        while overlay.scheduled and not overlay.closed:
            callback = overlay.scheduled.pop(0)
            callback()

    def exploding_annotator_factory():
        raise RuntimeError("annotator setup failed")

    payload, exit_code = start_recording(
        name="demo-flow",
        storage=storage,
        overlay_factory=lambda: FakeOverlay(script),
        recorder_factory=FakeRecorder,
        foreground_app_provider=lambda: "OpenClaw",
        session_id_factory=lambda: "sess123",
        annotator_factory=exploding_annotator_factory,
    )
    loaded = storage.load_flow(payload["recording_dir"])

    assert exit_code == 0
    assert payload["status"] == "completed"
    assert payload["annotation_status"] == "failed"
    assert payload["annotation_error_message"] == "annotator setup failed"
    assert loaded.annotation.status == "failed"


def test_swift_overlay_close_sends_close_command_before_marking_closed() -> None:
    overlay = SwiftRecordingOverlay()
    process = _FakeProcess()
    overlay._process = process

    overlay.close()

    assert process.stdin.writes == ["close\n"]
    assert process.wait_calls == 1
