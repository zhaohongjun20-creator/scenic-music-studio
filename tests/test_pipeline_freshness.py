"""Fast content-integrity tests; deliberately model renderer's incomplete key."""
import copy
import hashlib
import os
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import pipeline, provenance, review, store
from src.config import DEFAULTS
from src.jsonio import load_json, save_json


@pytest.fixture
def studio(iso, monkeypatch):
    calls = Counter()
    music = iso / "music.wav"
    music.write_bytes(b"music-original")
    style = iso / "style.json"
    save_json(style, {"derived": {"video": {"contrast": 1.0}}})
    brief = {"music": str(music), "duration_s": 10, "theme_zh": "mountains",
             "seed": 7, "style_profile": str(style)}
    pid = store.new_project(brief)["id"]
    pdir = store.project_dir(pid)
    cfg = copy.deepcopy(DEFAULTS)
    assignments, rendered = [], []

    def analyze(path, **kwargs):
        calls["music"] += 1
        return {"file": path, "duration_s": 30, "tempo": {"bpm": 120},
                "sections": [], "play_window": {"start": 0},
                "content": provenance.sha256_file(path)}

    def storyboard(brief, music_map, cfg, api_key, style=None):
        calls["story"] += 1
        return {"generator": {"mode": "rules"}, "brief": brief,
                "sections": [{"shots": [{}, {}]}]}

    def timeline(sb, mm, *args, **kwargs):
        calls["timeline"] += 1
        return {"target_s": 10, "audio": {"file": mm["file"], "offset_s": 0},
                "shots": [{"index": i, "scene": "scene-" + str(i),
                           "start": i * 5, "duration": 5, "render_length": 5,
                           "role": "build", "revision": 0,
                           "transition_out": {"duration": 0, "style": "fade"}}
                          for i in range(2)]}

    def assign(pid, tline, cfg, keys, offline=False):
        calls["materials"] += 1
        assignments.append([s["index"] for s in tline["shots"]])
        for s in tline["shots"]:
            aid = f'asset-{s["index"]}-{s["revision"]}'
            path = iso / (aid + ".mp4")
            if not path.exists():
                path.write_bytes(aid.encode())
            s["asset"] = {"id": aid, "file": str(path), "src_in": 0,
                          "license": "local_test", "provider": "local_test"}

    def clip(s, video, outdir):
        calls["clip_calls"] += 1
        # Same omissions as renderer: no width/height/fps or source content.
        key = hashlib.sha1(str((s["asset"]["id"], s["asset"]["src_in"],
                               s["render_length"], s["revision"])).encode()).hexdigest()[:10]
        dest = outdir / f's{s["index"]:02d}_{key}.mp4'
        if not dest.exists():
            calls["clip_writes"] += 1
            rendered.append(s["index"])
            dest.write_bytes(provenance.content_fingerprint({
                "shot": s, "video": video,
                "asset": provenance.sha256_file(s["asset"]["file"])}).encode())
        return dest

    def compose(clips, tline, dest):
        calls["render"] += 1
        dest.write_bytes(b"|".join(Path(p).read_bytes() for p in clips)
                         + Path(tline["audio"]["file"]).read_bytes())

    def cover(final, at_s, dest):
        calls["cover"] += 1
        dest.write_bytes(hashlib.sha256(Path(final).read_bytes()).digest())

    def qc(pdir, tline, mm, cfg):
        calls["qc"] += 1
        report = {"passed": True, "problems": [], "cut_report": {}}
        save_json(pdir / "output" / "qc_report.json", report)
        return True, [], report

    monkeypatch.setattr(pipeline.music_analyzer, "analyze", analyze)
    monkeypatch.setattr(pipeline.music_analyzer, "save_music_map", save_json_map)
    monkeypatch.setattr(pipeline.director, "plan_storyboard", storyboard)
    monkeypatch.setattr(pipeline.tl_mod, "plan_from_storyboard", timeline)
    monkeypatch.setattr(pipeline.tl_mod, "validate_timeline", lambda *args: [])
    monkeypatch.setattr(pipeline.material_manager, "assign_assets", assign)
    monkeypatch.setattr(pipeline.renderer, "render_clip", clip)
    monkeypatch.setattr(pipeline.renderer, "compose", compose)
    monkeypatch.setattr(pipeline.renderer, "make_cover", cover)
    monkeypatch.setattr(pipeline.qc, "qc_project", qc)
    return SimpleNamespace(pid=pid, pdir=pdir, cfg=cfg, music=music, style=style,
                           calls=calls, assignments=assignments, rendered=rendered,
                           make=lambda **kw: pipeline.make(pid, cfg, {}, offline=True, **kw))


def save_json_map(data, path):
    save_json(path, data)


def approve_fixture(s):
    # Test-only attestation of synthetic bytes; never applies to a real project.
    review.approve(s.pid, "unit-test fixture", reviewer="test-only",
                   checklist={key: True for key in review.CHECKLIST_ITEMS},
                   watched_full=True,
                   final_sha256=provenance.sha256_file(s.pdir / "output" / "final.mp4")["sha256"])


def test_noop_preserves_provenance_and_review(studio):
    s = studio
    s.make()
    proof = s.pdir / "provenance.json"
    before = (proof.read_bytes(), proof.stat().st_mtime_ns)
    approve_fixture(s)
    approved = (s.pdir / "review.json").read_bytes()
    calls = s.calls.copy()
    assert s.make()["qc_passed"]
    assert s.calls == calls
    assert (proof.read_bytes(), proof.stat().st_mtime_ns) == before
    assert (s.pdir / "review.json").read_bytes() == approved


def test_replaced_music_same_size_and_mtime_rebuilds(studio):
    s = studio
    s.make()
    before = s.music.stat()
    old_proof = load_json(s.pdir / "provenance.json")
    s.music.write_bytes(b"music-modified")
    os.utime(s.music, ns=(before.st_atime_ns, before.st_mtime_ns))
    s.make()
    assert s.calls["music"] == 2 and s.calls["render"] == 2
    proof = load_json(s.pdir / "provenance.json")
    assert proof["inputs"]["music"]["sha256"] != old_proof["inputs"]["music"]["sha256"]
    assert proof["outputs"]["final.mp4"] == provenance.sha1_file(s.pdir / "output" / "final.mp4")


@pytest.mark.parametrize("change", ["brief", "style", "video", "config"])
def test_changed_inputs_rebuild_and_do_not_relabel_old_output(studio, change):
    s = studio
    s.make()
    old_proof = load_json(s.pdir / "provenance.json")
    if change == "brief":
        brief = load_json(s.pdir / "brief.json")
        brief["theme_zh"] = "ocean"
        save_json(s.pdir / "brief.json", brief)
    elif change == "style":
        save_json(s.style, {"derived": {"video": {"contrast": 1.3}}})
    elif change == "video":
        s.cfg["video"].update(width=640, height=360, fps=24)
    else:
        s.cfg["qc"]["max_black_seconds"] = 1.0
    s.make()
    assert s.calls["render"] == 2
    assert s.calls["music"] == 1
    proof = load_json(s.pdir / "provenance.json")
    if change in ("style", "video"):
        assert s.calls["clip_writes"] == 4
        assert proof["outputs"]["final.mp4"] != old_proof["outputs"]["final.mp4"]
    if change in ("config", "video"):
        assert proof["config_sha256"] == provenance.content_fingerprint(s.cfg)


def test_same_asset_id_modified_bytes_invalidates_only_its_clip(studio):
    s = studio
    s.make()
    tline = load_json(s.pdir / "timeline.json")
    asset = Path(tline["shots"][0]["asset"]["file"])
    before = asset.stat()
    asset.write_bytes(b"changed-0")
    os.utime(asset, ns=(before.st_atime_ns, before.st_mtime_ns))
    s.make()
    assert s.calls["render"] == 2
    assert s.rendered == [0, 1, 0]
    assert s.assignments == [[0, 1]]
    proof = load_json(s.pdir / "provenance.json")
    assert proof["assets"][0]["sha256"] == provenance.sha256_file(asset)["sha256"]


@pytest.mark.parametrize("output", ["final.mp4", "cover.jpg", "qc_report.json"])
def test_missing_or_corrupt_output_is_not_a_cache_hit(studio, output):
    s = studio
    s.make()
    old_proof = (s.pdir / "provenance.json").read_bytes()
    path = s.pdir / "output" / output
    path.write_bytes(b"corrupt")
    s.make()
    assert s.calls["qc"] == 2
    assert s.calls["render"] == (1 if output == "qc_report.json" else 2)
    if output == "qc_report.json":
        assert (s.pdir / "provenance.json").read_bytes() == old_proof
    else:
        assert path.read_bytes() != b"corrupt"
    path.unlink()
    s.make()
    assert path.exists()


def test_corrupt_clip_does_not_poison_recomposition(studio):
    s = studio
    s.make()
    clip = next((s.pdir / "clips").glob("s00_*.mp4"))
    clip.write_bytes(b"bad")
    s.make()
    assert s.rendered == [0, 1, 0]
    assert clip.read_bytes() != b"bad"
    assert b"bad" not in (s.pdir / "output" / "final.mp4").read_bytes()


def test_redo_shot_preserves_other_assets_and_clip_bytes(studio):
    s = studio
    s.make()
    other = next((s.pdir / "clips").glob("s01_*.mp4"))
    before = (other.read_bytes(), other.stat().st_mtime_ns)
    review.redo_shot(s.pid, 0, "new scene")
    s.make()
    assert s.calls["music"] == s.calls["story"] == s.calls["timeline"] == 1
    assert s.assignments == [[0, 1], [0]]
    assert s.rendered == [0, 1, 0]
    tline = load_json(s.pdir / "timeline.json")
    assert [shot["revision"] for shot in tline["shots"]] == [1, 0]
    assert (other.read_bytes(), other.stat().st_mtime_ns) == before


def test_legacy_done_without_fingerprints_rebuilds(studio):
    s = studio
    s.make()
    mf = store.load_manifest(s.pid)
    del mf["freshness"]
    store.save_manifest(s.pid, mf)
    s.make()
    assert s.calls["music"] == s.calls["story"] == s.calls["timeline"] == 2
    assert s.calls["clip_writes"] == 4
    assert s.calls["render"] == 2


def test_failed_rebuild_leaves_original_provenance_and_cannot_approve(studio, monkeypatch):
    s = studio
    s.make()
    approve_fixture(s)
    proof = (s.pdir / "provenance.json").read_bytes()
    s.music.write_bytes(b"new-music")

    def fail(*args, **kwargs):
        raise RuntimeError("analysis failed")

    monkeypatch.setattr(pipeline.music_analyzer, "analyze", fail)
    with pytest.raises(RuntimeError, match="analysis failed"):
        s.make()
    assert (s.pdir / "provenance.json").read_bytes() == proof
    assert store.load_manifest(s.pid)["status"] == "draft"
    assert review.load_review(s.pid)["status"] == "draft"
    with pytest.raises(store.StoreError):
        approve_fixture(s)


def test_input_changed_during_render_cannot_get_new_provenance(studio, monkeypatch):
    s = studio
    s.make()
    proof = (s.pdir / "provenance.json").read_bytes()
    compose = pipeline.renderer.compose

    def mutate(clips, tline, dest):
        compose(clips, tline, dest)
        s.music.write_bytes(b"raced")

    monkeypatch.setattr(pipeline.renderer, "compose", mutate)
    with pytest.raises(store.StoreError, match="制作期间输入发生变化"):
        s.make(redo=True)
    assert (s.pdir / "provenance.json").read_bytes() == proof
    assert store.load_manifest(s.pid)["stages"]["render"]["status"] == "running"


def test_retry_clears_unreceipted_partial_clip(studio, monkeypatch):
    s = studio
    real_clip = pipeline.renderer.render_clip

    def interrupted(shot, video_cfg, directory):
        path = real_clip(shot, video_cfg, directory)
        path.write_bytes(b"partial")
        raise RuntimeError("clip interrupted")

    monkeypatch.setattr(pipeline.renderer, "render_clip", interrupted)
    with pytest.raises(RuntimeError, match="clip interrupted"):
        s.make()
    monkeypatch.setattr(pipeline.renderer, "render_clip", real_clip)
    s.make()
    assert b"partial" not in (s.pdir / "output" / "final.mp4").read_bytes()
    assert s.calls["music"] == 1


@pytest.mark.parametrize("artifact", ["music_map.json", "storyboard.json", "timeline.json", "provenance.json"])
def test_deleted_artifact_rebuilt_not_silently_trusted(studio, artifact):
    s = studio
    s.make()
    (s.pdir / artifact).unlink()
    s.make()
    assert (s.pdir / artifact).exists()
    assert s.calls["render"] == 2
    if artifact == "provenance.json":
        assert s.calls["music"] == s.calls["story"] == s.calls["timeline"] == 1


def test_missing_input_revokes_old_approval_without_relabeling(studio):
    s = studio
    s.make()
    approve_fixture(s)
    proof = (s.pdir / "provenance.json").read_bytes()
    s.music.unlink()
    with pytest.raises(store.StoreError):
        s.make()
    assert (s.pdir / "provenance.json").read_bytes() == proof
    assert review.load_review(s.pid)["status"] == "draft"
    with pytest.raises(store.StoreError):
        approve_fixture(s)


def test_redo_true_rebuilds_all_clips(studio):
    s = studio
    s.make()
    s.make(redo=True)
    assert s.calls["clip_writes"] == 4
    assert s.calls["music"] == s.calls["render"] == 2


def test_incomplete_stage_receipt_is_not_a_cache_hit(studio):
    s = studio
    s.make()
    mf = store.load_manifest(s.pid)
    mf["freshness"]["stages"]["music_map"]["outputs"] = {}
    store.save_manifest(s.pid, mf)
    (s.pdir / "music_map.json").unlink()
    s.make()
    assert s.calls["music"] == 2


def test_sha256_records_are_full_length_and_keep_legacy_sha1(studio):
    s = studio
    s.make()
    proof = load_json(s.pdir / "provenance.json")
    assert len(proof["config_sha256"]) == 64
    assert len(proof["config_fingerprint"]) == 16
    for record in [proof["inputs"]["music"], proof["inputs"]["brief_file"],
                   proof["inputs"]["style_profile"], *proof["assets"],
                   *proof["artifacts"].values(), *proof["outputs"].values()]:
        assert len(record["sha256"]) == 64
        assert len(record["sha1"]) == 40
