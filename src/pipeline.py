# -*- coding: utf-8 -*-
"""制作流水线：按阶段推进并在 manifest 记账。

断点续跑：stage_done 即跳过（redo=True 时全量重做；redo-shot 只把
materials/render/qc 置为 stale，重跑自动只补受影响的部分）。
每个阶段的产物都是一个 JSON 契约文件，人和程序都能读改——
"把高潮的镜头换壮阔一点"这类修改，改的是 timeline.json 里对应的镜头，
而不是整条视频从头再来。
"""
import random

from src import director, material_manager, music_analyzer, qc, renderer
from src import review as review_mod
from src import timeline as tl_mod
from src.jsonio import load_json, save_json
from src.store import (StoreError, load_manifest, mark_stage, project_dir,
                       set_status, stage_done)


def cover_time(tline: dict, music_map: dict) -> float:
    """封面取帧时刻：优先音乐高潮峰，夹在成片 20%-90% 之间。"""
    target = float(tline["target_s"])
    peak = (music_map.get("climax") or {}).get("peak_at")
    if peak is not None:
        t = float(peak) - music_map["play_window"]["start"]
        if 0 <= t <= target:
            return min(max(t, 0.2 * target), 0.9 * target)
    for s in tline["shots"]:
        if s["role"] == "climax":
            return s["start"] + s["duration"] / 2
    return 0.4 * target


def make(pid: str, cfg: dict, keys: dict, offline: bool = False,
         redo: bool = False) -> dict:
    pdir = project_dir(pid)
    manifest = load_manifest(pid)
    brief = load_json(pdir / "brief.json") or {}
    target_s = float(brief.get("duration_s") or cfg["video"]["target_duration_s"])
    if not brief.get("music"):
        raise StoreError("brief 缺少 music 字段")

    # 1 音乐理解
    if redo or not stage_done(manifest, "music_map"):
        mm = music_analyzer.analyze(
            brief["music"], target_s=target_s,
            beats_per_bar=int(cfg["timeline"]["beats_per_bar"]),
            min_novelty=float(cfg["timeline"]["min_sections_boundary_novelty"]))
        if mm["duration_s"] < target_s + 1.0:
            raise StoreError(
                f"音乐 {mm['duration_s']}s 短于目标 {target_s}s+1s，换曲或缩短时长")
        music_analyzer.save_music_map(mm, pdir / "music_map.json")
        mark_stage(pid, manifest, "music_map",
                   info={"tempo_bpm": mm["tempo"]["bpm"],
                         "sections": len(mm["sections"])})
    music_map = load_json(pdir / "music_map.json")

    # 2 导演计划
    if redo or not stage_done(manifest, "storyboard"):
        sb = director.plan_storyboard(brief, music_map, cfg, keys.get("zhipu", ""))
        save_json(pdir / "storyboard.json", sb)
        mark_stage(pid, manifest, "storyboard",
                   info={"mode": sb["generator"]["mode"],
                         "shots": sum(len(s["shots"]) for s in sb["sections"])})
    storyboard = load_json(pdir / "storyboard.json")

    # 3 时间轴
    if redo or not stage_done(manifest, "timeline"):
        rng = random.Random(str(brief.get("seed") or pid))
        tline = tl_mod.plan_from_storyboard(storyboard, music_map,
                                            cfg["timeline"], cfg["video"], rng=rng)
        tline["audio"].update({
            "fade_in_s": float(cfg["music"]["fade_in_s"]),
            "fade_out_s": float(cfg["music"]["fade_out_s"]),
            "loudnorm": bool(cfg["music"]["loudnorm"]) and not offline,
        })
        problems = tl_mod.validate_timeline(tline, cfg["timeline"])
        if problems:
            raise StoreError("时间轴校验失败: " + "; ".join(problems))
        save_json(pdir / "timeline.json", tline)
        mark_stage(pid, manifest, "timeline",
                   info={"shots": len(tline["shots"])})
    tline = load_json(pdir / "timeline.json")

    # 4 素材分配
    if redo or not stage_done(manifest, "materials"):
        material_manager.assign_assets(pid, tline, cfg, keys, offline=offline)
        save_json(pdir / "timeline.json", tline)
        mark_stage(pid, manifest, "materials")

    # 5 渲染（片段缓存生效：只有缺失/revision 变化的镜头真正重渲）
    if redo or not stage_done(manifest, "render"):
        clips = [renderer.render_clip(s, cfg["video"], pdir / "clips")
                 for s in tline["shots"]]
        final = pdir / "output" / "final.mp4"
        renderer.compose(clips, tline, final)
        renderer.make_cover(final, cover_time(tline, music_map),
                            pdir / "output" / "cover.jpg")
        mark_stage(pid, manifest, "render")

    # 6 质检 + 审片入口
    if redo or not stage_done(manifest, "qc"):
        passed, problems, report = qc.qc_project(pdir, tline, music_map, cfg)
        mark_stage(pid, manifest, "qc", status="done" if passed else "failed",
                   info={"problems": problems[:5]})
        set_status(pid, manifest, "review" if passed else "draft")
        review_mod.mark_rendered(pid, passed, problems)
        return {"pid": pid, "qc_passed": passed, "problems": problems,
                "cut_report": report.get("cut_report"),
                "final": str(pdir / "output" / "final.mp4")}

    report = load_json(pdir / "output" / "qc_report.json", {})
    return {"pid": pid, "qc_passed": report.get("passed", False),
            "problems": report.get("problems", []),
            "cut_report": report.get("cut_report"),
            "final": str(pdir / "output" / "final.mp4")}
