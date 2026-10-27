# -*- coding: utf-8 -*-
"""项目存储：每期一个 projects/project-NNN/ 目录，manifest.json 记录阶段状态。

目录内容：
    brief.json       创作指令
    music_map.json   音乐理解结果
    storyboard.json  镜头计划（导演输出）
    timeline.json    时间轴（切点/素材/转场）
    review.json      审片状态与意见
    manifest.json    阶段状态机（断点续跑的依据）
    clips/           逐镜头规范化渲染缓存
    output/          final.mp4 / cover.jpg / qc_report.json / upload_info.txt

阶段断点续跑：manifest.stages[stage] == "done" 即跳过；redo-shot 使
materials/render/qc 失效回到 draft，make 重跑时只重做受影响的部分。
"""
import re
import time
from pathlib import Path

from src.jsonio import load_json, save_json

ROOT = Path(__file__).resolve().parent.parent
MUSIC_DIR = ROOT / "music"
MATERIALS_DIR = ROOT / "materials"

STAGES = ("music_map", "storyboard", "timeline", "materials", "render", "qc")


class StoreError(RuntimeError):
    pass


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def projects_dir() -> Path:
    p = ROOT / "projects"
    p.mkdir(exist_ok=True)
    return p


def project_dir(pid: str) -> Path:
    pdir = projects_dir() / pid
    if not pdir.is_dir():
        raise StoreError(f"项目不存在: {pid}")
    return pdir


def new_project(brief: dict) -> dict:
    nums = []
    for d in projects_dir().iterdir():
        m = re.fullmatch(r"project-(\d+)", d.name)
        if m:
            nums.append(int(m.group(1)))
    pid = f"project-{max(nums, default=0) + 1:03d}"
    pdir = projects_dir() / pid
    (pdir / "clips").mkdir(parents=True, exist_ok=True)
    (pdir / "output").mkdir(exist_ok=True)
    save_json(pdir / "brief.json", brief)
    manifest = {"id": pid, "created_at": now(), "status": "draft", "stages": {}}
    save_json(pdir / "manifest.json", manifest)
    return manifest


def list_projects() -> list[dict]:
    out = []
    for d in sorted(projects_dir().iterdir()):
        mf = d / "manifest.json"
        if mf.exists():
            out.append(load_json(mf, {}))
    return out


def load_manifest(pid: str) -> dict:
    return load_json(project_dir(pid) / "manifest.json") or {}


def save_manifest(pid: str, manifest: dict) -> None:
    save_json(project_dir(pid) / "manifest.json", manifest)


def stage_done(manifest: dict, stage: str) -> bool:
    return manifest.get("stages", {}).get(stage, {}).get("status") == "done"


def mark_stage(pid: str, manifest: dict, stage: str, status: str = "done",
               info: dict | None = None) -> dict:
    manifest.setdefault("stages", {})[stage] = {"status": status, "at": now(),
                                                **(info or {})}
    save_manifest(pid, manifest)
    return manifest


def invalidate_stages(manifest: dict, stages: tuple[str, ...],
                      status: str = "stale") -> dict:
    """把指定阶段置为 stale（不存在也创建），下次 make 会重跑这些阶段。"""
    for s in stages:
        manifest.setdefault("stages", {})[s] = {"status": status, "at": now()}
    return manifest


def set_status(pid: str, manifest: dict, status: str) -> dict:
    manifest["status"] = status
    save_manifest(pid, manifest)
    return manifest
