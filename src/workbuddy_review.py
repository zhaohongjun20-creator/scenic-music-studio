# -*- coding: utf-8 -*-
"""workbuddy 代审桥：抽帧 → 腾讯 WorkBuddy/CodeBuddy CLI 审片 → 结构化判词。

发布前的第二道闸门：QC 是确定性闸门（规格/时长/切点），这里补内容维度——
人物/动物穿帮、主题契合度、坏帧过曝、片头片尾卡完整性。判词落盘
output/review_workbuddy.json（多轮累积），verdict=pass 才建议投稿。

依赖：codebuddy CLI（npm i -g @tencent-ai/codebuddy-code，已扫码登录）、
WorkBuddy 桌面端在线（提供 127.0.0.1:3425 模型网关）、包装脚本（默认
tools/workbuddy_review.sh，可在 config review.workbuddy_cmd 覆盖）。
任何一环缺失 → verdict='unavailable'，不阻塞发布（人工审片兜底）。
"""
import json
import re
import subprocess
import tempfile
from pathlib import Path

from src.jsonio import save_json

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CMD = "bash tools/workbuddy_review.sh"
TIMEOUT_S = 500


class ReviewError(RuntimeError):
    pass


def frame_times(target_s: float) -> list[float]:
    """抽帧时刻：片头标题卡 + 均布四帧 + 片尾落版前。升序去重。"""
    times = {1.5, target_s - 1.4}
    for i in (1, 2, 3, 4):
        times.add(round(target_s * i / 5.0, 1))
    return sorted(times)


def extract_frames(final: Path, times: list[float],
                   out_dir: Path) -> list[Path]:
    """从成片抽取审帧 jpg（覆盖写，供 CLI 会话读图）。"""
    from src.audio_io import ffmpeg_exe
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for t in times:
        out = out_dir / f"rv_{int(round(t * 10)):03d}.jpg"
        subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error", "-ss", str(t),
                        "-i", str(final), "-frames:v", "1", "-q:v", "3",
                        "-update", "1", str(out)], capture_output=True, timeout=120)
        if not out.exists() or out.stat().st_size == 0:
            raise ReviewError(f"抽帧失败: {out}")
        paths.append(out)
    return paths


def build_prompt(theme: str, credit_lines: list[str], frames: list[Path],
                 qc_path: Path) -> str:
    """审片提示词：自包含（workbuddy 无本会话上下文），末尾要求 VERDICT 行。"""
    frame_lines = "\n".join(f"- {f.resolve().as_posix()}" for f in frames)
    credit = "\n".join(credit_lines) or "（无）"
    qc = qc_path.resolve().as_posix()
    return f"""你是视频审片员。请审查一支即将发布到 B 站治愈系风景号的 60 秒纯音乐风景混剪（主题：{theme}；风格：快切硬切为主）。

请逐一查看以下抽帧图片（用你的读文件工具打开）：
{frame_lines}

再读质检报告：
{qc}

审查清单：
A. 每帧是否有人物/动物/第三方水印/商业 logo 等穿帮元素（纯风景号不能出现；片头标题卡与片尾署名是自设文字，不算穿帮）
B. 是否有黑帧、花屏、明显坏帧、过曝死白
C. 画面是否契合主题（{theme}）
D. 片头标题卡是否清晰可读；片尾是否出现署名落版（预期：{credit}）
E. qc_report.json 是否 passed=true
F. 整体是否达到可发布水准

最后一行必须严格输出（除此之外不要再输出其他 VERDICT 行）：
VERDICT: pass
或
VERDICT: fail - 具体原因"""


def parse_verdict(text: str) -> tuple[str, str]:
    """从审片文本取最后一行 VERDICT；缺失判 unknown。返回 (verdict, 全文)。"""
    verdict = "unknown"
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("VERDICT:"):
            low = s.lower()
            verdict = "pass" if low.startswith("verdict: pass") else "fail"
    return verdict, text


def _bash_exe() -> str:
    """解析真正的 Git Bash：Windows 下裸 `bash` 会命中 WSL 的 bash.exe
    （无分发版直接 rc=1），必须优先找 Git 安装目录。"""
    for p in (r"C:\Program Files\Git\bin\bash.exe",
              r"C:\Program Files\Git\usr\bin\bash.exe",
              r"C:\Program Files (x86)\Git\bin\bash.exe"):
        if Path(p).exists():
            return p
    return "bash"                               # POSIX 或已自行排好 PATH 的环境


def _run_cli(cmd: str, prompt: str, timeout: int) -> str:
    """调包装脚本（--output-format json），返回 result 文本。"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False,
                                     encoding="utf-8") as f:
        prompt_file = Path(f.name)
        f.write(prompt)
    try:
        argv = cmd.split()
        if argv and argv[0] == "bash":
            argv[0] = _bash_exe()
        argv += ["--output-format", "json", prompt_file.read_text(encoding="utf-8")]
        r = subprocess.run(argv, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           cwd=ROOT)
        if r.returncode != 0:
            raise ReviewError(f"workbuddy CLI 失败 rc={r.returncode}: "
                              f"{(r.stderr or r.stdout or '')[-300:]}")
        result = _extract_result(r.stdout)
        if not result.strip():
            # rc=0 但空输出：常见于模型网关（WorkBuddyAI 3425）掉线，错误只进 stderr
            raise ReviewError(f"workbuddy CLI 空结果（疑似网关掉线）: {r.stderr[-200:]}")
        return result
    finally:
        prompt_file.unlink(missing_ok=True)


def _extract_result(stdout_json: str) -> str:
    """从 CLI 的 json 流取最后 result 文本；解析失败回退原文。"""
    try:
        d = json.loads(stdout_json)
        if isinstance(d, list) and d and isinstance(d[-1], dict):
            return str(d[-1].get("result", ""))
    except (ValueError, TypeError):
        pass
    return stdout_json


def review_project(pid: str, cfg: dict, final: Path, target_s: float,
                   theme: str, credit_lines: list[str]) -> dict:
    """完整代审一轮：抽帧 → CLI 审片 → 判词落盘（多轮累积）。"""
    pdir = Path(final).parent
    qc_path = pdir / "qc_report.json"
    frames = extract_frames(final, frame_times(target_s),
                            pdir / "review_frames")
    prompt = build_prompt(theme, credit_lines, frames, qc_path)

    mcfg = cfg.get("review", {})
    cmd = mcfg.get("workbuddy_cmd") or DEFAULT_CMD
    script = ROOT / "tools" / "workbuddy_review.sh"
    if cmd == DEFAULT_CMD and not script.exists():
        raise ReviewError(f"包装脚本缺失: {script}（或 config review.workbuddy_cmd 指定）")
    timeout = int(mcfg.get("timeout_s", TIMEOUT_S))
    try:
        detail = _run_cli(cmd, prompt, timeout)
        verdict, detail = parse_verdict(detail)
    except (ReviewError, subprocess.TimeoutExpired, OSError) as e:
        verdict, detail = "unavailable", f"代审不可用，人工审片兜底: {e}"

    out = pdir / "review_workbuddy.json"
    rounds = []
    if out.exists():
        try:
            prev = json.loads(out.read_text(encoding="utf-8"))
            r = prev.get("rounds") if isinstance(prev, dict) else None
            rounds = r if isinstance(r, list) else [prev]
        except ValueError:
            pass
    rounds.append({"at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
                   "verdict": verdict, "detail": detail,
                   "frames": [f.name for f in frames]})
    data = {"project": pid, "reviewer": "workbuddy-ai (codebuddy CLI)",
            "latest_verdict": verdict, "rounds": rounds}
    save_json(out, data)
    return data
