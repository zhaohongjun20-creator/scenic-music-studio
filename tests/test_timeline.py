# -*- coding: utf-8 -*-
"""时间轴规划测试：段落边界守恒、切点吸附、铺满与守恒不变量。"""
import random

import pytest

from src import director
from src import timeline as tl

CFG_TL = {"min_shot_s": 2.0, "snap_tolerance_s": 0.6, "snap_penalty": 0.5}
CFG_VIDEO = {"width": 1920, "height": 1080, "fps": 30}


def synth_music_map() -> dict:
    sections = [
        {"index": 0, "role": "opening", "start": 0.0, "end": 10.0, "energy": 0.2},
        {"index": 1, "role": "build", "start": 10.0, "end": 24.0, "energy": 0.5},
        {"index": 2, "role": "climax", "start": 24.0, "end": 40.0, "energy": 0.9},
        {"index": 3, "role": "settle", "start": 40.0, "end": 52.0, "energy": 0.4},
        {"index": 4, "role": "ending", "start": 52.0, "end": 60.0, "energy": 0.15},
    ]
    cands = [{"t": round(i * 0.5, 3), "strength": 0.6, "kind": "bar"}
             for i in range(120)]                       # 0.5s 节拍网格
    cands += [{"t": 17.2, "strength": 0.95, "kind": "onset"},
              {"t": 30.1, "strength": 0.95, "kind": "onset"}]
    return {"file": "test.wav", "duration_s": 60.0,
            "play_window": {"start": 0.0, "end": 60.0},
            "sections": sections, "cut_candidates": cands}


def synth_storyboard(mm) -> dict:
    sections = tl.window_sections(mm)
    return {"brief": {}, "sections": director._rule_shots(
        sections, ["mountain", "sea"], CFG_TL["min_shot_s"])}


def test_timeline_invariants():
    mm = synth_music_map()
    tline = tl.plan_from_storyboard(synth_storyboard(mm), mm, CFG_TL, CFG_VIDEO,
                                    rng=random.Random(7))
    assert tl.validate_timeline(tline, CFG_TL) == []
    assert sum(s["duration"] for s in tline["shots"]) == pytest.approx(60.0, abs=1e-6)
    total_rl = sum(s["render_length"] for s in tline["shots"])
    total_tr = sum((s["transition_out"] or {"duration": 0})["duration"]
                   for s in tline["shots"])
    assert total_rl - total_tr == pytest.approx(60.0, abs=1e-3)


def test_section_boundaries_are_hard():
    mm = synth_music_map()
    tline = tl.plan_from_storyboard(synth_storyboard(mm), mm, CFG_TL, CFG_VIDEO,
                                    rng=random.Random(7))
    starts = {round(s["start"], 2) for s in tline["shots"]}
    for edge in (10.0, 24.0, 40.0, 52.0):
        assert edge in starts, f"段落边界 {edge}s 应原样保留"


def test_cuts_snap_to_candidates():
    mm = synth_music_map()
    tline = tl.plan_from_storyboard(synth_storyboard(mm), mm, CFG_TL, CFG_VIDEO,
                                    rng=random.Random(7))
    locked = [s["beat_locked"] for s in tline["shots"] if s["beat_locked"]]
    assert locked, "节拍网格密布时应发生吸附"
    for s in tline["shots"]:
        lock = s["beat_locked"]
        if lock:
            assert abs(lock["offset_s"]) <= 0.6 + 1e-6
    # 30.1s 的强 onset 附近必有切点被吸过去
    near = [s for s in tline["shots"]
            if s["beat_locked"] and abs(s["start"] - 30.1) <= 0.35]
    assert near, "强 onset 应吸引邻近切点"


def test_infeasible_section_raises():
    mm = synth_music_map()
    mm["sections"] = [mm["sections"][0]]
    mm["sections"][0]["end"] = 8.0
    mm["play_window"] = {"start": 0.0, "end": 8.0}
    sb = {"sections": [{"index": 0, "role": "opening", "shots": [
        {"shot_id": f"s{i}", "scene": "mountain", "duration_hint": 2.0}
        for i in range(5)]}]}          # 5×2s 装不进 8s
    with pytest.raises(tl.TimelineError):
        tl.plan_from_storyboard(sb, mm, CFG_TL, CFG_VIDEO)


def test_shot_count_mismatch_raises():
    mm = synth_music_map()
    sb = synth_storyboard(mm)
    sb["sections"] = sb["sections"][:2]           # 段落数对不上
    with pytest.raises(tl.TimelineError):
        tl.plan_from_storyboard(sb, mm, CFG_TL, CFG_VIDEO)
