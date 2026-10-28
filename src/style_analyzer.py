# -*- coding: utf-8 -*-
"""参考视频风格分析：样片 → 可测试的剪辑规律（reference_profile.json）。

分析链（全部离线，ffmpeg + numpy）：
  帧差镜头边界检测（直方图距离 × 像素差，自适应阈值；区分硬切/渐变）
  → 镜头时长分布 / 切镜密度随时间的曲线
  → 帧内运动强度（镜头内帧差）分类 static/medium/strong
  → 色彩统计（HSV 饱和度 / 冷暖 / 明度）
  → BGM 节奏（复用 music_analyzer）
  → 聚合多支样片 + 生成"derived"：本产线时间轴/转场/调色的参数建议

用途边界：仅量化剪辑规律（镜头时长、切镜密度、色彩倾向），
不复制参考视频的画面内容与音乐。
"""
import subprocess
from pathlib import Path

import numpy as np

from src.audio_io import ffmpeg_exe
from src.jsonio import save_json
from src.music_analyzer import analyze as analyze_music
from src.material_manager import probe_media

W, H = 160, 90                     # 分析用低分辨率（色彩/帧差足够）
HIST_BINS = 32
MIN_SHOT_S = 0.4                   # 短于此的两刀视为误检合并


def _decode_frames(path: str) -> tuple[np.ndarray, float]:
    """解码为 (N, H, W, 3) uint8 帧序列与帧率。"""
    probe = probe_media(path)
    fps = probe["fps"] or 30.0
    r = subprocess.run(
        [ffmpeg_exe(), "-v", "quiet", "-i", path,
         "-vf", f"scale={W}:{H},fps=10", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, timeout=1800)
    if not r.stdout:
        raise RuntimeError(f"解码失败: {path}")
    frames = np.frombuffer(r.stdout, dtype=np.uint8)
    n = frames.size // (W * H * 3)                 # 帧数（截掉尾部不满一帧的余量）
    if n == 0:
        raise RuntimeError(f"解码失败: {path}")
    return frames[:n * W * H * 3].reshape(n, H, W, 3), 10.0   # 统一 10fps 分析


def _frame_features(frames: np.ndarray):
    """每帧特征：三通道直方图（各 32bin 拼接，归一）与 RGB 帧本身。

    不用灰度：互补色剪切灰度不变（色相变、亮度不变），只有彩色
    通道差分能看见。
    """
    hists = np.stack([
        np.concatenate([np.histogram(frames[i, :, :, c], bins=HIST_BINS,
                                     range=(0, 255))[0] for c in range(3)])
        for i in range(len(frames))])
    hists = hists / np.maximum(hists.sum(axis=1, keepdims=True), 1)
    return frames, hists


def detect_boundaries(frames: np.ndarray, fps: float) -> list[dict]:
    """帧差切镜检测。返回 [{t, kind: hard|gradual, score}]。

    hard：单帧差分尖峰（直切）；gradual：连续 0.3-0.8s 的中等差分爬坡
    （溶解/淡入淡出）。阈值自适应（均值 + k·标准差），带下限。
    """
    rgb, hists = _frame_features(frames)
    n = len(rgb)
    if n < 3:
        return []
    delta = rgb[1:].astype(np.int16) - rgb[:-1].astype(np.int16)
    pix_diff = np.abs(delta).mean(axis=(1, 2, 3)) / 255.0
    hist_dist = 0.5 * np.abs(hists[1:] - hists[:-1]).sum(axis=1)
    score = 0.4 * pix_diff + 0.6 * hist_dist

    thr_hard = max(float(score.mean() + 3.0 * score.std()), 0.08)
    thr_grad = max(float(score.mean() + 1.5 * score.std()), 0.035)
    min_gap = max(2, int(MIN_SHOT_S * fps))

    cuts: list[dict] = []
    i = 0
    while i < n - 1:
        if score[i] >= thr_hard:
            cuts.append({"frame": i + 1, "kind": "hard", "score": float(score[i])})
            i += min_gap
            continue
        if score[i] >= thr_grad:                    # 渐变：看后续是否持续中等偏高
            j, run = i, 0
            while j < n - 1 and score[j] >= thr_grad * 0.6 and run < int(0.8 * fps):
                j += 1
                run += 1
            span = run / fps
            if 0.25 <= span <= 0.9 and (j - i) >= 2:
                cuts.append({"frame": (i + j) // 2, "kind": "gradual",
                             "score": float(score[i:j].mean())})
                i = j + min_gap
                continue
        i += 1
    out = []
    for c in cuts:
        t = round(c["frame"] / fps, 3)
        if out and t - out[-1]["t"] < MIN_SHOT_S:
            if c["score"] > out[-1]["score"]:
                out[-1] = {"t": t, "kind": c["kind"], "score": c["score"]}
        else:
            out.append({"t": t, "kind": c["kind"], "score": round(c["score"], 4)})
    return out


def shot_lengths(boundaries: list[dict], duration: float) -> list[float]:
    edges = [0.0] + [b["t"] for b in boundaries] + [duration]
    return [round(b - a, 3) for a, b in zip(edges, edges[1:]) if b - a > 0.05]


def _color_stats(frames: np.ndarray) -> dict:
    sample = frames[::5]                            # 每 0.5s 一帧
    f = sample.astype(np.float32) / 255.0
    mx, mn = f.max(axis=3), f.min(axis=3)
    sat = np.where(mx > 1e-6, (mx - mn) / np.maximum(mx, 1e-6), 0.0)
    warmth = (f[..., 0] - f[..., 2]).mean(axis=(1, 2))     # R - B
    val = f.mean(axis=(1, 2, 3))
    return {"saturation": round(float(np.median(sat)), 3),
            "warmth": round(float(np.median(warmth)), 3),
            "brightness": round(float(np.median(val)), 3)}


def _motion_stats(frames: np.ndarray, boundaries: list[dict], fps: float) -> dict:
    delta = frames[1:].astype(np.int16) - frames[:-1].astype(np.int16)
    diff = np.abs(delta).mean(axis=(1, 2, 3)) / 255.0
    cut_frames = {int(b["t"] * fps) for b in boundaries}
    inside = np.array([i for i in range(len(diff))
                       if i not in cut_frames and (i + 1) not in cut_frames])
    if inside.size == 0:
        return {"static_frac": 1.0, "medium_frac": 0.0, "strong_frac": 0.0}
    d = diff[inside]
    q25, q75 = np.quantile(d, [0.25, 0.75])
    return {"static_frac": round(float((d <= q25).mean()), 3),
            "medium_frac": round(float(((d > q25) & (d < q75)).mean()), 3),
            "strong_frac": round(float((d >= q75).mean()), 3),
            "frame_diff_p25": round(float(q25), 4),
            "frame_diff_p75": round(float(q75), 4)}


def density_by_third(boundaries: list[dict], duration: float) -> list[float]:
    """Count actual cuts in equal *time* thirds, not equal groups of shots."""
    if duration <= 0:
        raise ValueError("样片时长必须大于零")
    counts = [0, 0, 0]
    for boundary in boundaries:
        t = float(boundary["t"])
        if 0 < t < duration:
            counts[min(2, int(t * 3 / duration))] += 1
    return [round(n * 180.0 / duration, 2) for n in counts]


def analyze_sample(path: str) -> dict:
    frames, fps = _decode_frames(path)
    duration = round(len(frames) / fps, 2)
    boundaries = detect_boundaries(frames, fps)
    lens = shot_lengths(boundaries, duration)
    arr = np.array(lens) if lens else np.array([duration])
    return {
        "file": str(path),
        "duration_s": duration,
        "shots": len(lens),
        "shots_per_minute": round(len(lens) / (duration / 60.0), 2),
        "len_median": round(float(np.median(arr)), 3),
        "len_p25": round(float(np.quantile(arr, 0.25)), 3),
        "len_p75": round(float(np.quantile(arr, 0.75)), 3),
        "len_min": round(float(arr.min()), 3),
        "len_max": round(float(arr.max()), 3),
        "density_by_third_cpm": density_by_third(boundaries, duration),
        "cuts_per_minute": round(len(boundaries) * 60.0 / duration, 2),
        "metric_version": 2,  # v1 erroneously divided shots into equal-count groups
        "hard_cut_frac": round(
            sum(1 for b in boundaries if b["kind"] == "hard")
            / max(len(boundaries), 1), 3),
        "color": _color_stats(frames),
        "motion": _motion_stats(frames, boundaries, fps),
    }


def _pct(vals: list[float], q: float) -> float:
    return round(float(np.quantile(np.array(vals), q)), 3)


def derive(sample_stats: list[dict]) -> dict:
    """从样片统计生成本产线参数建议（人工可在 profile 里改）。"""
    medians = [s["len_median"] for s in sample_stats]
    p25 = _pct([s["len_p25"] for s in sample_stats], 0.5)
    p75 = _pct([s["len_p75"] for s in sample_stats], 0.5)
    hard = float(np.mean([s["hard_cut_frac"] for s in sample_stats]))
    sat = float(np.mean([s["color"]["saturation"] for s in sample_stats]))
    warmth = float(np.mean([s["color"]["warmth"] for s in sample_stats]))
    all_thirds = [v for s in sample_stats for v in s["density_by_third_cpm"]]
    # 硬切为主：本产线转场以 cut 为主，仅段落边界允许柔和转场
    dense, calm = max(all_thirds), min(all_thirds)
    return {
        "role_shot_len": {           # 替代 timeline.ROLE_SHOT_LEN 默认值
            "opening": [round(max(p25, 1.2), 2), round(max(p75, 1.2), 2)],
            "build": [round(max(p25, 1.0), 2), round(max(p75, 1.0), 2)],
            "climax": [round(max(p25 * 0.8, 0.8), 2), round(max(p75 * 0.8, 0.8), 2)],
            "settle": [round(max(p25, 1.4), 2), round(max(p75 * 1.2, 1.4), 2)],
            "ending": [round(max(p25, 1.4), 2), round(max(p75 * 1.3, 1.4), 2)],
        },
        "min_shot_s": max(1.0, round(p25 * 0.85, 2)),
        "hard_cut_frac_observed": round(hard, 3),
        "transition_policy": {
            "mode": "cut_dominant" if hard >= 0.7 else "mixed",
            "soft_pool": ["fade", "dissolve"],      # 仅段落边界用
            "soft_transition_s": 0.5,
        },
        "density_ratio_climax_to_calm": round(dense / max(calm, 0.1), 2),
        "video": {                 # 调色建议（saturation 基线 + 暖冷倾向）
            "saturation": round(min(1.0 + max(sat - 0.35, -0.1), 1.35), 3),
            "warmth_hint": "warm" if warmth > 0.02 else
                           ("cool" if warmth < -0.02 else "neutral"),
        },
    }


def build_profile(sample_paths: list[str], channel: str,
                  extra: dict | None = None) -> dict:
    stats = [analyze_sample(p) for p in sample_paths]
    audio = []
    for p in sample_paths:
        try:
            mm = analyze_music(p, target_s=None)
            audio.append({"file": Path(p).name,
                          "bpm": mm["tempo"]["bpm"],
                          "confidence": mm["tempo"]["confidence"]})
        except (RuntimeError, subprocess.TimeoutExpired):
            continue
    import time as _t
    return {
        "source": {"channel": channel,
                   "samples": [{"file": Path(p).name, "duration_s": s["duration_s"],
                                "shots": s["shots"]} for p, s in zip(sample_paths, stats)],
                   "analyzed_at": _t.strftime("%Y-%m-%dT%H:%M:%S"),
                   **(extra or {})},
        "samples": stats,
        "audio": audio,
        "aggregate": {
            "shots_per_minute_median": _pct([s["shots_per_minute"] for s in stats], 0.5),
            "len_median_of_medians": _pct([s["len_median"] for s in stats], 0.5),
            "hard_cut_frac_mean": round(
                float(np.mean([s["hard_cut_frac"] for s in stats])), 3),
        },
        "derived": derive(stats),
        "note": "样片仅用于剪辑规律分析（镜头时长/密度/色彩），不复制画面与音乐内容。",
    }


def save_profile(profile: dict, path) -> None:
    save_json(path, profile)
