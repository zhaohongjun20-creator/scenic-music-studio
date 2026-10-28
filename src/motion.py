# -*- coding: utf-8 -*-
"""动效层桥：Remotion 渲染片头标题卡 / 片尾落版（无损 alpha MOV），叠加回成片。

混合渲染架构：ffmpeg 负责主体合成（快、已验证），Remotion 只渲几秒的
动效卡（qtrle MOV 带 alpha），再由 ffmpeg overlay 叠回时间轴两端——
时长、音乐、QC 全部不受影响。

降级链（降级不阻塞）：Remotion（有动画）→ ffmpeg drawtext 直接上字
（静态，无新依赖）。remotion/ 的 node_modules 缺失时自动走保底。
"""
import shutil
import subprocess
from pathlib import Path

from src.audio_io import ffmpeg_exe, ffprobe_exe
from src.jsonio import save_json

ROOT = Path(__file__).resolve().parent.parent
REMOTION_DIR = ROOT / "remotion"

# Windows 系统浏览器（Remotion 渲染内核；免下载 Chrome Headless Shell）
_CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
# 中文标题字体（保底 drawtext 用；楷体优先，风景片气质最搭）
_FONT_CANDIDATES = ["simkai.ttf", "msyh.ttc", "simhei.ttf"]


class MotionError(RuntimeError):
    pass


def chrome_path() -> str | None:
    for p in _CHROME_CANDIDATES:
        if Path(p).exists():
            return p
    return None


def font_path() -> str | None:
    fonts = Path(r"C:\Windows\Fonts")
    for name in _FONT_CANDIDATES:
        f = fonts / name
        if f.exists():
            return str(f)
    return None


def remotion_available() -> bool:
    return (REMOTION_DIR / "node_modules" / ".bin").exists()


def render_card(comp: str, props: dict, out: Path, retries: int = 2) -> Path:
    """Remotion 渲染动效卡 → 无损 alpha 的 qtrle MOV（供 overlay）。

    走 PNG 序列两步走而非直接渲 webm：本仓库所用 imageio-ffmpeg 的
    ffmpeg 构建对 VP9 alpha 编解码双双失效（编码出的 alpha 解回恒不透明，
    即使送入的 PNG 确带透明度）；qtrle/argb 回路实测完好。Remotion 的
    --sequence 截图本身带 alpha（系统 Chrome 即可，无需 Headless Shell）。
    """
    if not remotion_available():
        raise MotionError("remotion/node_modules 缺失（先在 remotion/ 下 npm install）")
    chrome = chrome_path()
    if not chrome:
        raise MotionError("未找到 Chrome/Edge，无法渲染 Remotion 动效卡")

    if out.exists() and out.stat().st_size > 10_000:
        return out                                  # 同 props 缓存命中
    out.parent.mkdir(parents=True, exist_ok=True)
    # 序列目录不能带点（Remotion 会把 .seq 当扩展名拒绝）
    seq_dir = out.parent / (out.stem + "_seq")
    props_file = out.with_suffix(".props.json")
    save_json(props_file, props)

    cmd = ["cmd", "/c", "npx", "remotion", "render", comp, str(seq_dir),
           "--sequence", f"--props={props_file}",
           "--image-format=png", f"--browser-executable={chrome}",
           "--log=error", "--timeout=180000"]
    frames: list[Path] = []
    last = ""
    try:
        for _ in range(retries):
            r = subprocess.run(cmd, cwd=REMOTION_DIR, capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               timeout=900)
            frames = sorted(seq_dir.glob("element-*.png")) if seq_dir.exists() else []
            if r.returncode == 0 and frames:
                break
            last = (r.stderr or r.stdout or "")[-500:]
        else:
            raise MotionError(f"Remotion 渲染 {comp} 失败: {last}")

        # PNG 序列管道喂给 ffmpeg → qtrle/argb（无损 alpha，Windows 兼容好）
        enc = subprocess.Popen(
            [ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
             "-framerate", "30", "-f", "image2pipe", "-i", "-",
             "-c:v", "qtrle", "-pix_fmt", "argb", "-an", str(out)],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE)
        assert enc.stdin is not None
        try:
            for f in frames:
                enc.stdin.write(f.read_bytes())
            enc.stdin.close()
        except BrokenPipeError:
            pass
        _, err = enc.communicate(timeout=300)
        if enc.returncode != 0 or not out.exists() or out.stat().st_size < 1_000:
            raise MotionError(f"qtrle 编码失败: {(err or b'')[-300:]!r}")
        return out
    finally:
        shutil.rmtree(seq_dir, ignore_errors=True)
        props_file.unlink(missing_ok=True)


def apply_overlays(final: Path, title: Path | None, outro: Path | None,
                   title_s: float, outro_s: float, target_s: float,
                   out: Path) -> Path:
    """把标题卡（叠片头）与落版（叠片尾）overlay 到成片 → final_carded.mp4。"""
    inputs, chains = ["-i", str(final)], []
    idx = 1
    if title:
        inputs += ["-i", str(title)]
        chains.append((f"[{idx}:v]", 0.30, title_s))
        idx += 1
    if outro:
        inputs += ["-i", str(outro)]
        chains.append((f"[{idx}:v]", target_s - outro_s + 0.2, target_s - 0.05))
        idx += 1
    if not chains:
        raise MotionError("没有可叠加的动效卡")
    fc, prev = [], "0:v"
    for n, (label, start, end) in enumerate(chains):
        outl = f"c{n}" if n < len(chains) - 1 else "vout"
        fc.append(f"[{prev}]{label}overlay=0:0:"
                  f"enable='between(t,{start:.2f},{end:.2f})'[{outl}]")
        prev = outl
    cmd = [ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", *inputs,
           "-filter_complex", ";".join(fc),
           "-map", "[vout]", "-map", "0:a?",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
           "-pix_fmt", "yuv420p", "-c:a", "copy",
           "-movflags", "+faststart", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=1800)
    if r.returncode != 0:
        raise MotionError(f"overlay 合成失败: {r.stderr[-400:]}")
    return out


def _esc(text: str) -> str:
    """drawtext 文本转义（冒号/引号/逗号/百分号/反斜杠都会破坏 filter 语法）。"""
    return (text.replace("\\", "\\\\").replace(":", "\\:")
            .replace("'", "\\\\'").replace(",", "\\,")
            .replace("%", "\\%"))


def drawtext_fallback(final: Path, title: str, outro_lines: list[str],
                      title_s: float, outro_s: float, target_s: float,
                      out: Path) -> Path:
    """保底：无 Remotion 时用 ffmpeg drawtext 直接上字（静态、无动画）。"""
    font = font_path()
    if not font:
        raise MotionError("找不到中文字体（simkai/msyh/simhei），保底上字不可用")
    font_arg = font.replace("\\", "/").replace(":", "\\:")

    filters = [
        f"drawtext=fontfile='{font_arg}':text='{_esc(title)}':"
        f"fontsize=96:fontcolor=white:borderw=2:bordercolor=black@0.6:"
        f"x=(w-text_w)/2:y=h*0.40:enable='between(t,0.3,{title_s:.2f})'"
    ]
    start = target_s - outro_s + 0.3
    for i, line in enumerate(outro_lines[:4]):
        filters.append(
            f"drawtext=fontfile='{font_arg}':text='{_esc(line)}':"
            f"fontsize=26:fontcolor=white:borderw=2:bordercolor=black@0.6:"
            f"x=(w-text_w)/2:y=h*0.52+{i * 44}:"
            f"enable='between(t,{start + i * 0.4:.2f},{target_s - 0.1:.2f})'")
    vf = ",".join(filters)
    cmd = [ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
           "-i", str(final), "-vf", vf,
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
           "-c:a", "copy", "-movflags", "+faststart", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=1800)
    if r.returncode != 0:
        raise MotionError(f"drawtext 保底失败: {r.stderr[-400:]}")
    return out


def package_cards(pid: str, cfg: dict, brief: dict, final: Path,
                  target_s: float, music_credit_line: str | None = None,
                  force: bool = False) -> dict:
    """完整动效打包：渲卡（可降级）+ 叠加 → output/final_carded.mp4。

    返回 {"mode": "remotion"|"drawtext", "out": 路径, "skipped": [...]}
    """
    mcfg = cfg.get("motion", {})
    title_s = float(mcfg.get("title_s", 3.0))
    outro_s = float(mcfg.get("outro_s", 3.5))
    accent = str(mcfg.get("accent", "#e8d9b0"))
    out_dir = final.parent
    out = out_dir / "final_carded.mp4"
    if out.exists() and not force:
        return {"mode": "cached", "out": out, "skipped": []}

    title = brief.get("title_card") or brief.get("theme_zh", "风景混剪")
    subtitle = f"{brief.get('theme_zh', '风景')} · 纯音乐风景"
    outro_lines = [l for l in (music_credit_line,) if l]
    outro_lines.append("视频素材：Pexels · Pexels License")

    mode, skipped = "remotion", []
    title_card = outro_card = None
    if remotion_available() and chrome_path():
        try:
            title_card = render_card(
                "TitleCard",
                {"title": title, "subtitle": subtitle, "accent": accent,
                 "durationSec": title_s},
                out_dir / "title_card.mov")
            outro_card = render_card(
                "OutroCard",
                {"lines": outro_lines, "accent": accent,
                 "durationSec": outro_s},
                out_dir / "outro_card.mov")
        except MotionError as e:
            skipped.append(f"Remotion: {e}")
            title_card = outro_card = None
    else:
        skipped.append("remotion 不可用（node_modules 或浏览器缺失）")
    if title_card is None and outro_card is None:
        mode = "drawtext"
        drawtext_fallback(final, title, outro_lines, title_s, outro_s,
                          target_s, out)
        return {"mode": mode, "out": out, "skipped": skipped}

    apply_overlays(final, title_card, outro_card, title_s, outro_s,
                   target_s, out)
    return {"mode": mode, "out": out, "skipped": skipped}
