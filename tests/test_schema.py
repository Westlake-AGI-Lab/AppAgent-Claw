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


def build_click_step() -> FlowStep:
    return FlowStep(
        id="step_0001",
        action="click",
        monitor=MonitorInfo(id=1, left=0, top=0, width=1440, height=900),
        target=Target(abs_x=720, abs_y=300, rel_x=0.5, rel_y=0.33),
        locator=Locator(
            anchor_image="assets/step_0001/anchor.png",
            context_image="assets/step_0001/context.png",
            search_region=SearchRegion(left=600, top=200, width=240, height=180),
            match_threshold=0.92,
        ),
        timing=Timing(pre_delay_ms=0, post_delay_ms=800),
        retry=RetryPolicy(max_attempts=2, fallback_to_relative=True),
        validation=Validation(mode="none"),
        window_context=WindowContext(
            app_name="Google Chrome",
            window_title="Sign In",
            x=100,
            y=120,
            width=1280,
            height=720,
        ),
    )


def build_long_press_step(*, action: str = "long_press") -> FlowStep:
    step = build_click_step()
    step.action = action
    step.hold_duration_ms = 750
    return step


def test_flow_round_trip_preserves_click_step() -> None:
    flow = FlowDefinition(
        name="google-login",
        created_at="2026-03-19T12:00:00Z",
        app_context=AppContext(foreground_app="Google Chrome"),
        steps=[build_click_step()],
    )

    payload = flow.to_dict()
    loaded = FlowDefinition.from_dict(payload)

    assert loaded.to_dict() == payload


def test_flow_round_trip_preserves_long_press_step() -> None:
    flow = FlowDefinition(
        name="finder-hold",
        created_at="2026-03-19T12:00:00Z",
        app_context=AppContext(foreground_app="Finder"),
        steps=[build_long_press_step()],
    )

    payload = flow.to_dict()
    loaded = FlowDefinition.from_dict(payload)

    assert loaded.to_dict() == payload


def test_click_step_requires_locator() -> None:
    step = build_click_step()
    step.locator = None

    try:
        step.validate()
    except ValueError as exc:
        assert "requires locator" in str(exc)
    else:
        raise AssertionError("expected click validation to fail without locator")


def test_locator_paths_must_be_relative() -> None:
    step = build_click_step()
    step.locator.anchor_image = "/tmp/anchor.png"

    try:
        step.validate()
    except ValueError as exc:
        assert "relative path" in str(exc)
    else:
        raise AssertionError("expected locator path validation to fail")


def test_flow_without_schema_version_loads_as_legacy_version() -> None:
    payload = {
        "name": "google-login",
        "platform": "macos",
        "created_at": "2026-03-19T12:00:00Z",
        "steps": [],
    }

    flow = FlowDefinition.from_dict(payload)

    assert flow.schema_version == "0.1"


def test_flow_supports_previous_schema_version() -> None:
    payload = {
        "schema_version": "0.2",
        "name": "google-login",
        "platform": "macos",
        "created_at": "2026-03-19T12:00:00Z",
        "steps": [],
    }

    flow = FlowDefinition.from_dict(payload)

    assert flow.schema_version == "0.2"


def test_window_context_requires_non_empty_app_name() -> None:
    step = build_click_step()
    step.window_context = WindowContext(
        app_name="",
        x=0,
        y=25,
        width=1280,
        height=720,
    )

    try:
        step.validate()
    except ValueError as exc:
        assert "window_context.app_name" in str(exc)
    else:
        raise AssertionError("expected window_context validation to fail")


def test_target_requires_integer_pixels() -> None:
    step = build_click_step()
    step.target.abs_x = 720.5

    try:
        step.validate()
    except ValueError as exc:
        assert "target.abs_x must be an integer" == str(exc)
    else:
        raise AssertionError("expected target pixel validation to fail")


def test_long_press_step_requires_hold_duration() -> None:
    step = build_long_press_step()
    step.hold_duration_ms = None

    try:
        step.validate()
    except ValueError as exc:
        assert "requires hold_duration_ms" in str(exc)
    else:
        raise AssertionError("expected long_press validation to fail without hold_duration_ms")


def test_non_long_press_step_rejects_hold_duration() -> None:
    step = build_click_step()
    step.hold_duration_ms = 750

    try:
        step.validate()
    except ValueError as exc:
        assert "only valid for long_press steps" in str(exc)
    else:
        raise AssertionError("expected click validation to fail with hold_duration_ms")


def test_parameterized_text_step_requires_known_flow_input() -> None:
    flow = FlowDefinition(
        name="wechat-send",
        created_at="2026-03-19T12:00:00Z",
        app_context=AppContext(foreground_app="WeChat"),
        steps=[
            FlowStep(
                id="step_0001",
                action="type_text",
                text="hello",
                text_policy=TextPolicy(
                    mode="parameterized",
                    input_id="input_message_body_01",
                    reason="runtime message",
                ),
            )
        ],
    )

    try:
        flow.validate()
    except ValueError as exc:
        assert "unknown flow input" in str(exc)
    else:
        raise AssertionError("expected parameterized text step validation to fail")


def test_parameterized_text_round_trip_preserves_metadata() -> None:
    flow = FlowDefinition(
        name="wechat-send",
        created_at="2026-03-19T12:00:00Z",
        app_context=AppContext(foreground_app="WeChat"),
        description="Send a message in WeChat.",
        inputs=[
            FlowInput(
                id="input_message_body_01",
                semantic_role="message_body",
                description="Message body to send during replay.",
                example_text="你好",
            )
        ],
        steps=[
            FlowStep(
                id="step_0001",
                action="type_text",
                description="Type the replay-time message body.",
                text="你好",
                text_policy=TextPolicy(
                    mode="parameterized",
                    input_id="input_message_body_01",
                    reason="final send content",
                ),
            )
        ],
    )

    payload = flow.to_dict()
    loaded = FlowDefinition.from_dict(payload)

    assert loaded.to_dict() == payload
