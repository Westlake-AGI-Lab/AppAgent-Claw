from PIL import Image

from scripts.capture import ScreenCapture


class FakeShot:
    def __init__(self, image: Image.Image) -> None:
        self.size = image.size
        self.rgb = image.tobytes()


class FakeMSSClient:
    def __init__(self, monitor: dict[str, int], image: Image.Image) -> None:
        self.monitors = [
            monitor,
            monitor,
        ]
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
        return FakeShot(self._image.crop((local_left, local_top, local_right, local_bottom)))


def build_capture(monitor: dict[str, int]) -> ScreenCapture:
    image = Image.new("RGB", (monitor["width"], monitor["height"]), color=(10, 20, 30))
    return ScreenCapture(mss_factory=lambda: FakeMSSClient(monitor, image))


def test_capture_click_builds_locator_assets_and_target() -> None:
    monitor = {"left": 100, "top": 50, "width": 800, "height": 600}
    capture = build_capture(monitor)

    result = capture.capture_click(500, 300)

    assert result.monitor.id == 1
    assert result.target.abs_x == 500
    assert result.target.abs_y == 300
    assert result.target.rel_x == 0.5
    assert result.target.rel_y == (250 / 600)
    assert result.anchor_image.size == (96, 96)
    assert result.context_image.size == (240, 160)
    assert result.search_region.left == 284
    assert result.search_region.top == 124
    assert result.search_region.width == 432
    assert result.search_region.height == 352


def test_capture_click_clamps_context_and_search_region_near_edges() -> None:
    monitor = {"left": 100, "top": 50, "width": 200, "height": 120}
    capture = build_capture(monitor)

    result = capture.capture_click(105, 52)

    assert result.target.rel_x == 0.025
    assert result.target.rel_y == (2 / 120)
    assert result.anchor_image.size == (96, 96)
    assert result.context_image.size == (200, 120)
    assert result.search_region.left == 100
    assert result.search_region.top == 50
    assert result.search_region.width == 200
    assert result.search_region.height == 120


def test_capture_click_normalizes_fractional_coordinates_to_integer_pixels() -> None:
    monitor = {"left": 100, "top": 50, "width": 800, "height": 600}
    capture = build_capture(monitor)

    result = capture.capture_click(500.6, 300.4)

    assert result.target.abs_x == 501
    assert result.target.abs_y == 300
    assert isinstance(result.target.abs_x, int)
    assert isinstance(result.target.abs_y, int)
    assert isinstance(result.search_region.left, int)
    assert isinstance(result.search_region.top, int)


def test_capture_click_from_snapshot_uses_final_release_point() -> None:
    monitor = {"left": 100, "top": 50, "width": 400, "height": 300}
    image = Image.new("RGB", (monitor["width"], monitor["height"]), color=(10, 20, 30))
    image.putpixel((50, 60), (255, 0, 0))
    image.putpixel((70, 80), (0, 255, 0))
    capture = ScreenCapture(mss_factory=lambda: FakeMSSClient(monitor, image))

    snapshot = capture.capture_monitor_snapshot(150, 110)
    result = capture.capture_click_from_snapshot(snapshot, 170, 130)

    assert snapshot.monitor.left == 100
    assert result.target.abs_x == 170
    assert result.target.abs_y == 130
    assert result.target.rel_x == (70 / 400)
    assert result.target.rel_y == (80 / 300)
    assert result.anchor_image.getpixel((48, 48)) == (0, 255, 0)
