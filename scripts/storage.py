"""流程、素材与运行产物的持久化辅助。"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.schema import FlowDefinition


class Storage:
    """管理 ``data/`` 目录下的录制与回放文件布局。"""

    def __init__(self, root: str | Path = "data") -> None:
        """初始化存储根目录。"""
        self.root = self._resolve_root(root)

    @property
    def recordings_dir(self) -> Path:
        """返回正式录制结果目录。"""
        return self.root / "recordings"

    @property
    def runs_dir(self) -> Path:
        """返回回放调试与运行结果目录。"""
        return self.root / "runs"

    @property
    def recording_sessions_dir(self) -> Path:
        """返回录制中间态会话目录。"""
        return self.runs_dir / "recording_sessions"

    @property
    def active_session_path(self) -> Path:
        """返回当前活动录制会话清单路径。"""
        return self.recording_sessions_dir / "active_session.json"

    def create_recording(self, name: str) -> Path:
        """创建并返回新的录制目录。"""
        recording_dir = self._unique_dir(
            self.recordings_dir,
            f"{self._recording_timestamp()}_{self._safe_name(name, fallback='flow')}",
        )
        recording_dir.mkdir(parents=True, exist_ok=False)
        (recording_dir / "assets").mkdir(exist_ok=True)
        return recording_dir

    def create_run(self, name: str) -> Path:
        """创建并返回新的运行目录。"""
        run_dir = self._unique_dir(
            self.runs_dir,
            f"{self._run_timestamp()}_{self._safe_name(name, fallback='run')}",
        )
        run_dir.mkdir(parents=True, exist_ok=False)
        return run_dir

    def create_recording_session(self, name: str, session_id: str) -> Path:
        """创建录制中的临时会话目录。"""
        self.recording_sessions_dir.mkdir(parents=True, exist_ok=True)
        session_dir = self._unique_dir(
            self.recording_sessions_dir,
            f"{session_id}_{self._safe_name(name, fallback='flow')}",
        )
        session_dir.mkdir(parents=True, exist_ok=False)
        (session_dir / "assets").mkdir(exist_ok=True)
        return session_dir

    def step_asset_dir(self, recording_dir: str | Path, step_id: str) -> Path:
        """创建并返回指定步骤的素材目录。"""
        asset_dir = Path(recording_dir) / "assets" / step_id
        asset_dir.mkdir(parents=True, exist_ok=True)
        return asset_dir

    def save_flow(self, recording_dir: str | Path, flow: FlowDefinition) -> Path:
        """校验流程并将 ``flow.json`` 写入录制目录。"""
        flow.validate()
        recording_dir = Path(recording_dir)
        recording_dir.mkdir(parents=True, exist_ok=True)
        flow_path = recording_dir / "flow.json"
        with flow_path.open("w", encoding="utf-8") as handle:
            json.dump(flow.to_dict(), handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        return flow_path

    def load_flow(self, flow_path_or_recording_dir: str | Path) -> FlowDefinition:
        """从录制目录或 ``flow.json`` 文件加载流程定义。"""
        flow_path = Path(flow_path_or_recording_dir)
        if flow_path.is_dir():
            flow_path = flow_path / "flow.json"
        with flow_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return FlowDefinition.from_dict(payload)

    def write_run_json(self, run_dir: str | Path, payload: dict[str, Any]) -> Path:
        """将运行结果字典写入 ``run.json``。"""
        run_dir = Path(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        run_path = run_dir / "run.json"
        with run_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        return run_path

    def save_step_images(
        self,
        recording_dir: str | Path,
        step_id: str,
        *,
        anchor_image: Any,
        context_image: Any,
    ) -> tuple[str, str]:
        """保存步骤截图资源并返回相对路径。"""
        asset_dir = self.step_asset_dir(recording_dir, step_id)
        anchor_path = asset_dir / "anchor.png"
        context_path = asset_dir / "context.png"
        anchor_image.save(anchor_path)
        context_image.save(context_path)
        root_dir = Path(recording_dir)
        return (
            anchor_path.relative_to(root_dir).as_posix(),
            context_path.relative_to(root_dir).as_posix(),
        )

    def promote_recording_session(self, session_dir: str | Path, name: str) -> Path:
        """把临时录制会话提升为正式录制目录。"""
        self.recordings_dir.mkdir(parents=True, exist_ok=True)
        session_dir = Path(session_dir)
        target_dir = self._unique_dir(
            self.recordings_dir,
            f"{self._recording_timestamp()}_{self._safe_name(name, fallback='flow')}",
        )
        shutil.move(str(session_dir), str(target_dir))
        return target_dir

    def delete_recording_session(self, session_dir: str | Path) -> None:
        """删除临时录制会话目录。"""
        shutil.rmtree(Path(session_dir), ignore_errors=True)

    def write_active_session(self, payload: dict[str, Any]) -> Path:
        """写入当前活动录制会话清单。"""
        return self._write_json(self.active_session_path, payload)

    def load_active_session(self) -> dict[str, Any] | None:
        """读取当前活动录制会话；不存在时返回 ``None``。"""
        if not self.active_session_path.exists():
            return None
        return self._read_json(self.active_session_path)

    def resolve_input_path(self, path: str | Path) -> Path:
        """解析用户传入的路径，优先尊重 cwd，其次回退到项目根目录。"""
        candidate = Path(path).expanduser()
        if candidate.is_absolute():
            return candidate
        if candidate.exists():
            return candidate.resolve()
        return (self.root.parent / candidate).resolve()

    def clear_active_session(self) -> None:
        """删除当前活动录制会话清单。"""
        self.active_session_path.unlink(missing_ok=True)

    def session_ready_path(self, session_id: str) -> Path:
        """返回指定会话的 ready 标记路径。"""
        return self.recording_sessions_dir / f"{session_id}.ready.json"

    def session_result_path(self, session_id: str) -> Path:
        """返回指定会话的结果文件路径。"""
        return self.recording_sessions_dir / f"{session_id}.result.json"

    def write_session_ready(self, session_id: str, payload: dict[str, Any]) -> Path:
        """写入录制 worker 已就绪标记。"""
        return self._write_json(self.session_ready_path(session_id), payload)

    def write_session_result(self, session_id: str, payload: dict[str, Any]) -> Path:
        """写入录制 worker 最终结果。"""
        return self._write_json(self.session_result_path(session_id), payload)

    def load_session_result(self, session_id: str) -> dict[str, Any] | None:
        """读取指定会话的最终结果。"""
        result_path = self.session_result_path(session_id)
        if not result_path.exists():
            return None
        return self._read_json(result_path)

    @staticmethod
    def _recording_timestamp() -> str:
        """生成录制目录使用的本地时间戳。"""
        return datetime.now().strftime("%Y%m%d_%H%M%S")

    @staticmethod
    def _run_timestamp() -> str:
        """生成运行目录使用的 UTC 时间戳。"""
        return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    @staticmethod
    def _safe_name(name: str, fallback: str) -> str:
        """将流程名归一化为适合目录命名的短字符串。"""
        normalized = re.sub(r"\s+", "-", name.strip())
        normalized = re.sub(r"[^\w-]+", "-", normalized, flags=re.UNICODE)
        normalized = re.sub(r"-{2,}", "-", normalized).strip("-_")
        return normalized or fallback

    @staticmethod
    def _unique_dir(parent: Path, base_name: str) -> Path:
        """在同秒重名时生成一个唯一目录名。"""
        candidate = parent / base_name
        if not candidate.exists():
            return candidate

        suffix = 2
        while True:
            candidate = parent / f"{base_name}_{suffix}"
            if not candidate.exists():
                return candidate
            suffix += 1

    def _write_json(self, path: Path, payload: dict[str, Any]) -> Path:
        """写入 UTF-8 JSON 文件。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        return path

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        """读取 UTF-8 JSON 文件。"""
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    @classmethod
    def _resolve_root(cls, root: str | Path) -> Path:
        """把相对 ``data_root`` 解析到当前脚本所在项目根目录。"""
        path = Path(root).expanduser()
        if path.is_absolute():
            return path
        return (Path(__file__).resolve().parents[1] / path).resolve()
