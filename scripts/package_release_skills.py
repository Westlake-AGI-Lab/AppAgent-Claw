#!/usr/bin/env python3
from __future__ import annotations

import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
SKILL_CREATOR = Path("/Applications/ClawX.app/Contents/Resources/openclaw/skills/skill-creator/scripts/package_skill.py")

BUNDLES = [
    {
        "kind": "zip",
        "source": ROOT / ".codex/skills/appagent-claw",
        "artifact": DIST / "appagent-claw-codex.zip",
        "stage_name": "appagent-claw-codex",
        "include_agents": True,
    },
    {
        "kind": "zip",
        "source": ROOT / ".claude/skills/appagent-claw",
        "artifact": DIST / "appagent-claw-claude-code.zip",
        "stage_name": "appagent-claw-claude-code",
        "include_agents": False,
    },
    {
        "kind": "openclaw",
        "source": ROOT / ".openclaw/skills/appagent-claw",
        "artifact": DIST / "appagent-claw-openclaw.zip",
        "stage_name": "appagent-claw",
        "include_agents": False,
    },
]

COPY_DIRS = ["scripts", "references", "assets"]
COPY_FILES = ["SKILL.md", ".gitignore"]


def reset_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def copy_tree(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst, dirs_exist_ok=True)


def clean_tree(root: Path) -> None:
    for pattern in ["**/__pycache__", "**/*.pyc", ".venv"]:
        for path in root.glob(pattern):
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()


def stage_bundle(source: Path, dest: Path, *, include_agents: bool) -> None:
    if not source.exists():
        raise FileNotFoundError(f"Missing source bundle: {source}")

    dest.mkdir(parents=True, exist_ok=True)

    for name in COPY_FILES:
        src = source / name
        if src.exists():
            shutil.copy2(src, dest / name)

    for name in COPY_DIRS:
        src = source / name
        if src.exists():
            copy_tree(src, dest / name)

    if include_agents:
        src = source / "agents"
        if src.exists():
            copy_tree(src, dest / "agents")

    (dest / "data/recordings").mkdir(parents=True, exist_ok=True)
    (dest / "data/runs").mkdir(parents=True, exist_ok=True)
    (dest / "data/recordings/.gitkeep").touch()
    (dest / "data/runs/.gitkeep").touch()

    clean_tree(dest)


def zip_dir(source_dir: Path, artifact: Path) -> None:
    if artifact.exists():
        artifact.unlink()
    with zipfile.ZipFile(artifact, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(source_dir.rglob("*")):
            if file.is_file():
                zf.write(file, str(Path(source_dir.name) / file.relative_to(source_dir)))


def package_openclaw(staged_skill_dir: Path, artifact: Path) -> None:
    if artifact.exists():
        artifact.unlink()
    subprocess.run(
        ["python3", str(SKILL_CREATOR), str(staged_skill_dir), str(DIST)],
        check=True,
        cwd=str(ROOT),
    )
    generated = DIST / f"{staged_skill_dir.name}.skill"
    if not generated.exists():
        raise FileNotFoundError(f"Expected generated skill not found: {generated}")
    if artifact.exists():
        artifact.unlink()
    generated.rename(artifact)


def main() -> None:
    DIST.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="appagent-claw-release-") as tmp:
        tmp_path = Path(tmp)
        for bundle in BUNDLES:
            stage_root = tmp_path / bundle["stage_name"]
            if bundle["kind"] == "openclaw":
                stage_parent = tmp_path / "openclaw"
                staged_skill_dir = stage_parent / bundle["stage_name"]
                reset_dir(stage_parent)
                stage_bundle(bundle["source"], staged_skill_dir, include_agents=bundle["include_agents"])
                package_openclaw(staged_skill_dir, bundle["artifact"])
            else:
                reset_dir(stage_root)
                stage_bundle(bundle["source"], stage_root, include_agents=bundle["include_agents"])
                zip_dir(stage_root, bundle["artifact"])
            size = bundle["artifact"].stat().st_size
            print(f"[OK] {bundle['artifact'].name} ({size} bytes)")


if __name__ == "__main__":
    main()
