# -*- coding: utf-8 -*-
"""审查清单·风格测试：不同结构的音乐必须产出结构不同的剪辑方案。

不只是素材名不同——段落边界、各角色的镜头分布都要随音乐变化。
曲 A：强段在 20-32s（常规起承转合）；曲 B：强段在 4-14s（早高潮）。
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import make_click_track
from src import director, music_analyzer
from src import timeline as tl

CFG_TL = {"min_shot_s": 2.0, "snap_tolerance_s": 0.6, "snap_penalty": 0.5}
CFG_VIDEO = {"width": 1920, "height": 1080, "fps": 30}
BRIEF = {"theme_zh": "治愈系山海", "mood": "治愈"}

# 曲 A：默认包络（强段 20-32s）；曲 B：早高潮（4-14s 强，之后回落）
ENV_B = [(0.0, 0.2), (3.9, 0.2), (4.0, 1.0), (13.9, 1.0), (14.0, 0.5),
         (30.0, 0.5), (30.1, 0.15), (47.9, 0.15)]


def _plan(music_path: Path) -> dict:
    mm = music_analyzer.analyze(str(music_path), target_s=40.0)
    sb = director.plan_storyboard(BRIEF, mm, {"timeline": CFG_TL}, api_key="")
    tline = tl.plan_from_storyboard(sb, mm, CFG_TL, CFG_VIDEO,
                                    rng=random.Random(7))
    return mm, sb, tline


def test_different_music_yields_different_edit_plans(tmp_path):
    a = make_click_track(tmp_path / "a.wav")                    # 强段 20-32s
    b = make_click_track(tmp_path / "b.wav", env=ENV_B)         # 强段 4-14s
    mm_a, _, t_a = _plan(a)
    mm_b, _, t_b = _plan(b)

    # 1 高潮段位置随音乐移动（B 的高潮明显更早）
    climax_a = min(s["start"] for s in t_a["shots"] if s["role"] == "climax")
    climax_b = min(s["start"] for s in t_b["shots"] if s["role"] == "climax")
    assert climax_a > climax_b + 3.0, \
        f"高潮起点应随音乐结构变化: A@{climax_a} vs B@{climax_b}"

    # 2 剪辑方案结构不同：镜头的 (起点, 角色) 序列不能重合
    def skeleton(t):
        return [(round(s["start"], 1), s["role"]) for s in t["shots"]]

    assert skeleton(t_a) != skeleton(t_b), "两首音乐不应套用同一剪辑骨架"

    # 3 镜头时长节奏随段落结构变化：A 的前 1/4 平均镜头更长（还在铺陈），
    #   B 的前 1/4 已经在高潮（切镜更密）
    def front_density(t):
        q = t["target_s"] / 4
        durs = [s["duration"] for s in t["shots"] if s["start"] < q]
        return sum(durs) / len(durs)

    assert front_density(t_a) > front_density(t_b) + 0.3, \
        "早高潮的曲子在前段应该切得更密"


def test_same_music_same_seed_is_reproducible(tmp_path):
    a1 = make_click_track(tmp_path / "a1.wav")
    a2 = make_click_track(tmp_path / "a2.wav")
    _, _, t1 = _plan(a1)
    _, _, t2 = _plan(a2)
    s1 = [(s["start"], s["duration"], s["role"]) for s in t1["shots"]]
    s2 = [(s["start"], s["duration"], s["role"]) for s in t2["shots"]]
    assert s1 == s2, "同一音乐同一种子应产出完全一致的剪辑方案"
