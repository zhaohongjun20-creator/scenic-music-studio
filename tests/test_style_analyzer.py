# -*- coding: utf-8 -*-
"""参考视频风格分析测试：合成已知切点的视频 → 边界检测；风格接入时间轴。"""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.audio_io import ffmpeg_exe
from src import director, style_analyzer
from src import timeline as tl

CFG_TL = {"min_shot_s": 2.0, "snap_tolerance_s": 0.6, "snap_penalty": 0.5}
CFG_VIDEO = {"width": 1920, "height": 1080, "fps": 30}

# 风景线画像的 derived 关键字段（由真实样本 derive 的复刻值）
FENGJINGXIAN_STYLE = {
    "role_shot_len": {"opening": [1.4, 4.35], "build": [1.4, 4.35],
                      "climax": [1.12, 3.48], "settle": [1.4, 5.22],
                      "ending": [1.4, 5.65]},
    "min_shot_s": 1.2,
    "transition_policy": {"mode": "cut_dominant", "soft_pool": ["fade", "dissolve"],
                          "soft_transition_s": 0.5},
    "video": {"saturation": 1.02},
}


@pytest.fixture(scope="module")
def hardcut_video(tmp_path_factory) -> Path:
    """6 段纯色片段硬切拼接（切点在 2,4,6,8,10s），颜色互异。"""
    dest = tmp_path_factory.mktemp("ref") / "hardcut.mp4"
    colors = ["0x224488", "0x884422", "0x228844", "0x882244",
              "0x444488", "0x888822"]
    parts = []
    for i, c in enumerate(colors):
        p = dest.parent / f"part{i}.mp4"
        subprocess.run(
            [ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", f"color=c={c}:size=320x180:rate=10:duration=2",
             "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
             str(p)], check=True, timeout=300)
        parts.append(str(p))
    lst = dest.parent / "list.txt"
    lst.write_text("\n".join(f"file '{p}'" for p in parts), encoding="utf-8")
    subprocess.run([ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-c", "copy", str(dest)], check=True, timeout=300)
    return dest


def test_boundary_detection_on_known_cuts(hardcut_video):
    stats = style_analyzer.analyze_sample(str(hardcut_video))
    assert stats["duration_s"] == pytest.approx(12.0, abs=0.3)
    assert stats["shots"] == 6, f"应检出 6 镜，得 {stats['shots']}"
    boundaries = style_analyzer.detect_boundaries(
        *style_analyzer._decode_frames(str(hardcut_video)))
    got = sorted(b["t"] for b in boundaries)
    for want in (2.0, 4.0, 6.0, 8.0, 10.0):
        assert any(abs(g - want) <= 0.35 for g in got), f"切点 {want}s 漏检: {got}"
    assert all(b["kind"] == "hard" for b in boundaries)   # 纯色硬切应为 hard


def test_profile_derive_shape():
    fake_stats = [{
        "len_median": 2.6, "len_p25": 1.4, "len_p75": 4.35,
        "hard_cut_frac": 0.815, "density_by_third_cpm": [20.0, 19.5, 19.5],
        "color": {"saturation": 0.37, "warmth": 0.004, "brightness": 0.34},
    }]
    d = style_analyzer.derive(fake_stats)
    assert d["transition_policy"]["mode"] == "cut_dominant"
    assert d["min_shot_s"] >= 1.0
    assert d["role_shot_len"]["climax"][0] < d["role_shot_len"]["settle"][0]
    assert 0.9 <= d["video"]["saturation"] <= 1.35


def _plan(style):
    sections = [
        {"index": 0, "role": "opening", "start": 0.0, "end": 8.0, "energy": 0.2},
        {"index": 1, "role": "climax", "start": 8.0, "end": 26.0, "energy": 0.9},
        {"index": 2, "role": "ending", "start": 26.0, "end": 32.0, "energy": 0.2},
    ]
    mm = {"file": "x", "duration_s": 32.0,
          "play_window": {"start": 0.0, "end": 32.0}, "sections": sections,
          "cut_candidates": [{"t": round(i * 0.5, 3), "strength": 0.6,
                              "kind": "bar"} for i in range(64)]}
    import random
    sb = {"brief": {}, "sections": director._rule_shots(
        tl.window_sections(mm), ["mountain", "sea"], 1.2,
        tl.get_role_shot_len(style))}
    t = tl.plan_from_storyboard(sb, mm, CFG_TL, CFG_VIDEO,
                                rng=random.Random(7), style=style)
    return t


def test_style_changes_rhythm_and_transitions():
    base = _plan(None)
    styled = _plan(FENGJINGXIAN_STYLE)

    # 风景线画像：镜头更多更短
    assert len(styled["shots"]) > len(base["shots"])
    med = sorted(s["duration"] for s in styled["shots"])
    assert med[len(med) // 2] < 3.5

    # 不变量仍成立（最短镜头用画像下限 1.2s）
    assert tl.validate_timeline(styled, CFG_TL) == []
    assert styled["min_shot_s"] == 1.2

    # 转场策略：段内切点 = 快切(≈0.13s)，段落边界 = 柔和(0.5s)
    for i, s in enumerate(styled["shots"]):
        if i == 0:
            continue
        tr = s["transition_out"] or {}
        if (s["transition_out"] and i + 1 < len(styled["shots"])
                and styled["shots"][i + 1]["boundary_hard"]):
            assert tr["duration"] == 0.5
        elif s["transition_out"]:
            assert tr["duration"] == pytest.approx(0.13)
