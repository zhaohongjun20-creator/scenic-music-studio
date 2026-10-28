# -*- coding: utf-8 -*-
"""制作流水线：内容指纹 + 产物校验驱动断点续跑。

阶段 done 只是状态，不是缓存证明。manifest.freshness 记录每阶段实际输入
和产物 SHA-256；失效先落盘，成功再提交。旧项目无指纹时保守重建。
redo-shot 的 public revision 仍为整数，仅受影响镜头重新分配/渲染。
"""
import random
from pathlib import Path

from src import director, material_manager, music_analyzer, provenance, qc, renderer
from src import review as review_mod
from src import timeline as tl_mod
from src.jsonio import load_json, save_json
from src.store import (STAGES, StoreError, invalidate_stages, load_manifest,
                       mark_stage, project_dir, save_manifest, set_status,
                       stage_done)

FRESHNESS_VERSION = 1
STAGE_OUTPUTS = {
    "music_map": ("music_map.json",),
    "storyboard": ("storyboard.json",),
    "render": ("output/final.mp4", "output/cover.jpg", "provenance.json"),
    "qc": ("output/qc_report.json",),
}


def resolve_style(brief: dict, cfg: dict) -> tuple[dict | None, str | None]:
    """返回实际生效的 (derived, 风格文件路径)。"""
    path = brief.get("style_profile") or cfg.get("style", {}).get("profile")
    if not path:
        return None, None
    p = Path(path)
    if not p.is_absolute():
        p = Path(__file__).resolve().parent.parent / path
    prof = load_json(p)
    if not prof or "derived" not in prof:
        raise StoreError(f"风格画像缺失或无 derived 段: {path}")
    return prof["derived"], str(p)


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


def _file(path) -> dict:
    result = provenance.sha256_file(path)
    if not result or not result["bytes"]:
        raise StoreError(f"输入或产物缺失/为空: {path}")
    return result


def _files(pdir: Path, names: tuple[str, ...]) -> dict:
    return {name: _file(pdir / name) for name in names}


def _outputs_match(pdir: Path, outputs: dict) -> bool:
    return bool(outputs) and all(
        expected and expected.get("bytes", 0) > 0
        and provenance.sha256_file(pdir / name) == expected
        for name, expected in outputs.items())


def _source_inputs(pdir: Path, brief: dict, cfg: dict,
                   style_path: str | None, offline: bool) -> dict:
    return {"brief": _file(pdir / "brief.json"),
            "music": {"file": brief["music"], **_file(brief["music"])},
            "style": ({"file": style_path, **_file(style_path)}
                      if style_path else None),
            "config_sha256": provenance.content_fingerprint(cfg),
            "offline": offline}


def _plan_content(tline: dict) -> str:
    """素材分配和 redo-shot 不应使时间轴规划本身失效。"""
    local_fields = {"asset", "revision", "exclude_assets", "redo_note"}
    plan = {**tline, "shots": [
        {k: v for k, v in shot.items() if k not in local_fields}
        for shot in tline.get("shots", [])]}
    return provenance.content_fingerprint(plan)


def _asset_state(tline: dict) -> list[dict]:
    return [{"index": s["index"], "asset": s.get("asset"),
             "content": provenance.sha256_file((s.get("asset") or {}).get("file"))}
            for s in tline["shots"]]


def _material_requests(tline: dict) -> dict:
    return {str(s["index"]): provenance.content_fingerprint(
        {k: s.get(k) for k in ("index", "scene", "render_length", "revision",
                               "exclude_assets", "redo_note")})
            for s in tline["shots"]}


def _clip_inputs(shot: dict, video_cfg: dict) -> str:
    # renderer's historical cache key omits dimensions/fps/file bytes. Supply
    # a content revision on a COPY, leaving the editable timeline revision intact.
    return provenance.content_fingerprint({
        "version": FRESHNESS_VERSION, "video": video_cfg,
        "asset": shot["asset"], "content": _file(shot["asset"]["file"]),
        "render_length": shot["render_length"],
        "revision": shot.get("revision", 0), "index": shot["index"]})


def _discard_bad_clip(pdir: Path, shot: dict, signature: str, receipt: dict) -> None:
    """Retain only a hash-verified receipt; clear unverified files for THIS shot.

    An interrupted first render may leave a partial file with no receipt. The
    renderer only checks exists(), so even a new content revision is insufficient
    on retry. Never delete source assets or another shot's cache here.
    """
    clips_dir = pdir / "clips"
    root = clips_dir.resolve()
    if clips_dir.is_symlink():
        raise StoreError("clips 目录为链接，拒绝清理")
    prefix = f's{int(shot["index"]):02d}_'
    if receipt.get("input_sha256") == signature and receipt.get("path"):
        clip = pdir / receipt["path"]
        if (not clip.is_symlink() and clip.resolve().parent == root
                and clip.name.startswith(prefix) and clip.suffix == ".mp4"
                and receipt.get("output", {}).get("bytes", 0) > 0
                and provenance.sha256_file(clip) == receipt["output"]):
            return
    for clip in clips_dir.glob(prefix + "*.mp4"):
        if clip.is_symlink() or clip.resolve().parent != root:
            raise StoreError("片段缓存不在当前项目 clips 内，拒绝清理")
        clip.unlink(missing_ok=True)


def make(pid: str, cfg: dict, keys: dict, offline: bool = False,
         redo: bool = False) -> dict:
    pdir = project_dir(pid)
    manifest = load_manifest(pid)
    brief = load_json(pdir / "brief.json") or {}
    try:
        target_s = float(brief.get("duration_s") or cfg["video"]["target_duration_s"])
        if not brief.get("music"):
            raise StoreError("brief 缺少 music 字段")
        style, style_path = resolve_style(brief, cfg)
        sources = _source_inputs(pdir, brief, cfg, style_path, offline)
    except (StoreError, OSError, ValueError, TypeError, KeyError):
        invalidate_stages(manifest, STAGES)
        manifest["status"] = "draft"
        save_manifest(pid, manifest)
        review_mod.invalidate_review(pid, "制作输入缺失或无效")
        raise
    video_cfg = dict(cfg["video"])
    if style and style.get("video"):
        video_cfg.update({k: v for k, v in style["video"].items()
                          if k in video_cfg})
    if manifest.get("freshness", {}).get("version") != FRESHNESS_VERSION:
        manifest["freshness"] = {"version": FRESHNESS_VERSION, "stages": {}}
    ledger = manifest["freshness"]["stages"]

    def fresh(stage: str, inputs: dict, extra: bool = True) -> bool:
        previous = ledger.get(stage, {})
        required = STAGE_OUTPUTS.get(stage)
        outputs_valid = (not required or
                         (isinstance(previous.get("outputs"), dict)
                          and set(previous["outputs"]) == set(required)
                          and _outputs_match(pdir, previous["outputs"])))
        return (not redo and stage_done(manifest, stage) and extra
                and previous.get("input_sha256") == provenance.content_fingerprint(inputs)
                and outputs_valid)

    def begin(stage: str) -> None:
        invalidate_stages(manifest, STAGES[STAGES.index(stage):])
        manifest["status"] = "draft"
        manifest["stages"][stage]["status"] = "running"
        save_manifest(pid, manifest)
        review_mod.invalidate_review(pid, "制作输入或产物已失效")

    def finish(stage: str, inputs: dict, outputs: dict | None = None,
               extra: dict | None = None, info: dict | None = None,
               status: str = "done") -> None:
        ledger[stage] = {"input_sha256": provenance.content_fingerprint(inputs),
                         "outputs": outputs or {}, **(extra or {})}
        mark_stage(pid, manifest, stage, status=status, info=info)

    # 1 音乐理解。原路径内容替换也必须重新分析。
    music_inputs = {"music": sources["music"], "target_s": target_s,
                    "beats_per_bar": cfg["timeline"]["beats_per_bar"],
                    "min_novelty": cfg["timeline"]["min_sections_boundary_novelty"]}
    if not fresh("music_map", music_inputs):
        begin("music_map")
        mm = music_analyzer.analyze(
            brief["music"], target_s=target_s,
            beats_per_bar=int(cfg["timeline"]["beats_per_bar"]),
            min_novelty=float(cfg["timeline"]["min_sections_boundary_novelty"]))
        if mm["duration_s"] < target_s + 1.0:
            raise StoreError(
                f"音乐 {mm['duration_s']}s 短于目标 {target_s}s+1s，换曲或缩短时长")
        music_analyzer.save_music_map(mm, pdir / "music_map.json")
        finish("music_map", music_inputs, _files(pdir, ("music_map.json",)),
               info={"tempo_bpm": mm["tempo"]["bpm"], "sections": len(mm["sections"])})
    music_map = load_json(pdir / "music_map.json")

    # 2 导演计划。密钥本身绝不写指纹；只区分是否启用创意 API。
    story_inputs = {"brief": sources["brief"], "style": sources["style"],
                    "music_map": _file(pdir / "music_map.json"),
                    "timeline": cfg["timeline"], "glm": cfg.get("glm"),
                    "creative_api": bool(keys.get("zhipu"))}
    if not fresh("storyboard", story_inputs):
        begin("storyboard")
        sb = director.plan_storyboard(brief, music_map, cfg, keys.get("zhipu", ""),
                                      style=style)
        save_json(pdir / "storyboard.json", sb)
        finish("storyboard", story_inputs, _files(pdir, ("storyboard.json",)),
               info={"mode": sb["generator"]["mode"],
                     "shots": sum(len(s["shots"]) for s in sb["sections"]),
                     "style": bool(style)})
    storyboard = load_json(pdir / "storyboard.json")

    # 3 时间轴。asset/revision 等局部字段不属于规划阶段产物签名。
    timeline_inputs = {"storyboard": _file(pdir / "storyboard.json"),
                       "music_map": _file(pdir / "music_map.json"),
                       "timeline": cfg["timeline"], "video": video_cfg,
                       "music": cfg["music"], "style": sources["style"],
                       "offline": offline, "seed": str(brief.get("seed") or pid)}
    tline = load_json(pdir / "timeline.json")
    plan_matches = (isinstance(tline, dict) and isinstance(tline.get("shots"), list)
                    and ledger.get("timeline", {}).get("plan_sha256") == _plan_content(tline))
    if not fresh("timeline", timeline_inputs, plan_matches):
        begin("timeline")
        rng = random.Random(str(brief.get("seed") or pid))
        tline = tl_mod.plan_from_storyboard(storyboard, music_map,
                                           cfg["timeline"], video_cfg, rng=rng,
                                           style=style)
        tline["audio"].update({
            "fade_in_s": float(cfg["music"]["fade_in_s"]),
            "fade_out_s": float(cfg["music"]["fade_out_s"]),
            "loudnorm": bool(cfg["music"]["loudnorm"]) and not offline,
        })
        problems = tl_mod.validate_timeline(tline, cfg["timeline"])
        if problems:
            raise StoreError("时间轴校验失败: " + "; ".join(problems))
        save_json(pdir / "timeline.json", tline)
        finish("timeline", timeline_inputs,
               extra={"plan_sha256": _plan_content(tline)},
               info={"shots": len(tline["shots"])})

    # 4 素材分配。仅向 manager 传需分配的镜头，保留其余素材和索引历史。
    requests = _material_requests(tline)
    material_settings = {"material": cfg["material"], "offline": offline}
    material_inputs = {**material_settings, "requests": requests}
    assets = _asset_state(tline)
    asset_matches = (all(a["content"] and a["content"]["bytes"] for a in assets)
                     and ledger.get("materials", {}).get("assets_sha256")
                     == provenance.content_fingerprint(assets))
    if not fresh("materials", material_inputs, asset_matches):
        previous = ledger.get("materials", {})
        begin("materials")
        all_assets = (redo or previous.get("settings_sha256")
                      != provenance.content_fingerprint(material_settings))
        pending = [s for s, a in zip(tline["shots"], assets)
                   if all_assets or not a["content"] or not a["content"]["bytes"]
                   or previous.get("requests", {}).get(str(s["index"]))
                   != requests[str(s["index"])]]
        if pending:
            material_manager.assign_assets(pid, {**tline, "shots": pending}, cfg,
                                            keys, offline=offline)
        assets = _asset_state(tline)
        if not all(a["content"] and a["content"]["bytes"] for a in assets):
            raise StoreError("素材分配后仍有缺失/空素材，不能渲染")
        save_json(pdir / "timeline.json", tline)
        finish("materials", material_inputs, extra={
            "requests": requests,
            "settings_sha256": provenance.content_fingerprint(material_settings),
            "assets_sha256": provenance.content_fingerprint(assets)})

    # 5 渲染。仅实际 render/compose 成功后提交新 provenance，绝不替旧成片洗来源。
    render_inputs = {"sources": sources, "video": video_cfg,
                     "artifacts": _files(pdir, ("music_map.json", "storyboard.json", "timeline.json")),
                     "assets": assets}
    old_clips = ledger.get("render", {}).get("clips", {})
    clips_valid = bool(old_clips) and all(
        c.get("output") and provenance.sha256_file(pdir / c["path"]) == c["output"]
        for c in old_clips.values())
    if not fresh("render", render_inputs, clips_valid):
        begin("render")
        clips, receipts = [], {}
        for shot in tline["shots"]:
            signature = _clip_inputs(shot, video_cfg)
            _discard_bad_clip(pdir, shot, signature,
                              {} if redo else old_clips.get(str(shot["index"]), {}))
            clip = Path(renderer.render_clip({**shot, "revision": signature}, video_cfg,
                                             pdir / "clips"))
            clips.append(clip)
            receipts[str(shot["index"])] = {"input_sha256": signature,
                "path": str(clip.relative_to(pdir)), "output": _file(clip)}
        final = pdir / "output" / "final.mp4"
        renderer.compose(clips, tline, final)
        renderer.make_cover(final, cover_time(tline, music_map), pdir / "output" / "cover.jpg")
        # Catch files edited/replaced during the run, before claiming any provenance.
        current = {**render_inputs,
                   "sources": _source_inputs(pdir, brief, cfg, style_path, offline),
                   "artifacts": _files(pdir, ("music_map.json", "storyboard.json", "timeline.json")),
                   "assets": _asset_state(tline)}
        if current != render_inputs:
            raise StoreError("制作期间输入发生变化，请重新 make；未写入新 provenance")
        _files(pdir, ("output/final.mp4", "output/cover.jpg"))
        provenance.write_provenance(pid, pdir, cfg, style_path, tline)
        finish("render", render_inputs,
               _files(pdir, ("output/final.mp4", "output/cover.jpg", "provenance.json")),
               extra={"clips": receipts})

    # 6 QC 可独立重试，不改写已完成渲染的 provenance。
    qc_inputs = {"render": _files(pdir, ("output/final.mp4", "output/cover.jpg")),
                 "timeline": _file(pdir / "timeline.json"),
                 "music_map": _file(pdir / "music_map.json"), "config": cfg}
    if not fresh("qc", qc_inputs):
        begin("qc")
        passed, problems, report = qc.qc_project(pdir, tline, music_map, cfg)
        finish("qc", qc_inputs, _files(pdir, ("output/qc_report.json",)),
               status="done" if passed else "failed", info={"problems": problems[:5]})
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
