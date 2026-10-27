# -*- coding: utf-8 -*-
"""端到端离线集成测试：合成音乐 → 全链 → 成片 + QC（真实 ffmpeg，无 API）。"""
from pathlib import Path

from src import material_manager, pipeline, review, store
from src.config import load_config
from src.jsonio import load_json


def _make(iso, synth_music, duration_s: float = 20.0) -> tuple[str, dict]:
    mf = store.new_project({"theme_zh": "治愈系山海", "mood": "治愈",
                            "duration_s": duration_s, "music": str(synth_music),
                            "platform": "test"})
    return mf["id"], pipeline.make(mf["id"], load_config(), keys={},
                                   offline=True)


def test_offline_full_pipeline(iso, synth_music):
    pid, summary = _make(iso, synth_music)
    assert summary["qc_passed"], f"QC 问题: {summary['problems']}"
    assert review.load_review(pid)["status"] == "review"

    final = Path(summary["final"])
    assert final.exists() and final.stat().st_size > 100_000
    assert (final.parent / "cover.jpg").exists()

    report = load_json(final.parent / "qc_report.json")
    assert report["cut_report"]["cuts"] > 0
    assert report["cut_report"]["ratio"] >= 0.8

    tline = load_json(store.project_dir(pid) / "timeline.json")
    assert len(tline["shots"]) >= 3
    assert all(s["asset"] for s in tline["shots"])

    # 素材溯源索引必须真实落盘（回归：曾因漏 append 恒为空列表）
    index = load_json(material_manager.INDEX_PATH, [])
    indexed_ids = {e.get("id") for e in index}
    used_ids = {s["asset"]["id"] for s in tline["shots"]}
    assert used_ids <= indexed_ids, "用到的素材必须全部进溯源索引"
    sample_id = used_ids.pop()                 # 只取一次：pop 不能写在生成器条件里
    rec = next(e for e in index if e["id"] == sample_id)
    assert rec["used_by"], "溯源记录必须含使用方（项目/镜头/检索词）"


def test_offline_redo_shot_reuses_cache(iso, synth_music):
    pid, _ = _make(iso, synth_music)
    review.redo_shot(pid, 0, note="换一个")
    summary = pipeline.make(pid, load_config(), keys={}, offline=True)
    assert summary["qc_passed"], summary["problems"]

    tline = load_json(store.project_dir(pid) / "timeline.json")
    assert tline["shots"][0]["revision"] == 1
    assert all(s["revision"] == 0 for s in tline["shots"][1:])


def test_offline_partial_redo_only_touches_target_shot(iso, synth_music):
    """审查清单·缓存测试：只重做镜头 5，其余镜头的渲染片段必须原封不动。"""
    pid, _ = _make(iso, synth_music, duration_s=30.0)
    clips_dir = store.project_dir(pid) / "clips"

    def snapshot() -> dict:
        return {p.name: p.stat().st_mtime_ns for p in clips_dir.glob("*.mp4")}

    before = snapshot()
    assert len(before) >= 6, "测试需要至少 6 个镜头才有区分度"

    review.redo_shot(pid, 5, note="只重做这个")
    summary = pipeline.make(pid, load_config(), keys={}, offline=True)
    assert summary["qc_passed"], summary["problems"]

    after = snapshot()
    old_s5 = {n for n in before if n.startswith("s05_")}
    new_s5 = {n for n in after if n.startswith("s05_")}
    assert new_s5 and not (new_s5 & old_s5), "镜头 5 应产出新缓存键的片段"

    others_before = {n: t for n, t in before.items() if not n.startswith("s05_")}
    others_after = {n: t for n, t in after.items() if not n.startswith("s05_")}
    assert others_before == others_after, \
        "未受影响的镜头片段被重复渲染（缓存失效范围过大）"
