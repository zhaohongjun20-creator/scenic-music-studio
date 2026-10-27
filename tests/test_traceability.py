# -*- coding: utf-8 -*-
"""溯源、决策报告、QC 相邻重复、A/B 盲评包测试。"""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import ab_experiment, material_manager, provenance, qc, report, store
from src.audio_io import ffmpeg_exe
from src.jsonio import load_json


def test_find_duplicate_adjacent():
    mk = lambda i, aid, sin: {"index": i, "start": i * 3.0, "asset":
                              {"id": aid, "src_in": sin}}
    t = {"shots": [mk(0, "a", 2.0), mk(1, "a", 2.1), mk(2, "b", 0.0)]}
    probs = qc.find_duplicate_adjacent(t)
    assert len(probs) == 1 and "#0→#1" in probs[0] and "a" in probs[0]
    assert qc.find_duplicate_adjacent(
        {"shots": [mk(0, "a", 2.0), mk(1, "a", 5.0)]}) == []   # 同素材不同区间不报
    assert qc.find_duplicate_adjacent(
        {"shots": [mk(0, "a", 2.0), mk(1, "b", 2.0)]}) == []


def test_provenance_record(iso, synth_music):
    from src import pipeline
    from src.config import load_config
    mf = store.new_project({"theme_zh": "测试", "music": str(synth_music),
                            "duration_s": 15, "seed": 7})
    pid = mf["id"]
    pipeline.make(pid, load_config(), keys={}, offline=True)
    prov = load_json(store.project_dir(pid) / "provenance.json")
    assert prov["project"] == pid
    assert prov["inputs"]["music"]["sha1"] == \
        provenance.sha1_file(synth_music)["sha1"]
    assert prov["config_fingerprint"] == provenance.config_fingerprint(
        load_config())
    tline = load_json(store.project_dir(pid) / "timeline.json")
    used = {s["asset"]["id"] for s in tline["shots"]}
    assert {a["id"] for a in prov["assets"]} == used
    assert all(a.get("license") for a in prov["assets"])
    final = store.project_dir(pid) / "output" / "final.mp4"
    assert prov["outputs"]["final.mp4"]["sha1"] == \
        provenance.sha1_file(final)["sha1"]
    assert prov["generator"]["mode"] == "rules"


def test_decision_report(iso, synth_music):
    from src import pipeline
    from src.config import load_config
    mf = store.new_project({"theme_zh": "测试山海", "music": str(synth_music),
                            "duration_s": 15, "seed": 7})
    pid = mf["id"]
    pipeline.make(pid, load_config(), keys={}, offline=True)
    text = report.build_markdown(pid, store.project_dir(pid))
    assert "剪辑决策报告" in text and "镜头明细" in text
    assert "段落边界" in text and "决策规则" in text
    assert "redo-shot" in text and "materials/index.json" in text
    tline = load_json(store.project_dir(pid) / "timeline.json")
    assert f"{len(tline['shots'])} 镜" in text


@pytest.fixture(scope="module")
def tiny_finals(tmp_path_factory):
    """两个 2s 纯色小成片，供盲评包测试。"""
    d = tmp_path_factory.mktemp("ab")
    for pid, color in (("project-101", "0x224488"), ("project-102", "0x884422")):
        pdir = d / pid
        (pdir / "output").mkdir(parents=True)
        subprocess.run(
            [ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
             "-f", "lavfi",
             "-i", f"color=c={color}:size=320x180:rate=10:duration=2",
             "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
             str(pdir / "output" / "final.mp4")], check=True, timeout=300)
    return d


def test_ab_package_blind(iso, tiny_finals):
    import shutil
    for pid in ("project-101", "project-102"):
        shutil.copytree(tiny_finals / pid, store.ROOT / "projects" / pid)
    out = store.ROOT / "ab" / "exp1"
    result = ab_experiment.package("project-101", "project-102", out)
    assert (out / "版本甲.mp4").exists() and (out / "版本乙.mp4").exists()
    assert (out / "盲评表.md").exists()
    sheet = (out / "盲评表.md").read_text(encoding="utf-8")
    assert "音乐匹配" in sheet and "总评" in sheet
    # 实验包内绝不泄漏项目号
    assert "project-101" not in sheet and "project-102" not in sheet
    # key 在包外且能无损还原对应关系
    key = load_json(result["key"])
    pids = {info["pid"] for info in key["mapping"].values()}
    assert pids == {"project-101", "project-102"}
    assert all(info["final_sha1"] for info in key["mapping"].values())


def test_prune_cache(iso):
    material_manager.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    import os
    for i in range(5):
        f = material_manager.CACHE_DIR / f"pexels-{i}.mp4"
        f.write_bytes(b"x" * 100)
        os.utime(f, (1000 + i, 1000 + i))
    assert material_manager.prune_cache(3) == 2
    left = {f.name for f in material_manager.CACHE_DIR.glob("*.mp4")}
    assert left == {"pexels-2.mp4", "pexels-3.mp4", "pexels-4.mp4"}
