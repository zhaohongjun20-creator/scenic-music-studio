# -*- coding: utf-8 -*-
"""Portable evidence snapshots, not a substitute for watching or licensing media.

Every bundled file is SHA256-addressed. JSON paths are rewritten in the copy,
never in the live project. Metadata-only bundles can be integrity-checked but
are deliberately not labelled self-contained. No missing file gets a hash.
"""
import hashlib
import shutil
from pathlib import Path, PurePosixPath, PureWindowsPath

from src import store
from src.jsonio import load_json, save_json


class EvidenceError(store.StoreError):
    pass


def digest(path: Path) -> dict:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return {"sha256": h.hexdigest(), "bytes": path.stat().st_size}


def _safe_file(root: Path, name: str) -> Path:
    """Reject absolute/traversing/symlink references even on the other OS."""
    if not isinstance(name, str) or not name or "\\" in name:
        raise EvidenceError(f"非法证据相对路径: {name!r}")
    p, w = PurePosixPath(name), PureWindowsPath(name)
    if p.is_absolute() or w.drive or ".." in p.parts or ":" in name:
        raise EvidenceError(f"非法证据相对路径: {name!r}")
    target = root.joinpath(*p.parts)
    if any(parent.is_symlink() for parent in (target, *target.parents)
           if parent == root or root in parent.parents):
        raise EvidenceError(f"证据路径不可为符号链接: {name}")
    if not target.resolve().is_relative_to(root.resolve()):
        raise EvidenceError(f"证据路径越界: {name}")
    return target


def _absolute(value: str) -> bool:
    return Path(value).is_absolute() or PureWindowsPath(value).is_absolute()


def _source_file(value: str, pdir: Path) -> Path:
    p = Path(value)
    if p.is_absolute():
        return p
    # Live projects use repository-relative music/style paths. Prefer a real
    # project-local path when importing a previously exported snapshot.
    local = pdir / p
    return local if local.is_file() else store.ROOT / p


def export_project(pid: str, out, *, include_media: bool = False) -> dict:
    return export_snapshot(pid, store.project_dir(pid), out,
                           include_media=include_media)


def export_snapshot(pid: str, pdir, out, *, include_media: bool = False) -> dict:
    """Export a live project or historical evidence directory without mutation.

    include_media is an explicit redistribution choice; callers must ensure
    licences permit it. Existing destinations are never overwritten.
    """
    pdir, out = Path(pdir).resolve(), Path(out).resolve()
    if out.exists() or out == pdir or pdir.is_relative_to(out):
        raise EvidenceError("证据输出必须是新的目录，且不能覆盖源目录")
    timeline = load_json(pdir / "timeline.json")
    music_map = load_json(pdir / "music_map.json")
    if not timeline or not music_map:
        raise EvidenceError("证据导出需要合法 timeline.json 和 music_map.json")
    refs, files, problems = {}, {}, []

    def reference(value: str, kind: str) -> str:
        if value in refs:
            return refs[value]["path"]
        source = _source_file(value, pdir)
        info = digest(source) if source.is_file() else None
        ext = source.suffix.lower()
        if not ext or len(ext) > 10 or not ext[1:].isalnum():
            ext = ".bin"
        token = info["sha256"][:24] if info else hashlib.sha256(
            value.encode("utf-8")).hexdigest()[:24]
        rel = f"media/{kind}/{token}{ext}"
        entry = {"path": rel, "kind": kind, "included": bool(info and include_media),
                 "source_available": bool(info), "fingerprint": info}
        refs[value] = entry
        if not info:
            problems.append(f"源文件缺失，未生成哈希: {rel}")
        return rel

    # Register known source fields before normalizing nested provenance etc.
    brief = load_json(pdir / "brief.json", {})
    for value in (brief.get("music"), timeline.get("audio", {}).get("file"),
                  music_map.get("file")):
        if value:
            reference(value, "music")
    if brief.get("style_profile"):
        reference(brief["style_profile"], "style")
    for shot in timeline.get("shots", []):
        value = (shot.get("asset") or {}).get("file")
        if value:
            reference(value, "assets")

    def normalize(obj, key=""):
        if isinstance(obj, dict):
            return {k: normalize(v, k) for k, v in obj.items()}
        if isinstance(obj, list):
            return [normalize(v, key) for v in obj]
        if isinstance(obj, str):
            if obj in refs:
                return refs[obj]["path"]
            if key in ("file", "music", "style_profile") and obj:
                return reference(obj, "other")
            if _absolute(obj):
                # Unknown absolute metadata is not a portable input contract.
                problems.append(f"移除了绝对路径元数据字段: {key}")
                return "[local path omitted]"
        return obj

    out.mkdir(parents=True)
    names = ("brief.json", "music_map.json", "storyboard.json", "timeline.json",
             "materials_used.json", "manifest.json", "review.json", "provenance.json")
    try:
        for name in names:
            source = pdir / name
            if source.is_file():
                obj = load_json(source)
                if obj is None:
                    raise EvidenceError(f"源 JSON 无效: {name}")
                save_json(out / name, normalize(obj))
                files[name] = {**digest(out / name), "source_sha256": digest(source)["sha256"]}
        for name in ("qc_report.json", "final.mp4", "cover.jpg", "contact_sheet.jpg"):
            source = pdir / "output" / name
            if not source.is_file():
                source = pdir / name
            if not source.is_file():
                if name == "final.mp4":
                    problems.append("成片未包含且源文件不可用；不能核对历史 QC 对应成片")
                continue
            if name.endswith(".json"):
                obj = load_json(source)
                if obj is None:
                    raise EvidenceError(f"源 JSON 无效: {name}")
                save_json(out / name, normalize(obj))
            elif name == "final.mp4" and not include_media:
                # Final is needed for actual human viewing, not metadata QA.
                reference(str(source), "outputs")
                continue
            else:
                shutil.copy2(source, out / name)
            files[name] = {**digest(out / name), "source_sha256": digest(source)["sha256"]}

        for value, entry in refs.items():
            if entry["included"]:
                target = _safe_file(out, entry["path"])
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(_source_file(value, pdir), target)
                copied = digest(target)
                if copied != entry["fingerprint"]:
                    raise EvidenceError("源素材在导出过程中改变，请重新导出")
                files[entry["path"]] = copied
        qc = load_json(out / "qc_report.json", {})
        final_included = "final.mp4" in files
        record = {
            "schema_version": 1, "project": pid,
            "path_base": "evidence-directory", "files": files,
            "references": list(refs.values()), "warnings": problems,
            "acceptance": {
                "technical_qc": "reported_pass" if qc.get("passed") else "not_passed_or_missing",
                "human_review": "not_verified_by_export",
                "reference_style": "not_verified_by_export",
                "final_included": final_included,
            },
            "note": "哈希证明文件一致性，不证明来源可信、授权或审美合格。QC 是源记录，不是本次重新渲染/重新质检。",
        }
        save_json(out / "evidence_manifest.json", record)
        return verify_bundle(out)
    except Exception:
        # Keep failed staging data visible; never describe it as a valid bundle.
        (out / "EXPORT_FAILED.txt").write_text("导出未完成，不可作为验收通过证据。", encoding="utf-8")
        raise


def verify_bundle(root) -> dict:
    root = Path(root).resolve()
    manifest = load_json(root / "evidence_manifest.json")
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise EvidenceError("证据 manifest 缺失或版本不支持")
    files = manifest.get("files")
    refs = manifest.get("references")
    if not isinstance(files, dict) or not files or not isinstance(refs, list):
        raise EvidenceError("证据 manifest 文件清单无效")
    problems = []
    if (root / "EXPORT_FAILED.txt").exists():
        problems.append("证据包存在失败标记")
    for name, expected in files.items():
        target = _safe_file(root, name)
        if not target.is_file():
            problems.append(f"文件缺失: {name}")
        elif not isinstance(expected, dict) or any(
                digest(target).get(k) != expected.get(k) for k in ("sha256", "bytes")):
            problems.append(f"文件哈希或大小不符: {name}")
    missing = []
    for ref in refs:
        if not isinstance(ref, dict):
            raise EvidenceError("素材引用记录无效")
        name = ref.get("path")
        target = _safe_file(root, name)
        fingerprint = ref.get("fingerprint")
        if not ref.get("included"):
            missing.append(name)
        elif name not in files or not target.is_file() or not fingerprint or digest(target) != fingerprint:
            problems.append(f"素材引用与文件清单不符: {name}")
    # Structural cross-check: paths used by normalized timeline/music/brief
    # must correspond to references; never fall back to the original machine.
    allowed = {r["path"] for r in refs}

    def check_paths(obj, key=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                check_paths(v, k)
        elif isinstance(obj, list):
            for v in obj:
                check_paths(v, key)
        elif isinstance(obj, str) and key in ("file", "music", "style_profile") and obj:
            if obj not in allowed:
                problems.append(f"未登记素材引用: {obj}")
    for name in ("timeline.json", "music_map.json", "brief.json"):
        if name in files:
            check_paths(load_json(_safe_file(root, name), {}))
    return {"out": str(root), "integrity_passed": not problems,
            "self_contained": not problems and not missing and "final.mp4" in files,
            "missing_media": missing, "problems": problems,
            "warnings": manifest.get("warnings", []),
            "acceptance": manifest.get("acceptance", {})}
