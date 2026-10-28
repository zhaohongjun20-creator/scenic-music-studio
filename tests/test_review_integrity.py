# -*- coding: utf-8 -*-
"""审片证据/发布复核回归测试；所有媒体和人工声明仅为临时 fixture。"""
import copy
import hashlib

import pytest

from src import publisher, review, store
from src.jsonio import save_json

CFG = {"glm": {"model": "test", "temperature": 0.5, "timeout_s": 5}}


@pytest.fixture
def rendered(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "ROOT", tmp_path)
    pid = store.new_project({"theme_zh": "山海"})["id"]
    pdir = store.project_dir(pid)
    (pdir / "output" / "final.mp4").write_bytes(b"fixture-only-render-v1")
    save_json(pdir / "timeline.json", {"target_s": 10.0, "shots": []})
    save_json(pdir / "output" / "qc_report.json", {"passed": True, "problems": []})
    review.mark_rendered(pid, True, [])
    return pid


def attestation(pid):
    return {"reviewer": "fixture-reviewer", "watched_full": True,
            "checklist": {key: True for key in review.CHECKLIST_ITEMS},
            "final_sha256": review.load_review(pid)["binding"]["final_sha256"]}


def approve_fixture(pid):
    return review.approve(pid, "fixture-only approval", **attestation(pid))


def test_render_binds_exact_file_bytes_and_never_auto_approves(rendered):
    record = review.load_review(rendered)
    pdir = store.project_dir(rendered)
    assert record["status"] == "review"
    assert "approval" not in record
    for key, relpath in (("final_sha256", "output/final.mp4"),
                         ("timeline_sha256", "timeline.json"),
                         ("qc_sha256", "output/qc_report.json")):
        assert record["binding"][key] == hashlib.sha256((pdir / relpath).read_bytes()).hexdigest()


@pytest.mark.parametrize("key", ["reviewer", "watched_full", "checklist", "final_sha256"])
def test_each_explicit_attestation_field_is_required(rendered, key):
    args = attestation(rendered)
    args.pop(key)
    with pytest.raises(store.StoreError):
        review.approve(rendered, **args)
    assert review.load_review(rendered)["status"] == "review"


@pytest.mark.parametrize("value", ["", "  ", None, 12, False])
def test_reviewer_must_be_nonempty_text(rendered, value):
    args = attestation(rendered)
    args["reviewer"] = value
    with pytest.raises(store.StoreError, match="reviewer"):
        review.approve(rendered, **args)


@pytest.mark.parametrize("value", [False, 1, "true", None])
def test_full_viewing_requires_literal_true(rendered, value):
    args = attestation(rendered)
    args["watched_full"] = value
    with pytest.raises(store.StoreError, match="完整观看"):
        review.approve(rendered, **args)


@pytest.mark.parametrize("item", review.CHECKLIST_ITEMS)
@pytest.mark.parametrize("value", [False, 1, "true", None])
def test_every_checklist_item_requires_literal_true(rendered, item, value):
    args = attestation(rendered)
    args["checklist"][item] = value
    with pytest.raises(store.StoreError, match="六项"):
        review.approve(rendered, **args)


@pytest.mark.parametrize("mode", ["missing", "extra", "list"])
def test_checklist_shape_is_exact(rendered, mode):
    args = attestation(rendered)
    if mode == "missing":
        args["checklist"].pop("ending")
    elif mode == "extra":
        args["checklist"]["automatic_qc"] = True
    else:
        args["checklist"] = list(review.CHECKLIST_ITEMS)
    with pytest.raises(store.StoreError, match="六项"):
        review.approve(rendered, **args)


@pytest.mark.parametrize("digest", ["", "bad", "0" * 63, "z" * 64, "0" * 64, None])
def test_explicit_digest_must_match(rendered, digest):
    args = attestation(rendered)
    args["final_sha256"] = digest
    with pytest.raises(store.StoreError, match="SHA256"):
        review.approve(rendered, **args)


def test_approval_captures_identity_checklist_and_version(rendered):
    args = attestation(rendered)
    args["reviewer"] = " fixture-reviewer "
    args["final_sha256"] = args["final_sha256"].upper()
    record = review.approve(rendered, "fixture-only", **args)
    approval = record["approval"]
    args["checklist"]["opening"] = False
    assert approval["reviewer"] == "fixture-reviewer"
    assert approval["watched_full"] is True
    assert approval["declaration"] == review.WATCHED_FULL_DECLARATION
    assert all(approval["checklist"].values())
    assert approval["binding"] == record["binding"]
    assert approval["render_id"] == record["render_id"]
    assert store.load_manifest(rendered)["status"] == "ready"
    assert review.validate_approval(rendered) == record


@pytest.mark.parametrize("path", ["output/final.mp4", "timeline.json", "output/qc_report.json"])
@pytest.mark.parametrize("operation", ["change", "delete", "empty"])
def test_changed_or_missing_artifact_blocks_approve_and_publish(rendered, path, operation):
    approve_fixture(rendered)
    args = attestation(rendered)
    out = publisher.prepare(rendered, CFG, "")
    target = store.project_dir(rendered) / path
    if operation == "delete":
        target.unlink()
    elif operation == "empty":
        target.write_bytes(b"")
    else:
        # A whitespace-only change to JSON also creates a different version.
        target.write_bytes(target.read_bytes() + b" ")
    with pytest.raises(store.StoreError):
        review.approve(rendered, **args)
    with pytest.raises(publisher.PublishError):
        publisher.prepare(rendered, CFG, "")
    assert not out.exists()


@pytest.mark.parametrize("report", [{}, {"passed": False, "problems": []},
                                   {"passed": True}, {"passed": "true", "problems": []},
                                   {"passed": True, "problems": ["bad"]}, [], None])
def test_qc_flag_alone_cannot_mint_valid_binding(rendered, report):
    save_json(store.project_dir(rendered) / "output" / "qc_report.json", report)
    record = review.mark_rendered(rendered, True, [])
    assert record["status"] == "draft"
    assert record["binding"] is None
    assert record["integrity_error"]
    assert store.load_manifest(rendered)["status"] == "draft"


def test_missing_mock_artifacts_leave_draft_without_breaking_pipeline(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "ROOT", tmp_path)
    pid = store.new_project({})["id"]
    record = review.mark_rendered(pid, True, [])
    assert record["status"] == "draft"
    assert "integrity_error" in record
    with pytest.raises(publisher.PublishError):
        publisher.prepare(pid, CFG, "")


def test_same_bytes_new_render_still_requires_fresh_approval(rendered):
    before = approve_fixture(rendered)
    old_approval = copy.deepcopy(before["approval"])
    out = publisher.prepare(rendered, CFG, "")
    after = review.mark_rendered(rendered, True, [])
    assert after["binding"] == before["binding"]
    assert after["render_id"] != before["render_id"]
    assert after["status"] == "review"
    assert "approval" not in after
    assert not out.exists()
    assert store.load_manifest(rendered)["status"] == "review"
    after.update(status="ready", approval=old_approval)
    review.save_review(rendered, after)
    manifest = store.load_manifest(rendered)
    manifest["status"] = "ready"
    store.save_manifest(rendered, manifest)
    with pytest.raises(publisher.PublishError, match="旧审批不可继承"):
        publisher.prepare(rendered, CFG, "")


@pytest.mark.parametrize("record", [{"status": "ready"},
    {"status": "ready", "history": [{"action": "approve", "note": "legacy"}]}])
def test_legacy_ready_record_is_not_approval(rendered, record):
    review.save_review(rendered, record)
    with pytest.raises(publisher.PublishError, match="旧审批不可继承"):
        publisher.prepare(rendered, CFG, "")


@pytest.mark.parametrize("field,value", [("reviewer", ""), ("watched_full", False),
    ("checklist", {}), ("binding", {}), ("render_id", "old"),
    ("declaration", ""), ("at", ""), ("at", None), ("final_sha256", "0" * 64)])
def test_publish_rechecks_all_approval_evidence(rendered, field, value):
    record = approve_fixture(rendered)
    record["approval"][field] = value
    review.save_review(rendered, record)
    with pytest.raises(publisher.PublishError):
        publisher.prepare(rendered, CFG, "")


@pytest.mark.parametrize("transition", ["reject", "invalidate", "failed_qc"])
def test_non_ready_transitions_revoke_approval_and_publication(rendered, transition):
    approve_fixture(rendered)
    out = publisher.prepare(rendered, CFG, "")
    if transition == "reject":
        record = review.reject(rendered, "fixture-only rejection")
        expected = "rejected"
    elif transition == "invalidate":
        record = review.invalidate_review(rendered, "fixture-only rebuild")
        expected = "draft"
    else:
        record = review.mark_rendered(rendered, False, ["fixture QC failure"])
        expected = "draft"
    assert record["status"] == expected
    assert store.load_manifest(rendered)["status"] == expected
    assert "approval" not in record
    assert not out.exists()
    with pytest.raises(publisher.PublishError):
        publisher.prepare(rendered, CFG, "")


@pytest.mark.parametrize("status", ["stale", "running", "failed"])
def test_failed_rebuild_cannot_reuse_old_unmodified_outputs(rendered, status):
    approve_fixture(rendered)
    manifest = store.load_manifest(rendered)
    manifest["stages"]["render"] = {"status": status}
    store.save_manifest(rendered, manifest)
    with pytest.raises(publisher.PublishError, match="重建"):
        publisher.prepare(rendered, CFG, "")


@pytest.mark.parametrize("manifest", [None, [], ["corrupt"], "corrupt"])
def test_invalid_manifest_is_fail_closed(rendered, manifest):
    approve_fixture(rendered)
    save_json(store.project_dir(rendered) / "manifest.json", manifest)
    with pytest.raises(publisher.PublishError, match="manifest"):
        publisher.prepare(rendered, CFG, "")


def test_invalidate_preserves_pipeline_stage_ledger(rendered):
    approve_fixture(rendered)
    manifest = store.load_manifest(rendered)
    manifest["stages"] = {"render": {"status": "stale", "custom": "keep"}}
    manifest["freshness"] = {"input_digest": "fixture-only"}
    store.save_manifest(rendered, manifest)
    review.invalidate_review(rendered, "fixture-only rebuild")
    updated = store.load_manifest(rendered)
    assert updated["stages"] == manifest["stages"]
    assert updated["freshness"] == manifest["freshness"]
    assert updated["status"] == "draft"


def test_manifest_state_mismatch_is_fail_closed(rendered):
    approve_fixture(rendered)
    manifest = store.load_manifest(rendered)
    manifest["status"] = "draft"
    store.save_manifest(rendered, manifest)
    with pytest.raises(publisher.PublishError, match="重建"):
        publisher.prepare(rendered, CFG, "")


@pytest.mark.parametrize("change", ["final", "timeline", "qc", "reject", "approval"])
def test_publish_checks_again_after_copy_generation(rendered, monkeypatch, change):
    approve_fixture(rendered)
    pdir = store.project_dir(rendered)

    def slow_copy(*args, **kwargs):
        if change == "final":
            (pdir / "output" / "final.mp4").write_bytes(b"changed-during-copy")
        elif change == "timeline":
            save_json(pdir / "timeline.json", {"target_s": 20.0, "shots": []})
        elif change == "qc":
            save_json(pdir / "output" / "qc_report.json", {"passed": False, "problems": []})
        elif change == "reject":
            review.reject(rendered, "fixture-only reject during copy")
        else:
            approve_fixture(rendered)
        return {"title": "fixture-only", "desc": "test", "tags": []}

    monkeypatch.setattr(publisher, "chat_json", slow_copy)
    with pytest.raises(publisher.PublishError):
        publisher.prepare(rendered, CFG, "fixture-not-a-real-api-key")
    assert not (pdir / "output" / "upload_info.txt").exists()


def test_upload_info_records_exact_approval_binding(rendered):
    record = approve_fixture(rendered)
    text = publisher.prepare(rendered, CFG, "").read_text(encoding="utf-8")
    assert record["approval"]["reviewer"] in text
    assert record["render_id"] in text
    assert review.WATCHED_FULL_DECLARATION in text
    for digest in record["binding"].values():
        assert digest in text
