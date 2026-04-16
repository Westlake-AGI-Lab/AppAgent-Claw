"""录制完成后的 flow 标注与文本参数化分析。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from scripts.schema import (
    CLICK_ACTIONS,
    CURRENT_FLOW_SCHEMA_VERSION,
    FlowAnnotation,
    FlowDefinition,
    FlowInput,
    FlowStep,
    TextPolicy,
)
from scripts.storage import Storage


WAIT_LIKE_ACTIONS = {"wait"}
MESSAGE_APP_KEYWORDS = ("wechat", "messages", "discord", "slack", "telegram")
COMMENT_APP_KEYWORDS = ("neteasemusic", "music", "spotify")
SUBMIT_KEYS = {
    ("enter",),
    ("return",),
    ("command", "enter"),
    ("command", "return"),
}


@dataclass(slots=True)
class AnnotationRunResult:
    """一次录后标注的结果。"""

    recording_dir: Path
    flow_path: Path
    flow: FlowDefinition
    success: bool
    error_message: str | None = None


class HeuristicFlowAnnotator:
    """基于本地上下文的保守型 flow 标注器。"""

    def annotate(
        self,
        flow: FlowDefinition,
        *,
        recording_dir: str | Path,
    ) -> FlowDefinition:
        """为 flow 补充描述信息与可参数化文本槽位。"""
        del recording_dir
        inputs: list[FlowInput] = []
        steps: list[FlowStep] = []
        role_counters: dict[str, int] = {}

        for index, step in enumerate(flow.steps):
            text_policy = step.text_policy
            if step.action == "type_text":
                text_policy, flow_input = self._build_text_metadata(
                    flow=flow,
                    step=step,
                    index=index,
                    role_counters=role_counters,
                )
                if flow_input is not None:
                    inputs.append(flow_input)
            steps.append(
                replace(
                    step,
                    description=self._describe_step(
                        flow=flow,
                        step=step,
                        index=index,
                        text_policy=text_policy,
                    ),
                    text_policy=text_policy,
                )
            )

        parameterized_inputs = [flow_input for flow_input in inputs]
        return replace(
            flow,
            schema_version=CURRENT_FLOW_SCHEMA_VERSION,
            description=self._describe_flow(flow, inputs=parameterized_inputs),
            inputs=parameterized_inputs,
            steps=steps,
        )

    def _build_text_metadata(
        self,
        *,
        flow: FlowDefinition,
        step: FlowStep,
        index: int,
        role_counters: dict[str, int],
    ) -> tuple[TextPolicy, FlowInput | None]:
        app_name = _app_name(flow, step)
        next_index = _next_meaningful_step_index(flow.steps, index)
        next_step = flow.steps[next_index] if next_index is not None else None
        next_is_submit_hotkey = _is_submit_hotkey(next_step)
        next_is_click = next_step is not None and next_step.action in CLICK_ACTIONS
        looks_like_freeform = _looks_like_freeform_text(step.text or "")
        wait_after_submit = _wait_after(flow.steps, next_index)
        is_last_text_step = not any(
            candidate.action == "type_text" for candidate in flow.steps[index + 1 :]
        )

        score = 0
        if next_is_submit_hotkey:
            score += 3
        if next_is_click:
            score += 1
        if looks_like_freeform:
            score += 1
        if wait_after_submit >= 1.0:
            score += 1
        if is_last_text_step:
            score += 1
        if _app_matches(app_name, MESSAGE_APP_KEYWORDS) or _app_matches(
            app_name, COMMENT_APP_KEYWORDS
        ):
            score += 1

        if next_step is None:
            score += 1

        if score < 4:
            return (
                TextPolicy(
                    mode="fixed",
                    reason=(
                        "Keep the recorded text fixed because it likely affects "
                        "subsequent UI state or target resolution."
                    ),
                ),
                None,
            )

        semantic_role = _infer_semantic_role(app_name)
        role_counters[semantic_role] = role_counters.get(semantic_role, 0) + 1
        input_id = _build_input_id(semantic_role, role_counters[semantic_role])
        return (
            TextPolicy(
                mode="parameterized",
                input_id=input_id,
                reason=(
                    "This text looks like final freeform content that can be "
                    "provided by the calling agent at replay time."
                ),
            ),
            FlowInput(
                id=input_id,
                kind="text",
                semantic_role=semantic_role,
                description=_input_description(semantic_role, app_name=app_name),
                example_text=step.text,
            ),
        )

    def _describe_flow(
        self,
        flow: FlowDefinition,
        *,
        inputs: list[FlowInput],
    ) -> str:
        app_name = _app_name(flow, None)
        action_counts = _count_actions(flow.steps)
        action_summary = ", ".join(
            f"{count} {action}"
            for action, count in sorted(action_counts.items())
            if count > 0
        )
        if not action_summary:
            action_summary = "no recorded actions"

        if inputs:
            input_summary = "; ".join(
                f"{flow_input.id} ({flow_input.semantic_role})"
                for flow_input in inputs
            )
            parameterization_text = (
                f"Replay-time text inputs are available for: {input_summary}. "
                "Only those text slots may be changed; recorded click anchors and "
                "other fixed steps must stay unchanged."
            )
        else:
            parameterization_text = (
                "No safe replay-time text inputs were identified, so all recorded "
                "text remains fixed."
            )

        return (
            f"Recorded macOS foreground-app workflow for {app_name}. "
            f"The flow contains {len(flow.steps)} steps covering {action_summary}. "
            f"{parameterization_text}"
        )

    def _describe_step(
        self,
        *,
        flow: FlowDefinition,
        step: FlowStep,
        index: int,
        text_policy: TextPolicy | None,
    ) -> str:
        if step.action in CLICK_ACTIONS:
            if step.action == "long_press":
                return (
                    "Press and hold the recorded target using the saved anchor and "
                    "context images."
                )
            if step.action == "right_long_press":
                return (
                    "Press and hold the recorded target with the right mouse button "
                    "using the saved anchor and context images."
                )
            if _next_meaningful_step(flow.steps, index, action="type_text") is not None:
                return "Click the recorded target to focus the next input or UI state."
            if _previous_meaningful_step(flow.steps, index, action="type_text") is not None:
                return "Click the recorded target to submit or advance after text entry."
            return "Click the recorded target using the saved anchor and context images."

        if step.action == "type_text":
            if text_policy is not None and text_policy.mode == "parameterized":
                semantic_role = _semantic_role_label(text_policy.input_id)
                return (
                    "Type runtime-provided text into the focused input field "
                    f"for {semantic_role}."
                )
            return (
                "Type the recorded text into the focused input field and keep it "
                "unchanged for stable replay."
            )

        if step.action == "wait":
            seconds = step.seconds or 0.0
            return f"Wait {seconds:.2f} seconds for the app to settle before continuing."

        if step.action == "hotkey":
            keys = step.keys or ([step.key] if step.key else [])
            joined = " + ".join(keys) if keys else "recorded keys"
            return f"Send the recorded hotkey sequence: {joined}."

        if step.action == "scroll":
            return "Scroll the recorded viewport position to reveal the next target."

        if step.action == "move":
            return "Move the pointer to the recorded location."

        return f"Run the recorded {step.action} action."


def annotate_recording(
    *,
    target: str,
    storage: Storage | None = None,
    data_root: str = "data",
    annotator: HeuristicFlowAnnotator | None = None,
    source: str = "agent",
) -> AnnotationRunResult:
    """对已保存录制做一次录后标注，并回写 ``flow.json``。"""
    storage = storage or Storage(data_root)
    recording_dir, flow_path, flow = _resolve_target(storage, target)
    annotator = annotator or HeuristicFlowAnnotator()

    try:
        annotated_flow = annotator.annotate(flow, recording_dir=recording_dir)
        annotated_flow = replace(
            annotated_flow,
            schema_version=CURRENT_FLOW_SCHEMA_VERSION,
            annotation=FlowAnnotation(
                status="completed",
                source=source,
                analyzed_at=_utc_now_iso(),
            ),
        )
        saved_path = storage.save_flow(recording_dir, annotated_flow)
        return AnnotationRunResult(
            recording_dir=recording_dir,
            flow_path=saved_path,
            flow=annotated_flow,
            success=True,
        )
    except Exception as exc:  # noqa: BLE001
        failed_flow = replace(
            flow,
            schema_version=CURRENT_FLOW_SCHEMA_VERSION,
            annotation=FlowAnnotation(
                status="failed",
                source=source,
                analyzed_at=_utc_now_iso(),
                error_message=str(exc),
            ),
        )
        saved_path = storage.save_flow(recording_dir, failed_flow)
        return AnnotationRunResult(
            recording_dir=recording_dir,
            flow_path=saved_path,
            flow=failed_flow,
            success=False,
            error_message=str(exc),
        )


def _resolve_target(
    storage: Storage,
    target: str,
) -> tuple[Path, Path, FlowDefinition]:
    """按路径或 flow 名称找到录制目录与 flow。"""
    target_path = storage.resolve_input_path(target)
    if target_path.exists():
        flow_path = target_path / "flow.json" if target_path.is_dir() else target_path
        recording_dir = flow_path.parent
        return recording_dir, flow_path, storage.load_flow(flow_path)

    normalized_target = storage._safe_name(target, fallback="flow")
    if not storage.recordings_dir.exists():
        raise FileNotFoundError(
            f"recordings directory does not exist: {storage.recordings_dir}"
        )

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


def _app_name(flow: FlowDefinition, step: FlowStep | None) -> str:
    if step is not None and step.window_context is not None:
        return step.window_context.app_name
    if flow.app_context is not None and flow.app_context.foreground_app is not None:
        return flow.app_context.foreground_app
    return "the target app"


def _count_actions(steps: Iterable[FlowStep]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for step in steps:
        counts[step.action] = counts.get(step.action, 0) + 1
    return counts


def _next_meaningful_step_index(steps: list[FlowStep], index: int) -> int | None:
    for candidate_index in range(index + 1, len(steps)):
        if steps[candidate_index].action not in WAIT_LIKE_ACTIONS:
            return candidate_index
    return None


def _next_meaningful_step(
    steps: list[FlowStep],
    index: int,
    *,
    action: str,
) -> FlowStep | None:
    for candidate in steps[index + 1 :]:
        if candidate.action == action:
            return candidate
        if candidate.action not in WAIT_LIKE_ACTIONS:
            return None
    return None


def _previous_meaningful_step(
    steps: list[FlowStep],
    index: int,
    *,
    action: str,
) -> FlowStep | None:
    for candidate in reversed(steps[:index]):
        if candidate.action == action:
            return candidate
        if candidate.action not in WAIT_LIKE_ACTIONS:
            return None
    return None


def _wait_after(steps: list[FlowStep], index: int | None) -> float:
    if index is None:
        return 0.0
    total = 0.0
    for candidate in steps[index + 1 :]:
        if candidate.action != "wait":
            break
        total += candidate.seconds or 0.0
    return total


def _is_submit_hotkey(step: FlowStep | None) -> bool:
    if step is None:
        return False
    if step.action == "hotkey":
        keys = tuple((step.keys or ([step.key] if step.key else [])))
        return tuple(key.lower() for key in keys) in SUBMIT_KEYS
    return False


def _looks_like_freeform_text(text: str) -> bool:
    normalized = text.strip()
    if len(normalized) >= 8:
        return True
    if any(char in normalized for char in {" ", "!", "?", ".", ",", "。", "，", "！", "？"}):
        return True
    return False


def _app_matches(app_name: str, keywords: tuple[str, ...]) -> bool:
    normalized = app_name.casefold()
    return any(keyword in normalized for keyword in keywords)


def _infer_semantic_role(app_name: str) -> str:
    if _app_matches(app_name, MESSAGE_APP_KEYWORDS):
        return "message_body"
    if _app_matches(app_name, COMMENT_APP_KEYWORDS):
        return "comment_body"
    return "generic_text"


def _build_input_id(semantic_role: str, index: int) -> str:
    if semantic_role == "message_body":
        prefix = "input_message_body"
    elif semantic_role == "comment_body":
        prefix = "input_comment_body"
    else:
        prefix = "input_text"
    return f"{prefix}_{index:02d}"


def _input_description(semantic_role: str, *, app_name: str) -> str:
    if semantic_role == "message_body":
        return f"Message body to send in {app_name} during replay."
    if semantic_role == "comment_body":
        return f"Comment body to submit in {app_name} during replay."
    return f"Replay-time text content for {app_name}."


def _semantic_role_label(input_id: str | None) -> str:
    if input_id is None:
        return "the parameterized slot"
    if input_id.startswith("input_message_body_"):
        return "the message body"
    if input_id.startswith("input_comment_body_"):
        return "the comment body"
    if input_id.startswith("input_text_"):
        return "the recorded text slot"
    return input_id


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
