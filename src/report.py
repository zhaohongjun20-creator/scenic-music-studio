# -*- coding: utf-8 -*-
"""剪辑决策报告：时间轴 + 音乐结构 → 人读得懂的 markdown。

每个镜头回答三件事：为什么在这个时间切（段落边界/吸附到哪类候选）、
为什么用这段素材（检索词/来源/取材点）、配了什么转场（策略依据）。
不满意某个镜头时，按报告定位 → redo-shot 局部返工。
"""
from pathlib import Path

from src.jsonio import load_json


def _fmt_snap(shot: dict) -> str:
    if shot.get("boundary_hard"):
        return "段落边界（音乐结构硬切点）"
    lock = shot.get("beat_locked")
    if lock:
        return (f"吸附 {lock['kind']} 候选 @ {lock['snapped_to']:.2f}s"
                f"（偏移 {lock['offset_s']:+.2f}s）")
    return "未吸附（容差内无合适候选）"


def build_markdown(pid: str, pdir: Path) -> str:
    brief = load_json(pdir / "brief.json") or {}
    mm = load_json(pdir / "music_map.json") or {}
    tline = load_json(pdir / "timeline.json") or {}
    shots = tline.get("shots", [])
    tempo = mm.get("tempo", {})
    roles = {}
    for s in mm.get("sections", []):
        roles.setdefault(s["role"], []).append(f"{s['start']:.1f}-{s['end']:.1f}s")

    lines = [
        f"# 剪辑决策报告 · {pid}",
        "",
        f"- 主题：{brief.get('theme_zh', '?')}　基调：{brief.get('mood', '?')}　"
        f"平台：{brief.get('platform', '?')}",
        f"- 音乐：`{Path(mm.get('file', '?')).name}`　"
        f"{tempo.get('bpm', '?')} BPM（置信 {tempo.get('confidence', '?')}）　"
        f"播放窗口 {mm.get('play_window', {}).get('start', 0):.0f}-"
        f"{mm.get('play_window', {}).get('end', 0):.0f}s",
        f"- 段落结构：{'；'.join(f'{r} {"/".join(v)}' for r, v in roles.items())}",
        f"- 时间轴：{tline.get('target_s', 0):.1f}s，{len(shots)} 镜，"
        f"最短镜头 {tline.get('min_shot_s', 2.0)}s，"
        f"风格画像{'已应用' if tline.get('style_applied') else '未应用'}",
        "",
        "## 镜头明细",
        "",
        "| # | 时间 | 时长 | 角色 | 切点依据 | 转场 | 素材（取材点） |",
        "|---|---|---|---|---|---|---|",
    ]
    for s in shots:
        a = s.get("asset") or {}
        tr = s.get("transition_out")
        tr_s = f"{tr['style']} {tr['duration']:.2f}s" if tr else "—"
        src = a.get("page_url") or a.get("url") or a.get("id", "?")
        lines.append(
            f"| #{s.get('index')} | {s['start']:.2f}-{s['start'] + s['duration']:.2f}s "
            f"| {s['duration']:.2f}s | {s['role']} | {_fmt_snap(s)} | {tr_s} "
            f"| [{a.get('id', '?')}]({src})"
            f"（{a.get('src_in', 0):.1f}s 起） |")

    locked = sum(1 for s in shots if s.get("beat_locked"))
    lines += [
        "",
        "## 决策规则（为什么这样剪）",
        "",
        "- 段落边界来自音乐实测结构（能量+起振变化点），镜头不跨段落；",
        "- 段内切点在 ±0.6s 容差内向最强候选吸附（段落边界 > 强起振 > 小节线），"
        f"本片 {locked}/{max(len(shots) - 1, 0)} 个内部切点完成吸附，"
        "吸附会压破最短镜头时放弃（接近而非硬卡节拍）；",
        "- 转场：" + ("cut_dominant——段内 0.13s 快切，仅段落边界柔和转场（风格画像）"
                     if tline.get("style_applied") else
                     "按角色选池：高潮区更干脆、首尾更柔和（默认基线）") + "；",
        "- 素材：Pexels 实拍（Pexels License），按导演检索词检索，"
        "相邻镜头不用同一素材；来源与许可见 materials/index.json。",
        "",
        "## 修改指引",
        "",
        "- 某个镜头不满意：`python cli.py redo-shot " + pid + " <#序号> --note 原因`"
        "（只重做该镜头）",
        "- 整体节奏不满意：换风格画像重建（`create --style ...`）或改 seed 重排",
    ]
    return "\n".join(lines) + "\n"


def write_report(pid: str, pdir: Path) -> Path:
    out = pdir / "output" / "decision_report.md"
    out.write_text(build_markdown(pid, pdir), encoding="utf-8")
    return out
