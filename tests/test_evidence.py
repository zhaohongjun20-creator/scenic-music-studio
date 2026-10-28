import json
import shutil
from pathlib import Path

import pytest

from src import evidence, store
from src.jsonio import load_json, save_json


@pytest.fixture
def snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "ROOT", tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    music = tmp_path / "external music.wav"
    music.write_bytes(b"actual music bytes")
    asset = tmp_path / "external footage.mp4"
    asset.write_bytes(b"actual video bytes")
    (source / "output").mkdir()
    (source / "output" / "final.mp4").write_bytes(b"rendered movie")
    (source / "output" / "cover.jpg").write_bytes(b"cover bytes")
    save_json(source / "brief.json", {"music": str(music), "music_credit": {"license": "test"}})
    save_json(source / "music_map.json", {"file": str(music), "duration_s": 60})
    save_json(source / "timeline.json", {"audio": {"file": str(music)}, "shots": [
        {"asset": {"file": str(asset), "id": "p1", "license": "test", "page_url": "https://example.com/a"}}]})
    save_json(source / "output" / "qc_report.json", {"passed": True, "problems": []})
    return source


def test_full_bundle_can_move_and_does_not_change_live_project(snapshot, tmp_path):
    before = (snapshot / "timeline.json").read_bytes()
    out = tmp_path / "bundle"
    result = evidence.export_snapshot("project-001", snapshot, out, include_media=True)
    assert result["integrity_passed"] and result["self_contained"]
    assert result["acceptance"]["human_review"] == "not_verified_by_export"
    assert before == (snapshot / "timeline.json").read_bytes()
    manifest_text = (out / "evidence_manifest.json").read_text(encoding="utf-8")
    timeline_text = (out / "timeline.json").read_text(encoding="utf-8")
    assert str(tmp_path) not in manifest_text + timeline_text
    assert "https://example.com/a" in timeline_text
    moved = tmp_path / "moved" / "bundle"
    shutil.copytree(out, moved)
    assert evidence.verify_bundle(moved)["self_contained"]
    music = load_json(moved / "music_map.json")["file"]
    assert load_json(moved / "timeline.json")["audio"]["file"] == music
    assert (moved / music).read_bytes() == b"actual music bytes"


def test_metadata_bundle_is_not_self_contained(snapshot, tmp_path):
    out = tmp_path / "metadata"
    result = evidence.export_snapshot("project-001", snapshot, out)
    assert result["integrity_passed"]
    assert not result["self_contained"] and len(result["missing_media"]) == 3
    assert result["acceptance"]["technical_qc"] == "reported_pass"


def test_tamper_and_delete_fail(snapshot, tmp_path):
    out = tmp_path / "bundle"
    evidence.export_snapshot("p", snapshot, out, include_media=True)
    (out / "final.mp4").write_bytes(b"tampered")
    assert not evidence.verify_bundle(out)["integrity_passed"]
    (out / "final.mp4").unlink()
    assert "文件缺失: final.mp4" in evidence.verify_bundle(out)["problems"]


@pytest.mark.parametrize("name", ["../outside", "/tmp/x", "C:/Users/x", "C:\\Users\\x", "media/../../x"])
def test_path_escape_rejected(snapshot, tmp_path, name):
    out = tmp_path / "bundle"
    evidence.export_snapshot("p", snapshot, out)
    manifest = load_json(out / "evidence_manifest.json")
    manifest["files"][name] = {"sha256": "fake", "bytes": 1}
    save_json(out / "evidence_manifest.json", manifest)
    with pytest.raises(evidence.EvidenceError):
        evidence.verify_bundle(out)


def test_missing_historical_music_not_fabricated(snapshot, tmp_path):
    missing = "C:\\unknown\\old-repo\\music.mp3"
    save_json(snapshot / "music_map.json", {"file": missing})
    save_json(snapshot / "timeline.json", {"audio": {"file": missing}, "shots": []})
    save_json(snapshot / "brief.json", {"music": missing})
    out = tmp_path / "bundle"
    result = evidence.export_snapshot("p", snapshot, out, include_media=True)
    manifest = load_json(out / "evidence_manifest.json")
    ref = next(r for r in manifest["references"] if r["kind"] == "music")
    assert ref["fingerprint"] is None and not ref["included"]
    assert result["warnings"] and not result["self_contained"]
    assert missing not in (out / "music_map.json").read_text(encoding="utf-8")


def test_existing_out_is_not_overwritten(snapshot, tmp_path):
    out = tmp_path / "bundle"
    out.mkdir()
    (out / "user.txt").write_text("keep")
    with pytest.raises(evidence.EvidenceError):
        evidence.export_snapshot("p", snapshot, out)
    assert (out / "user.txt").read_text() == "keep"


def test_unregistered_path_fails_even_if_json_hash_updated(snapshot, tmp_path):
    out = tmp_path / "bundle"
    evidence.export_snapshot("p", snapshot, out)
    save_json(out / "music_map.json", {"file": "media/unknown.mp3"})
    manifest = load_json(out / "evidence_manifest.json")
    manifest["files"]["music_map.json"] = evidence.digest(out / "music_map.json")
    save_json(out / "evidence_manifest.json", manifest)
    assert not evidence.verify_bundle(out)["integrity_passed"]
