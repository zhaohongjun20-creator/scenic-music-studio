# -*- coding: utf-8 -*-
"""音乐理解引擎测试：合成曲（120 BPM、明确的五段能量结构）上的断言。"""
from src import music_analyzer


def test_tempo_and_beats(synth_music):
    mm = music_analyzer.analyze(str(synth_music), target_s=40.0)
    bpm = mm["tempo"]["bpm"]
    # 容忍八度错判，但不允许拍面乱飘
    assert min(abs(bpm - 120), abs(bpm - 60), abs(bpm - 240)) < 5, bpm

    def near_click(t: float) -> bool:
        phase = t % 0.5
        return min(phase, 0.5 - phase) < 0.12

    assert mm["beats"], "应检出节拍网格"
    ratio = sum(1 for b in mm["beats"] if near_click(b)) / len(mm["beats"])
    assert ratio >= 0.7, f"节拍落点命中率 {ratio:.0%} 过低"


def test_sections_and_climax(synth_music):
    mm = music_analyzer.analyze(str(synth_music), target_s=48.0)
    assert 3 <= len(mm["sections"]) <= 7
    climax = next(s for s in mm["sections"] if s["role"] == "climax")
    # 强段在 20-32s：高潮段必须覆盖它
    assert climax["start"] <= 21 and climax["end"] >= 31, climax
    assert mm["climax"] and 18 <= mm["climax"]["peak_at"] <= 34
    # 段落边界本身应成为切镜候选
    assert any(abs(c["t"] - 20.0) <= 1.0 and c["strength"] >= 1.0
               for c in mm["cut_candidates"])


def test_play_window_prefers_loud_part(synth_music):
    mm = music_analyzer.analyze(str(synth_music), target_s=30.0)
    w = mm["play_window"]
    assert w["end"] - w["start"] == 30.0
    # 30s 窗口应盖住 20-32s 的强段大头
    assert w["start"] <= 20 and w["end"] >= 30


def test_window_sections_rebases_time(synth_music):
    mm = music_analyzer.analyze(str(synth_music), target_s=30.0)
    secs = music_analyzer.window_sections(mm)
    assert secs, "窗口内应有段落"
    assert secs[0]["start"] == 0.0
    for a, b in zip(secs, secs[1:]):
        assert b["start"] >= a["end"] - 0.01


def test_curves_shape(synth_music):
    mm = music_analyzer.analyze(str(synth_music), target_s=40.0)
    n = int(mm["duration_s"] * music_analyzer.CURVE_FPS)
    assert abs(len(mm["curves"]["energy"]) - n) <= 2
    assert abs(len(mm["curves"]["onset"]) - n) <= 2
    assert max(mm["curves"]["energy"]) <= 1.0 + 1e-6
