"""通过模板匹配与坐标兜底解析步骤目标。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw

from scripts.capture import ScreenCapture
from scripts.schema import CLICK_ACTIONS, FlowStep, SearchRegion

LOW_INFORMATION_GRAYSCALE_STD_THRESHOLD = 4.0
LOW_INFORMATION_CHANNEL_RANGE_THRESHOLD = 16


@dataclass(slots=True)
class TemplateMatchResult:
    """单次模板匹配的结果。"""

    matched: bool
    score: float
    threshold: float
    top_left_x: int | None = None
    top_left_y: int | None = None
    center_x: int | None = None
    center_y: int | None = None
    debug_paths: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """转换为可序列化字典。"""
        return {
            "matched": self.matched,
            "score": self.score,
            "threshold": self.threshold,
            "top_left_x": self.top_left_x,
            "top_left_y": self.top_left_y,
            "center_x": self.center_x,
            "center_y": self.center_y,
            "debug_paths": self.debug_paths,
        }


@dataclass(slots=True)
class ResolveResult:
    """点击类步骤的最终定位结果。"""

    success: bool
    x: int | None = None
    y: int | None = None
    strategy: str | None = None
    match_score: float | None = None
    used_fallback: bool = False
    attempts: list[dict[str, Any]] = field(default_factory=list)
    debug_paths: dict[str, str] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """转换为可序列化字典。"""
        return {
            "success": self.success,
            "x": self.x,
            "y": self.y,
            "strategy": self.strategy,
            "match_score": self.match_score,
            "used_fallback": self.used_fallback,
            "attempts": self.attempts,
            "debug_paths": self.debug_paths,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }


@dataclass(slots=True)
class TemplateResolutionStrategy:
    """一次模板定位策略的输入定义。"""

    name: str
    image_path: str
    search_region: SearchRegion
    capture_name: str
    match_name: str

    def to_attempt_dict(self, match: TemplateMatchResult) -> dict[str, Any]:
        """转换为 resolve 结果里记录的 attempt 结构。"""
        return {
            "strategy": self.name,
            "search_region": _region_to_dict(self.search_region),
            "match": match.to_dict(),
        }


class Resolver:
    """负责在回放时找到步骤应该执行的位置。"""

    def __init__(
        self,
        *,
        flow_dir: str | Path,
        capture: ScreenCapture,
        before_capture: Any = None,
        after_capture: Any = None,
        cv2_module: Any = cv2,
        image_module: Any = Image,
    ) -> None:
        """初始化流程根目录、截图能力和图像库依赖。"""
        self._flow_dir = Path(flow_dir)
        self._capture = capture
        self._before_capture = before_capture or (lambda: None)
        self._after_capture = after_capture or (lambda: None)
        self._cv2 = cv2_module
        self._image_module = image_module

    def resolve(
        self,
        step: FlowStep,
        *,
        debug: bool = False,
        debug_dir: str | Path | None = None,
        save_failure_debug: bool = True,
    ) -> ResolveResult:
        """解析点击类步骤的最终执行坐标。"""
        if step.action not in CLICK_ACTIONS:
            return ResolveResult(
                success=False,
                error_code="unsupported_action",
                error_message=f"resolver only supports click actions, got {step.action}",
            )
        if step.monitor is None or step.target is None or step.locator is None:
            return ResolveResult(
                success=False,
                error_code="invalid_step",
                error_message=f"{step.action} step is missing locator metadata",
            )

        attempts: list[dict[str, Any]] = []
        debug_paths: dict[str, str] = {}
        debug_path = Path(debug_dir) if debug_dir is not None else None
        threshold = step.locator.match_threshold
        strategies = self.template_strategies(step)
        strategies, skipped_attempts = self._filter_low_information_strategies(strategies)
        attempts.extend(skipped_attempts)
        last_match_score: float | None = None

        for strategy in strategies:
            try:
                match = self._run_template_strategy(
                    strategy,
                    threshold=threshold,
                    debug=debug,
                    debug_dir=debug_path,
                    save_on_failure=False,
                )
            except Exception as exc:  # noqa: BLE001
                return ResolveResult(
                    success=False,
                    attempts=attempts,
                    debug_paths=debug_paths,
                    error_code="resolve_failed",
                    error_message=str(exc),
                )
            attempts.append(strategy.to_attempt_dict(match))
            debug_paths.update(_prefix_debug_paths(match.debug_paths, strategy.name))
            last_match_score = match.score
            if match.matched:
                return ResolveResult(
                    success=True,
                    x=match.center_x,
                    y=match.center_y,
                    strategy=strategy.name,
                    match_score=match.score,
                    attempts=attempts,
                    debug_paths=debug_paths,
                )

        fallback_result = self.resolve_relative_fallback(step, last_match_score=last_match_score)
        if fallback_result is not None:
            attempts.append(
                {
                    "strategy": "relative",
                    "target": {"x": fallback_result.x, "y": fallback_result.y},
                }
            )
            fallback_result.attempts = attempts
            fallback_result.debug_paths = debug_paths
            return fallback_result

        if save_failure_debug and not debug and debug_path is not None:
            attempts, debug_paths = self._save_failure_debug_for_strategies(
                strategies,
                threshold=threshold,
                debug_dir=debug_path,
                attempts=attempts,
                debug_paths=debug_paths,
            )

        return ResolveResult(
            success=False,
            attempts=attempts,
            debug_paths=debug_paths,
            error_code="target_not_found",
            error_message="failed to match anchor and relative fallback is disabled",
        )

    def template_strategies(self, step: FlowStep) -> list[TemplateResolutionStrategy]:
        """返回当前步骤默认启用的模板定位策略链。"""
        if step.monitor is None or step.locator is None:
            raise ValueError("template strategies require monitor and locator metadata")
        monitor_region = SearchRegion(
            left=step.monitor.left,
            top=step.monitor.top,
            width=step.monitor.width,
            height=step.monitor.height,
        )
        return [
            TemplateResolutionStrategy(
                name="anchor",
                image_path=step.locator.anchor_image,
                search_region=step.locator.search_region,
                capture_name="search_region",
                match_name="match_debug",
            ),
            TemplateResolutionStrategy(
                name="context",
                image_path=step.locator.context_image,
                search_region=monitor_region,
                capture_name="monitor_region",
                match_name="context_match_debug",
            ),
        ]

    def _filter_low_information_strategies(
        self,
        strategies: list[TemplateResolutionStrategy],
    ) -> tuple[list[TemplateResolutionStrategy], list[dict[str, Any]]]:
        """跳过近似纯色的模板图，避免无效模板匹配污染结果。"""
        filtered: list[TemplateResolutionStrategy] = []
        skipped_attempts: list[dict[str, Any]] = []

        for strategy in strategies:
            template_path = self._flow_dir / strategy.image_path
            if not template_path.exists():
                filtered.append(strategy)
                continue
            with self._image_module.open(template_path) as template_handle:
                template_image = template_handle.convert("RGB")
            is_low_information, stats = self._is_low_information_image(template_image)
            if not is_low_information:
                filtered.append(strategy)
                continue
            skipped_attempts.append(
                {
                    "strategy": strategy.name,
                    "skipped": True,
                    "reason": "low_information_template",
                    "template": {
                        "image_path": strategy.image_path,
                        "grayscale_std": stats["grayscale_std"],
                        "channel_ranges": stats["channel_ranges"],
                    },
                }
            )

        return filtered, skipped_attempts

    def resolve_relative_fallback(
        self,
        step: FlowStep,
        *,
        last_match_score: float | None,
    ) -> ResolveResult | None:
        """按需要返回相对坐标兜底结果。"""
        if step.retry is None or not step.retry.fallback_to_relative:
            return None
        fallback_x, fallback_y = self._relative_target(step)
        return ResolveResult(
            success=True,
            x=fallback_x,
            y=fallback_y,
            strategy="relative",
            match_score=last_match_score,
            used_fallback=True,
        )

    def _run_template_strategy(
        self,
        strategy: TemplateResolutionStrategy,
        *,
        threshold: float,
        debug: bool,
        debug_dir: Path | None,
        save_on_failure: bool,
    ) -> TemplateMatchResult:
        """执行一次模板定位策略。"""
        return self.locate_image(
            strategy.image_path,
            strategy.search_region,
            threshold=threshold,
            debug=debug,
            debug_dir=debug_dir,
            capture_name=strategy.capture_name,
            match_name=strategy.match_name,
            save_on_failure=save_on_failure,
        )

    def _save_failure_debug_for_strategies(
        self,
        strategies: list[TemplateResolutionStrategy],
        *,
        threshold: float,
        debug_dir: Path,
        attempts: list[dict[str, Any]],
        debug_paths: dict[str, str],
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        """仅在最终失败时为全部模板策略补落调试图。"""
        refreshed_attempts = list(attempts)
        refreshed_debug_paths = dict(debug_paths)
        for index, strategy in enumerate(strategies):
            match = self._run_template_strategy(
                strategy,
                threshold=threshold,
                debug=False,
                debug_dir=debug_dir,
                save_on_failure=True,
            )
            refreshed_attempts[index]["match"] = match.to_dict()
            refreshed_debug_paths.update(_prefix_debug_paths(match.debug_paths, strategy.name))
        return refreshed_attempts, refreshed_debug_paths

    def locate_image(
        self,
        image_path: str,
        search_region: SearchRegion,
        *,
        threshold: float,
        debug: bool = False,
        debug_dir: str | Path | None = None,
        capture_name: str = "search_region",
        match_name: str = "match_debug",
        save_on_failure: bool = False,
    ) -> TemplateMatchResult:
        """在给定搜索区域中查找模板图片。"""
        template_path = self._flow_dir / image_path
        if not template_path.exists():
            raise FileNotFoundError(f"template image not found: {template_path}")

        with self._image_module.open(template_path) as template_handle:
            template_image = template_handle.convert("RGB")
        self._before_capture()
        try:
            haystack_image = self._capture.capture_region(search_region)
        finally:
            self._after_capture()
        matched, score, top_left = self._match_template(
            haystack_image=haystack_image,
            template_image=template_image,
            threshold=threshold,
        )

        center_x = None
        center_y = None
        top_left_x = None
        top_left_y = None
        if top_left is not None:
            top_left_x = search_region.left + top_left[0]
            top_left_y = search_region.top + top_left[1]
            center_x = top_left_x + (template_image.width // 2)
            center_y = top_left_y + (template_image.height // 2)
        if not matched:
            center_x = None
            center_y = None

        debug_paths: dict[str, str] = {}
        should_save = debug or (save_on_failure and not matched)
        if should_save and debug_dir is not None:
            debug_paths = self._save_debug_artifacts(
                haystack_image=haystack_image,
                template_size=template_image.size,
                local_top_left=top_left,
                debug_dir=Path(debug_dir),
                capture_name=capture_name,
                match_name=match_name,
            )

        return TemplateMatchResult(
            matched=matched,
            score=score,
            threshold=threshold,
            top_left_x=top_left_x,
            top_left_y=top_left_y,
            center_x=center_x,
            center_y=center_y,
            debug_paths=debug_paths,
        )

    def _match_template(
        self,
        *,
        haystack_image: Image.Image,
        template_image: Image.Image,
        threshold: float,
    ) -> tuple[bool, float, tuple[int, int] | None]:
        """执行一次标准化模板匹配。"""
        haystack = self._to_grayscale_array(haystack_image)
        template = self._to_grayscale_array(template_image)

        if (
            haystack.shape[0] < template.shape[0]
            or haystack.shape[1] < template.shape[1]
        ):
            return False, 0.0, None

        if float(np.std(template)) == 0.0 or float(np.std(haystack)) == 0.0:
            match_output = self._cv2.matchTemplate(
                haystack,
                template,
                self._cv2.TM_SQDIFF_NORMED,
            )
            min_val, _max_val, min_loc, _max_loc = self._cv2.minMaxLoc(match_output)
            score = 1.0 - float(min_val)
            return score >= threshold, score, (int(min_loc[0]), int(min_loc[1]))

        match_output = self._cv2.matchTemplate(
            haystack,
            template,
            self._cv2.TM_CCOEFF_NORMED,
        )
        _min_val, max_val, _min_loc, max_loc = self._cv2.minMaxLoc(match_output)
        score = float(max_val)
        return score >= threshold, score, (int(max_loc[0]), int(max_loc[1]))

    def _is_low_information_image(
        self,
        image: Image.Image,
    ) -> tuple[bool, dict[str, Any]]:
        """判断模板图是否过于接近纯色，不适合参与模板匹配。"""
        rgb = np.array(image.convert("RGB"), dtype=np.uint8)
        grayscale = self._to_grayscale_array(image)
        channel_ranges = [int(np.ptp(rgb[:, :, index])) for index in range(rgb.shape[2])]
        grayscale_std = round(float(np.std(grayscale)), 4)
        is_low_information = (
            grayscale_std <= LOW_INFORMATION_GRAYSCALE_STD_THRESHOLD
            and max(channel_ranges, default=0) <= LOW_INFORMATION_CHANNEL_RANGE_THRESHOLD
        )
        return is_low_information, {
            "grayscale_std": grayscale_std,
            "channel_ranges": channel_ranges,
        }

    @staticmethod
    def _to_grayscale_array(image: Image.Image) -> np.ndarray:
        """把 PIL 图片转成 OpenCV 灰度数组。"""
        return np.array(image.convert("L"))

    def _save_debug_artifacts(
        self,
        *,
        haystack_image: Image.Image,
        template_size: tuple[int, int],
        local_top_left: tuple[int, int] | None,
        debug_dir: Path,
        capture_name: str,
        match_name: str,
    ) -> dict[str, str]:
        """保存搜索区域截图与匹配标注图。"""
        debug_dir.mkdir(parents=True, exist_ok=True)
        capture_path = debug_dir / f"{capture_name}.png"
        haystack_image.save(capture_path)

        annotated = haystack_image.copy()
        if local_top_left is not None:
            draw = ImageDraw.Draw(annotated)
            left, top = local_top_left
            width, height = template_size
            draw.rectangle(
                (left, top, left + width, top + height),
                outline=(255, 0, 0),
                width=2,
            )
        match_path = debug_dir / f"{match_name}.png"
        annotated.save(match_path)
        return {
            capture_name: capture_path.relative_to(debug_dir).as_posix(),
            match_name: match_path.relative_to(debug_dir).as_posix(),
        }

    @staticmethod
    def _relative_target(step: FlowStep) -> tuple[int, int]:
        """按录制 monitor 的相对坐标计算兜底坐标。"""
        if step.monitor is None or step.target is None:
            raise ValueError("relative target requires monitor and target")
        max_local_x = max(step.monitor.width - 1, 0)
        max_local_y = max(step.monitor.height - 1, 0)
        local_x = min(max(int(round(step.target.rel_x * step.monitor.width)), 0), max_local_x)
        local_y = min(max(int(round(step.target.rel_y * step.monitor.height)), 0), max_local_y)
        return step.monitor.left + local_x, step.monitor.top + local_y


def _region_to_dict(region: SearchRegion) -> dict[str, int]:
    """把搜索区域转换为普通字典。"""
    return {
        "left": region.left,
        "top": region.top,
        "width": region.width,
        "height": region.height,
    }


def _prefix_debug_paths(paths: dict[str, str], prefix: str) -> dict[str, str]:
    """避免多次匹配时的 debug key 冲突。"""
    return {f"{prefix}_{key}": value for key, value in paths.items()}
