# -*- coding: utf-8 -*-
"""音频解码：任意 ffmpeg 认识的格式 → 单声道 float32。

优先用 PATH 里的 ffmpeg，退回 imageio-ffmpeg 随 pip 安装的二进制，
不引入额外解码依赖。
"""
import shutil
import subprocess

import imageio_ffmpeg
import numpy as np

SR = 22050


def ffmpeg_exe() -> str:
    return shutil.which("ffmpeg") or imageio_ffmpeg.get_ffmpeg_exe()


def ffprobe_exe() -> str | None:
    """PATH 里一般带 ffprobe；没有就返回 None（调用方退回 ffmpeg -i 解析）。"""
    return shutil.which("ffprobe")


def load_mono(path: str, target_sr: int = SR) -> tuple[np.ndarray, int]:
    """解码为 (单声道 float32 信号, 采样率)。失败抛 RuntimeError。"""
    cmd = [ffmpeg_exe(), "-v", "quiet", "-i", str(path),
           "-ac", "1", "-ar", str(target_sr), "-f", "f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True, timeout=600)
    if raw.returncode != 0 or not raw.stdout:
        raise RuntimeError(
            f"音频解码失败: {path} ({raw.stderr[:200]!r})")
    sig = np.frombuffer(raw.stdout, dtype=np.float32)
    if sig.size < target_sr:                      # 少于 1 秒直接拒绝
        raise RuntimeError(f"音频过短: {path}")
    return sig, target_sr
