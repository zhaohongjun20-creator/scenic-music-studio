# -*- coding: utf-8 -*-
"""pytest 公共设施：包路径 + 合成测试音频（120 BPM 节拍脉冲、五段能量）。"""
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SR = 22050


def make_click_track(path: Path, duration: float = 48.0, bpm: float = 120.0) -> Path:
    """五段能量包络的节拍脉冲曲：
    0-8s 弱(0.15) / 8-20 中(0.5) / 20-32 强(1.0) / 32-40 中(0.5) / 40-48 弱(0.12)。
    """
    rng = np.random.default_rng(7)                 # 固定种子保证可复现
    n = int(duration * SR)
    sig = np.zeros(n, dtype=np.float32)
    period = 60.0 / bpm
    length = int(0.03 * SR)
    idx = np.arange(length) / SR
    envlp = np.exp(-idx * 120.0)
    tone = (np.sin(2 * np.pi * 1000.0 * idx) * 0.8
            + rng.uniform(-1.0, 1.0, length) * 0.4).astype(np.float32)
    click = (tone * envlp).astype(np.float32)
    k = 0
    while k * period < duration:
        start = int(k * period * SR)
        end = min(start + length, n)
        sig[start:end] += click[:end - start]
        k += 1
    t = np.arange(n) / SR
    amp = np.interp(
        t, [0, 7.9, 8.0, 19.9, 20.0, 31.9, 32.0, 39.9, 40.0, duration],
        [0.15, 0.15, 0.5, 0.5, 1.0, 1.0, 0.5, 0.5, 0.12, 0.12])
    sig *= amp.astype(np.float32)
    sig *= np.float32(0.7)                         # 留削波余量，避免误触 QC
    pcm = (np.clip(sig, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SR)
        f.writeframes(pcm.tobytes())
    return path


@pytest.fixture(scope="session")
def synth_music(tmp_path_factory) -> Path:
    return make_click_track(tmp_path_factory.mktemp("music") / "click_120bpm.wav")


@pytest.fixture
def synth_map() -> dict:
    """手工构造的 music_map：60s、五段结构、0.5s 节拍网格 + 两个强 onset。"""
    sections = [
        {"index": 0, "role": "opening", "start": 0.0, "end": 10.0, "energy": 0.2},
        {"index": 1, "role": "build", "start": 10.0, "end": 24.0, "energy": 0.5},
        {"index": 2, "role": "climax", "start": 24.0, "end": 40.0, "energy": 0.9},
        {"index": 3, "role": "settle", "start": 40.0, "end": 52.0, "energy": 0.4},
        {"index": 4, "role": "ending", "start": 52.0, "end": 60.0, "energy": 0.15},
    ]
    cands = [{"t": round(i * 0.5, 3), "strength": 0.6, "kind": "bar"}
             for i in range(120)]
    cands += [{"t": 17.2, "strength": 0.95, "kind": "onset"},
              {"t": 30.1, "strength": 0.95, "kind": "onset"}]
    return {"file": "test.wav", "duration_s": 60.0,
            "play_window": {"start": 0.0, "end": 60.0},
            "sections": sections, "cut_candidates": cands}
