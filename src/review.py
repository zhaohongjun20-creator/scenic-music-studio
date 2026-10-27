# -*- coding: utf-8 -*-
"""审片：人工放行闸门 + 局部重做。

状态流：draft →（make 完成 + QC 通过）review →（人工 approve）ready
                                ↘（人工 reject）rejected
设计文档的硬约束：没有明确发布指令不投稿——publisher 只在 ready 时
生成上传物料，上传动作永远由人完成。

redo-shot：把某个镜头标记重做（revision+1、当前素材进排除名单、
删片段缓存），make 重跑时素材与渲染阶段自动只重做受影响部分。
"""
import time
from pathlib import Path

from src.jsonio import load_json, save_json
from src.store import StoreError, invalidate_stages, project_dir, save_manifest

REVIEW_FILE = "review.json"


def _path(pid: str) -> Path:
    return project_dir(pid) / REVIEW_FILE


def load_review(pid: str) -> dict:
    return load_json(_path(pid), {"status": "draft", "comments": [], "history": []})


def save_review(pid: str, review: dict) -> None:
    save_json(_path(pid), review)


def mark_rendered(pid: str, qc_passed: bool, problems: list[str]) -> dict:
    """make 完成后调用：QC 过 → review 等人放行；不过 → 留在 draft。"""
    review = load_review(pid)
    review["status"] = "review" if qc_passed else "draft"
    if not qc_passed:
        review["comments"] = [{"at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                               "kind": "qc", "note": "；".join(problems[:5])}]
    review["history"].append(
        {"at": time.strftime("%Y-%m-%dT%H:%M:%S"),
         "action": "rendered", "qc_passed": qc_passed})
    save_review(pid, review)
    return review


def approve(pid: str, note: str = "") -> dict:
    review = load_review(pid)
    if review["status"] not in ("review", "ready"):
        raise StoreError(f"当前状态 {review['status']} 不可放行（需先 make 出片过 QC）")
    review["status"] = "ready"
    review["history"].append({"at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                              "action": "approve", "note": note})
    save_review(pid, review)
    return review


def reject(pid: str, note: str) -> dict:
    if not note.strip():
        raise StoreError("打回必须附意见（--note），否则下一轮无从改起")
    review = load_review(pid)
    review["status"] = "rejected"
    review["history"].append({"at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                              "action": "reject", "note": note})
    save_review(pid, review)
    return review


def redo_shot(pid: str, shot_index: int, note: str = "") -> dict:
    """局部重做：只失效该镜头的素材与渲染，其余阶段缓存全部保留。"""
    pdir = project_dir(pid)
    timeline = load_json(pdir / "timeline.json")
    if not timeline:
        raise StoreError("还没有 timeline.json，先跑 make")
    shots = timeline["shots"]
    if not (0 <= shot_index < len(shots)):
        raise StoreError(f"镜头序号 {shot_index} 越界（共 {len(shots)} 个）")
    shot = shots[shot_index]
    current = (shot.get("asset") or {}).get("id")
    if current:
        shot.setdefault("exclude_assets", []).append(current)
    shot["revision"] = int(shot.get("revision", 0)) + 1
    if note:
        shot["redo_note"] = note
    save_json(pdir / "timeline.json", timeline)

    for old in pdir.glob(f'clips/s{shot_index:02d}_*.mp4'):
        old.unlink(missing_ok=True)

    manifest = load_json(pdir / "manifest.json") or {}
    invalidate_stages(manifest, ("materials", "render", "qc"))
    save_manifest(pid, manifest)

    review = load_review(pid)
    review["status"] = "draft"
    review["history"].append({"at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                              "action": "redo-shot", "shot": shot_index,
                              "note": note})
    save_review(pid, review)
    return review
