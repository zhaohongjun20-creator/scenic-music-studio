# -*- coding: utf-8 -*-
"""渲染：逐镜头规范化（片段级缓存）→ xfade 合成 → 封面抽取。

- 每个镜头独立渲染成 1920x1080@30 无音轨片段，缓存键 = 素材 id +
  取材区间 + 调色参数 + revision——redo-shot 换素材只重渲染该镜头；
- 合成沿 see-china/bili-autopost-sau 验证过的 xfade 链配方：
  成片时长 = Σrender_length − Σ转场时长 = target_s（时间轴已守恒）；
- 音乐按播放窗口偏移取材，响度归一 + 首尾淡入淡出，与画面同步收尾。
"""
import hashlib
import subprocess
from pathlib import Path

from src.audio_io import ffmpeg_exe


class RenderError(RuntimeError):
    pass


def _run(cmd: list, timeout: int = 1800) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RenderError(f"ffmpeg 失败: {r.stderr[-600:]}")


def render_clip(shot: dict, video_cfg: dict, clips_dir: Path) -> Path:
    a = shot["asset"]
    key = hashlib.sha1("|".join([
        a["id"], f'{float(a["src_in"]):.3f}', f'{float(shot["render_length"]):.3f}',
        f'{video_cfg["contrast"]}', f'{video_cfg["brightness"]}',
        f'{video_cfg["saturation"]}', str(shot.get("revision", 0)),
    ]).encode("utf-8")).hexdigest()[:10]
    out = clips_dir / f's{shot["index"]:02d}_{key}.mp4'
    if out.exists():
        return out
    w, h, fps = int(video_cfg["width"]), int(video_cfg["height"]), int(video_cfg["fps"])
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
          f"fps={fps},setsar=1,"
          f"eq=contrast={video_cfg['contrast']}:brightness={video_cfg['brightness']}:"
          f"saturation={video_cfg['saturation']}")
    cmd = [ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
           "-ss", f'{float(a["src_in"]):.3f}', "-t", f'{float(shot["render_length"]):.3f}',
           "-i", a["file"], "-vf", vf, "-an",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
           "-pix_fmt", "yuv420p", str(out)]
    _run(cmd)
    if not out.exists() or out.stat().st_size < 10_000:
        out.unlink(missing_ok=True)
        raise RenderError(
            f"镜头 {shot['index']} 渲染产物为空（素材 {a['id']} 可能损坏，"
            f"可 redo-shot 该镜头换素材）")
    return out


def compose(clip_paths: list, timeline: dict, out_path: Path) -> float:
    """xfade 链 + 音乐合成 → final.mp4。返回成片时长（= target_s）。"""
    n = len(clip_paths)
    if n == 0:
        raise RenderError("没有可合成的镜头片段")
    target = float(timeline["target_s"])
    audio = timeline["audio"]

    inputs: list[str] = []
    for p in clip_paths:
        inputs += ["-i", str(p)]
    inputs += ["-ss", f'{float(audio["offset_s"]):.3f}',
               "-t", f"{target:.3f}", "-i", audio["file"]]

    fc: list[str] = []
    prev = "0:v"
    acc = float(timeline["shots"][0]["render_length"])
    for i in range(1, n):
        tr = timeline["shots"][i - 1]["transition_out"]
        off = acc - float(tr["duration"])
        fc.append(f"[{prev}][{i}:v]xfade=transition={tr['style']}:"
                  f"duration={float(tr['duration']):.3f}:offset={off:.3f}[x{i}]")
        prev = f"x{i}"
        acc = off + float(timeline["shots"][i]["render_length"])

    chain = []
    if audio.get("loudnorm"):
        chain += ["loudnorm=I=-16:TP=-1.5:LRA=11", "aresample=48000"]
    fi = float(audio.get("fade_in_s") or 0.0)
    fo = float(audio.get("fade_out_s") or 0.0)
    if fi > 0:
        chain.append(f"afade=t=in:st=0:d={fi:.2f}")
    if fo > 0:
        chain.append(f"afade=t=out:st={max(0.0, target - fo):.2f}:d={fo:.2f}")
    chain.append(f"atrim=0:{target:.3f}")
    fc.append(f"[{n}:a]{','.join(chain)}[aout]")

    cmd = [ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", *inputs,
           "-filter_complex", ";".join(fc)]
    if n == 1:                       # 单镜头没有 xfade，直接映射输入流
        cmd += ["-map", "0:v"]
    else:
        cmd += ["-map", f"[{prev}]"]
    cmd += ["-map", "[aout]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", "-t", f"{target:.3f}", str(out_path)]
    _run(cmd, timeout=3600)
    return target


def make_cover(final_path: Path, at_s: float, out_path: Path) -> Path:
    _run([ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
          "-ss", f"{max(0.0, at_s):.3f}", "-i", str(final_path),
          "-frames:v", "1", "-q:v", "2", str(out_path)], timeout=300)
    return out_path
