# -*- coding: utf-8 -*-
"""pytest 公共设施：包路径 + 合成测试音频（可调 BPM/能量包络的节拍脉冲）。"""
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SR = 22050

# 默认能量包络：弱起(0-8s) → 铺陈(8-20) → 强段(20-32) → 回落(32-40) → 收尾(40-48)
DEFAULT_ENV = [(0.0, 0.15), (7.9, 0.15), (8.0, 0.5), (19.9, 0.5), (20.0, 1.0),
               (31.9, 1.0), (32.0, 0.5), (39.9, 0.5), (40.0, 0.12), (48.0, 0.12)]


def make_click_track(path: Path, duration: float = 48.0, bpm: float = 120.0,
                     env: list | None = None) -> Path:
    """节拍脉冲合成曲：能量包络（时间断点列表）决定段落结构。"""
    env = env or DEFAULT_ENV
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
    amp = np.interp(t, [p[0] for p in env], [p[1] for p in env])
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


@pytest.fixture
def iso(tmp_path, monkeypatch):
    """隔离存储：项目/素材缓存/索引全部落到临时目录。"""
    from src import material_manager, store
    monkeypatch.setattr(store, "ROOT", tmp_path)
    monkeypatch.setattr(material_manager, "CACHE_DIR", tmp_path / "mcache")
    monkeypatch.setattr(material_manager, "INDEX_PATH", tmp_path / "mindex.json")
    return tmp_path
