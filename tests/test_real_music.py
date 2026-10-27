# -*- coding: utf-8 -*-
"""真实音乐样本的识别回归（人工核对过的结论）。

样本：Awake（johnfrantz17，CC BY 3.0，ccMixter）的 20-82s 窗口截段，
      单声道 64kbps，来源见 tests/assets/CREDITS.md。
以下断言全部来自 2026-10-27 的人工核对运行（与整曲分析结果一致：
      build/climax 边界整曲换算 36.68s vs 截段实测 36.66s）。

注意：样本随仓库分发，缺失按 FAIL 处理而非 SKIP——CI 绿灯必须意味着
真实音乐测试真实执行过（独立验收项，不允许静默跳过）。
"""
import statistics
from pathlib import Path

import pytest

from src import music_analyzer

ASSET = Path(__file__).parent / "assets" / "awake_excerpt.mp3"

if not ASSET.exists():                     # 独立验收项：缺样本 = 失败，不是跳过
    pytest.fail(
        "真实音乐样本缺失: tests/assets/awake_excerpt.mp3 "
        "（应随仓库分发，见 tests/assets/CREDITS.md）")


def test_real_music_tempo_and_beats():
    mm = music_analyzer.analyze(str(ASSET), target_s=60.0)
    # 核对值：119.71 BPM / 置信度 1.0 / 119 个节拍
    assert 115 <= mm["tempo"]["bpm"] <= 125
    assert mm["tempo"]["confidence"] >= 0.8
    beats = mm["beats"]
    assert len(beats) >= 100                      # 60s × ~2 拍/s
    intervals = [b - a for a, b in zip(beats, beats[1:])]
    med = statistics.median(intervals)
    assert abs(med - 0.501) < 0.02                # 节拍间隔稳定（非乱拍）
    assert statistics.stdev(intervals) < 0.05


def test_real_music_sections():
    mm = music_analyzer.analyze(str(ASSET), target_s=60.0)
    # 核对值：opening 0-4.8 / build 4.8-36.7 / climax 36.7-60.0
    secs = mm["sections"]
    assert 2 <= len(secs) <= 5
    climax = next((s for s in secs if s["role"] == "climax"), None)
    assert climax, "强段在样本后半段，必须识别出高潮"
    assert climax["start"] > 30.0, "高潮应在样本后 40% 才开始"
    assert climax["end"] >= 55.0
    assert mm["climax"] and mm["climax"]["peak_at"] > 30.0
    # 段落能量走向合理：高潮段能量不低于 opening
    opening = next(s for s in secs if s["role"] == "opening")
    assert climax["energy"] >= opening["energy"] - 0.1


def test_real_music_cut_candidates():
    mm = music_analyzer.analyze(str(ASSET), target_s=60.0)
    cands = mm["cut_candidates"]
    assert len(cands) >= 40                       # 核对值：130
    assert any(c["kind"] == "section" for c in cands)
    # 候选点单调且无重复
    times = [c["t"] for c in cands]
    assert times == sorted(times)
