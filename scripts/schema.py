"""录制流程与步骤的数据协议定义。"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal


CURRENT_FLOW_SCHEMA_VERSION = "0.3"
PREVIOUS_FLOW_SCHEMA_VERSION = "0.2"
LEGACY_FLOW_SCHEMA_VERSION = "0.1"
SUPPORTED_FLOW_SCHEMA_VERSIONS = {
    LEGACY_FLOW_SCHEMA_VERSION,
    PREVIOUS_FLOW_SCHEMA_VERSION,
    CURRENT_FLOW_SCHEMA_VERSION,
}

ActionName = Literal[
    "move",
    "click",
    "double_click",
    "right_click",
    "long_press",
    "right_long_press",
    "scroll",
    "type_text",
    "hotkey",
    "wait",
]
ValidationMode = Literal["none", "anchor_present", "anchor_absent"]

SUPPORTED_ACTIONS = {
    "move",
    "click",
    "double_click",
    "right_click",
    "long_press",
    "right_long_press",
    "scroll",
    "type_text",
    "hotkey",
    "wait",
}
LONG_PRESS_ACTIONS = {"long_press", "right_long_press"}
CLICK_ACTIONS = {"click", "double_click", "right_click", *LONG_PRESS_ACTIONS}
VALIDATION_MODES = {"none", "anchor_present", "anchor_absent"}
TEXT_POLICY_MODES = {"fixed", "parameterized"}
FLOW_INPUT_KINDS = {"text"}
TEXT_INPUT_SEMANTIC_ROLES = {"generic_text", "message_body", "comment_body"}
ANNOTATION_STATUSES = {"pending", "completed", "failed", "skipped"}
ANNOTATION_SOURCES = {"agent", "manual", "none"}


def _clean_for_json(value: Any) -> Any:
    """把嵌套 dataclass 转成可写入 JSON 的结构，并去掉 ``None``。"""
    if is_dataclass(value):
        return _clean_for_json(
            {field.name: getattr(value, field.name) for field in fields(value)}
        )
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            normalized = _clean_for_json(item)
            if normalized is not None:
                cleaned[key] = normalized
        return cleaned
    if isinstance(value, list):
        return [_clean_for_json(item) for item in value]
    return value


def _parse_datetime(value: str, field_name: str) -> None:
    """校验时间字符串是否符合 ISO-8601 兼容格式。"""
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be ISO-8601 compatible") from exc


def _ensure_relative_path(value: str, field_name: str) -> None:
    """确保资源路径存在且为相对路径。"""
    if not value:
        raise ValueError(f"{field_name} must not be empty")
    if Path(value).is_absolute():
        raise ValueError(f"{field_name} must be a relative path")


def _ensure_int(value: Any, field_name: str) -> None:
    """确保字段是整数像素值，而不是浮点或布尔值。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")


@dataclass(slots=True)
class AppContext:
    """流程级应用上下文信息。"""

    foreground_app: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """将上下文对象转换为可序列化字典。"""
        return _clean_for_json(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppContext":
        """从字典反序列化应用上下文。"""
        return cls(foreground_app=data.get("foreground_app"))

    def validate(self) -> None:
        """校验应用上下文字段是否合法。"""
        if self.foreground_app is not None and not self.foreground_app.strip():
            raise ValueError("app_context.foreground_app must not be empty")


@dataclass(slots=True)
class MonitorInfo:
    """录制时捕获到的屏幕几何信息。"""

    id: int
    left: int
    top: int
    width: int
    height: int

    def to_dict(self) -> dict[str, Any]:
        """将屏幕信息转换为可序列化字典。"""
        return _clean_for_json(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MonitorInfo":
        """从字典反序列化屏幕信息。"""
        return cls(
            id=int(data["id"]),
            left=int(data["left"]),
            top=int(data["top"]),
            width=int(data["width"]),
            height=int(data["height"]),
        )

    def validate(self) -> None:
        """校验屏幕尺寸是否合法。"""
        _ensure_int(self.id, "monitor.id")
        _ensure_int(self.left, "monitor.left")
        _ensure_int(self.top, "monitor.top")
        _ensure_int(self.width, "monitor.width")
        _ensure_int(self.height, "monitor.height")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("monitor.width and monitor.height must be positive")


@dataclass(slots=True)
class Target:
    """步骤目标的绝对与相对坐标。"""

    abs_x: int
    abs_y: int
    rel_x: float
    rel_y: float

    def to_dict(self) -> dict[str, Any]:
        """将目标坐标转换为可序列化字典。"""
        return _clean_for_json(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Target":
        """从字典反序列化目标坐标。"""
        return cls(
            abs_x=int(data["abs_x"]),
            abs_y=int(data["abs_y"]),
            rel_x=float(data["rel_x"]),
            rel_y=float(data["rel_y"]),
        )

    def validate(self) -> None:
        """校验相对坐标是否落在有效范围内。"""
        _ensure_int(self.abs_x, "target.abs_x")
        _ensure_int(self.abs_y, "target.abs_y")
        if not 0.0 <= self.rel_x <= 1.0:
            raise ValueError("target.rel_x must be between 0 and 1")
        if not 0.0 <= self.rel_y <= 1.0:
            raise ValueError("target.rel_y must be between 0 and 1")


@dataclass(slots=True)
class SearchRegion:
    """模板匹配时使用的搜索区域。"""

    left: int
    top: int
    width: int
    height: int

    def to_dict(self) -> dict[str, Any]:
        """将搜索区域转换为可序列化字典。"""
        return _clean_for_json(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SearchRegion":
        """从字典反序列化搜索区域。"""
        return cls(
            left=int(data["left"]),
            top=int(data["top"]),
            width=int(data["width"]),
            height=int(data["height"]),
        )

    def validate(self) -> None:
        """校验搜索区域尺寸是否合法。"""
        _ensure_int(self.left, "locator.search_region.left")
        _ensure_int(self.top, "locator.search_region.top")
        _ensure_int(self.width, "locator.search_region.width")
        _ensure_int(self.height, "locator.search_region.height")
        if self.width <= 0 or self.height <= 0:
            raise ValueError(
                "locator.search_region.width and height must be positive"
            )


@dataclass(slots=True)
class WindowContext:
    """步骤执行时所属窗口的应用、标题与几何信息。"""

    app_name: str
    x: int
    y: int
    width: int
    height: int
    window_title: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """将窗口上下文转换为可序列化字典。"""
        return _clean_for_json(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WindowContext":
        """从字典反序列化窗口上下文。"""
        return cls(
            app_name=str(data["app_name"]),
            window_title=(
                str(data["window_title"])
                if data.get("window_title") is not None
                else None
            ),
            x=int(data["x"]),
            y=int(data["y"]),
            width=int(data["width"]),
            height=int(data["height"]),
        )

    def validate(self) -> None:
        """校验窗口名、标题与几何值是否合法。"""
        if not self.app_name.strip():
            raise ValueError("window_context.app_name must not be empty")
        if self.window_title is not None and not self.window_title.strip():
            raise ValueError("window_context.window_title must not be empty")
        _ensure_int(self.x, "window_context.x")
        _ensure_int(self.y, "window_context.y")
        _ensure_int(self.width, "window_context.width")
        _ensure_int(self.height, "window_context.height")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("window_context.width and height must be positive")


@dataclass(slots=True)
class Locator:
    """点击类步骤的定位资源与匹配参数。"""

    anchor_image: str
    context_image: str
    search_region: SearchRegion
    match_threshold: float = 0.92

    def to_dict(self) -> dict[str, Any]:
        """将定位配置转换为可序列化字典。"""
        return _clean_for_json(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Locator":
        """从字典反序列化定位配置。"""
        return cls(
            anchor_image=str(data["anchor_image"]),
            context_image=str(data["context_image"]),
            search_region=SearchRegion.from_dict(data["search_region"]),
            match_threshold=float(data.get("match_threshold", 0.92)),
        )

    def validate(self) -> None:
        """校验定位资源路径和匹配阈值。"""
        _ensure_relative_path(self.anchor_image, "locator.anchor_image")
        _ensure_relative_path(self.context_image, "locator.context_image")
        self.search_region.validate()
        if not 0.0 <= self.match_threshold <= 1.0:
            raise ValueError("locator.match_threshold must be between 0 and 1")


@dataclass(slots=True)
class Timing:
    """动作前后可选延迟配置。"""

    pre_delay_ms: int = 0
    post_delay_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        """将延迟配置转换为可序列化字典。"""
        return _clean_for_json(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Timing":
        """从字典反序列化延迟配置。"""
        return cls(
            pre_delay_ms=int(data.get("pre_delay_ms", 0)),
            post_delay_ms=int(data.get("post_delay_ms", 0)),
        )

    def validate(self) -> None:
        """校验延迟值是否为非负数。"""
        if self.pre_delay_ms < 0 or self.post_delay_ms < 0:
            raise ValueError("timing delays must be zero or positive")


@dataclass(slots=True)
class RetryPolicy:
    """点击类步骤的重试策略。"""

    max_attempts: int = 1
    fallback_to_relative: bool = True

    def to_dict(self) -> dict[str, Any]:
        """将重试策略转换为可序列化字典。"""
        return _clean_for_json(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RetryPolicy":
        """从字典反序列化重试策略。"""
        return cls(
            max_attempts=int(data.get("max_attempts", 1)),
            fallback_to_relative=bool(data.get("fallback_to_relative", True)),
        )

    def validate(self) -> None:
        """校验重试次数是否合法。"""
        if self.max_attempts < 1:
            raise ValueError("retry.max_attempts must be at least 1")


@dataclass(slots=True)
class Validation:
    """动作后的基础校验配置。"""

    mode: ValidationMode = "none"
    timeout_seconds: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """将校验配置转换为可序列化字典。"""
        return _clean_for_json(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Validation":
        """从字典反序列化校验配置。"""
        return cls(
            mode=data.get("mode", "none"),
            timeout_seconds=(
                float(data["timeout_seconds"])
                if data.get("timeout_seconds") is not None
                else None
            ),
        )

    def validate(self) -> None:
        """校验校验模式与超时时间是否合法。"""
        if self.mode not in VALIDATION_MODES:
            raise ValueError(f"validation.mode must be one of {sorted(VALIDATION_MODES)}")
        if self.timeout_seconds is not None and self.timeout_seconds < 0:
            raise ValueError("validation.timeout_seconds must be zero or positive")


@dataclass(slots=True)
class TextPolicy:
    """文本步骤的固定/参数化策略。"""

    mode: str = "fixed"
    input_id: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """将文本策略转换为可序列化字典。"""
        return _clean_for_json(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TextPolicy":
        """从字典反序列化文本策略。"""
        return cls(
            mode=str(data.get("mode", "fixed")),
            input_id=(
                str(data["input_id"]) if data.get("input_id") is not None else None
            ),
            reason=str(data["reason"]) if data.get("reason") is not None else None,
        )

    def validate(self) -> None:
        """校验文本策略是否自洽。"""
        if self.mode not in TEXT_POLICY_MODES:
            raise ValueError(f"text_policy.mode must be one of {sorted(TEXT_POLICY_MODES)}")
        if self.mode == "parameterized" and self.input_id is None:
            raise ValueError("parameterized text_policy requires input_id")
        if self.mode == "fixed" and self.input_id is not None:
            raise ValueError("fixed text_policy must not include input_id")
        if self.input_id is not None and not self.input_id.strip():
            raise ValueError("text_policy.input_id must not be empty")
        if self.reason is not None and not self.reason.strip():
            raise ValueError("text_policy.reason must not be empty")


@dataclass(slots=True)
class FlowInput:
    """顶层可复用输入槽位定义。"""

    id: str
    kind: str = "text"
    semantic_role: str = "generic_text"
    description: str | None = None
    example_text: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """将输入槽位转换为可序列化字典。"""
        return _clean_for_json(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FlowInput":
        """从字典反序列化输入槽位。"""
        return cls(
            id=str(data["id"]),
            kind=str(data.get("kind", "text")),
            semantic_role=str(data.get("semantic_role", "generic_text")),
            description=(
                str(data["description"])
                if data.get("description") is not None
                else None
            ),
            example_text=(
                str(data["example_text"])
                if data.get("example_text") is not None
                else None
            ),
        )

    def validate(self) -> None:
        """校验输入槽位是否合法。"""
        if not self.id.strip():
            raise ValueError("flow_input.id must not be empty")
        if self.kind not in FLOW_INPUT_KINDS:
            raise ValueError(f"flow_input.kind must be one of {sorted(FLOW_INPUT_KINDS)}")
        if self.semantic_role not in TEXT_INPUT_SEMANTIC_ROLES:
            raise ValueError(
                "flow_input.semantic_role must be one of "
                f"{sorted(TEXT_INPUT_SEMANTIC_ROLES)}"
            )
        if self.description is not None and not self.description.strip():
            raise ValueError("flow_input.description must not be empty")
        if self.example_text is not None and not self.example_text:
            raise ValueError("flow_input.example_text must not be empty")


@dataclass(slots=True)
class FlowAnnotation:
    """录后分析的执行状态。"""

    status: str = "skipped"
    source: str = "none"
    analyzed_at: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """将分析状态转换为可序列化字典。"""
        return _clean_for_json(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FlowAnnotation":
        """从字典反序列化分析状态。"""
        return cls(
            status=str(data.get("status", "skipped")),
            source=str(data.get("source", "none")),
            analyzed_at=(
                str(data["analyzed_at"])
                if data.get("analyzed_at") is not None
                else None
            ),
            error_message=(
                str(data["error_message"])
                if data.get("error_message") is not None
                else None
            ),
        )

    def validate(self) -> None:
        """校验分析状态是否合法。"""
        if self.status not in ANNOTATION_STATUSES:
            raise ValueError(
                f"annotation.status must be one of {sorted(ANNOTATION_STATUSES)}"
            )
        if self.source not in ANNOTATION_SOURCES:
            raise ValueError(
                f"annotation.source must be one of {sorted(ANNOTATION_SOURCES)}"
            )
        if self.analyzed_at is not None:
            _parse_datetime(self.analyzed_at, "annotation.analyzed_at")
        if self.error_message is not None and not self.error_message.strip():
            raise ValueError("annotation.error_message must not be empty")


@dataclass(slots=True)
class FlowStep:
    """单个录制步骤的数据定义。"""

    id: str
    action: str
    timing: Timing = field(default_factory=Timing)
    monitor: MonitorInfo | None = None
    target: Target | None = None
    locator: Locator | None = None
    retry: RetryPolicy | None = None
    validation: Validation | None = None
    window_context: WindowContext | None = None
    description: str | None = None
    text: str | None = None
    text_policy: TextPolicy | None = None
    key: str | None = None
    keys: list[str] | None = None
    scroll_x: int | None = None
    scroll_y: int | None = None
    seconds: float | None = None
    hold_duration_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """将步骤对象转换为可序列化字典。"""
        return _clean_for_json(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FlowStep":
        """从字典反序列化单个步骤。"""
        return cls(
            id=str(data["id"]),
            action=str(data["action"]),
            timing=Timing.from_dict(data.get("timing", {})),
            monitor=(
                MonitorInfo.from_dict(data["monitor"])
                if data.get("monitor") is not None
                else None
            ),
            target=(
                Target.from_dict(data["target"])
                if data.get("target") is not None
                else None
            ),
            locator=(
                Locator.from_dict(data["locator"])
                if data.get("locator") is not None
                else None
            ),
            retry=(
                RetryPolicy.from_dict(data["retry"])
                if data.get("retry") is not None
                else None
            ),
            validation=(
                Validation.from_dict(data["validation"])
                if data.get("validation") is not None
                else None
            ),
            window_context=(
                WindowContext.from_dict(data["window_context"])
                if data.get("window_context") is not None
                else None
            ),
            description=(
                str(data["description"])
                if data.get("description") is not None
                else None
            ),
            text=data.get("text"),
            text_policy=(
                TextPolicy.from_dict(data["text_policy"])
                if data.get("text_policy") is not None
                else None
            ),
            key=data.get("key"),
            keys=list(data["keys"]) if data.get("keys") is not None else None,
            scroll_x=(
                int(data["scroll_x"]) if data.get("scroll_x") is not None else None
            ),
            scroll_y=(
                int(data["scroll_y"]) if data.get("scroll_y") is not None else None
            ),
            seconds=(
                float(data["seconds"]) if data.get("seconds") is not None else None
            ),
            hold_duration_ms=(
                int(data["hold_duration_ms"])
                if data.get("hold_duration_ms") is not None
                else None
            ),
        )

    def validate(self) -> None:
        """按动作类型校验步骤所需字段是否完整。"""
        if not self.id.strip():
            raise ValueError("step.id must not be empty")
        if self.action not in SUPPORTED_ACTIONS:
            raise ValueError(f"step.action must be one of {sorted(SUPPORTED_ACTIONS)}")
        self.timing.validate()

        if self.action in {"move", *CLICK_ACTIONS}:
            if self.target is None:
                raise ValueError(f"{self.action} step requires target")
            self.target.validate()

        if self.window_context is not None:
            self.window_context.validate()
        if self.description is not None and not self.description.strip():
            raise ValueError("step.description must not be empty")

        if self.action in CLICK_ACTIONS:
            if self.monitor is None:
                raise ValueError(f"{self.action} step requires monitor")
            if self.locator is None:
                raise ValueError(f"{self.action} step requires locator")
            if self.retry is None:
                raise ValueError(f"{self.action} step requires retry")
            if self.validation is None:
                raise ValueError(f"{self.action} step requires validation")
            self.monitor.validate()
            self.locator.validate()
            self.retry.validate()
            self.validation.validate()
            if self.action in LONG_PRESS_ACTIONS:
                if self.hold_duration_ms is None:
                    raise ValueError(f"{self.action} step requires hold_duration_ms")
                _ensure_int(self.hold_duration_ms, "hold_duration_ms")
                if self.hold_duration_ms <= 0:
                    raise ValueError("hold_duration_ms must be positive")
            elif self.hold_duration_ms is not None:
                raise ValueError(
                    "hold_duration_ms is only valid for long_press steps"
                )
        elif self.hold_duration_ms is not None:
            raise ValueError("hold_duration_ms is only valid for long_press steps")

        if self.action == "scroll":
            if self.scroll_x is None and self.scroll_y is None:
                raise ValueError("scroll step requires scroll_x or scroll_y")
            if self.target is not None:
                self.target.validate()

        if self.action == "type_text":
            if self.text is None or not self.text:
                raise ValueError("type_text step requires text")
            if self.text_policy is not None:
                self.text_policy.validate()
        elif self.text_policy is not None:
            raise ValueError("text_policy is only valid for type_text steps")

        if self.action == "hotkey":
            if self.keys is None and self.key is None:
                raise ValueError("hotkey step requires key or keys")
            if self.keys is not None and not self.keys:
                raise ValueError("hotkey keys must not be empty")
            if self.keys is not None and any(not key for key in self.keys):
                raise ValueError("hotkey keys must not contain empty values")
            if self.key is not None and not self.key:
                raise ValueError("hotkey key must not be empty")

        if self.action == "wait":
            if self.seconds is None:
                raise ValueError("wait step requires seconds")
            if self.seconds < 0:
                raise ValueError("wait seconds must be zero or positive")


@dataclass(slots=True)
class FlowDefinition:
    """顶层流程定义对象。"""

    name: str
    created_at: str
    steps: list[FlowStep]
    schema_version: str = CURRENT_FLOW_SCHEMA_VERSION
    platform: str = "macos"
    app_context: AppContext | None = None
    description: str | None = None
    inputs: list[FlowInput] = field(default_factory=list)
    annotation: FlowAnnotation = field(default_factory=FlowAnnotation)

    def to_dict(self) -> dict[str, Any]:
        """将流程对象转换为可序列化字典。"""
        return _clean_for_json(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FlowDefinition":
        """从字典反序列化顶层流程定义。"""
        flow = cls(
            schema_version=str(data.get("schema_version", LEGACY_FLOW_SCHEMA_VERSION)),
            name=str(data["name"]),
            platform=str(data.get("platform", "macos")),
            created_at=str(data["created_at"]),
            app_context=(
                AppContext.from_dict(data["app_context"])
                if data.get("app_context") is not None
                else None
            ),
            description=(
                str(data["description"])
                if data.get("description") is not None
                else None
            ),
            inputs=[
                FlowInput.from_dict(item) for item in data.get("inputs", [])
            ],
            annotation=FlowAnnotation.from_dict(data.get("annotation", {})),
            steps=[FlowStep.from_dict(step) for step in data.get("steps", [])],
        )
        flow.validate()
        return flow

    def validate(self) -> None:
        """校验流程级元数据与全部步骤。"""
        if self.schema_version not in SUPPORTED_FLOW_SCHEMA_VERSIONS:
            raise ValueError(
                "schema_version must be one of "
                f"{sorted(SUPPORTED_FLOW_SCHEMA_VERSIONS)}, got {self.schema_version}"
            )
        if self.platform != "macos":
            raise ValueError("platform must be macos for v1")
        if not self.name.strip():
            raise ValueError("flow.name must not be empty")
        _parse_datetime(self.created_at, "created_at")
        if self.app_context is not None:
            self.app_context.validate()
        if self.description is not None and not self.description.strip():
            raise ValueError("flow.description must not be empty")
        self.annotation.validate()
        input_ids: set[str] = set()
        for flow_input in self.inputs:
            flow_input.validate()
            if flow_input.id in input_ids:
                raise ValueError(f"duplicate flow input id: {flow_input.id}")
            input_ids.add(flow_input.id)
        seen_step_ids: set[str] = set()
        for step in self.steps:
            step.validate()
            if step.id in seen_step_ids:
                raise ValueError(f"duplicate step id: {step.id}")
            seen_step_ids.add(step.id)
            if (
                step.action == "type_text"
                and step.text_policy is not None
                and step.text_policy.mode == "parameterized"
                and step.text_policy.input_id not in input_ids
            ):
                raise ValueError(
                    "parameterized text step references unknown flow input: "
                    f"{step.text_policy.input_id}"
                )
