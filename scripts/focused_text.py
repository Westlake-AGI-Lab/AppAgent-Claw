"""读取当前聚焦输入控件的文本值。"""

from __future__ import annotations

import math
import subprocess
import time
from typing import Any


def get_focused_text_value(
    app_name: str,
    *,
    subprocess_module: Any = subprocess,
) -> str | None:
    """读取指定前台应用当前聚焦输入控件的文本值。"""
    if not app_name.strip():
        raise ValueError("app_name must not be empty")

    escaped_name = _escape_applescript_string(app_name)
    script = f'''
    tell application "System Events"
        if not (exists process "{escaped_name}") then error "process not found"
        tell process "{escaped_name}"
            set focusedElement to value of attribute "AXFocusedUIElement"
            if focusedElement is missing value then error "focused element not found"
            try
                set rawValue to value of attribute "AXValue" of focusedElement
            on error
                error "focused element has no AXValue"
            end try
            if rawValue is missing value then
                return ""
            end if
            return rawValue as text
        end tell
    end tell
    '''
    result = subprocess_module.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "failed to get focused text value")
    return result.stdout.rstrip("\n")


def try_get_focused_text_value(
    app_name: str,
    *,
    subprocess_module: Any = subprocess,
) -> str | None:
    """尽量读取聚焦输入框文本；失败时返回 ``None``。"""
    try:
        return get_focused_text_value(app_name, subprocess_module=subprocess_module)
    except Exception:  # noqa: BLE001
        return None


def wait_for_focused_text_change(
    app_name: str,
    *,
    previous_text: str | None,
    focused_text_getter: Any = None,
    timeout_seconds: float = 0.35,
    poll_interval_seconds: float = 0.05,
    sleep: Any = time.sleep,
) -> str | None:
    """短时轮询聚焦输入框文本，直到观察到变化。"""
    if not app_name.strip():
        raise ValueError("app_name must not be empty")
    if timeout_seconds < 0:
        raise ValueError("timeout_seconds must be non-negative")
    if poll_interval_seconds <= 0:
        raise ValueError("poll_interval_seconds must be positive")

    text_getter = focused_text_getter or try_get_focused_text_value
    attempts = max(1, math.ceil(timeout_seconds / poll_interval_seconds)) + 1

    for attempt in range(attempts):
        try:
            latest_text = text_getter(app_name)
        except Exception:  # noqa: BLE001
            latest_text = None
        if latest_text is not None and latest_text != previous_text:
            return latest_text
        if attempt < attempts - 1:
            sleep(poll_interval_seconds)
    return None


def _escape_applescript_string(value: str) -> str:
    """对 AppleScript 字符串字面量做最小转义。"""
    return value.replace("\\", "\\\\").replace('"', '\\"')
