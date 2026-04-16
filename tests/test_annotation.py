from scripts.annotation import HeuristicFlowAnnotator, annotate_recording
from scripts.schema import (
    AppContext,
    FlowDefinition,
    FlowStep,
    FlowAnnotation,
    Locator,
    MonitorInfo,
    RetryPolicy,
    SearchRegion,
    Target,
    Timing,
    Validation,
    WindowContext,
)
from scripts.storage import Storage


WECHAT_WINDOW = WindowContext(
    app_name="WeChat",
    window_title="Chat",
    x=100,
    y=120,
    width=800,
    height=700,
)


NETEASE_WINDOW = WindowContext(
    app_name="NeteaseMusic",
    window_title="Song",
    x=100,
    y=120,
    width=900,
    height=700,
)


def test_annotator_parameterizes_wechat_message_body() -> None:
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
                window_context=WECHAT_WINDOW,
            ),
            FlowStep(
                id="step_0002",
                action="hotkey",
                timing=Timing(),
                key="enter",
                window_context=WECHAT_WINDOW,
            ),
        ],
    )

    annotated = HeuristicFlowAnnotator().annotate(flow, recording_dir="unused")
    text_step = annotated.steps[0]

    assert annotated.description is not None
    assert annotated.inputs[0].semantic_role == "message_body"
    assert text_step.text_policy is not None
    assert text_step.text_policy.mode == "parameterized"
    assert text_step.text_policy.input_id == "input_message_body_01"
    assert "runtime-provided text" in text_step.description


def test_annotator_parameterizes_netease_comment_body_with_submit_click_pattern() -> None:
    flow = FlowDefinition(
        name="netease-comment",
        created_at="2026-03-28T00:00:00Z",
        app_context=AppContext(foreground_app="NeteaseMusic"),
        steps=[
            FlowStep(
                id="step_0001",
                action="type_text",
                timing=Timing(),
                text="zhenbucuo ",
                window_context=NETEASE_WINDOW,
            ),
            FlowStep(
                id="step_0002",
                action="wait",
                timing=Timing(),
                seconds=0.5,
                window_context=NETEASE_WINDOW,
            ),
            FlowStep(
                id="step_0003",
                action="click",
                timing=Timing(),
                monitor=MonitorInfo(id=1, left=0, top=0, width=1440, height=900),
                target=Target(abs_x=200, abs_y=220, rel_x=0.2, rel_y=0.24),
                locator=Locator(
                    anchor_image="assets/step_0003/anchor.png",
                    context_image="assets/step_0003/context.png",
                    search_region=SearchRegion(left=120, top=140, width=180, height=120),
                ),
                retry=RetryPolicy(max_attempts=1, fallback_to_relative=True),
                validation=Validation(mode="none"),
                window_context=NETEASE_WINDOW,
            ),
            FlowStep(
                id="step_0004",
                action="wait",
                timing=Timing(),
                seconds=4.0,
                window_context=NETEASE_WINDOW,
            ),
        ],
    )

    annotated = HeuristicFlowAnnotator().annotate(flow, recording_dir="unused")

    assert annotated.inputs[0].semantic_role == "comment_body"
    assert annotated.steps[0].text_policy is not None
    assert annotated.steps[0].text_policy.mode == "parameterized"


def test_annotate_recording_marks_failed_status_when_annotator_raises(tmp_path) -> None:
    storage = Storage(tmp_path / "data")
    recording_dir = storage.create_recording("demo")
    flow = FlowDefinition(
        name="demo",
        created_at="2026-03-28T00:00:00Z",
        app_context=AppContext(foreground_app="TextEdit"),
        steps=[],
        annotation=FlowAnnotation(status="pending", source="none"),
    )
    storage.save_flow(recording_dir, flow)

    class ExplodingAnnotator:
        def annotate(self, flow, *, recording_dir):  # noqa: ANN001
            del flow, recording_dir
            raise RuntimeError("annotator boom")

    result = annotate_recording(
        target=str(recording_dir),
        storage=storage,
        annotator=ExplodingAnnotator(),
    )

    assert result.success is False
    assert result.flow.annotation.status == "failed"
    assert result.flow.annotation.error_message == "annotator boom"
