# -*- coding: utf-8 -*-
"""Pexels 候选的入点与人物筛选（下载 mp4 之前）。

导演写在镜头上的 motion_hint 在这里才参与选窗：
- aerial / push / pan：要一段有运动、且窗口内部没有硬切的入点；
- static：要一段帧差低的入点；
- 没有 hint、或预览帧不足以比较时，仍用时长前 1/3。

人物判断用的是入点附近的一帧预览，不是成片抽帧。检测器不可用
（未安装 opencv，或初始化失败）时放行，由调用方把原因记在素材上。
"""
import subprocess

import numpy as np
import requests

from src.audio_io import ffmpeg_exe

TAIL_MARGIN_S = 0.2
# 低于这个绝对帧差的台阶一律不当成硬切（连续运镜的正常帧差）。
_CUT_FLOOR = 0.22
# 窗口之间差不到这一档，就当成测不出运动，退回前 1/3。
# 预览图上 1% 的平均亮度变化以下分不出「有运动」和压缩噪声。
_MOTION_FLOOR = 0.01

MOTION_HINTS = {"aerial", "push", "pan"}
STATIC_HINT = "static"

# 人物检测器不可用时的固定说明（素材 people_check_note 与 PR 一致）。
DETECTOR_UNAVAILABLE_NOTE = (
    "人物检测不可用（未安装 opencv 或检测器初始化失败），"
    "本候选未做人物过滤而放行"
)


class PeopleVerdict:
    """人物检测的一次结论。status: clean / rejected / unavailable。"""

    __slots__ = ("status", "detail")

    def __init__(self, status: str, detail: str = ""):
        self.status = status
        self.detail = detail


class UnavailablePeopleDetector:
    """检测器缺失。check 永远是 unavailable，调用方必须放行。"""

    available = False

    def __init__(self, detail: str = DETECTOR_UNAVAILABLE_NOTE):
        self.detail = detail

    def check(self, frame) -> PeopleVerdict:
        return PeopleVerdict("unavailable", self.detail)


class HogPeopleDetector:
    """OpenCV 自带的 HOG 行人检测器。权重在 opencv 包内，不另行下载。"""

    available = True

    def __init__(self, cv2_module):
        hog = cv2_module.HOGDescriptor()
        hog.setSVMDetector(cv2_module.HOGDescriptor_getDefaultPeopleDetector())
        self._cv2 = cv2_module
        self._hog = hog
        self.detail = "opencv-hog"

    def check(self, frame) -> PeopleVerdict:
        img = _as_uint8_gray(frame)
        if img.size == 0:
            return PeopleVerdict("rejected", "empty_frame")
        h, w = img.shape[:2]
        # 默认行人窗是 64x128，预览图更小就先放大，否则 detect 直接空跑。
        if h < 256 or w < 128:
            scale = max(256 / max(h, 1), 128 / max(w, 1))
            img = self._cv2.resize(
                img, (max(128, int(round(w * scale))),
                      max(256, int(round(h * scale)))),
                interpolation=self._cv2.INTER_LINEAR)
        rects, _weights = self._hog.detectMultiScale(
            img, winStride=(8, 8), padding=(8, 8), scale=1.05)
        if len(rects):
            return PeopleVerdict("rejected", f"hog:{len(rects)}")
        return PeopleVerdict("clean", "hog")


def resolve_people_detector(detector=None):
    """显式检测器优先；否则尝试 HOG，失败则返回不可用检测器（放行）。"""
    if detector is not None:
        return detector
    try:
        import cv2
    except ImportError:
        return UnavailablePeopleDetector(DETECTOR_UNAVAILABLE_NOTE)
    try:
        return HogPeopleDetector(cv2)
    except Exception as exc:
        return UnavailablePeopleDetector(
            f"{DETECTOR_UNAVAILABLE_NOTE}（{type(exc).__name__}）")


def detector_available(detector) -> bool:
    return bool(getattr(detector, "available", True))


def legacy_src_in(duration: float, need: float) -> float:
    """时长前 1/3；剩余不够截满镜头时贴尾部。"""
    duration = float(duration)
    need = float(need)
    third = duration / 3.0
    if third + need <= duration - TAIL_MARGIN_S:
        return round(third, 3)
    return round(max(0.0, duration - need - TAIL_MARGIN_S), 3)


def clamp_src_in(src_in: float, duration: float, need: float) -> float:
    """探测时长和 API 时长不一致时，把入点夹进仍能截满的范围。"""
    latest = float(duration) - float(need) - TAIL_MARGIN_S
    if latest < 0:
        latest = max(0.0, float(duration) - float(need))
    return round(min(max(0.0, float(src_in)), max(0.0, latest)), 3)


def choose_src_in(duration: float, need: float, motion_hint: str,
                  samples: list | None) -> tuple[float, str]:
    """按 motion_hint 在预览帧上选入点。返回 (秒, rule)。

    samples 是 (时间秒, 帧) ，帧为灰度或 RGB 数组。帧太少无法比较时
    退回前 1/3。
    """
    hint = str(motion_hint or "").strip().lower()
    ordered = sorted((float(t), frame) for t, frame in (samples or []))
    if (hint not in MOTION_HINTS and hint != STATIC_HINT) or len(ordered) < 2:
        return legacy_src_in(duration, need), "legacy_third"
    times = [t for t, _ in ordered]
    diffs = _frame_diffs([frame for _, frame in ordered])
    return select_src_in(duration, need, hint, times, diffs)


def select_src_in(duration: float, need: float, motion_hint: str,
                  times: list[float], diffs: list[float]) -> tuple[float, str]:
    """在已经算好的帧差上选入点。times 必须按时间升序，与 diffs 等长。"""
    hint = str(motion_hint or "").strip().lower()
    if (hint not in MOTION_HINTS and hint != STATIC_HINT) or len(times) < 2:
        return legacy_src_in(duration, need), "legacy_third"
    scored = window_scores(duration, need, times, diffs)
    motions = [motion for _, motion, _ in scored]
    # 全片帧差拉不开（均匀静止、只有噪声，或预览太稀）：不要被浮点噪声带离前 1/3。
    if (not motions or max(motions) < _MOTION_FLOOR
            or max(motions) - min(motions) < _MOTION_FLOOR):
        return legacy_src_in(duration, need), "legacy_third"
    clean = [row for row in scored if not row[2]]
    pool = clean or scored
    if hint == STATIC_HINT:
        start = min(pool, key=lambda row: (row[1], row[0]))[0]
        return round(start, 3), "static"
    start = max(pool, key=lambda row: (row[1], -row[0]))[0]
    return round(start, 3), "motion"


def window_scores(duration: float, need: float, times: list[float],
                  diffs: list[float]) -> list[tuple[float, float, bool]]:
    """每个合法入点的 (start, 窗口内平均帧差, 是否含硬切)。"""
    times = [float(t) for t in times]
    diffs = [float(d) for d in diffs]
    if len(times) != len(diffs):
        raise ValueError("times 与 diffs 长度必须一致")
    cuts = _hard_cuts(diffs)
    out = []
    for start in _starts(float(duration), float(need)):
        motion, cut = _window_motion(start, float(need), times, diffs, cuts)
        out.append((start, motion, cut))
    return out


def frame_near(samples: list, src_in: float, need: float,
               slack: float = 1.0):
    """取入点附近的一帧：(时间, 帧)。窗口内优先，否则要离入点够近。"""
    ordered = sorted((float(t), frame) for t, frame in (samples or []))
    if not ordered:
        return None
    lo = float(src_in) - 0.05
    hi = float(src_in) + max(float(need), 0.0)
    inside = [item for item in ordered if lo <= item[0] <= hi + 1e-6]
    target = float(src_in) + min(0.5, max(float(need), 0.0) * 0.25)
    pool = inside or [min(ordered, key=lambda item: abs(item[0] - float(src_in)))]
    best = min(pool, key=lambda item: (abs(item[0] - target), item[0]))
    if not inside and abs(best[0] - float(src_in)) > max(float(slack), float(need)):
        return None
    return best


def load_preview_samples(video: dict, getter=None) -> list[tuple[float, np.ndarray]]:
    """下载 Pexels video_pictures 预览图，不下载视频文件。

    图片按 nr 铺在 API 时长上，时间取每段中点。单张失败就跳过。
    """
    pics = [p for p in (video.get("video_pictures") or [])
            if isinstance(p, dict) and p.get("picture")]
    pics.sort(key=lambda p: int(p.get("nr") or 0))
    duration = float(video.get("duration") or 0)
    if len(pics) < 2 or duration <= 0:
        return []
    if getter is None:
        getter = _get_bytes
    out = []
    n = len(pics)
    for i, pic in enumerate(pics):
        try:
            blob = getter(pic["picture"])
        except Exception:
            continue
        if not blob or len(blob) > 2_000_000:
            continue
        frame = decode_image_rgb(blob)
        if frame is not None and frame.size:
            out.append((round((i + 0.5) / n * duration, 3), frame))
    return out


def decode_image_rgb(blob: bytes) -> np.ndarray | None:
    """用已随依赖安装的 ffmpeg 把图片解成 RGB。失败返回 None。"""
    if not blob:
        return None
    cmd = [ffmpeg_exe(), "-v", "error", "-i", "pipe:0", "-frames:v", "1",
           "-vf", "scale=96:-2", "-f", "image2pipe", "-vcodec", "ppm", "pipe:1"]
    try:
        proc = subprocess.run(cmd, input=blob, capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return parse_ppm(proc.stdout)


def parse_ppm(data: bytes) -> np.ndarray | None:
    """解析 P6 PPM。只接受 maxval ≤ 255 的 8-bit RGB。"""
    if not data.startswith(b"P6"):
        return None
    i = 2
    tokens: list[bytes] = []
    n = len(data)
    while len(tokens) < 3 and i < n:
        while i < n and data[i] in b" \t\r\n":
            i += 1
        if i < n and data[i] == ord("#"):
            while i < n and data[i] not in b"\n\r":
                i += 1
            continue
        start = i
        while i < n and data[i] not in b" \t\r\n":
            i += 1
        if start == i:
            break
        tokens.append(data[start:i])
    if len(tokens) < 3:
        return None
    try:
        width, height, maxv = (int(tokens[0]), int(tokens[1]), int(tokens[2]))
    except ValueError:
        return None
    if width <= 0 or height <= 0 or not 0 < maxv <= 255:
        return None
    if i < n and data[i] in b" \t\r\n":
        i += 1
    need = width * height * 3
    raw = data[i:i + need]
    if len(raw) != need:
        return None
    return np.frombuffer(raw, dtype=np.uint8).reshape((height, width, 3)).copy()


def _get_bytes(url: str) -> bytes:
    response = requests.get(url, timeout=20)
    response.raise_for_status()
    return response.content


def _starts(duration: float, need: float, step: float = 0.5) -> list[float]:
    latest = duration - need - TAIL_MARGIN_S
    if latest < 0:
        latest = max(0.0, duration - need)
    if latest <= 1e-6:
        return [0.0]
    out = []
    t = 0.0
    while t < latest - 1e-6:
        out.append(round(t, 3))
        t += step
    out.append(round(latest, 3))
    deduped = []
    for value in out:
        if not deduped or abs(value - deduped[-1]) > 1e-6:
            deduped.append(value)
    return deduped


def _window_motion(start: float, need: float, times: list[float],
                   diffs: list[float], cuts: list[bool]) -> tuple[float, bool]:
    """窗口内两端都落在 [start, start+need] 的台阶。边界外的切不算进这段。"""
    lo, hi = start, start + need
    values = []
    cut = False
    for i in range(1, len(times)):
        if times[i - 1] + 1e-6 >= lo and times[i] - 1e-6 <= hi:
            values.append(diffs[i])
            cut = cut or cuts[i]
    if not values:
        return 0.0, False
    return float(sum(values) / len(values)), cut


def _hard_cuts(diffs: list[float]) -> list[bool]:
    """硬切：这一步既明显高于本片典型帧差，又换掉了画面的一大块。"""
    body = diffs[1:]
    if not body:
        return [False] * len(diffs)
    median = float(np.median(np.asarray(body, dtype=np.float64)))
    limit = max(_CUT_FLOOR, median * 2.5)
    return [False] + [value >= limit for value in body]


def _frame_diffs(frames: list) -> list[float]:
    grays = [_gray_small(frame) for frame in frames]
    diffs = [0.0]
    for prev, nxt in zip(grays, grays[1:]):
        diffs.append(_mean_abs(prev, nxt))
    return diffs


def _gray_small(frame, width: int = 32) -> np.ndarray:
    arr = np.asarray(frame)
    if arr.ndim == 3:
        channels = arr[..., :3].astype(np.float32)
        gray = (0.299 * channels[..., 0] + 0.587 * channels[..., 1]
                + 0.114 * channels[..., 2])
    else:
        gray = arr.astype(np.float32)
    if gray.size == 0:
        return np.zeros((1, 1), dtype=np.float32)
    peak = float(np.nanmax(gray))
    if peak > 1.5:
        gray = gray / 255.0
    gray = np.nan_to_num(gray, nan=0.0)
    step = max(1, int(round(gray.shape[1] / width))) if gray.shape[1] > width else 1
    if step > 1:
        gray = gray[::step, ::step]
    return np.ascontiguousarray(gray, dtype=np.float32)


def _mean_abs(a: np.ndarray, b: np.ndarray) -> float:
    height = min(a.shape[0], b.shape[0])
    width = min(a.shape[1], b.shape[1])
    if height == 0 or width == 0:
        return 0.0
    return float(np.abs(a[:height, :width] - b[:height, :width]).mean())


def _as_uint8_gray(frame) -> np.ndarray:
    arr = np.asarray(frame)
    if arr.dtype != np.uint8:
        values = arr.astype(np.float32)
        if values.size and float(np.nanmax(values)) <= 1.5:
            values = values * 255.0
        arr = np.clip(np.nan_to_num(values, nan=0.0), 0, 255).astype(np.uint8)
    if arr.ndim == 2:
        return arr
    if arr.ndim == 3 and arr.shape[-1] >= 3:
        rgb = arr[..., :3].astype(np.float32)
        return (0.299 * rgb[..., 0] + 0.587 * rgb[..., 1]
                + 0.114 * rgb[..., 2]).astype(np.uint8)
    return arr[..., 0]
