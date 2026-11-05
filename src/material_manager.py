# -*- coding: utf-8 -*-
"""素材管理：镜头检索词 → Pexels 实拍素材的检索、下载与建档。

- 全局缓存 materials/cache/{id}.mp4，跨项目复用，已下载不重下；
- materials/index.json 记录每个用过的素材：来源页/作者/许可/规格/
  被哪些镜头使用——发布争议时可追溯；
- 相邻 N 个镜头不重复用同一素材；redo-shot 时被换掉的素材进入该镜头
  的排除名单，不会再次选中；
- 接受 Pexels 候选之前先看预览帧：按 motion_hint 选入点，并在入点附近
  拒掉有人的画面。被拒的候选不下载 mp4，直接试下一条；
- offline 模式生成本地占位视频（testsrc2 + 按检索词哈希调色），
  全链可离线跑通，用于开发与测试，不消耗 API 额度。
"""
import hashlib
import json
import os
import subprocess
import time
from collections import deque
from pathlib import Path

import requests

from src.audio_io import ffmpeg_exe, ffprobe_exe
from src.jsonio import load_json, save_json
from src.shot_window import (choose_src_in, clamp_src_in, detector_available,
                             frame_near, legacy_src_in, load_preview_samples,
                             resolve_people_detector)
from src.store import MATERIALS_DIR, StoreError, project_dir

PEXELS_SEARCH = "https://api.pexels.com/videos/search"
CACHE_DIR = MATERIALS_DIR / "cache"
INDEX_PATH = MATERIALS_DIR / "index.json"


class MaterialError(RuntimeError):
    pass


# ---------------------------------------------------------------- 探测

def _fps_of(ratio: str) -> float:
    try:
        num, den = str(ratio).split("/")
        den_f = float(den) or 1.0
        return round(float(num) / den_f, 3)
    except (ValueError, ZeroDivisionError):
        return 0.0


def probe_media(path) -> dict:
    """ffprobe（优先）或 ffmpeg -i 解析 {duration,width,height,fps}。"""
    path = str(path)
    fp = ffprobe_exe()
    if fp:
        r = subprocess.run(
            [fp, "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", path],
            capture_output=True, text=True, timeout=120)
        if r.returncode == 0:
            try:
                data = json.loads(r.stdout or "{}")
                fmt = data.get("format", {})
                vs = next((s for s in data.get("streams", [])
                           if s.get("codec_type") == "video"), {})
                dur = fmt.get("duration") or vs.get("duration") or 0
                return {"duration": float(dur),
                        "width": int(vs.get("width", 0)),
                        "height": int(vs.get("height", 0)),
                        "fps": _fps_of(vs.get("r_frame_rate", "0/1"))}
            except (ValueError, StopIteration):
                pass
    import re
    r = subprocess.run([ffmpeg_exe(), "-i", path], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=120)
    err = r.stderr
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err)
    dur = (int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))) if m else 0.0
    vm = re.search(r"Video:.*?,\s*(\d{3,5})x(\d{3,5})", err)
    fm = re.search(r"(\d+(?:\.\d+)?)\s*fps", err)
    return {"duration": dur,
            "width": int(vm.group(1)) if vm else 0,
            "height": int(vm.group(2)) if vm else 0,
            "fps": float(fm.group(1)) if fm else 0.0}


# ---------------------------------------------------------------- Pexels

def _search(api_key: str, query: str, per_page: int) -> dict:
    r = requests.get(
        PEXELS_SEARCH,
        headers={"Authorization": api_key},
        params={"query": query, "per_page": per_page, "orientation": "landscape"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def _pick_files(video_files: list, max_height: int) -> list[dict]:
    """画质从高到低排序的 mp4 直链列表（排除 HLS）；失败时逐级降画质重试。"""
    mp4s = [f for f in video_files
            if f.get("file_type") == "video/mp4"
            and 0 < f.get("height", 0) <= max_height]
    return sorted(mp4s, key=lambda f: (f["height"], f.get("width", 0)),
                  reverse=True)


def _candidates(api_result: dict, need_s: float, min_src_s: float,
                max_height: int) -> list[dict]:
    out = []
    for v in api_result.get("videos", []):
        if not v.get("width", 0) > v.get("height", 0):
            continue
        if v.get("duration", 0) < min_src_s:
            continue
        files = _pick_files(v.get("video_files", []), max_height)
        if files:
            out.append({"video": v, "files": files})
    out.sort(key=lambda c: (abs(c["video"].get("duration", 0) - (need_s + 2)),
                            -c["files"][0].get("height", 0)))
    return out


def _download(link: str, dest: Path, retries: int = 3) -> None:
    """断点续传下载：先写 .part，Range 续传，完成后原子改名成正式缓存。

    大文件（几十上百 MB）在网络抖动下几乎必然遇到 IncompleteRead——
    半截文件绝不能留在缓存里被当成完整素材。
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    last_err: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            headers = {}
            mode = "wb"
            if part.exists() and part.stat().st_size > 0:
                headers["Range"] = f"bytes={part.stat().st_size}-"
                mode = "ab"
            with requests.get(link, stream=True, timeout=(15, 180),
                              headers=headers) as r:
                if r.status_code == 200 and mode == "ab":
                    mode = "wb"                 # 服务器不支持 Range，整体重下
                r.raise_for_status()
                with open(part, mode) as f:
                    for chunk in r.iter_content(chunk_size=1 << 20):
                        f.write(chunk)
            os.replace(part, dest)
            return
        except requests.RequestException as e:
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    part.unlink(missing_ok=True)
    raise MaterialError(f"下载失败（重试 {retries} 次）: {link} ({last_err})")


# ---------------------------------------------------------------- 素材对象

def _src_in(duration: float, need: float) -> float:
    """没有运动样本时的入点：素材前 1/3，剩余不够就贴尾部。"""
    return legacy_src_in(duration, need)


def _storyboard_hints(pid: str) -> list[str]:
    """时间轴还没带上 motion_hint 时，按镜头顺序回读导演计划。"""
    try:
        data = load_json(project_dir(pid) / "storyboard.json", {}) or {}
    except (StoreError, OSError, TypeError, ValueError):
        return []
    hints = []
    for section in data.get("sections") or []:
        if not isinstance(section, dict):
            continue
        for shot in section.get("shots") or []:
            if isinstance(shot, dict):
                hints.append(str(shot.get("motion_hint") or ""))
    return hints


def _motion_hint(shot: dict, hints: list[str]) -> str:
    hint = str(shot.get("motion_hint") or "").strip().lower()
    if hint:
        return hint
    index = shot.get("index")
    if isinstance(index, int) and 0 <= index < len(hints):
        return str(hints[index] or "").strip().lower()
    return ""


def _placeholder(shot: dict) -> dict:
    """offline 占位素材：检索词哈希决定色相，镜头之间视觉可分辨。"""
    digest = hashlib.sha1(shot["scene"].encode("utf-8")).hexdigest()
    hue = int(digest[:6], 16) % 360
    dest = CACHE_DIR / f"local-{digest[:10]}.mp4"
    if not dest.exists():
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cmd = [ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
               "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=30",
               "-vf", f"hue=h={hue}:s=1.4,eq=saturation=1.3",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
               "-pix_fmt", "yuv420p", str(dest)]
        subprocess.run(cmd, check=True, timeout=600)
    return {"id": f"local-{digest[:10]}", "provider": "local_test",
            "file": str(dest), "url": "", "page_url": "", "author": "",
            "author_url": "", "license": "本地占位（仅测试）", "license_url": "",
            "width": 1280, "height": 720, "fps": 30.0, "duration_s": 30.0}


def _materialize(cand: dict, vid: str) -> dict:
    """下载（或命中缓存）并探测，返回带许可信息的素材档案。

    完整性闸门：缓存文件大小必须与 API 报告的某一档画质一致——
    崩溃遗留的半截文件头部可读（ffprobe 能报出时长）但数据缺失，
    只有字节数对不上能识破它。
    """
    dest = CACHE_DIR / f"{vid}.mp4"
    api_sizes = {int(f.get("size") or 0) for f in cand["files"]} - {0}
    if dest.exists() and api_sizes and dest.stat().st_size not in api_sizes:
        dest.unlink(missing_ok=True)        # 半截/来源不明的缓存：作废重下
    if not dest.exists():
        used_link = None
        for f in cand["files"]:
            try:
                _download(f["link"], dest)
                used_link = f["link"]
                break
            except MaterialError:
                continue
        if used_link is None:
            raise MaterialError(f"所有画质均下载失败: {vid}")
    else:
        used_link = cand["files"][0]["link"]
    probe = probe_media(dest)
    if probe["width"] == 0 or probe["duration"] <= 1.0:
        dest.unlink(missing_ok=True)
        raise MaterialError(f"素材不可解码: {vid}")
    v = cand["video"]
    return {"id": vid, "provider": "pexels", "file": str(dest),
            "url": used_link, "page_url": v.get("url", ""),
            "author": (v.get("user") or {}).get("name", ""),
            "author_url": (v.get("user") or {}).get("url", ""),
            "license": "Pexels License",
            "license_url": "https://www.pexels.com/license/",
            "width": probe["width"], "height": probe["height"],
            "fps": probe["fps"],
            "duration_s": probe["duration"] or float(v.get("duration", 0))}


# ---------------------------------------------------------------- 缓存护栏

def cache_stats() -> dict:
    files = list(CACHE_DIR.glob("*.mp4"))
    return {"files": len(files),
            "bytes": sum(f.stat().st_size for f in files)}


def prune_cache(max_files: int) -> int:
    """按修改时间保留最新 max_files 个缓存素材，返回删除数。

    素材库刻意保持小体积（本机磁盘/内存约束）；被删素材如再次被选
    中会自动重下。
    """
    files = sorted(CACHE_DIR.glob("*.mp4"),
                   key=lambda f: f.stat().st_mtime, reverse=True)
    removed = 0
    for f in files[max(0, max_files):]:
        f.unlink(missing_ok=True)
        removed += 1
    return removed


# ---------------------------------------------------------------- 分配

def _screen_candidate(cand: dict, vid: str, need: float, hint: str,
                      detector, loader) -> tuple[str, dict | None]:
    """下载前筛选。返回 ('ok', asset) / ('people', None) / ('download', None)。

    people：有人，或检测器可用但抽不到入点附近的帧。不调用下载。
    download：文件本身失败，可以试下一条。
    """
    video = cand["video"]
    duration = float(video.get("duration") or 0)
    try:
        samples = list(loader(video) or [])
    except Exception:
        samples = []
    src_in, rule = choose_src_in(duration, need, hint, samples)
    if not detector_available(detector):
        verdict_status = "unavailable"
        verdict_detail = getattr(detector, "detail", "") or ""
    else:
        near = frame_near(samples, src_in, need)
        if near is None:
            return "people", None
        try:
            verdict = detector.check(near[1])
        except Exception:
            return "people", None
        if verdict.status == "rejected":
            return "people", None
        if verdict.status == "unavailable":
            verdict_status = "unavailable"
            verdict_detail = verdict.detail
        elif verdict.status == "clean":
            verdict_status = "clean"
            verdict_detail = ""
        else:
            return "people", None
    try:
        asset = _materialize(cand, vid)
    except MaterialError:
        return "download", None
    probed = float(asset.get("duration_s") or duration)
    asset["src_in"] = clamp_src_in(src_in, probed, need)
    asset["src_in_rule"] = rule
    asset["motion_hint"] = hint
    asset["people_check"] = verdict_status
    if verdict_status == "unavailable" and verdict_detail:
        asset["people_check_note"] = str(verdict_detail)[:200]
    return "ok", asset


def assign_assets(pid: str, timeline: dict, cfg: dict, keys: dict,
                  offline: bool = False, *, people_detector=None,
                  preview_loader=None) -> dict:
    """为每个镜头挑选素材并填 shot["asset"]。就地修改并返回 timeline。

    people_detector / preview_loader 供测试注入。默认检测器不可用时放行。
    """
    if not offline and not keys.get("pexels"):
        raise MaterialError("缺少 PEXELS_API_KEY（或改用 --offline 模式）")
    mat = cfg["material"]
    recent: deque = deque(maxlen=max(1, int(mat["avoid_reuse_shots"])))
    index = load_json(INDEX_PATH, []) or []
    by_id = {e.get("id"): e for e in index}
    api_cache: dict[str, dict] = {}
    detector = None if offline else resolve_people_detector(people_detector)
    loader = preview_loader or load_preview_samples
    hints = [] if offline else _storyboard_hints(pid)

    for shot in timeline["shots"]:
        need = float(shot["render_length"])
        exclude = set(shot.get("exclude_assets") or [])
        asset = None
        if offline:
            asset = _placeholder(shot)
            asset["src_in"] = _src_in(float(asset["duration_s"]), need)
        else:
            hint = _motion_hint(shot, hints)
            query = shot["scene"]
            if query not in api_cache:
                api_cache[query] = _search(keys["pexels"], query,
                                           int(mat["candidates_per_query"]))
            cands = _candidates(api_cache[query], need,
                                float(mat["min_source_duration_s"]),
                                int(mat["max_height"]))
            deferred = []
            for cand in cands:
                vid = f"pexels-{cand['video']['id']}"
                if vid in exclude:
                    continue
                if vid in recent:
                    deferred.append(cand)      # 近邻去重先让路，人的否决优先
                    continue
                kind, asset = _screen_candidate(
                    cand, vid, need, hint, detector, loader)
                if kind == "ok":
                    break
                asset = None
            if asset is None:                  # 放宽近邻，仍不接受有人的候选
                for cand in deferred:
                    vid = f"pexels-{cand['video']['id']}"
                    kind, asset = _screen_candidate(
                        cand, vid, need, hint, detector, loader)
                    if kind == "ok":
                        break
                    asset = None
        if asset is None:
            raise MaterialError(
                f"镜头 {shot['index']}（{shot['scene']}）无可用素材，"
                f"可改检索词后重跑")
        recent.append(asset["id"])
        shot["asset"] = asset
        rec = by_id.get(asset["id"])
        if rec is None:                       # 新素材：建档案并登记进落盘列表
            rec = {**asset, "used_by": []}
            by_id[asset["id"]] = rec
            index.append(rec)
        rec.setdefault("used_by", []).append(
            {"project": pid, "shot": shot["index"], "scene": shot["scene"]})

    save_json(INDEX_PATH, index)
    return timeline
