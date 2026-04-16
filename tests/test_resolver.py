from pathlib import Path

from PIL import Image, ImageDraw

from scripts.capture import ScreenCapture
from scripts.resolver import Resolver, TemplateResolutionStrategy
from scripts.schema import (
    FlowStep,
    Locator,
    RetryPolicy,
    SearchRegion,
    Timing,
    Validation,
)


class FakeShot:
    def __init__(self, image: Image.Image) -> None:
        self.size = image.size
        self.rgb = image.tobytes()


class FakeMSSClient:
    def __init__(self, monitor: dict[str, int], image: Image.Image) -> None:
        self.monitors = [monitor, monitor]
        self._monitor = monitor
        self._image = image

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None

    def grab(self, region: dict[str, int]) -> FakeShot:
        local_left = region["left"] - self._monitor["left"]
        local_top = region["top"] - self._monitor["top"]
        local_right = local_left + region["width"]
        local_bottom = local_top + region["height"]
        cropped = self._image.crop((local_left, local_top, local_right, local_bottom))
        return FakeShot(cropped)


def build_capture(tmp_path: Path) -> tuple[ScreenCapture, FlowStep, Path]:
    monitor = {"left": 100, "top": 50, "width": 400, "height": 300}
    click_x = 260
    click_y = 170
    image = Image.new("RGB", (monitor["width"], monitor["height"]), color=(235, 235, 235))
    draw = ImageDraw.Draw(image)
    local_x = click_x - monitor["left"]
    local_y = click_y - monitor["top"]
    draw.rectangle((20, 20, 70, 55), fill=(50, 50, 50))
    draw.rectangle((local_x - 22, local_y - 22, local_x + 22, local_y + 22), fill=(20, 110, 220))
    draw.line((local_x - 22, local_y - 22, local_x + 22, local_y + 22), fill=(255, 255, 255), width=4)
    draw.line((local_x - 22, local_y + 22, local_x + 22, local_y - 22), fill=(255, 230, 0), width=4)
    draw.ellipse((local_x - 10, local_y - 10, local_x + 10, local_y + 10), fill=(220, 50, 50))
    draw.rectangle((local_x + 30, local_y - 12, local_x + 48, local_y + 12), fill=(20, 160, 80))
    capture = ScreenCapture(mss_factory=lambda: FakeMSSClient(monitor, image))
    captured = capture.capture_click(click_x, click_y)

    recording_dir = tmp_path / "recording"
    asset_dir = recording_dir / "assets" / "step_0001"
    asset_dir.mkdir(parents=True)
    captured.anchor_image.save(asset_dir / "anchor.png")
    captured.context_image.save(asset_dir / "context.png")

    step = FlowStep(
        id="step_0001",
        action="click",
        monitor=captured.monitor,
        target=captured.target,
        locator=Locator(
            anchor_image="assets/step_0001/anchor.png",
            context_image="assets/step_0001/context.png",
            search_region=captured.search_region,
            match_threshold=0.92,
        ),
        timing=Timing(pre_delay_ms=0, post_delay_ms=0),
        retry=RetryPolicy(max_attempts=2, fallback_to_relative=True),
        validation=Validation(mode="none"),
    )
    return capture, step, recording_dir


def test_resolver_prefers_anchor_match_in_search_region(tmp_path) -> None:
    capture, step, recording_dir = build_capture(tmp_path)
    resolver = Resolver(flow_dir=recording_dir, capture=capture)

    result = resolver.resolve(step)

    assert result.success is True
    assert result.strategy == "anchor"
    assert result.x == step.target.abs_x
    assert result.y == step.target.abs_y


def test_resolver_falls_back_to_context_match_on_monitor(tmp_path) -> None:
    capture, step, recording_dir = build_capture(tmp_path)
    resolver = Resolver(flow_dir=recording_dir, capture=capture)
    step.locator.search_region = SearchRegion(left=100, top=50, width=40, height=40)

    result = resolver.resolve(step)

    assert result.success is True
    assert result.strategy == "context"
    assert result.x == step.target.abs_x
    assert result.y == step.target.abs_y


def test_resolver_uses_relative_coordinates_as_last_fallback(tmp_path) -> None:
    capture, step, recording_dir = build_capture(tmp_path)
    resolver = Resolver(flow_dir=recording_dir, capture=capture)
    Image.new("RGB", (96, 96), color="black").save(recording_dir / step.locator.anchor_image)
    Image.new("RGB", (240, 160), color="black").save(recording_dir / step.locator.context_image)

    result = resolver.resolve(step)

    assert result.success is True
    assert result.strategy == "relative"
    assert result.used_fallback is True
    assert result.x == step.target.abs_x
    assert result.y == step.target.abs_y


def test_resolver_skips_low_information_anchor_and_uses_context(tmp_path) -> None:
    capture, step, recording_dir = build_capture(tmp_path)
    resolver = Resolver(flow_dir=recording_dir, capture=capture)
    Image.new("RGB", (96, 96), color=(248, 249, 252)).save(recording_dir / step.locator.anchor_image)

    result = resolver.resolve(step)

    assert result.success is True
    assert result.strategy == "context"
    assert result.attempts[0]["strategy"] == "anchor"
    assert result.attempts[0]["skipped"] is True
    assert result.attempts[0]["reason"] == "low_information_template"


def test_resolver_uses_relative_when_all_templates_are_low_information(tmp_path) -> None:
    capture, step, recording_dir = build_capture(tmp_path)
    resolver = Resolver(flow_dir=recording_dir, capture=capture)
    Image.new("RGB", (96, 96), color=(248, 249, 252)).save(recording_dir / step.locator.anchor_image)
    Image.new("RGB", (240, 160), color=(247, 249, 252)).save(recording_dir / step.locator.context_image)

    result = resolver.resolve(step)

    assert result.success is True
    assert result.strategy == "relative"
    assert result.used_fallback is True
    assert result.attempts[0]["reason"] == "low_information_template"
    assert result.attempts[1]["reason"] == "low_information_template"
    assert result.attempts[2]["strategy"] == "relative"


def test_resolver_writes_debug_artifacts_only_for_final_failure(tmp_path) -> None:
    capture, step, recording_dir = build_capture(tmp_path)
    resolver = Resolver(flow_dir=recording_dir, capture=capture)
    anchor_image = Image.new("RGB", (96, 96), color=(20, 20, 20))
    anchor_draw = ImageDraw.Draw(anchor_image)
    anchor_draw.line((0, 0, 95, 95), fill=(255, 255, 0), width=6)
    anchor_draw.line((0, 95, 95, 0), fill=(0, 255, 255), width=6)
    anchor_image.save(recording_dir / step.locator.anchor_image)

    context_image = Image.new("RGB", (240, 160), color=(40, 40, 40))
    context_draw = ImageDraw.Draw(context_image)
    context_draw.rectangle((20, 20, 220, 140), outline=(255, 0, 255), width=8)
    context_draw.line((20, 80, 220, 80), fill=(255, 255, 255), width=6)
    context_image.save(recording_dir / step.locator.context_image)
    step.retry = RetryPolicy(max_attempts=1, fallback_to_relative=False)
    debug_dir = tmp_path / "run" / "steps" / step.id / "attempt_1"

    result = resolver.resolve(step, debug_dir=debug_dir)

    assert result.success is False
    assert result.error_code == "target_not_found"
    assert (debug_dir / "search_region.png").is_file()
    assert (debug_dir / "match_debug.png").is_file()
    assert (debug_dir / "monitor_region.png").is_file()
    assert (debug_dir / "context_match_debug.png").is_file()


def test_resolver_can_be_extended_by_overriding_template_strategies(tmp_path) -> None:
    capture, step, recording_dir = build_capture(tmp_path)

    class CustomResolver(Resolver):
        def template_strategies(self, step: FlowStep) -> list[TemplateResolutionStrategy]:
            return [
                TemplateResolutionStrategy(
                    name="custom_context",
                    image_path=step.locator.context_image,
                    search_region=SearchRegion(
                        left=step.monitor.left,
                        top=step.monitor.top,
                        width=step.monitor.width,
                        height=step.monitor.height,
                    ),
                    capture_name="custom_region",
                    match_name="custom_match_debug",
                )
            ]

    resolver = CustomResolver(flow_dir=recording_dir, capture=capture)

    result = resolver.resolve(step)

    assert result.success is True
    assert result.strategy == "custom_context"
