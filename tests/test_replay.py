import json
from pathlib import Path

from scripts.executor import ActionResult
from scripts.replay import main, replay_flow
from scripts.resolver import ResolveResult, TemplateMatchResult
from scripts.schema import (
    AppContext,
    FlowDefinition,
    FlowInput,
    FlowStep,
    Locator,
    MonitorInfo,
    RetryPolicy,
    SearchRegion,
    Target,
    TextPolicy,
    Timing,
    Validation,
    WindowContext,
)
from scripts.storage import Storage


WINDOW_CONTEXT = WindowContext(
    app_name="TextEdit",
    window_title="Doc",
    x=100,
    y=120,
    width=900,
    height=700,
)


def make_action_result(
    *,
    success: bool,
    action: str,
    step_id: str | None = None,
    details: dict | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> ActionResult:
    return ActionResult(
        success=success,
        action=action,
        step_id=step_id,
        started_at="2026-03-23T00:00:00Z",
        ended_at="2026-03-23T00:00:00Z",
        duration_ms=0,
        details=details or {},
        error_code=error_code,
        error_message=error_message,
    )


class FakeExecutor:
    planned_prepare_results: list[ActionResult | BaseException] = []
    planned_run_results: list[ActionResult | BaseException] = []
    last_instance = None

    def __init__(self) -> None:
        self.prepare_results = list(self.__class__.planned_prepare_results)
        self.run_results = list(self.__class__.planned_run_results)
        self.prepare_calls: list[WindowContext] = []
        self.run_calls: list[FlowStep] = []
        self.__class__.last_instance = self

    def prepare_window(self, window_context: WindowContext):
        self.prepare_calls.append(window_context)
        if self.prepare_results:
            result = self.prepare_results.pop(0)
            if isinstance(result, BaseException):
                raise result
            return result
        return make_action_result(
            success=True,
            action="prepare_window",
            details={"app_name": window_context.app_name},
        )

    def run_step(self, step: FlowStep):
        self.run_calls.append(step)
        if self.run_results:
            result = self.run_results.pop(0)
            if isinstance(result, BaseException):
                raise result
            return result
        return make_action_result(
            success=True,
            action=step.action,
            step_id=step.id,
            details={"step_id": step.id},
        )


class FakeResolver:
    planned_resolve_results: list[ResolveResult] = []
    planned_match_results: list[TemplateMatchResult] = []
    last_instance = None

    def __init__(
        self,
        *,
        flow_dir: str | Path,
        capture,  # noqa: ANN001
        before_capture=lambda: None,  # noqa: B008
        after_capture=lambda: None,  # noqa: B008
    ) -> None:
        self.flow_dir = Path(flow_dir)
        self.capture = capture
        self.before_capture = before_capture
        self.after_capture = after_capture
        self.resolve_results = list(self.__class__.planned_resolve_results)
        self.match_results = list(self.__class__.planned_match_results)
        self.resolve_calls: list[dict] = []
        self.match_calls: list[dict] = []
        self.__class__.last_instance = self

    def resolve(self, step: FlowStep, **kwargs) -> ResolveResult:  # noqa: ANN003
        self.resolve_calls.append({"step_id": step.id, **kwargs})
        self.before_capture()
        self.after_capture()
        if self.resolve_results:
            return self.resolve_results.pop(0)
        return ResolveResult(
            success=True,
            x=step.target.abs_x if step.target is not None else None,
            y=step.target.abs_y if step.target is not None else None,
            strategy="anchor",
            match_score=0.95,
        )

    def locate_image(  # noqa: PLR0913
        self,
        image_path: str,
        search_region,
        *,
        threshold: float,
        debug: bool = False,
        debug_dir: str | Path | None = None,
        capture_name: str = "search_region",
        match_name: str = "match_debug",
        save_on_failure: bool = False,
    ) -> TemplateMatchResult:
        self.match_calls.append(
            {
                "image_path": image_path,
                "search_region": search_region,
                "threshold": threshold,
                "debug": debug,
                "debug_dir": Path(debug_dir) if debug_dir is not None else None,
                "capture_name": capture_name,
                "match_name": match_name,
                "save_on_failure": save_on_failure,
            }
        )
        self.before_capture()
        self.after_capture()
        if self.match_results:
            return self.match_results.pop(0)
        return TemplateMatchResult(
            matched=True,
            score=0.95,
            threshold=threshold,
            center_x=0,
            center_y=0,
        )


class FakeOverlay:
    planned_start_error: BaseException | None = None
    last_instance = None

    def __init__(self) -> None:
        self.events: list[object] = []
        self.statuses: list[str] = []
        self.closed = False
        self.__class__.last_instance = self

    def start(self) -> None:
        self.events.append("start")
        if self.__class__.planned_start_error is not None:
            raise self.__class__.planned_start_error

    def set_status(self, text: str) -> None:
        self.events.append(("status", text))
        self.statuses.append(text)

    def hide(self) -> None:
        self.events.append("hide")

    def show(self) -> None:
        self.events.append("show")

    def close(self) -> None:
        self.events.append("close")
        self.closed = True


def build_click_step(
    step_id: str,
    *,
    action: str = "click",
    hold_duration_ms: int | None = None,
    validation: Validation | None = None,
    retry: RetryPolicy | None = None,
    window_context: WindowContext | None = WINDOW_CONTEXT,
) -> FlowStep:
    return FlowStep(
        id=step_id,
        action=action,
        monitor=MonitorInfo(id=1, left=0, top=0, width=1440, height=900),
        target=Target(abs_x=100, abs_y=200, rel_x=0.1, rel_y=0.2),
        locator=Locator(
            anchor_image=f"assets/{step_id}/anchor.png",
            context_image=f"assets/{step_id}/context.png",
            search_region=SearchRegion(left=10, top=20, width=300, height=220),
            match_threshold=0.92,
        ),
        timing=Timing(pre_delay_ms=0, post_delay_ms=0),
        retry=retry or RetryPolicy(max_attempts=1, fallback_to_relative=True),
        validation=validation or Validation(mode="none"),
        window_context=window_context,
        hold_duration_ms=hold_duration_ms,
    )


def build_wait_step(step_id: str) -> FlowStep:
    return FlowStep(
        id=step_id,
        action="wait",
        timing=Timing(pre_delay_ms=0, post_delay_ms=0),
        seconds=0.1,
    )


def build_type_text_step(
    step_id: str,
    *,
    text: str,
    window_context: WindowContext | None = WINDOW_CONTEXT,
    text_policy: TextPolicy | None = None,
) -> FlowStep:
    return FlowStep(
        id=step_id,
        action="type_text",
        timing=Timing(pre_delay_ms=0, post_delay_ms=0),
        text=text,
        text_policy=text_policy,
        window_context=window_context,
    )


def save_flow(
    storage: Storage,
    recording_dir: Path,
    *,
    name: str,
    steps: list[FlowStep],
    inputs: list[FlowInput] | None = None,
) -> Path:
    flow = FlowDefinition(
        name=name,
        created_at="2026-03-23T00:00:00Z",
        app_context=AppContext(foreground_app="TextEdit"),
        inputs=inputs or [],
        steps=steps,
    )
    return storage.save_flow(recording_dir, flow)


def reset_fakes() -> None:
    FakeExecutor.planned_prepare_results = []
    FakeExecutor.planned_run_results = []
    FakeExecutor.last_instance = None
    FakeResolver.planned_resolve_results = []
    FakeResolver.planned_match_results = []
    FakeResolver.last_instance = None
    FakeOverlay.planned_start_error = None
    FakeOverlay.last_instance = None


def test_replay_runs_explicit_flow_path_with_window_prepare_and_resolved_target(tmp_path) -> None:
    reset_fakes()
    storage = Storage(tmp_path / "data")
    recording_dir = storage.create_recording("demo")
    flow_path = save_flow(storage, recording_dir, name="demo", steps=[build_click_step("step_0001")])
    FakeResolver.planned_resolve_results = [
        ResolveResult(success=True, x=321, y=432, strategy="anchor", match_score=0.98)
    ]

    payload, exit_code = replay_flow(
        target=str(flow_path),
        storage=storage,
        capture_factory=lambda: object(),
        executor_factory=FakeExecutor,
        resolver_factory=FakeResolver,
    )

    assert exit_code == 0
    assert payload["status"] == "completed"
    assert FakeExecutor.last_instance.prepare_calls == [WINDOW_CONTEXT]
    assert FakeExecutor.last_instance.run_calls[0].target.abs_x == 321
    assert FakeExecutor.last_instance.run_calls[0].target.abs_y == 432


def test_replay_flow_uses_relative_only_resolver_when_requested(tmp_path) -> None:
    reset_fakes()
    storage = Storage(tmp_path / "data")
    recording_dir = storage.create_recording("demo")
    flow_path = save_flow(
        storage,
        recording_dir,
        name="demo",
        steps=[build_wait_step("step_0001")],
    )

    class UnexpectedResolver:
        def __init__(self, **_kwargs) -> None:
            raise AssertionError("custom resolver_factory should be bypassed in relative_only mode")

    payload, exit_code = replay_flow(
        target=str(flow_path),
        storage=storage,
        capture_factory=lambda: object(),
        executor_factory=FakeExecutor,
        resolver_factory=UnexpectedResolver,
        relative_only=True,
    )

    assert exit_code == 0
    assert payload["status"] == "completed"


def test_replay_name_lookup_uses_latest_matching_recording(tmp_path) -> None:
    reset_fakes()
    storage = Storage(tmp_path / "data")
    storage._recording_timestamp = lambda: "20260319_120000"
    first_dir = storage.create_recording("demo")
    first_flow_path = save_flow(storage, first_dir, name="Demo Flow", steps=[build_wait_step("step_0001")])
    storage._recording_timestamp = lambda: "20260320_120000"
    second_dir = storage.create_recording("demo")
    second_flow_path = save_flow(storage, second_dir, name="Demo Flow", steps=[build_wait_step("step_0001")])

    payload, exit_code = replay_flow(
        target="Demo Flow",
        storage=storage,
        capture_factory=lambda: object(),
        executor_factory=FakeExecutor,
        resolver_factory=FakeResolver,
    )

    assert exit_code == 0
    assert payload["status"] == "completed"
    assert payload["flow_path"] == str(second_flow_path)
    assert payload["flow_path"] != str(first_flow_path)


def test_replay_runs_long_press_step_with_resolved_target(tmp_path) -> None:
    reset_fakes()
    storage = Storage(tmp_path / "data")
    recording_dir = storage.create_recording("demo")
    flow_path = save_flow(
        storage,
        recording_dir,
        name="demo",
        steps=[
            build_click_step(
                "step_0001",
                action="long_press",
                hold_duration_ms=750,
            )
        ],
    )
    FakeResolver.planned_resolve_results = [
        ResolveResult(success=True, x=444, y=555, strategy="anchor", match_score=0.97)
    ]

    payload, exit_code = replay_flow(
        target=str(flow_path),
        storage=storage,
        capture_factory=lambda: object(),
        executor_factory=FakeExecutor,
        resolver_factory=FakeResolver,
    )

    assert exit_code == 0
    assert payload["status"] == "completed"
    assert FakeExecutor.last_instance.run_calls[0].action == "long_press"
    assert FakeExecutor.last_instance.run_calls[0].target.abs_x == 444
    assert FakeExecutor.last_instance.run_calls[0].target.abs_y == 555
    assert FakeExecutor.last_instance.run_calls[0].hold_duration_ms == 750


def test_replay_skips_window_prepare_for_inconsistent_click_window_context(tmp_path) -> None:
    reset_fakes()
    storage = Storage(tmp_path / "data")
    recording_dir = storage.create_recording("demo")
    inconsistent_context = WindowContext(
        app_name="TextEdit",
        window_title="Other",
        x=400,
        y=300,
        width=200,
        height=160,
    )
    flow_path = save_flow(
        storage,
        recording_dir,
        name="demo",
        steps=[build_click_step("step_0001", window_context=inconsistent_context)],
    )
    FakeResolver.planned_resolve_results = [
        ResolveResult(success=True, x=321, y=432, strategy="anchor", match_score=0.98)
    ]

    payload, exit_code = replay_flow(
        target=str(flow_path),
        storage=storage,
        capture_factory=lambda: object(),
        executor_factory=FakeExecutor,
        resolver_factory=FakeResolver,
    )

    assert exit_code == 0
    assert payload["status"] == "completed"
    assert FakeExecutor.last_instance.prepare_calls == []
    assert FakeExecutor.last_instance.run_calls[0].target.abs_x == 321
    assert FakeExecutor.last_instance.run_calls[0].target.abs_y == 432


def test_replay_skips_duplicate_window_prepare_for_same_context(tmp_path) -> None:
    reset_fakes()
    storage = Storage(tmp_path / "data")
    recording_dir = storage.create_recording("demo")
    flow_path = save_flow(
        storage,
        recording_dir,
        name="demo",
        steps=[build_click_step("step_0001"), build_click_step("step_0002")],
    )
    FakeResolver.planned_resolve_results = [
        ResolveResult(success=True, x=100, y=200, strategy="anchor", match_score=0.95),
        ResolveResult(success=True, x=110, y=210, strategy="anchor", match_score=0.94),
    ]

    payload, exit_code = replay_flow(
        target=str(flow_path),
        storage=storage,
        capture_factory=lambda: object(),
        executor_factory=FakeExecutor,
        resolver_factory=FakeResolver,
    )

    assert exit_code == 0
    assert payload["status"] == "completed"
    assert len(FakeExecutor.last_instance.prepare_calls) == 1


def test_replay_retries_click_after_validation_failure_and_prepares_window_again(tmp_path) -> None:
    reset_fakes()
    storage = Storage(tmp_path / "data")
    recording_dir = storage.create_recording("demo")
    flow_path = save_flow(
        storage,
        recording_dir,
        name="demo",
        steps=[
            build_click_step(
                "step_0001",
                validation=Validation(mode="anchor_absent", timeout_seconds=0.0),
                retry=RetryPolicy(max_attempts=2, fallback_to_relative=True),
            )
        ],
    )
    FakeResolver.planned_resolve_results = [
        ResolveResult(success=True, x=100, y=200, strategy="anchor", match_score=0.95),
        ResolveResult(success=True, x=100, y=200, strategy="anchor", match_score=0.96),
    ]
    FakeResolver.planned_match_results = [
        TemplateMatchResult(matched=True, score=0.98, threshold=0.92),
        TemplateMatchResult(matched=False, score=0.20, threshold=0.92),
    ]

    payload, exit_code = replay_flow(
        target=str(flow_path),
        storage=storage,
        capture_factory=lambda: object(),
        executor_factory=FakeExecutor,
        resolver_factory=FakeResolver,
    )

    assert exit_code == 0
    assert payload["status"] == "completed"
    assert len(payload["steps"][0]["attempts"]) == 2
    assert len(FakeExecutor.last_instance.prepare_calls) == 2


def test_replay_anchor_present_uses_next_click_step_locator(tmp_path) -> None:
    reset_fakes()
    storage = Storage(tmp_path / "data")
    recording_dir = storage.create_recording("demo")
    flow_path = save_flow(
        storage,
        recording_dir,
        name="demo",
        steps=[
            build_click_step(
                "step_0001",
                validation=Validation(mode="anchor_present", timeout_seconds=0.0),
            ),
            build_wait_step("step_0002"),
            build_click_step("step_0003"),
        ],
    )
    FakeResolver.planned_resolve_results = [
        ResolveResult(success=True, x=100, y=200, strategy="anchor", match_score=0.95),
        ResolveResult(success=True, x=110, y=210, strategy="anchor", match_score=0.94),
    ]
    FakeResolver.planned_match_results = [
        TemplateMatchResult(matched=True, score=0.94, threshold=0.92),
    ]

    payload, exit_code = replay_flow(
        target=str(flow_path),
        storage=storage,
        capture_factory=lambda: object(),
        executor_factory=FakeExecutor,
        resolver_factory=FakeResolver,
    )

    assert exit_code == 0
    assert payload["status"] == "completed"
    assert FakeResolver.last_instance.match_calls[0]["image_path"] == "assets/step_0003/anchor.png"


def test_replay_anchor_present_fails_without_later_click_step(tmp_path) -> None:
    reset_fakes()
    storage = Storage(tmp_path / "data")
    recording_dir = storage.create_recording("demo")
    flow_path = save_flow(
        storage,
        recording_dir,
        name="demo",
        steps=[
            build_click_step(
                "step_0001",
                validation=Validation(mode="anchor_present", timeout_seconds=0.0),
            ),
            build_wait_step("step_0002"),
        ],
    )
    FakeResolver.planned_resolve_results = [
        ResolveResult(success=True, x=100, y=200, strategy="anchor", match_score=0.95),
    ]

    payload, exit_code = replay_flow(
        target=str(flow_path),
        storage=storage,
        capture_factory=lambda: object(),
        executor_factory=FakeExecutor,
        resolver_factory=FakeResolver,
    )

    assert exit_code == 1
    assert payload["status"] == "failed"
    assert payload["error_code"] == "validation_target_missing"


def test_replay_returns_cancelled_on_keyboard_interrupt_and_writes_run_json(tmp_path) -> None:
    reset_fakes()
    storage = Storage(tmp_path / "data")
    recording_dir = storage.create_recording("demo")
    flow_path = save_flow(
        storage,
        recording_dir,
        name="demo",
        steps=[build_wait_step("step_0001"), build_wait_step("step_0002")],
    )
    FakeExecutor.planned_run_results = [
        make_action_result(success=True, action="wait", step_id="step_0001"),
        KeyboardInterrupt(),
    ]

    payload, exit_code = replay_flow(
        target=str(flow_path),
        storage=storage,
        capture_factory=lambda: object(),
        executor_factory=FakeExecutor,
        resolver_factory=FakeResolver,
    )

    run_json_path = Path(payload["run_dir"]) / "run.json"

    assert exit_code == 130
    assert payload["status"] == "cancelled"
    assert run_json_path.is_file()


def test_replay_updates_overlay_status_for_each_step_and_closes(tmp_path) -> None:
    reset_fakes()
    storage = Storage(tmp_path / "data")
    recording_dir = storage.create_recording("demo")
    first_step = build_wait_step("step_0001")
    first_step.description = "打开目标窗口"
    second_step = build_wait_step("step_0002")
    second_step.description = "等待界面稳定"
    flow_path = save_flow(
        storage,
        recording_dir,
        name="demo",
        steps=[first_step, second_step],
    )
    sleep_calls: list[float] = []

    payload, exit_code = replay_flow(
        target=str(flow_path),
        storage=storage,
        capture_factory=lambda: object(),
        executor_factory=FakeExecutor,
        resolver_factory=FakeResolver,
        overlay_factory=FakeOverlay,
        sleep=sleep_calls.append,
    )

    overlay = FakeOverlay.last_instance

    assert exit_code == 0
    assert payload["status"] == "completed"
    assert overlay is not None
    assert overlay.events[0] == "start"
    assert overlay.statuses[0] == "准备回放 2 步"
    assert "回放中 1/2 · wait · 打开目标窗口" in overlay.statuses
    assert "回放中 2/2 · wait · 等待界面稳定" in overlay.statuses
    assert overlay.statuses[-1] == "回放完成 2/2"
    assert overlay.closed is True
    assert sleep_calls == [0.6]


def test_replay_overlay_shows_retry_attempt_and_hides_for_capture(tmp_path) -> None:
    reset_fakes()
    storage = Storage(tmp_path / "data")
    recording_dir = storage.create_recording("demo")
    click_step = build_click_step(
        "step_0001",
        validation=Validation(mode="anchor_absent", timeout_seconds=0.0),
        retry=RetryPolicy(max_attempts=2, fallback_to_relative=True),
    )
    click_step.description = "点击提交按钮"
    flow_path = save_flow(
        storage,
        recording_dir,
        name="demo",
        steps=[click_step],
    )
    FakeResolver.planned_resolve_results = [
        ResolveResult(success=True, x=100, y=200, strategy="anchor", match_score=0.95),
        ResolveResult(success=True, x=100, y=200, strategy="anchor", match_score=0.96),
    ]
    FakeResolver.planned_match_results = [
        TemplateMatchResult(matched=True, score=0.98, threshold=0.92),
        TemplateMatchResult(matched=False, score=0.20, threshold=0.92),
    ]
    sleep_calls: list[float] = []

    payload, exit_code = replay_flow(
        target=str(flow_path),
        storage=storage,
        capture_factory=lambda: object(),
        executor_factory=FakeExecutor,
        resolver_factory=FakeResolver,
        overlay_factory=FakeOverlay,
        sleep=sleep_calls.append,
    )

    overlay = FakeOverlay.last_instance

    assert exit_code == 0
    assert payload["status"] == "completed"
    assert overlay is not None
    assert "回放中 1/1 · click · 点击提交按钮" in overlay.statuses
    assert "回放中 1/1 · click · 第 2 次尝试 · 点击提交按钮" in overlay.statuses
    assert overlay.events.count("hide") == overlay.events.count("show")
    assert overlay.events.count("hide") >= 4
    assert sleep_calls == [0.6]


def test_replay_continues_when_overlay_start_fails(tmp_path) -> None:
    reset_fakes()
    storage = Storage(tmp_path / "data")
    recording_dir = storage.create_recording("demo")
    flow_path = save_flow(
        storage,
        recording_dir,
        name="demo",
        steps=[build_wait_step("step_0001")],
    )
    FakeOverlay.planned_start_error = RuntimeError("overlay boom")

    payload, exit_code = replay_flow(
        target=str(flow_path),
        storage=storage,
        capture_factory=lambda: object(),
        executor_factory=FakeExecutor,
        resolver_factory=FakeResolver,
        overlay_factory=FakeOverlay,
    )

    assert exit_code == 0
    assert payload["status"] == "completed"


def test_replay_closes_overlay_on_keyboard_interrupt(tmp_path) -> None:
    reset_fakes()
    storage = Storage(tmp_path / "data")
    recording_dir = storage.create_recording("demo")
    flow_path = save_flow(
        storage,
        recording_dir,
        name="demo",
        steps=[build_wait_step("step_0001")],
    )
    FakeExecutor.planned_run_results = [KeyboardInterrupt()]
    sleep_calls: list[float] = []

    payload, exit_code = replay_flow(
        target=str(flow_path),
        storage=storage,
        capture_factory=lambda: object(),
        executor_factory=FakeExecutor,
        resolver_factory=FakeResolver,
        overlay_factory=FakeOverlay,
        sleep=sleep_calls.append,
    )

    overlay = FakeOverlay.last_instance

    assert exit_code == 130
    assert payload["status"] == "cancelled"
    assert overlay is not None
    assert overlay.statuses[-1] == "回放已取消"
    assert overlay.closed is True
    assert sleep_calls == [0.6]


def test_replay_type_text_validates_focused_text_contains_target(tmp_path) -> None:
    reset_fakes()
    storage = Storage(tmp_path / "data")
    recording_dir = storage.create_recording("demo")
    flow_path = save_flow(
        storage,
        recording_dir,
        name="demo",
        steps=[build_type_text_step("step_0001", text="hello")],
    )
    FakeExecutor.planned_run_results = [
        make_action_result(
            success=True,
            action="type_text",
            step_id="step_0001",
            details={"text": "hello", "method": "paste", "clipboard_restored": True},
        )
    ]
    focused_values = iter(["", "hello"])

    payload, exit_code = replay_flow(
        target=str(flow_path),
        storage=storage,
        capture_factory=lambda: object(),
        executor_factory=FakeExecutor,
        resolver_factory=FakeResolver,
        focused_text_getter=lambda app_name: next(focused_values),
    )

    attempt = payload["steps"][0]["attempts"][0]

    assert exit_code == 0
    assert payload["status"] == "completed"
    assert attempt["text_validation"]["success"] is True
    assert attempt["text_validation"]["mode"] == "focused_text"
    assert attempt["text_validation"]["observed_text"] == "hello"


def test_replay_type_text_falls_back_to_wait_when_focused_text_unavailable(tmp_path) -> None:
    reset_fakes()
    storage = Storage(tmp_path / "data")
    recording_dir = storage.create_recording("demo")
    flow_path = save_flow(
        storage,
        recording_dir,
        name="demo",
        steps=[build_type_text_step("step_0001", text="hello")],
    )
    FakeExecutor.planned_run_results = [
        make_action_result(
            success=True,
            action="type_text",
            step_id="step_0001",
            details={"text": "hello", "method": "paste", "clipboard_restored": True},
        )
    ]
    sleep_calls: list[float] = []

    payload, exit_code = replay_flow(
        target=str(flow_path),
        storage=storage,
        capture_factory=lambda: object(),
        executor_factory=FakeExecutor,
        resolver_factory=FakeResolver,
        focused_text_getter=lambda app_name: None,
        sleep=sleep_calls.append,
    )

    attempt = payload["steps"][0]["attempts"][0]

    assert exit_code == 0
    assert payload["status"] == "completed"
    assert attempt["text_validation"]["success"] is True
    assert attempt["text_validation"]["mode"] == "fallback_wait"
    assert attempt["text_validation"]["reason"] == "focused_text_unavailable"
    assert sleep_calls == [1.0]


def test_replay_parameterized_type_text_uses_runtime_input(tmp_path) -> None:
    reset_fakes()
    storage = Storage(tmp_path / "data")
    recording_dir = storage.create_recording("demo")
    flow_path = save_flow(
        storage,
        recording_dir,
        name="demo",
        inputs=[
            FlowInput(
                id="input_message_body_01",
                semantic_role="message_body",
                description="Message body during replay.",
                example_text="hello",
            )
        ],
        steps=[
            build_type_text_step(
                "step_0001",
                text="hello",
                text_policy=TextPolicy(
                    mode="parameterized",
                    input_id="input_message_body_01",
                    reason="runtime message",
                ),
            )
        ],
    )
    FakeExecutor.planned_run_results = [
        make_action_result(
            success=True,
            action="type_text",
            step_id="step_0001",
            details={"text": "dynamic hello", "method": "paste", "clipboard_restored": True},
        )
    ]
    focused_values = iter(["", "dynamic hello"])

    payload, exit_code = replay_flow(
        target=str(flow_path),
        storage=storage,
        capture_factory=lambda: object(),
        executor_factory=FakeExecutor,
        resolver_factory=FakeResolver,
        focused_text_getter=lambda app_name: next(focused_values),
        inputs={"input_message_body_01": "dynamic hello"},
    )

    attempt = payload["steps"][0]["attempts"][0]

    assert exit_code == 0
    assert payload["status"] == "completed"
    assert FakeExecutor.last_instance.run_calls[0].text == "dynamic hello"
    assert attempt["text_resolution"] == {
        "mode": "parameterized",
        "input_id": "input_message_body_01",
        "recorded_text": "hello",
        "resolved_text": "dynamic hello",
        "source": "input",
    }
    assert attempt["text_validation"]["target_text"] == "dynamic hello"


def test_replay_parameterized_type_text_falls_back_to_recorded_text(tmp_path) -> None:
    reset_fakes()
    storage = Storage(tmp_path / "data")
    recording_dir = storage.create_recording("demo")
    flow_path = save_flow(
        storage,
        recording_dir,
        name="demo",
        inputs=[
            FlowInput(
                id="input_message_body_01",
                semantic_role="message_body",
                description="Message body during replay.",
                example_text="hello",
            )
        ],
        steps=[
            build_type_text_step(
                "step_0001",
                text="hello",
                text_policy=TextPolicy(
                    mode="parameterized",
                    input_id="input_message_body_01",
                    reason="runtime message",
                ),
            )
        ],
    )
    FakeExecutor.planned_run_results = [
        make_action_result(
            success=True,
            action="type_text",
            step_id="step_0001",
            details={"text": "hello", "method": "paste", "clipboard_restored": True},
        )
    ]
    focused_values = iter(["", "hello"])

    payload, exit_code = replay_flow(
        target=str(flow_path),
        storage=storage,
        capture_factory=lambda: object(),
        executor_factory=FakeExecutor,
        resolver_factory=FakeResolver,
        focused_text_getter=lambda app_name: next(focused_values),
    )

    attempt = payload["steps"][0]["attempts"][0]

    assert exit_code == 0
    assert payload["status"] == "completed"
    assert attempt["text_resolution"]["source"] == "recorded_fallback"
    assert attempt["text_resolution"]["resolved_text"] == "hello"


def test_replay_rejects_unknown_runtime_input_id(tmp_path) -> None:
    reset_fakes()
    storage = Storage(tmp_path / "data")
    recording_dir = storage.create_recording("demo")
    flow_path = save_flow(
        storage,
        recording_dir,
        name="demo",
        steps=[build_type_text_step("step_0001", text="hello")],
    )

    payload, exit_code = replay_flow(
        target=str(flow_path),
        storage=storage,
        capture_factory=lambda: object(),
        executor_factory=FakeExecutor,
        resolver_factory=FakeResolver,
        inputs={"input_message_body_01": "dynamic hello"},
    )

    assert exit_code == 1
    assert payload["status"] == "error"
    assert payload["error_code"] == "invalid_inputs"
    assert "unknown replay input ids" in payload["error_message"]


def test_replay_main_prints_summary_by_default(monkeypatch, capsys) -> None:
    payload = {
        "status": "completed",
        "flow_name": "demo",
        "flow_path": "examples/recordings/demo/flow.json",
        "recording_dir": "examples/recordings/demo",
        "run_dir": "data/runs/20260324T000000Z_demo",
        "started_at": "2026-03-24T00:00:00Z",
        "ended_at": "2026-03-24T00:00:01Z",
        "step_count": 2,
        "steps": [
            {"step_id": "step_0001", "success": True},
            {"step_id": "step_0002", "success": True},
        ],
        "debug": False,
    }
    monkeypatch.setattr("scripts.replay.replay_flow", lambda **_: (payload, 0))

    exit_code = main(["run", "demo"])
    stdout = capsys.readouterr().out.strip()
    printed = json.loads(stdout)

    assert exit_code == 0
    assert printed["status"] == "completed"
    assert printed["completed_step_count"] == 2
    assert printed["steps_omitted"] is True
    assert "steps" not in printed


def test_replay_main_prints_full_payload_with_json_flag(monkeypatch, capsys) -> None:
    payload = {
        "status": "completed",
        "flow_name": "demo",
        "flow_path": "examples/recordings/demo/flow.json",
        "recording_dir": "examples/recordings/demo",
        "run_dir": "data/runs/20260324T000000Z_demo",
        "started_at": "2026-03-24T00:00:00Z",
        "ended_at": "2026-03-24T00:00:01Z",
        "step_count": 1,
        "steps": [{"step_id": "step_0001", "success": True, "attempts": []}],
        "debug": False,
    }
    monkeypatch.setattr("scripts.replay.replay_flow", lambda **_: (payload, 0))

    exit_code = main(["run", "demo", "--json"])
    stdout = capsys.readouterr().out.strip()
    printed = json.loads(stdout)

    assert exit_code == 0
    assert printed == payload


def test_replay_main_prints_full_payload_with_debug_flag(monkeypatch, capsys) -> None:
    payload = {
        "status": "completed",
        "flow_name": "demo",
        "flow_path": "examples/recordings/demo/flow.json",
        "recording_dir": "examples/recordings/demo",
        "run_dir": "data/runs/20260324T000000Z_demo",
        "started_at": "2026-03-24T00:00:00Z",
        "ended_at": "2026-03-24T00:00:01Z",
        "step_count": 1,
        "steps": [{"step_id": "step_0001", "success": True, "attempts": []}],
        "debug": True,
    }
    monkeypatch.setattr("scripts.replay.replay_flow", lambda **_: (payload, 0))

    exit_code = main(["run", "demo", "--debug"])
    stdout = capsys.readouterr().out.strip()
    printed = json.loads(stdout)

    assert exit_code == 0
    assert printed == payload


def test_replay_main_passes_relative_only_flag(monkeypatch, capsys) -> None:
    captured_kwargs: dict[str, object] = {}

    def fake_replay_flow(**kwargs):
        captured_kwargs.update(kwargs)
        return ({"status": "completed", "steps": [], "debug": False}, 0)

    monkeypatch.setattr("scripts.replay.replay_flow", fake_replay_flow)

    exit_code = main(["run", "demo", "--relative-only"])
    printed = json.loads(capsys.readouterr().out.strip())

    assert exit_code == 0
    assert printed["status"] == "completed"
    assert captured_kwargs["relative_only"] is True


def test_replay_main_rejects_invalid_inputs_json(capsys) -> None:
    exit_code = main(["run", "demo", "--inputs-json", "{invalid"])
    printed = json.loads(capsys.readouterr().out.strip())

    assert exit_code == 1
    assert printed["status"] == "error"
    assert printed["error_code"] == "invalid_inputs"


def test_replay_type_text_fails_when_focused_text_never_contains_target(tmp_path) -> None:
    reset_fakes()
    storage = Storage(tmp_path / "data")
    recording_dir = storage.create_recording("demo")
    flow_path = save_flow(
        storage,
        recording_dir,
        name="demo",
        steps=[build_type_text_step("step_0001", text="hello")],
    )
    FakeExecutor.planned_run_results = [
        make_action_result(
            success=True,
            action="type_text",
            step_id="step_0001",
            details={"text": "hello", "method": "paste", "clipboard_restored": True},
        )
    ]
    focused_values = iter(["", "nope", "still nope", "still nope"])
    time_values = iter([0.0, 0.0, 0.5, 1.0, 1.5, 2.1])
    sleep_calls: list[float] = []

    payload, exit_code = replay_flow(
        target=str(flow_path),
        storage=storage,
        capture_factory=lambda: object(),
        executor_factory=FakeExecutor,
        resolver_factory=FakeResolver,
        focused_text_getter=lambda app_name: next(focused_values),
        sleep=sleep_calls.append,
        time_source=lambda: next(time_values),
    )

    attempt = payload["steps"][0]["attempts"][0]

    assert exit_code == 1
    assert payload["status"] == "failed"
    assert payload["error_code"] == "text_validation_failed"
    assert attempt["text_validation"]["success"] is False
    assert attempt["text_validation"]["mode"] == "focused_text"
    assert attempt["text_validation"]["error_code"] == "text_validation_failed"
    assert sleep_calls == [0.2, 0.2, 0.2, 0.2]
