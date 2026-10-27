# -*- coding: utf-8 -*-
"""时间轴规划：storyboard 的镜头计划 × 音乐结构 → timeline.json。

原则（对应设计文档）：
- 段落边界（乐句结构）是硬约束：镜头不跨段落，段落时长按镜头数铺满；
- 段内切点向最近的切镜候选点吸附（±容差），吸附会让相邻镜头短于
  下限时放弃——"切点接近节拍/乐句"，而不是强行卡拍；
- 首尾与回落段保留长镜头与情绪停顿，高潮区切镜更紧凑。

时间轴严格铺满 target_s（误差 < 1e-6）。每个镜头的 render_length
已含 xfade 重叠的均摊量：Σrender_length − Σ转场时长 = target_s。
"""
import random

from src.music_analyzer import window_sections

# 各角色单镜头时长参考区间（继承 see-china music_edit 的经验值）
ROLE_SHOT_LEN = {
    "opening": (3.5, 6.0),
    "build": (3.0, 5.0),
    "climax": (2.4, 4.2),
    "settle": (4.0, 6.5),
    "ending": (4.5, 7.0),
}

# 转场按切进镜头所属段落的角色选池（必须是 ffmpeg xfade 合法样式）
ROLE_TRANSITIONS = {
    "opening": ["fade", "dissolve"],
    "build": ["dissolve", "circleopen"],
    "climax": ["dissolve", "circleopen", "wipeleft"],
    "settle": ["fade", "dissolve"],
    "ending": ["fade"],
}

# 转场时长（秒）：高潮区更干脆，首尾更柔和
ROLE_TRANSITION_S = {"opening": 0.8, "build": 0.7, "climax": 0.5,
                     "settle": 0.8, "ending": 1.0}

FALLBACK_ROLE = "build"


class TimelineError(ValueError):
    """时间轴不可行（镜头数装不下 / 违反最短镜头约束）。"""


def _fit_durations(hints: list[float], total: float, min_s: float) -> list[float]:
    """把段落时长按 hints 比例分给各镜头，任何镜头不短于 min_s。"""
    d = [max(float(h), 0.1) for h in hints]
    scale = total / sum(d)
    d = [x * scale for x in d]
    for _ in range(30):
        deficit = sum(max(min_s - x, 0.0) for x in d)
        if deficit <= 1e-9:
            break
        room = sum(x - min_s for x in d if x > min_s + 1e-9)
        if room < deficit - 1e-9:
            raise TimelineError(
                f"段落时长 {total:.2f}s 装不下 {len(d)} 个 ≥{min_s}s 的镜头")
        d = [x - (x - min_s) * deficit / room if x > min_s + 1e-9 else min_s
             for x in d]
    d[-1] += total - sum(d)          # 浮点残差归末镜，严格铺满
    return d


def _snap_cut(t: float, candidates: list[dict], tol: float,
              penalty: float) -> dict | None:
    """为切点 t 找最佳候选：分数 = 强度 − 罚分×偏移；容差外返回 None。"""
    best, best_score = None, -1e9
    for c in candidates:
        delta = abs(c["t"] - t)
        if delta > tol:
            continue
        score = c["strength"] - penalty * delta
        if score > best_score:
            best, best_score = c, score
    return best


def plan_from_storyboard(storyboard: dict, music_map: dict,
                         cfg_timeline: dict, video_cfg: dict,
                         rng=None) -> dict:
    """生成 timeline dict（asset 字段留空，由素材管理器填充）。"""
    rng = rng or random
    min_s = float(cfg_timeline["min_shot_s"])
    tol = float(cfg_timeline["snap_tolerance_s"])
    penalty = float(cfg_timeline["snap_penalty"])

    sections = window_sections(music_map)
    if not sections:
        raise TimelineError("播放窗口内没有音乐段落")
    sb_sections = storyboard["sections"]
    if len(sb_sections) != len(sections):
        raise TimelineError(
            f"storyboard 段落数 {len(sb_sections)} != 音乐段落数 {len(sections)}")

    win0 = music_map["play_window"]["start"]
    target_s = round(music_map["play_window"]["end"] - win0, 3)
    cands = [c for c in music_map.get("cut_candidates", [])
             if 0.0 <= c["t"] - win0 <= target_s]
    cands = [{**c, "t": round(c["t"] - win0, 3)} for c in cands]

    slots: list[dict] = []
    for sec, sb in zip(sections, sb_sections):
        shots = sb["shots"]
        if not shots:
            raise TimelineError(f"段落 {sec['index']}（{sec['role']}）没有镜头")
        durs = _fit_durations([s.get("duration_hint") or 0 for s in shots],
                              sec["end"] - sec["start"], min_s)
        for j, (shot, d) in enumerate(zip(shots, durs)):
            slots.append({
                "index": len(slots),
                "shot_id": shot.get("shot_id") or f"s{len(slots)+1:02d}",
                "scene": shot.get("scene", ""),
                "mood": shot.get("mood_zh") or shot.get("mood", ""),
                "role": sec["role"],
                "section_index": sec["index"],
                "start": round(sec["start"], 3),
                "duration": round(float(d), 6),
                "boundary_hard": j == 0,       # 段首切点 = 段落边界，不吸附
                "beat_locked": None,
            })
    # 重新计算精确 start
    t = 0.0
    for s in slots:
        s["start"] = round(t, 6)
        t += s["duration"]
    if abs(t - target_s) > 1e-3:
        raise TimelineError(f"镜头总时长 {t:.3f}s != 目标 {target_s:.3f}s")

    # 段内切点吸附（段首跳过：段落边界本身是音乐结构，不挪）
    for i in range(1, len(slots)):
        s = slots[i]
        if s["boundary_hard"]:
            s["beat_locked"] = None
            continue
        prev_end = round(s["start"] + s["duration"], 6)
        cand = _snap_cut(s["start"], cands, tol, penalty)
        ok = cand is not None
        if ok:
            new_d_prev = cand["t"] - slots[i - 1]["start"]
            new_d_self = prev_end - cand["t"]
            ok = (new_d_prev >= min_s - 1e-6 and new_d_self >= min_s - 1e-6)
        if ok:
            s["beat_locked"] = {"snapped_to": cand["t"],
                                "offset_s": round(cand["t"] - s["start"], 3),
                                "kind": cand["kind"]}
            s["start"] = round(cand["t"], 6)
        else:
            s["beat_locked"] = None
    for i, s in enumerate(slots):
        if i + 1 < len(slots):
            s["duration"] = round(slots[i + 1]["start"] - s["start"], 6)
    slots[-1]["duration"] = round(target_s - slots[-1]["start"], 6)

    # 转场与渲染长度
    trans = [None]        # trans[i] = 切进镜头 i 的转场（i=0 无）
    for i in range(1, len(slots)):
        role = slots[i]["role"]
        pool = ROLE_TRANSITIONS.get(role, ROLE_TRANSITIONS[FALLBACK_ROLE])
        trans.append({"style": rng.choice(pool),
                      "duration": float(ROLE_TRANSITION_S.get(
                          role, ROLE_TRANSITION_S[FALLBACK_ROLE]))})
    for i, s in enumerate(slots):
        s["transition_out"] = trans[i + 1] if i + 1 < len(slots) else None
        t_in = trans[i]["duration"] if i > 0 else 0.0
        t_out = trans[i + 1]["duration"] if i + 1 < len(slots) else 0.0
        s["render_length"] = round(s["duration"] + (t_in + t_out) / 2.0, 6)
        s["revision"] = 0
        s["asset"] = None

    return {
        "version": 1,
        "target_s": target_s,
        "resolution": [int(video_cfg["width"]), int(video_cfg["height"])],
        "fps": int(video_cfg["fps"]),
        "audio": {
            "file": music_map["file"],
            "offset_s": round(win0, 3),
            "fade_in_s": None,        # 由 pipeline 按 config 填
            "fade_out_s": None,
            "loudnorm": None,
        },
        "shots": slots,
    }


def validate_timeline(timeline: dict, cfg_timeline: dict) -> list[str]:
    """时间轴不变量检查（测试与 QC 共用）。返回问题列表，空即通过。"""
    problems = []
    min_s = float(cfg_timeline["min_shot_s"])
    shots = timeline["shots"]
    if not shots:
        return ["时间轴没有镜头"]
    t = 0.0
    for i, s in enumerate(shots):
        if abs(s["start"] - t) > 1e-3:
            problems.append(f"镜头 {i} 起点 {s['start']} 不连续（应为 {t:.3f}）")
        if s["duration"] < min_s - 1e-4:
            problems.append(f"镜头 {i} 时长 {s['duration']:.2f}s < 下限 {min_s}s")
        t += s["duration"]
    if abs(t - timeline["target_s"]) > 1e-3:
        problems.append(f"镜头总时长 {t:.3f}s != target {timeline['target_s']}s")
    total_rl = sum(s["render_length"] for s in shots)
    total_tr = sum((s["transition_out"] or {"duration": 0})["duration"] for s in shots)
    if abs(total_rl - total_tr - timeline["target_s"]) > 1e-3:
        problems.append("render_length 与转场时长不守恒")
    tol = float(cfg_timeline["snap_tolerance_s"])
    for i, s in enumerate(shots):
        lock = s.get("beat_locked")
        if lock and not s.get("boundary_hard") and abs(lock["offset_s"]) > tol + 1e-6:
            problems.append(f"镜头 {i} 吸附偏移 {lock['offset_s']} 超容差")
    return problems
