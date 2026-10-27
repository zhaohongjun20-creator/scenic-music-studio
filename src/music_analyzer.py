# -*- coding: utf-8 -*-
"""音乐理解引擎：一段音频 → 结构化 music_map。

设计文档的核心要求：不止检测 BPM。输出五类信息——
  tempo/节拍网格、能量曲线、起振强度、段落边界（intro→climax→outro）、
  切镜候选点（段落边界 + 强起振 + 小节线），以及播放窗口选择。

实现为纯 numpy STFT，不引入 librosa/numba（轻依赖、跨版本稳定）；
analyze() 是唯一入口，后端可整体替换。

时间单位约定：能量/起振曲线的帧率 ENV_FPS ≈ 43 帧/秒；落盘曲线降采样到
CURVE_FPS = 20；所有落盘时间为秒（保留 3 位小数）。
"""
import numpy as np

from src.audio_io import SR, load_mono
from src.jsonio import save_json

FFT_SIZE = 2048
HOP = 512
ENV_FPS = SR / HOP                    # ≈ 43.07
CURVE_FPS = 20                        # 落盘曲线的时间分辨率

ROLES = ("opening", "build", "climax", "settle", "ending")


# ---------------------------------------------------------------- 基础曲线

def _smooth(x: np.ndarray, seconds: float) -> np.ndarray:
    """滑动平均（两端用不完整窗口归一，避免边缘塌陷）。"""
    w = max(1, int(round(seconds * ENV_FPS)))
    kernel = np.ones(w)
    num = np.convolve(x, kernel, mode="same")
    den = np.convolve(np.ones_like(x), kernel, mode="same")
    return num / np.maximum(den, 1e-9)


def _stft_mag(sig: np.ndarray) -> np.ndarray:
    win = np.hanning(FFT_SIZE).astype(np.float32)
    frames = np.lib.stride_tricks.sliding_window_view(sig, FFT_SIZE)[::HOP]
    return np.abs(np.fft.rfft(frames * win, axis=1))


def _energy_curve(mag: np.ndarray) -> np.ndarray:
    """帧级 RMS（由频谱能量按 Parseval 定理近似），0.5s 平滑后归一。"""
    rms = np.sqrt((mag ** 2).sum(axis=1) / FFT_SIZE)
    e = _smooth(rms, 0.5)
    return e / max(float(e.max()), 1e-12)


def _onset_envelope(mag: np.ndarray) -> np.ndarray:
    """谱通量起振强度：压缩 → 半波整流差分 → 去局部均值 → 归一。"""
    comp = np.sqrt(mag)
    diff = np.diff(comp, axis=0, prepend=comp[:1])
    flux = np.maximum(diff, 0.0).sum(axis=1)
    flux = _smooth(flux, 0.08)
    onset = np.maximum(flux - _smooth(flux, 1.0), 0.0)
    peak = float(onset.max())
    return onset / peak if peak > 0 else onset


# ---------------------------------------------------------------- tempo/节拍

def _estimate_tempo(onset: np.ndarray) -> tuple[float, float]:
    """onset 包络自相关 + 以 110 BPM 为中心的轻先验（抑制八度错判）。"""
    ac = np.correlate(onset, onset, mode="full")[len(onset) - 1:]
    ac = ac / max(float(ac[0]), 1e-12)
    lo = max(2, int(ENV_FPS * 60 / 190))
    hi = min(int(ENV_FPS * 60 / 60) + 1, len(ac) - 2)
    best_lag, best_score = None, -1.0
    for lag in range(lo, hi):
        bpm = 60.0 * ENV_FPS / lag
        prior = float(np.exp(-0.5 * ((bpm - 110.0) / 50.0) ** 2))
        score = float(ac[lag]) * prior
        if score > best_score:
            best_score, best_lag = score, lag
    if best_lag is None:
        return 0.0, 0.0
    y0, y1, y2 = float(ac[best_lag - 1]), float(ac[best_lag]), float(ac[best_lag + 1])
    denom = y0 - 2 * y1 + y2
    shift = 0.5 * (y0 - y2) / denom if abs(denom) > 1e-12 else 0.0
    lag_f = best_lag + float(np.clip(shift, -1.0, 1.0))
    bpm = 60.0 * ENV_FPS / lag_f
    conf = float(np.clip(float(ac[best_lag]) * 4.0, 0.0, 1.0))
    return bpm, conf


def _beat_grid(onset: np.ndarray, bpm: float, duration_s: float) -> list[float]:
    """在 tempo 周期上搜索相位（48 个候选取 onset 能量和最大者），铺满全曲。"""
    if bpm <= 0:
        return []
    period = 60.0 * ENV_FPS / bpm
    n = len(onset)
    base = np.arange(n, dtype=np.float64)
    ks = np.arange(0, n / period + 2)
    best_phi, best_score = 0.0, -1.0
    for phi in np.linspace(0.0, period, 48, endpoint=False):
        times = phi + ks * period
        times = times[times <= n - 1]
        if times.size == 0:
            continue
        score = float(np.interp(times, base, onset).sum())
        if score > best_score:
            best_score, best_phi = score, float(phi)
    beats, t = [], best_phi / ENV_FPS
    step = period / ENV_FPS
    while t < duration_s:
        beats.append(round(t, 3))
        t += step
    return beats


# ---------------------------------------------------------------- 段落

def _detect_segments(energy: np.ndarray, onset: np.ndarray,
                     min_novelty: float) -> list[tuple[float, float]]:
    """能量×起振密度的窗口差异（novelty）找变化点 → 段落列表 [(s,e)]。

    至少 4 段（结构平缓时退回固定比例切分），至多 7 段，段间最短 6 秒。
    """
    feat = 0.6 * _smooth(energy, 2.0) + 0.4 * _smooth(onset, 2.0)
    n = len(feat)
    w = int(2.0 * ENV_FPS)
    csum = np.concatenate(([0.0], np.cumsum(feat)))

    def mean_win(a: int, b: int) -> float:
        a, b = max(0, a), min(n, b)
        return float((csum[b] - csum[a]) / max(b - a, 1))

    novelty = np.zeros(n)
    for t in range(w, n - w):
        novelty[t] = abs(mean_win(t - w, t) - mean_win(t, t + w))
    novelty = _smooth(novelty, 0.5)

    min_dist = int(6.0 * ENV_FPS)
    margin = int(2.0 * ENV_FPS)
    bounds: list[int] = []
    for i in np.argsort(novelty)[::-1]:
        if novelty[i] < min_novelty:
            break
        i = int(i)
        if i < margin or i > n - margin:
            continue
        if all(abs(i - b) >= min_dist for b in bounds):
            bounds.append(i)
        if len(bounds) >= 6:
            break
    bounds.sort()
    if len(bounds) < 2:
        bounds = [int(n * f) for f in (0.18, 0.62, 0.85)]
    edges = [0] + bounds + [n]
    return [(edges[i] / ENV_FPS, edges[i + 1] / ENV_FPS)
            for i in range(len(edges) - 1)]


def _assign_roles(segments: list[tuple[float, float]],
                  energy: np.ndarray) -> list[dict]:
    """按能量序映射角色：最高能段 = climax，首段 opening（除非即高潮），尾段 ending。"""
    energies = [float(np.mean(energy[int(a * ENV_FPS):max(int(b * ENV_FPS), int(a * ENV_FPS) + 1)]))
                for a, b in segments]
    climax_idx = int(np.argmax(energies))
    out = []
    for i in range(len(segments)):
        if i == climax_idx:
            role = "climax"
        elif i == len(segments) - 1:
            role = "ending"
        elif i == 0:
            role = "opening"
        elif i < climax_idx:
            role = "build"
        else:
            role = "settle"
        out.append({"index": i, "role": role, "start": round(segments[i][0], 3),
                    "end": round(segments[i][1], 3), "energy": round(energies[i], 4)})
    return out


def _select_play_window(sections: list[dict], energy: np.ndarray,
                        target_s: float, duration_s: float) -> float:
    """选播放窗口：整曲够短用全曲；否则滑窗取能量积分最高、
    且尽量完整覆盖高潮段的 target_s 窗口，返回窗口起点（秒）。"""
    if duration_s <= target_s + 2.0:
        return 0.0
    climax = next((s for s in sections if s["role"] == "climax"), None)
    best_start, best_score = 0.0, -1.0
    for st in np.arange(0.0, max(duration_s - target_s, 0.0), 1.0):
        st, en = float(st), float(st) + target_s
        seg = energy[int(st * ENV_FPS):int(en * ENV_FPS)]
        score = float(np.mean(seg)) if seg.size else 0.0
        if climax:
            overlap = min(en, climax["end"]) - max(st, climax["start"])
            span = max(climax["end"] - climax["start"], 1e-6)
            score += 0.25 * float(np.clip(overlap / span, 0.0, 1.0))
        if score > best_score:
            best_score, best_start = score, st
    return round(best_start, 3)


# ---------------------------------------------------------------- 切镜候选

def _cut_candidates(sections: list[dict], onset: np.ndarray,
                    beats: list[float], beats_per_bar: int) -> list[dict]:
    """三类候选：段落边界(1.0) > 强起振(0-1) > 小节线(0.5)；0.15s 内合并取强者。"""
    cands: list[dict] = []
    for s in sections[1:]:
        cands.append({"t": float(s["start"]), "strength": 1.0, "kind": "section"})
    thr = float(np.quantile(onset, 0.80))
    min_sep = int(0.25 * ENV_FPS)
    peaks: list[tuple[int, float]] = []
    last = -10 ** 9
    for i in range(1, len(onset) - 1):
        v = float(onset[i])
        if v >= thr and v >= float(onset[i - 1]) and v > float(onset[i + 1]) \
                and i - last >= min_sep:
            peaks.append((i, v))
            last = i
    peaks.sort(key=lambda p: -p[1])
    peaks = peaks[:150]
    cands += [{"t": round(i / ENV_FPS, 3), "strength": round(v, 3), "kind": "onset"}
              for i, v in peaks]
    for k in range(0, len(beats), max(1, beats_per_bar)):
        cands.append({"t": float(beats[k]), "strength": 0.5, "kind": "bar"})
    cands.sort(key=lambda c: c["t"])
    merged: list[dict] = []
    for c in cands:
        if merged and c["t"] - merged[-1]["t"] < 0.15:
            if c["strength"] > merged[-1]["strength"]:
                merged[-1] = c
        else:
            merged.append(c)
    return merged


# ---------------------------------------------------------------- 入口

def _decimate(x: np.ndarray, duration_s: float) -> list[float]:
    count = max(2, int(duration_s * CURVE_FPS))
    idx = np.linspace(0, len(x) - 1, count).astype(int)
    return [round(float(v), 4) for v in x[idx]]


def analyze(path: str, target_s: float | None = None,
            beats_per_bar: int = 4,
            min_novelty: float = 0.12) -> dict:
    """分析整曲并选择播放窗口。target_s 给定时窗口长度取它。"""
    sig, sr = load_mono(path)
    duration_s = round(len(sig) / sr, 3)
    mag = _stft_mag(sig)
    energy = _energy_curve(mag)
    onset = _onset_envelope(mag)
    del mag

    bpm, conf = _estimate_tempo(onset)
    beats = _beat_grid(onset, bpm, duration_s) if conf >= 0.15 else []
    sections = _assign_roles(_detect_segments(energy, onset, min_novelty), energy)
    win_len = float(target_s) if target_s else duration_s
    win_start = _select_play_window(sections, energy, win_len, duration_s)

    climax = next((s for s in sections if s["role"] == "climax"), None)
    peak_at = None
    if climax:
        seg = energy[int(climax["start"] * ENV_FPS):int(climax["end"] * ENV_FPS)]
        if seg.size:
            peak_at = round(climax["start"] + float(np.argmax(seg)) / ENV_FPS, 3)

    return {
        "file": str(path),
        "duration_s": duration_s,
        "sample_rate": sr,
        "tempo": {"bpm": round(bpm, 2), "confidence": round(conf, 3)},
        "beat_interval_s": round(60.0 / bpm, 4) if bpm > 0 else None,
        "beats": beats,
        "curves": {"fps": CURVE_FPS,
                   "energy": _decimate(energy, duration_s),
                   "onset": _decimate(onset, duration_s)},
        "sections": sections,
        "climax": ({"start": climax["start"], "end": climax["end"],
                    "peak_at": peak_at} if climax else None),
        "play_window": {"start": win_start, "end": round(win_start + win_len, 3)},
        "cut_candidates": _cut_candidates(sections, onset, beats, beats_per_bar),
        "analyzer": {"backend": "numpy-stft-v1"},
    }


def window_sections(music_map: dict, min_len_s: float = 2.0) -> list[dict]:
    """取播放窗口内的段落，时间换算到视频时间轴（窗口起点为 0）。

    过碎的段落（窗口切割边缘、检测噪声）并入更大的邻居——存活段落
    必须能装下至少一个最短镜头（默认 2s），否则下游时间轴必然不可行。
    """
    w0 = music_map["play_window"]["start"]
    w1 = music_map["play_window"]["end"]
    frags = []
    for s in music_map["sections"]:
        a, b = max(s["start"], w0), min(s["end"], w1)
        if b - a > 1e-6:
            frags.append({**s, "start": round(a - w0, 3), "end": round(b - w0, 3)})
    frags.sort(key=lambda x: x["start"])
    while len(frags) > 1:
        sizes = [f["end"] - f["start"] for f in frags]
        i = min(range(len(frags)), key=lambda k: sizes[k])
        if sizes[i] >= min_len_s:
            break
        if i == 0:
            j = 1
        elif i == len(frags) - 1:
            j = i - 1
        else:
            j = i + 1 if sizes[i + 1] >= sizes[i - 1] else i - 1
        lo, hi = min(i, j), max(i, j)
        keep = frags[j] if sizes[j] >= sizes[i] else frags[i]
        merged = dict(keep)                      # 保留较大者的角色与能量
        merged["start"], merged["end"] = frags[lo]["start"], frags[hi]["end"]
        frags[lo:hi + 1] = [merged]
    if frags:
        # 段落边界按帧量化，可能比窗口短几十毫秒：末段强制补齐到窗口长度，
        # 首段起点对齐 0，保证 Σ段时长 == target_s
        frags[-1]["end"] = round(w1 - w0, 3)
        frags[0]["start"] = 0.0
    for k, f in enumerate(frags):
        f["index"] = k
        f["start"], f["end"] = round(f["start"], 3), round(f["end"], 3)
    return frags


def save_music_map(music_map: dict, path) -> None:
    save_json(path, music_map)
