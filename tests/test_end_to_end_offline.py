# -*- coding: utf-8 -*-
"""端到端离线集成测试：合成音乐 → 全链 → 成片 + QC（真实 ffmpeg，无 API）。"""
from pathlib import Path

import pytest

from src import material_manager, pipeline, review, store
from src.config import load_config
from src.jsonio import load_json


@pytest.fixture
def iso(tmp_path, monkeypatch):
    """隔离存储：项目/素材缓存全部落到临时目录。"""
    monkeypatch.setattr(store, "ROOT", tmp_path)
    monkeypatch.setattr(material_manager, "CACHE_DIR", tmp_path / "mcache")
    monkeypatch.setattr(material_manager, "INDEX_PATH", tmp_path / "mindex.json")
    return tmp_path


def _make(iso, synth_music) -> tuple[str, dict]:
    mf = store.new_project({"theme_zh": "治愈系山海", "mood": "治愈",
                            "duration_s": 20.0, "music": str(synth_music),
                            "platform": "test"})
    return mf["id"], pipeline.make(mf["id"], load_config(), keys={},
                                   offline=True)


def test_offline_full_pipeline(iso, synth_music):
    pid, summary = _make(iso, synth_music)
    assert summary["qc_passed"], f"QC 问题: {summary['problems']}"
    assert review.load_review(pid)["status"] == "review"

    final = Path(summary["final"])
    assert final.exists() and final.stat().st_size > 100_000
    assert (final.parent / "cover.jpg").exists()

    report = load_json(final.parent / "qc_report.json")
    assert report["cut_report"]["cuts"] > 0
    assert report["cut_report"]["ratio"] >= 0.8

    tline = load_json(store.project_dir(pid) / "timeline.json")
    assert len(tline["shots"]) >= 3
    assert all(s["asset"] for s in tline["shots"])


def test_offline_redo_shot_reuses_cache(iso, synth_music):
    pid, _ = _make(iso, synth_music)
    review.redo_shot(pid, 0, note="换一个")
    summary = pipeline.make(pid, load_config(), keys={}, offline=True)
    assert summary["qc_passed"], summary["problems"]

    tline = load_json(store.project_dir(pid) / "timeline.json")
    assert tline["shots"][0]["revision"] == 1
    assert all(s["revision"] == 0 for s in tline["shots"][1:])
