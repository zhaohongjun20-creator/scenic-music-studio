# -*- coding: utf-8 -*-
"""审片闸门与发布物料测试（不依赖 ffmpeg/API）。"""
import pytest

from src import publisher, review, store
from src.jsonio import load_json, save_json

CFG = {"glm": {"model": "t", "temperature": 0.5, "max_tokens": 10,
               "timeout_s": 5}}


@pytest.fixture
def pid(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "ROOT", tmp_path)
    mf = store.new_project({
        "theme_zh": "测试山海", "music": "m.wav", "duration_s": 10,
        "music_credit": {"title": "At Rest", "artist": "Kevin MacLeod",
                         "license": "CC BY 4.0",
                         "source_url": "https://example.com/at-rest"}})
    shots = [
        {"index": i, "revision": 0, "role": ["opening", "climax"][i],
         "asset": {"id": f"pexels-{i + 1}", "provider": "pexels",
                   "page_url": f"https://www.pexels.com/video/{i + 1}/",
                   "url": f"https://v/{i + 1}", "author": chr(65 + i)}}
        for i in range(2)]
    save_json(store.project_dir(mf["id"]) / "timeline.json",
              {"target_s": 10.0, "audio": {}, "shots": shots})
    return mf["id"]


def test_publish_requires_ready(pid):
    review.mark_rendered(pid, True, [])
    assert review.load_review(pid)["status"] == "review"
    with pytest.raises(publisher.PublishError):
        publisher.prepare(pid, CFG, api_key="")


def test_approve_then_prepare(pid):
    review.mark_rendered(pid, True, [])
    review.approve(pid, "可以")
    assert review.load_review(pid)["status"] == "ready"
    out = publisher.prepare(pid, CFG, api_key="")
    text = out.read_text(encoding="utf-8")
    assert "At Rest" in text and "CC BY 4.0" in text          # 音乐署名
    assert "pexels.com/video/1/" in text                       # 素材可追溯
    assert "final.mp4" in text and "cover.jpg" in text         # 文件清单


def test_fallback_copy_when_no_glm(pid):
    review.mark_rendered(pid, True, [])
    review.approve(pid)
    out = publisher.prepare(pid, CFG, api_key="")
    assert "把测试山海的风" in out.read_text(encoding="utf-8")


def test_reject_requires_note(pid):
    with pytest.raises(store.StoreError):
        review.reject(pid, note="   ")


def test_redo_shot_invalidates_downstream(pid):
    review.mark_rendered(pid, True, [])
    review.approve(pid)
    review.redo_shot(pid, 0, note="换一个更壮阔的")
    tline = load_json(store.project_dir(pid) / "timeline.json")
    assert tline["shots"][0]["revision"] == 1
    assert "pexels-1" in tline["shots"][0]["exclude_assets"]
    assert tline["shots"][1]["revision"] == 0
    manifest = store.load_manifest(pid)
    for stage in ("materials", "render", "qc"):
        assert manifest["stages"][stage]["status"] == "stale"
    assert review.load_review(pid)["status"] == "draft"
