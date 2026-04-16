"""录制与回放共用的截图辅助。"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from typing import Any

import mss
from PIL import Image

from scripts.schema import MonitorInfo, SearchRegion, Target


@dataclass(slots=True)
class CaptureConfig:
    """截图尺寸与搜索区域扩展配置。"""

    anchor_width: int = 96
    anchor_height: int = 96
    context_width: int = 240
    context_height: int = 160
    search_padding: int = 96


@dataclass(slots=True)
class CapturedClick:
    """点击步骤需要的截图与几何信息。"""

    monitor: MonitorInfo
    target: Target
    search_region: SearchRegion
    anchor_image: Image.Image
    context_image: Image.Image


@dataclass(slots=True)
class MonitorSnapshot:
    """点击按下时缓存的整张监视器截图。"""

    monitor: MonitorInfo
    monitor_image: Image.Image


class ScreenCapture:
    """负责截取锚点图、上下文图和临时匹配区域。"""

    def __init__(
        self,
        *,
        mss_factory: Any = mss.mss,
        image_module: Any = Image,
        config: CaptureConfig | None = None,
    ) -> None:
        """初始化截图依赖与裁剪配置。"""
        self._mss_factory = mss_factory
        self._image_module = image_module
        self._config = config or CaptureConfig()

    def capture_click(self, x: int, y: int) -> CapturedClick:
        """为点击类步骤截取 anchor、context，并计算定位元数据。"""
        x = _normalize_pixel(x)
        y = _normalize_pixel(y)
        snapshot = self.capture_monitor_snapshot(x, y)
        return self.capture_click_from_snapshot(snapshot, x, y)

    def capture_monitor_snapshot(self, x: int, y: int) -> MonitorSnapshot:
        """缓存给定点所在监视器的整张截图，供稍后按最终点击点裁图。"""
        x = _normalize_pixel(x)
        y = _normalize_pixel(y)
        with self._open_client() as client:
            monitor = self._find_monitor_for_point(client.monitors, x, y)
            monitor_image = self._grab_region(client, monitor)
        return MonitorSnapshot(
            monitor=monitor,
            monitor_image=monitor_image,
        )

    def capture_click_from_snapshot(
        self,
        snapshot: MonitorSnapshot,
        x: int,
        y: int,
    ) -> CapturedClick:
        """基于已缓存的整张监视器图，按最终点击点裁切步骤素材。"""
        x = _normalize_pixel(x)
        y = _normalize_pixel(y)
        monitor = snapshot.monitor
        if not (
            monitor.left <= x < monitor.left + monitor.width
            and monitor.top <= y < monitor.top + monitor.height
        ):
            raise ValueError("point is outside cached monitor snapshot")
        target = self._build_target(monitor, x, y)

        local_x = x - monitor.left
        local_y = y - monitor.top
        anchor_box = self._centered_box(
            local_x,
            local_y,
            monitor.width,
            monitor.height,
            self._config.anchor_width,
            self._config.anchor_height,
        )
        context_box = self._centered_box(
            local_x,
            local_y,
            monitor.width,
            monitor.height,
            self._config.context_width,
            self._config.context_height,
        )
        anchor_image = snapshot.monitor_image.crop(anchor_box)
        context_image = snapshot.monitor_image.crop(context_box)
        search_region = self._expand_box(
            monitor,
            context_box,
            padding=self._config.search_padding,
        )
        return CapturedClick(
            monitor=monitor,
            target=target,
            search_region=search_region,
            anchor_image=anchor_image,
            context_image=context_image,
        )

    def describe_point(self, x: int, y: int) -> tuple[MonitorInfo, Target]:
        """返回给定坐标所在屏幕及其相对坐标。"""
        x = _normalize_pixel(x)
        y = _normalize_pixel(y)
        with self._open_client() as client:
            monitor = self._find_monitor_for_point(client.monitors, x, y)
        return monitor, self._build_target(monitor, x, y)

    def capture_region(self, region: SearchRegion) -> Image.Image:
        """按绝对屏幕区域截图并返回内存图片。"""
        with self._open_client() as client:
            return self._grab_region(client, region)

    def _build_target(self, monitor: MonitorInfo, x: int, y: int) -> Target:
        """构造步骤目标的绝对与相对坐标。"""
        rel_x = (x - monitor.left) / monitor.width
        rel_y = (y - monitor.top) / monitor.height
        return Target(abs_x=x, abs_y=y, rel_x=rel_x, rel_y=rel_y)

    def _grab_region(
        self,
        client: Any,
        region: MonitorInfo | SearchRegion,
    ) -> Image.Image:
        """从 mss 客户端抓取指定区域并转成 PIL 图片。"""
        shot = client.grab(
            {
                "left": region.left,
                "top": region.top,
                "width": region.width,
                "height": region.height,
            }
        )
        return self._image_module.frombytes("RGB", shot.size, shot.rgb)

    @staticmethod
    def _find_monitor_for_point(monitors: list[dict[str, int]], x: int, y: int) -> MonitorInfo:
        """找到包含目标坐标的 monitor。"""
        for index, monitor in enumerate(monitors[1:], start=1):
            left = int(monitor["left"])
            top = int(monitor["top"])
            width = int(monitor["width"])
            height = int(monitor["height"])
            if left <= x < left + width and top <= y < top + height:
                return MonitorInfo(
                    id=index,
                    left=left,
                    top=top,
                    width=width,
                    height=height,
                )
        raise ValueError(f"point ({x}, {y}) is outside available monitors")

    @staticmethod
    def _centered_box(
        center_x: int,
        center_y: int,
        max_width: int,
        max_height: int,
        desired_width: int,
        desired_height: int,
    ) -> tuple[int, int, int, int]:
        """围绕点击点计算裁剪区域，并保证结果不越界。"""
        width = min(desired_width, max_width)
        height = min(desired_height, max_height)
        left = center_x - (width // 2)
        top = center_y - (height // 2)
        left = max(0, min(left, max_width - width))
        top = max(0, min(top, max_height - height))
        return left, top, left + width, top + height

    @staticmethod
    def _expand_box(
        monitor: MonitorInfo,
        box: tuple[int, int, int, int],
        *,
        padding: int,
    ) -> SearchRegion:
        """把局部 box 扩展成用于回放的搜索区域。"""
        left, top, right, bottom = box
        left = max(0, left - padding)
        top = max(0, top - padding)
        right = min(monitor.width, right + padding)
        bottom = min(monitor.height, bottom + padding)
        return SearchRegion(
            left=monitor.left + left,
            top=monitor.top + top,
            width=right - left,
            height=bottom - top,
        )

    def _open_client(self) -> "_MSSClientContext":
        """统一处理 mss 客户端的生命周期。"""
        return _MSSClientContext(self._mss_factory)


class _MSSClientContext:
    """兼容上下文管理器与普通对象的 mss 包装。"""

    def __init__(self, factory: Any) -> None:
        self._factory = factory
        self._resource: Any = None
        self._client: Any = None

    def __enter__(self) -> Any:
        self._resource = self._factory()
        if hasattr(self._resource, "__enter__"):
            self._client = self._resource.__enter__()
        else:
            self._client = self._resource
        return self._client

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        if self._resource is None:
            return
        if hasattr(self._resource, "__exit__"):
            self._resource.__exit__(exc_type, exc, tb)
            return
        with suppress(AttributeError):
            self._resource.close()


def _normalize_pixel(value: int | float) -> int:
    """把事件坐标规范化为整数像素。"""
    return int(round(value))
