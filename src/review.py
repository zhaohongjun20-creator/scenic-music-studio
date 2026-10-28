# -*- coding: utf-8 -*-
"""人工审片闸门：明确声明 + 成片/时间轴/QC 的版本绑定。

QC 不是人工审美验收。只有用户本人完整观看具体 SHA256 对应的成片，
逐项确认清单并提供 reviewer 标识，才可 approve。本地记录是人工自声明，
不是身份认证；程序不会替用户观看、签审或上传。
"""
import hashlib
import json
import re
import time
import uuid
from pathlib import Path

from src.jsonio import load_json, save_json
from src.store import StoreError, invalidate_stages, project_dir, save_manifest

REVIEW_FILE = "review.json"
REVIEW_SCHEMA_VERSION = 1
CHECKLIST_ITEMS = ("opening", "climax", "cuts", "transitions", "color", "ending")
WATCHED_FULL_DECLARATION = "我已完整观看本次 SHA256 对应的成片，并亲自确认六项审片清单。"
_BINDING_FILES = {
    "final_sha256": "output/final.mp4",
    "timeline_sha256": "timeline.json",
    "qc_sha256": "output/qc_report.json",
}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _path(pid: str) -> Path:
    return project_dir(pid) / REVIEW_FILE


def load_review(pid: str) -> dict:
    review = load_json(_path(pid))
    if not isinstance(review, dict):
        return {"status": "draft", "comments": [], "history": []}
    review.setdefault("status", "draft")
    for key in ("comments", "history"):
        if not isinstance(review.get(key), list):
            review[key] = []
    return review


def save_review(pid: str, review: dict) -> None:
    save_json(_path(pid), review)


def _save_review_state(pid: str, review: dict) -> None:
    """所有状态转换同步 manifest，撤回上轮发布物料（不会上传）。"""
    (project_dir(pid) / "output" / "upload_info.txt").unlink(missing_ok=True)
    save_review(pid, review)
    manifest = load_json(project_dir(pid) / "manifest.json") or {}
    manifest["status"] = review["status"]
    save_manifest(pid, manifest)


def _stat_key(stat) -> tuple:
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns,
            stat.st_ctime_ns)


def _read_artifact(path: Path, *, parse_json: bool = False) -> tuple[str, object]:
    """流式哈希大视频；JSON 的解析和哈希使用同一份字节。"""
    try:
        before = path.stat()
        if not path.is_file() or before.st_size == 0:
            raise StoreError(f"审片文件缺失或为空: {path.name}")
        digest = hashlib.sha256()
        chunks = []
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
                if parse_json:
                    chunks.append(chunk)
        if _stat_key(before) != _stat_key(path.stat()):
            raise StoreError(f"审片文件在校验期间发生变化: {path.name}")
        data = json.loads(b"".join(chunks)) if parse_json else None
        return digest.hexdigest(), data
    except (OSError, ValueError, UnicodeError) as exc:
        raise StoreError(f"无法校验审片文件 {path.name}: {exc}") from exc


def _current_binding(pid: str) -> dict:
    pdir = project_dir(pid)
    binding = {}
    for key, filename in _BINDING_FILES.items():
        digest, data = _read_artifact(pdir / filename, parse_json=key != "final_sha256")
        if key == "timeline_sha256" and (not isinstance(data, dict) or not data):
            raise StoreError("缺少有效 timeline.json，需重新 make")
        if key == "qc_sha256" and (
                not isinstance(data, dict) or data.get("passed") is not True
                or data.get("problems") != []):
            raise StoreError("QC 报告未通过或不完整，需重新 make")
        binding[key] = digest
    return binding


def _validate_attestation(reviewer, checklist, watched_full, final_sha256) -> str:
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise StoreError("人工审片必须提供 reviewer 身份标识")
    if watched_full is not True:
        raise StoreError("必须由人工明确声明已完整观看本次成片（watched_full=True）")
    if (not isinstance(checklist, dict) or set(checklist) != set(CHECKLIST_ITEMS)
            or any(checklist[key] is not True for key in CHECKLIST_ITEMS)):
        raise StoreError("必须逐项人工确认六项清单: " + ", ".join(CHECKLIST_ITEMS))
    if not isinstance(final_sha256, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", final_sha256):
        raise StoreError("必须明确提供已完整观看成片的 64 位 SHA256（final_sha256）")
    return final_sha256.lower()


def _validate_render_binding(pid: str, review: dict) -> dict:
    manifest = load_json(project_dir(pid) / "manifest.json")
    if not isinstance(manifest, dict):
        raise StoreError("项目 manifest 缺失或损坏，需重新 make 和人工审片")
    stages = manifest.get("stages", {})
    if (manifest.get("status") != review.get("status")
            or not isinstance(stages, dict)
            or any(not isinstance(stage, dict) or stage.get("status") != "done"
                   for stage in stages.values())):
        raise StoreError("项目处于重建、失效或失败状态，需重新 make 和人工审片")
    if (review.get("schema_version") != REVIEW_SCHEMA_VERSION
            or not isinstance(review.get("render_id"), str) or not review["render_id"]
            or not isinstance(review.get("binding"), dict)):
        raise StoreError("审片记录缺少当前版本绑定（旧审批不可继承），需重新 make 和人工审片")
    binding = _current_binding(pid)
    if review["binding"] != binding:
        raise StoreError("成片、timeline 或 QC 已变更，旧审批不可继承；需重新 make 和人工审片")
    return binding


def invalidate_review(pid: str, note: str = "") -> dict:
    """在开始重建时撤销旧审片，防止失败后沿用未改写的旧产物。"""
    review = load_review(pid)
    review["status"] = "draft"
    for key in ("approval", "binding", "render_id", "integrity_error"):
        review.pop(key, None)
    review["history"].append({"at": _now(), "action": "invalidate", "note": note})
    _save_review_state(pid, review)
    return review


def mark_rendered(pid: str, qc_passed: bool, problems: list[str]) -> dict:
    """保持 pipeline 原调用签名；新一轮渲染始终撤销旧审批。

    缺文件的 mock/失败流水线返回 draft 和 integrity_error，而不是抛错阻塞
    不涉及审片的单测。只有真实存在且完整绑定的产物才能进入 review。
    """
    review = load_review(pid)
    review.update({"schema_version": REVIEW_SCHEMA_VERSION, "status": "draft",
                   "render_id": uuid.uuid4().hex, "binding": None})
    review.pop("approval", None)
    review.pop("integrity_error", None)
    if qc_passed is True and not problems:
        try:
            review["binding"] = _current_binding(pid)
            review["status"] = "review"
        except StoreError as exc:
            review["integrity_error"] = str(exc)
    else:
        review["comments"] = [{"at": _now(), "kind": "qc",
                               "note": "；".join(problems[:5])}]
    review["history"].append(
        {"at": _now(), "action": "rendered", "qc_passed": qc_passed,
         "render_id": review["render_id"], "binding": review["binding"],
         "status": review["status"]})
    _save_review_state(pid, review)
    return review


def approve(pid: str, note: str = "", *, reviewer: str = "",
            checklist: dict[str, bool] | None = None, watched_full: bool = False,
            final_sha256: str = "") -> dict:
    """记录用户本人的签审；禁止用默认值/旧签审自动放行。

    checklist 必须正好包含 CHECKLIST_ITEMS 的六个键且每值严格为 True。
    final_sha256 必须由调用者明确传入，不从当前文件自动代填观看声明。
    """
    final_sha256 = _validate_attestation(reviewer, checklist, watched_full, final_sha256)
    review = load_review(pid)
    if review["status"] not in ("review", "ready"):
        raise StoreError(f"当前状态 {review['status']} 不可放行（需先 make 出片过 QC）")
    binding = _validate_render_binding(pid, review)
    if final_sha256 != binding["final_sha256"]:
        raise StoreError("声明观看的成片 SHA256 与当前版本不一致，不能放行")
    approval = {
        "schema_version": REVIEW_SCHEMA_VERSION, "id": uuid.uuid4().hex,
        "at": _now(), "render_id": review["render_id"], "binding": dict(binding),
        "reviewer": reviewer.strip(), "checklist": dict(checklist),
        "watched_full": True, "declaration": WATCHED_FULL_DECLARATION,
        "final_sha256": final_sha256, "note": note,
    }
    review["status"] = "ready"
    review["approval"] = approval
    review["history"].append({"action": "approve", **approval})
    _save_review_state(pid, review)
    return review


def validate_approval(pid: str) -> dict:
    """发布前重新读取并复核人工声明及三个文件；失败抛 StoreError。"""
    review = load_review(pid)
    if review["status"] != "ready":
        raise StoreError(f"成片未过审（status={review['status']}），先人工 approve")
    approval = review.get("approval")
    if (not isinstance(approval, dict)
            or approval.get("schema_version") != REVIEW_SCHEMA_VERSION
            or not isinstance(approval.get("id"), str) or not approval["id"]
            or not isinstance(approval.get("at"), str) or not approval["at"]
            or approval.get("declaration") != WATCHED_FULL_DECLARATION):
        raise StoreError("缺少完整人工审批记录（旧审批不可继承），需重新人工审片")
    final_sha256 = _validate_attestation(
        approval.get("reviewer"), approval.get("checklist"),
        approval.get("watched_full"), approval.get("final_sha256"))
    binding = _validate_render_binding(pid, review)
    if (approval.get("render_id") != review["render_id"]
            or approval.get("binding") != binding
            or final_sha256 != binding["final_sha256"]):
        raise StoreError("人工审批与当前渲染版本不一致，旧审批不可继承")
    return review


def reject(pid: str, note: str) -> dict:
    if not note.strip():
        raise StoreError("打回必须附意见（--note），否则下一轮无从改起")
    review = load_review(pid)
    review["status"] = "rejected"
    review.pop("approval", None)
    review["history"].append({"at": _now(), "action": "reject", "note": note})
    _save_review_state(pid, review)
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
    review.pop("approval", None)
    review.pop("binding", None)
    review.pop("render_id", None)
    review["history"].append({"at": _now(), "action": "redo-shot", "shot": shot_index,
                              "note": note})
    _save_review_state(pid, review)
    return review
