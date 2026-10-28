# -*- coding: utf-8 -*-
"""动效层（motion）测试：全部离线、素材合成，不依赖 Remotion/浏览器。

核心回归：alpha 必须全程存活——曾用 vp9 webm 中间格式时，本仓库捆绑的
imageio-ffmpeg 构建把带透明度的 PNG 编出的 alpha 解回恒不透明，标题卡
变成黑底盖住整帧风景。现在中间格式为 qtrle/argb MOV，这里的用例钉死
「卡片透明区不遮背景、文字区确实叠加」这条底线。
"""
import subprocess

import pytest

from src import motion
from src.motion import MotionError

W, H = 64, 36


def _ffmpeg() -> str:
    from src.audio_io import ffmpeg_exe
    return ffmpeg_exe()


def _run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    assert r.returncode == 0, r.stderr[-400:]


def _rgba(path: str, t: float | None = None) -> bytes:
    cmd = [_ffmpeg()]
    if t is not None:
        cmd += ["-ss", str(t)]
    cmd += ["-i", path, "-frames:v", "1", "-vf", "format=rgba",
            "-f", "rawvideo", "-"]
    r = subprocess.run(cmd, capture_output=True, timeout=120)
    assert len(r.stdout) >= W * H * 4, f"帧数据不足: {len(r.stdout)}"
    return r.stdout


def _alpha(buf: bytes, x: int, y: int) -> int:
    return buf[(y * W + x) * 4 + 3]


def make_alpha_png(path, box=(20, 10, 24, 16), color="red@1.0") -> None:
    """透明底 + 中央色块的 alpha PNG。

    注意不能用 drawbox：它只写 RGB 不写 alpha，透明画布上会得到
    「透明的红块」。geq 逐通道构造才可靠。
    """
    x0, y0, w_, h_ = box
    cond = (f"between(X,{x0},{x0 + w_ - 1})*between(Y,{y0},{y0 + h_ - 1})")
    _run([_ffmpeg(), "-y", "-loglevel", "error",
          "-f", "lavfi", "-i", f"color=c=black:s={W}x{H}",
          "-vf", f"format=rgba,geq=r='if({cond},255,0)':g=0:b=0"
                 f":a='if({cond},255,0)'",
          "-frames:v", "1", "-update", "1", str(path)])


def make_card_mov(path, frames: int = 6) -> None:
    """alpha PNG ×N → qtrle/argb MOV（与 motion.render_card 编码步一致）。"""
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as td:
        for i in range(frames):
            make_alpha_png(Path(td) / f"element-{i:02d}.png")
        enc = subprocess.Popen(
            [_ffmpeg(), "-y", "-loglevel", "error", "-framerate", "10",
             "-f", "image2pipe", "-i", "-",
             "-c:v", "qtrle", "-pix_fmt", "argb", "-an", str(path)],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        for i in range(frames):
            enc.stdin.write((Path(td) / f"element-{i:02d}.png").read_bytes())
        enc.stdin.close()
        _, err = enc.communicate(timeout=120)
        assert enc.returncode == 0, err


def make_main_video(path, seconds: float = 2.0, gray: str = "0x808080") -> None:
    _run([_ffmpeg(), "-y", "-loglevel", "error",
          "-f", "lavfi", "-i", f"color=c={gray}:s={W}x{H}:d={seconds}:r=10",
          "-pix_fmt", "yuv420p", str(path)])


def test_alpha_fixture_and_qtrle_roundtrip(tmp_path):
    """合成 alpha PNG 经 qtrle MOV 回路后透明度逐像素存活。"""
    png = tmp_path / "fx.png"
    make_alpha_png(png)
    b = _rgba(str(png))
    assert _alpha(b, 2, 2) == 0                      # 角落全透明
    assert _alpha(b, 30, 17) == 255                  # 色块不透明

    mov = tmp_path / "fx.mov"
    make_card_mov(mov)
    b2 = _rgba(str(mov))
    assert _alpha(b2, 2, 2) == 0, "qtrle 回路丢了透明度（回到黑底卡事故）"
    assert _alpha(b2, 30, 17) == 255


def test_overlay_transparent_card_keeps_background(tmp_path):
    """叠加透明卡：背景灰度保持、色块处确实合成——不透明卡回归的端到端钉子。"""
    main = tmp_path / "main.mp4"
    card = tmp_path / "card.mov"
    out = tmp_path / "out.mp4"
    make_main_video(main, seconds=2.0)
    make_card_mov(card, frames=12)                   # 1.2s @10fps

    motion.apply_overlays(main, card, None,
                          title_s=1.0, outro_s=0.0, target_s=2.0, out=out)

    inside = _rgba(str(out), t=0.5)                  # 窗口内：色块中心=红
    r0, g0, b0 = inside[(17 * W + 30) * 4: (17 * W + 30) * 4 + 3]
    assert r0 > 150 and g0 < 100 and b0 < 100, "色块没有叠上"
    corner = inside[(2 * W + 2) * 4: (2 * W + 2) * 4 + 3]
    assert corner[0] > 90, f"透明角落被黑底盖住: rgb={tuple(corner)}"

    outside = _rgba(str(out), t=1.8)                 # 窗口外：整帧原样
    corner2 = outside[(2 * W + 2) * 4: (2 * W + 2) * 4 + 3]
    assert corner2[0] > 90, "enable 窗口外被改动"


def test_drawtext_escape():
    assert motion._esc("a:b'c,d%e\\f") == "a\\:b\\\\'c\\,d\\%e\\\\f"


def test_font_chrome_detection_types():
    assert motion.font_path() is None or isinstance(motion.font_path(), str)
    assert motion.chrome_path() is None or isinstance(motion.chrome_path(), str)
    assert isinstance(motion.remotion_available(), bool)


def test_render_card_cache_hit(tmp_path, monkeypatch):
    """卡片文件已存在且够大时直接命中缓存，不再起任何子进程。"""
    out = tmp_path / "title_card.mov"
    out.write_bytes(b"x" * 20_000)
    monkeypatch.setattr(motion, "remotion_available", lambda: True)
    monkeypatch.setattr(motion, "chrome_path", lambda: "chrome")
    monkeypatch.setattr(motion.subprocess, "run",
                        lambda *a, **k: pytest.fail("缓存命中不应渲染"))
    assert motion.render_card("TitleCard", {"title": "t"}, out) == out


def test_render_card_needs_remotion(tmp_path, monkeypatch):
    monkeypatch.setattr(motion, "remotion_available", lambda: False)
    with pytest.raises(MotionError):
        motion.render_card("TitleCard", {}, tmp_path / "x.mov")


def test_package_cards_cached_shortcircuit(tmp_path):
    final = tmp_path / "final.mp4"
    final.write_bytes(b"f" * 100)
    (tmp_path / "final_carded.mp4").write_bytes(b"c" * 100)
    res = motion.package_cards("p", {}, {}, final, 60.0)
    assert res["mode"] == "cached"


def test_resolve_credit_chain(tmp_path):
    from cli import _resolve_credit
    # 1) brief 显式
    assert _resolve_credit(
        {"music_credit": {"title": "T", "artist": "A", "license": "CC BY"}}) == \
        {"title": "T", "artist": "A", "license": "CC BY"}
    # 2) 曲库 manifest 按文件名匹配
    bgm = tmp_path / "bgm"
    (bgm / "hopeful").mkdir(parents=True)
    mp3 = bgm / "hopeful" / "Awake [artist17].mp3"
    mp3.write_bytes(b"")
    (bgm / "manifest.json").write_text(
        '{"tracks": [{"file": "hopeful/Awake [artist17].mp3", "title": "Awake",'
        ' "artist": "artist17", "license": "CC BY 3.0"}]}', encoding="utf-8")
    assert _resolve_credit({"music": str(mp3)}) == \
        {"title": "Awake", "artist": "artist17", "license": "CC BY 3.0"}
    # 3) 文件名 Title [Artist] 兜底
    m2 = tmp_path / "m2"
    m2.mkdir()
    assert _resolve_credit(
        {"music": str(m2 / "Sea Breeze [someone].mp3")}) == \
        {"title": "Sea Breeze", "artist": "someone"}
    # 4) 无从推导
    assert _resolve_credit({"music": str(m2 / "plain.mp3")}) is None
    assert _resolve_credit({}) is None
