# -*- coding: utf-8 -*-
"""A/B 盲评实验：两个版本成片 → 匿名化双盲包。

"新能力是否真的更好"必须由人在不知道版本身份的情况下裁决：
- 两版成片随机分配为「版本甲/版本乙」，匿名文件复制到实验目录；
- 对应关系（key）写在 projects/.ab_keys/（gitignore 区域），不进实验包；
- 盲评表（四维 1-5 分 + 总评）随包生成，评完再对答案。
"""
import secrets
import shutil
import time
from pathlib import Path

from src.jsonio import save_json
from src.material_manager import probe_media
from src.provenance import sha1_file
from src.store import ROOT, project_dir

RUBRIC = [
    ("音乐匹配", "高潮段画面是否有张力、舒缓段是否沉得住、切点是否贴乐句"),
    ("画面节奏", "镜头时长与切换密度是否舒适，无碎剪或拖沓"),
    ("风格一致性", "调色、运动、景别变化是否统一协调"),
    ("整体观感", "看完是否想再看一遍，有没有想拖动的段落"),
]


def package(pid_a: str, pid_b: str, out_dir, note: str = "") -> dict:
    """打包两版为盲评包。返回 {out, key, mapping}；mapping 不对外打印。"""
    finals = []
    for pid in (pid_a, pid_b):
        f = project_dir(pid) / "output" / "final.mp4"
        if not f.exists() or f.stat().st_size == 0:
            raise RuntimeError(f"{pid} 还没有成片（先 make）")
        finals.append((pid, f))

    out = Path(out_dir)
    if out.exists() and any(out.iterdir()):
        raise RuntimeError(f"实验目录已存在且非空: {out}")
    out.mkdir(parents=True, exist_ok=True)

    order = list(finals)
    secrets.SystemRandom().shuffle(order)
    mapping = {label: {"pid": pid,
                       "final_sha1": sha1_file(f)}
               for label, (pid, f) in zip(("版本甲", "版本乙"), order)}
    for label, info in mapping.items():
        shutil.copy2(project_dir(info["pid"]) / "output" / "final.mp4",
                     out / f"{label}.mp4")

    # key 藏在包外（projects/ 整体 gitignore），实验包内不留任何身份信息
    key_dir = ROOT / "projects" / ".ab_keys"
    key_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    key_path = key_dir / f"{stamp}.json"
    save_json(key_path, {"out": str(out), "mapping": mapping,
                         "note": note, "created": stamp})

    lines = [
        "# A/B 盲评表",
        "",
        f"> 生成于 {time.strftime('%Y-%m-%dT%H:%M:%S')}。"
        "两个版本使用**同一首音乐、同一素材池、同一输出规格**，"
        "差别只在剪辑方案。请先盲评，再对答案（key 在 projects/.ab_keys/）。",
        "",
        f"- 观看文件：本目录 `版本甲.mp4` 与 `版本乙.mp4`",
        "- 建议同一副耳机、相近音量连看，每版至少看两遍",
        "",
        "| 维度 | 说明 | 版本甲（1-5） | 版本乙（1-5） | 备注 |",
        "|---|---|---|---|---|",
    ]
    for name, desc in RUBRIC:
        lines.append(f"| {name} | {desc} |  |  |  |")
    lines += [
        "",
        "## 总评",
        "",
        "- 更喜欢哪一版（甲/乙/难分）：",
        "- 具体说一个甲更好的瞬间（时间点）：",
        "- 具体说一个乙更好的瞬间（时间点）：",
        "- 是否愿意把其中一版投稿（哪版）：",
    ]
    if note:
        lines.insert(3, f"> 实验说明：{note}")
    (out / "盲评表.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # 成片规格一致性快查（写入 key 供事后核对，不进实验包）
    specs = {}
    for label, info in mapping.items():
        f = project_dir(info["pid"]) / "output" / "final.mp4"
        specs[label] = probe_media(str(f))
    save_json(key_path, {"out": str(out), "mapping": mapping,
                         "specs": specs, "note": note, "created": stamp})
    return {"out": str(out), "key": str(key_path), "mapping": mapping}
