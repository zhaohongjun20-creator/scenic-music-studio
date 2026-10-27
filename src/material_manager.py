# -*- coding: utf-8 -*-
"""素材管理：镜头检索词 → Pexels 实拍素材的检索、下载与建档。

- 全局缓存 materials/cache/{id}.mp4，跨项目复用，已下载不重下；
- materials/index.json 记录每个用过的素材：来源页/作者/许可/规格/
  被哪些镜头使用——发布争议时可追溯；
- 相邻 N 个镜头不重复用同一素材；redo-shot 时被换掉的素材进入该镜头
  的排除名单，不会再次选中；
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
from src.store import MATERIALS_DIR

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
    """默认从素材前 1/3 处取，保证剩余长度够截满；不够就贴尾部。"""
    third = duration / 3.0
    if third + need <= duration - 0.2:
        return round(third, 3)
    return round(max(0.0, duration - need - 0.2), 3)


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


# ---------------------------------------------------------------- 分配

def assign_assets(pid: str, timeline: dict, cfg: dict, keys: dict,
                  offline: bool = False) -> dict:
    """为每个镜头挑选素材并填 shot["asset"]。就地修改并返回 timeline。"""
    if not offline and not keys.get("pexels"):
        raise MaterialError("缺少 PEXELS_API_KEY（或改用 --offline 模式）")
    mat = cfg["material"]
    recent: deque = deque(maxlen=max(1, int(mat["avoid_reuse_shots"])))
    index = load_json(INDEX_PATH, []) or []
    by_id = {e.get("id"): e for e in index}
    api_cache: dict[str, dict] = {}

    for shot in timeline["shots"]:
        need = float(shot["render_length"])
        exclude = set(shot.get("exclude_assets") or [])
        asset = None
        if offline:
            asset = _placeholder(shot)
        else:
            query = shot["scene"]
            if query not in api_cache:
                api_cache[query] = _search(keys["pexels"], query,
                                           int(mat["candidates_per_query"]))
            cands = _candidates(api_cache[query], need,
                                float(mat["min_source_duration_s"]),
                                int(mat["max_height"]))
            for c in cands:
                vid = f"pexels-{c['video']['id']}"
                if vid in exclude or vid in recent:
                    continue
                try:
                    asset = _materialize(c, vid)
                except MaterialError:
                    continue                # 这个候选坏了/网断了：换下一个
                break
            if asset is None and cands:      # 全部撞车：放宽近邻限制
                asset = _materialize(cands[0], f"pexels-{cands[0]['video']['id']}")
        if asset is None:
            raise MaterialError(
                f"镜头 {shot['index']}（{shot['scene']}）无可用素材，"
                f"可改检索词后重跑")
        asset["src_in"] = _src_in(float(asset["duration_s"]), need)
        recent.append(asset["id"])
        shot["asset"] = asset
        rec = by_id.setdefault(asset["id"], {**asset, "used_by": []})
        rec.setdefault("used_by", []).append(
            {"project": pid, "shot": shot["index"], "scene": shot["scene"]})

    save_json(INDEX_PATH, index)
    return timeline
