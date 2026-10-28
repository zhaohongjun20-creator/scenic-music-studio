# -*- coding: utf-8 -*-
"""Content-bound evidence, written only for a newly rendered deliverable.

SHA-1 fields remain for older report consumers; new integrity checks use SHA-256.
A record describes files that actually produced this render, not a later brief.
"""
import hashlib
import json
import time
from pathlib import Path

from src.jsonio import load_json, save_json


def _hash_file(path, algorithms: tuple[str, ...]) -> dict | None:
    if not path:
        return None
    try:
        before = Path(path).stat()
        hashes = {name: hashlib.new(name) for name in algorithms}
        size = 0
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                size += len(chunk)
                for h in hashes.values():
                    h.update(chunk)
        after = Path(path).stat()
        stat_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if (size != before.st_size
                or any(getattr(before, k) != getattr(after, k) for k in stat_fields)):
            return None                 # A concurrently changed file is not evidence.
        return {**{name: h.hexdigest() for name, h in hashes.items()},
                "bytes": size}
    except OSError:
        return None


def sha1_file(path) -> dict | None:
    """Legacy API, now returning both hashes in a single streaming pass."""
    return _hash_file(path, ("sha1", "sha256"))


def sha256_file(path) -> dict | None:
    return _hash_file(path, ("sha256",))


def content_fingerprint(value) -> str:
    """Stable, full-length SHA-256 of a JSON-compatible value."""
    blob = json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def config_fingerprint(cfg: dict) -> str:
    """Retain the historical short SHA-1 config identifier for compatibility."""
    blob = json.dumps(cfg, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def build_record(pid: str, pdir: Path, cfg: dict, style_path: str | None,
                 timeline: dict) -> dict:
    brief = load_json(pdir / "brief.json") or {}
    storyboard = load_json(pdir / "storyboard.json") or {}
    music = brief.get("music")

    assets, seen = [], set()
    for s in timeline.get("shots", []):
        a = s.get("asset") or {}
        identity = (a.get("id"), a.get("file"))
        if a.get("id") and identity not in seen:
            seen.add(identity)
            assets.append({**{k: a.get(k) for k in
                              ("id", "provider", "file", "page_url", "author",
                               "author_url", "license", "license_url")},
                           **(sha1_file(a.get("file")) or {})})

    final, cover = pdir / "output" / "final.mp4", pdir / "output" / "cover.jpg"
    return {
        "schema_version": 2,
        "project": pid,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "inputs": {
            "music": ({"file": music, **(sha1_file(music) or {})}
                      if music else None),
            "brief": {k: brief.get(k) for k in
                      ("theme_zh", "mood", "duration_s", "platform",
                       "seed", "style_profile")},
            "brief_file": sha1_file(pdir / "brief.json"),
            "style_profile": ({"file": style_path, **(sha1_file(style_path) or {})}
                              if style_path else None),
        },
        "config_fingerprint": config_fingerprint(cfg),
        "config_sha256": content_fingerprint(cfg),
        "generator": storyboard.get("generator"),
        "assets": assets,
        "artifacts": {name: sha1_file(pdir / name) for name in
                      ("music_map.json", "storyboard.json", "timeline.json")},
        "outputs": {"final.mp4": sha1_file(final), "cover.jpg": sha1_file(cover)},
    }


def write_provenance(pid: str, pdir: Path, cfg: dict, style_path: str | None,
                     timeline: dict) -> Path:
    """The caller must have rendered and verified these inputs and outputs."""
    out = pdir / "provenance.json"
    save_json(out, build_record(pid, pdir, cfg, style_path, timeline))
    return out
