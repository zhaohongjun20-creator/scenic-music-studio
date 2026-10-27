# -*- coding: utf-8 -*-
"""生成物可追溯：每次制作落盘 provenance.json。

同一项目多轮修改后，"最终成片到底用了什么"必须有据可查：
输入（音乐/brief/风格画像的哈希与大小）、配置指纹、导演信息、
素材清单（含许可）、输出文件哈希。验收与复盘都以此为凭。
"""
import hashlib
import json
import time
from pathlib import Path

from src.jsonio import load_json, save_json


def sha1_file(path) -> dict | None:
    try:
        h = hashlib.sha1()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return {"sha1": h.hexdigest(), "bytes": Path(path).stat().st_size}
    except OSError:
        return None


def config_fingerprint(cfg: dict) -> str:
    blob = json.dumps(cfg, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def build_record(pid: str, pdir: Path, cfg: dict, style_path: str | None,
                 timeline: dict) -> dict:
    brief = load_json(pdir / "brief.json") or {}
    storyboard = load_json(pdir / "storyboard.json") or {}
    music = brief.get("music")

    assets, seen = [], set()
    for s in timeline.get("shots", []):
        a = s.get("asset") or {}
        if a.get("id") and a["id"] not in seen:
            seen.add(a["id"])
            assets.append({k: a.get(k) for k in
                           ("id", "provider", "page_url", "author",
                            "author_url", "license", "license_url")})

    final, cover = pdir / "output" / "final.mp4", pdir / "output" / "cover.jpg"
    return {
        "project": pid,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "inputs": {
            "music": ({"file": music, **(sha1_file(music) or {})}
                      if music else None),
            "brief": {k: brief.get(k) for k in
                      ("theme_zh", "mood", "duration_s", "platform",
                       "seed", "style_profile")},
            "style_profile": ({"file": style_path, **(sha1_file(style_path) or {})}
                              if style_path else None),
        },
        "config_fingerprint": config_fingerprint(cfg),
        "generator": storyboard.get("generator"),
        "assets": assets,
        "outputs": {"final.mp4": sha1_file(final), "cover.jpg": sha1_file(cover)},
    }


def write_provenance(pid: str, pdir: Path, cfg: dict, style_path: str | None,
                     timeline: dict) -> Path:
    out = pdir / "provenance.json"
    save_json(out, build_record(pid, pdir, cfg, style_path, timeline))
    return out
