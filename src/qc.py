# -*- coding: utf-8 -*-
"""质检：发布前的最后一道确定性闸门 + 切点-音乐对齐报告。

检查项：文件可读、分辨率/帧率、时长容差、无意外静音/削波、无长黑帧、
封面可解码、时间轴不变量，以及本项目特有的验收线——切点吸附率。
"""
import re
import subprocess
import time
from pathlib import Path

from src.audio_io import ffmpeg_exe
from src.jsonio import save_json
from src.timeline import validate_timeline

DEFAULTS = {
    "max_black_seconds": 2.0,
    "max_volume_db": -0.5,
    "min_mean_volume_db": -45.0,
}


def _probe(path: str) -> dict:
    r = subprocess.run([ffmpeg_exe(), "-i", path], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=120)
    err = r.stderr
    if "Duration: N/A" in err or ("Duration" not in err and "Video:" not in err):
        raise RuntimeError(f"ffmpeg 无法读取: {path}")
    dur = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err)
    if not dur:
        raise RuntimeError(f"无时长信息: {path}")
    h, m, s = dur.groups()
    info = {"duration": int(h) * 3600 + int(m) * 60 + float(s),
            "width": 0, "height": 0, "fps": 0.0, "has_audio": False}
    vm = re.search(r"Video:.*?,\s*(\d{3,5})x(\d{3,5})", err)
    if vm:
        info["width"], info["height"] = int(vm.group(1)), int(vm.group(2))
        fm = re.search(r"(\d+(?:\.\d+)?)\s*fps", err)
        info["fps"] = float(fm.group(1)) if fm else 0.0
    info["has_audio"] = "Audio:" in err
    return info


def _audio_levels(path: str) -> dict:
    r = subprocess.run(
        [ffmpeg_exe(), "-i", path, "-vn", "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=900)
    mean = re.search(r"mean_volume:\s*(-?[\d.]+)\s*dB", r.stderr)
    peak = re.search(r"max_volume:\s*(-?[\d.]+)\s*dB", r.stderr)
    return {"mean": float(mean.group(1)) if mean else 0.0,
            "peak": float(peak.group(1)) if peak else 0.0}


def _black_seconds(path: str) -> float:
    r = subprocess.run(
        [ffmpeg_exe(), "-i", path, "-vf", "blackdetect=d=0.5:pix_th=0.10",
         "-an", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=1800)
    return sum(float(m.group(1)) for m in re.finditer(
        r"black_start:\S+\s+black_end:\S+\s+black_duration:([\d.]+)", r.stderr))


def cut_alignment_report(timeline: dict, music_map: dict,
                         cfg_timeline: dict) -> dict:
    """信息性报告：每个内部切点到最近音乐候选点的偏移。"""
    win0 = music_map["play_window"]["start"]
    cands = sorted(c["t"] - win0 for c in music_map.get("cut_candidates", []))
    tol = float(cfg_timeline["snap_tolerance_s"])
    offsets = []
    for i, s in enumerate(timeline["shots"]):
        if i == 0 or not cands:
            continue
        t = float(s["start"])
        offsets.append(round(min(abs(t - c) for c in cands), 3))
    within = [o for o in offsets if o <= tol]
    return {"cuts": len(offsets),
            "within_tolerance": len(within),
            "ratio": round(len(within) / len(offsets), 3) if offsets else 1.0,
            "tolerance_s": tol,
            "mean_abs_offset_s": round(sum(within) / len(within), 3) if within else None}


def find_duplicate_adjacent(timeline: dict) -> list[str]:
    """相邻镜头重复使用同一素材同一取材区间（观众能直接看出的硬伤）。"""
    problems = []
    shots = timeline.get("shots", [])
    for a, b in zip(shots, shots[1:]):
        aa, bb = a.get("asset") or {}, b.get("asset") or {}
        if aa.get("id") and aa.get("id") == bb.get("id") \
                and abs(float(aa.get("src_in") or 0) - float(bb.get("src_in") or 0)) < 0.2:
            problems.append(
                f"相邻镜头 #{a.get('index')}→#{b.get('index')} 重复同一素材同区间 "
                f"({aa['id']}，切点 {a.get('start', 0):.1f}s)")
    return problems


def qc_project(project_dir: Path, timeline: dict, music_map: dict,
               cfg: dict) -> tuple[bool, list[str], dict]:
    """全部通过返回 (True, [], report)；报告同时落盘 qc_report.json。"""
    th = {**DEFAULTS, **cfg["qc"]}
    vcfg = cfg["video"]
    out_dir = project_dir / "output"
    final = out_dir / "final.mp4"
    cover = out_dir / "cover.jpg"
    problems: list[str] = []

    if not final.exists() or final.stat().st_size == 0:
        problems.append(f"成片缺失或为空: {final}")
        report = {"passed": False, "problems": problems, "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        save_json(out_dir / "qc_report.json", report)
        return False, problems, report

    try:
        info = _probe(str(final))
    except (RuntimeError, subprocess.TimeoutExpired) as e:
        problems.append(f"成片不可读取: {e}")
        info = None
    if info:
        if (info["width"], info["height"]) != (int(vcfg["width"]), int(vcfg["height"])):
            problems.append(f"分辨率 {info['width']}x{info['height']} != "
                            f"{vcfg['width']}x{vcfg['height']}")
        if info["fps"] and abs(info["fps"] - float(vcfg["fps"])) > 2:
            problems.append(f"帧率 {info['fps']} != {vcfg['fps']}")
        tol = float(vcfg.get("duration_tolerance_s", 1.5))
        if abs(info["duration"] - float(timeline["target_s"])) > tol:
            problems.append(f"成片时长 {info['duration']:.1f}s 偏离目标 "
                            f"{timeline['target_s']:.1f}s 超过 {tol}s")
        if not info["has_audio"]:
            problems.append("成片没有音频轨")
    problems += validate_timeline(timeline, cfg["timeline"])

    try:
        lv = _audio_levels(str(final))
        if lv["peak"] >= th["max_volume_db"]:
            problems.append(f"音频削波风险: max {lv['peak']}dB")
        if lv["mean"] <= th["min_mean_volume_db"]:
            problems.append(f"音频近乎静音: mean {lv['mean']}dB")
    except subprocess.TimeoutExpired:
        problems.append("音量探测超时（人工复查）")

    try:
        black = _black_seconds(str(final))
        if black > th["max_black_seconds"]:
            problems.append(f"黑帧过多: {black:.1f}s")
    except subprocess.TimeoutExpired:
        problems.append("黑帧探测超时（人工复查）")

    if not cover.exists() or cover.stat().st_size < 10240:
        problems.append(f"封面缺失或过小: {cover}")
    else:
        try:
            _probe(str(cover))
        except (RuntimeError, subprocess.TimeoutExpired):
            problems.append(f"封面无法解码: {cover}")

    cut_report = cut_alignment_report(timeline, music_map, cfg["timeline"])
    if cut_report["cuts"] and cut_report["ratio"] < float(cfg["qc"]["min_cut_snap_ratio"]):
        problems.append(f"切点吸附率 {cut_report['ratio']:.0%} 低于验收线 "
                        f"{cfg['qc']['min_cut_snap_ratio']:.0%}")
    problems += find_duplicate_adjacent(timeline)

    report = {"passed": not problems, "problems": problems,
              "cut_report": cut_report,
              "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    save_json(out_dir / "qc_report.json", report)
    return (not problems), problems, report
