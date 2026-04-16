"""读取 macOS 前台窗口标题与几何信息。"""

from __future__ import annotations

import subprocess
from typing import Any

from scripts.schema import WindowContext

_WINDOW_SELECTORS: tuple[tuple[str, str], ...] = (
    (
        "focused",
        """
        set targetWindow to missing value
        try
            set targetWindow to value of attribute "AXFocusedWindow"
        end try
        """.strip(),
    ),
    (
        "main",
        """
        set targetWindow to missing value
        try
            set targetWindow to value of attribute "AXMainWindow"
        end try
        """.strip(),
    ),
    (
        "first",
        """
        if (count of windows) is 0 then error "window not found"
        set targetWindow to window 1
        """.strip(),
    ),
)


def get_window_context(
    app_name: str,
    *,
    subprocess_module: Any = subprocess,
) -> WindowContext:
    """读取指定应用窗口的标题、位置和大小。"""
    if not app_name.strip():
        raise ValueError("app_name must not be empty")

    last_error: str | None = None
    for _selector_name, selector_script in _WINDOW_SELECTORS:
        result = subprocess_module.run(
            ["osascript", "-e", _build_window_context_script(app_name, selector_script)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            last_error = result.stderr.strip() or "failed to get window context"
            continue
        title, position, size = _parse_window_context_output(result.stdout)
        x_str, y_str = _parse_pair(position, "window position")
        width_str, height_str = _parse_pair(size, "window size")
        return WindowContext(
            app_name=app_name,
            window_title=title or None,
            x=int(x_str),
            y=int(y_str),
            width=int(width_str),
            height=int(height_str),
        )
    raise RuntimeError(last_error or "failed to get window context")


def try_get_window_context(
    app_name: str,
    *,
    subprocess_module: Any = subprocess,
) -> WindowContext | None:
    """尽量读取窗口信息；失败时返回 ``None``。"""
    try:
        return get_window_context(app_name, subprocess_module=subprocess_module)
    except Exception:  # noqa: BLE001
        return None


def _build_window_context_script(app_name: str, selector_script: str) -> str:
    """按给定窗口选择器构造 AppleScript。"""
    escaped_name = _escape_applescript_string(app_name)
    return f'''
    tell application "System Events"
        if not (exists process "{escaped_name}") then error "process not found"
        tell process "{escaped_name}"
            {selector_script}
            if targetWindow is missing value then error "window not found"
            set titleText to ""
            try
                set rawTitle to value of attribute "AXTitle" of targetWindow
                if rawTitle is not missing value then
                    set titleText to rawTitle as text
                end if
            end try
            set windowPosition to position of targetWindow
            set windowSize to size of targetWindow
            return titleText & linefeed & (item 1 of windowPosition as text) & "," & (item 2 of windowPosition as text) & linefeed & (item 1 of windowSize as text) & "," & (item 2 of windowSize as text)
        end tell
    end tell
    '''


def _parse_window_context_output(output: str) -> tuple[str, str, str]:
    """解析 AppleScript 返回的标题、位置和大小三段信息。"""
    lines = output.rstrip("\n").splitlines()
    if len(lines) != 3:
        raise RuntimeError(f"invalid window context output: {output!r}")
    return lines[0], lines[1], lines[2]


def _parse_pair(output: str, field_name: str) -> tuple[str, str]:
    """解析两个逗号分隔值。"""
    parts = [part.strip() for part in output.split(",") if part.strip()]
    if len(parts) != 2:
        raise RuntimeError(f"invalid {field_name} output: {output!r}")
    return parts[0], parts[1]


def _escape_applescript_string(value: str) -> str:
    """对 AppleScript 字符串字面量做最小转义。"""
    return value.replace("\\", "\\\\").replace('"', '\\"')
