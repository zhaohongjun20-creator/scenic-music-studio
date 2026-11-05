# -*- coding: utf-8 -*-
"""入点随 motion_hint，人物在下载 mp4 之前被拒绝。不需要 GPU，也不下载权重。"""
import builtins

import numpy as np
import pytest

from src import material_manager
from src.jsonio import save_json
from src import shot_window
from src.shot_window import (DETECTOR_UNAVAILABLE_NOTE, PeopleVerdict,
                             UnavailablePeopleDetector, choose_src_in,
                             frame_near, legacy_src_in, load_preview_samples,
                             parse_ppm, resolve_people_detector, select_src_in,
                             window_scores)


CFG = {"material": {"avoid_reuse_shots": 2, "candidates_per_query": 8,
                    "min_source_duration_s": 6, "max_height": 1080}}


def _sine(phase: float, mark_t: int, person: bool = False) -> np.ndarray:
    xs = np.linspace(0, 1, 40, dtype=np.float32)
    ys = np.linspace(0, 1, 24, dtype=np.float32)
    wave = 0.45 + 0.45 * np.sin(
        (2 * np.pi) * (xs[None, :] * 2.0 + ys[:, None] + phase))
    frame = np.clip(wave * 255.0, 0, 255).astype(np.uint8)
    frame[0, 0] = np.uint8(255 if person else 0)
    frame[0, 1] = np.uint8(mark_t)
    return frame


def _clip(duration: int, phase_at) -> list[tuple[float, np.ndarray]]:
    return [(float(t), _sine(float(phase_at(t)), t)) for t in range(duration)]


class _FakeDetector:
    def __init__(self, available=True, detail="fake"):
        self.available = available
        self.detail = detail
        self.seen = []

    def check(self, frame):
        self.seen.append(int(frame[0, 1]))
        if int(frame[0, 0]) == 255:
            return PeopleVerdict("rejected", "fake-person")
        return PeopleVerdict("clean", "fake")


def _video(vid: int, frames, duration=30):
    return {
        "id": vid, "width": 1920, "height": 1080, "duration": duration,
        "url": f"https://www.pexels.com/video/{vid}/",
        "user": {"name": "Ada", "url": "https://www.pexels.com/@ada"},
        "video_files": [{
            "file_type": "video/mp4", "height": 720, "width": 1280,
            "link": f"https://example.invalid/{vid}.mp4", "size": 1000,
        }],
        "_frames": frames,
    }


def _assign(monkeypatch, videos, detector, shots):
    downloaded = []

    def search(_key, _query, _per_page):
        return {"videos": videos}

    def materialize(cand, vid):
        downloaded.append(vid)
        return {"id": vid, "provider": "pexels", "file": f"/tmp/{vid}.mp4",
                "url": cand["files"][0]["link"], "page_url": cand["video"]["url"],
                "author": "Ada", "author_url": "", "license": "Pexels License",
                "license_url": "https://www.pexels.com/license/",
                "width": 1280, "height": 720, "fps": 30.0,
                "duration_s": float(cand["video"]["duration"])}

    def download(*_args, **_kwargs):
        raise AssertionError("筛选阶段不应下载 mp4")

    monkeypatch.setattr(material_manager, "_search", search)
    monkeypatch.setattr(material_manager, "_materialize", materialize)
    monkeypatch.setattr(material_manager, "_download", download)
    timeline = {"shots": shots}
    return downloaded, timeline


def test_aerial_and_push_leave_the_fixed_first_third():
    duration, need = 30.0, 4.0
    # 前半静止，后半才有连续位移。前 1/3（10s）落在静止段。
    samples = _clip(30, lambda t: 0.0 if t < 16 else 0.12 * (t - 16))
    legacy = legacy_src_in(duration, need)
    for hint in ("aerial", "push", "pan"):
        src, rule = choose_src_in(duration, need, hint, samples)
        assert rule == "motion"
        assert src >= 15.0
        assert abs(src - legacy) > 3.0


def test_static_prefers_the_low_motion_stretch():
    duration, need = 30.0, 4.0
    samples = _clip(30, lambda t: 0.15 * t if t < 14 else 0.15 * 14)
    src, rule = choose_src_in(duration, need, "static", samples)
    assert rule == "static"
    assert src >= 13.0
    assert abs(src - legacy_src_in(duration, need)) > 2.0


def test_hard_cut_loses_to_a_cleaner_motion_window():
    """运动更强但窗口里有硬切时，让给没有硬切的那段。"""
    times = [float(t) for t in range(31)]
    diffs = [0.0] + [0.02] * 30
    for index in range(21, 29):
        diffs[index] = 0.08
    diffs[10] = 0.80
    scored = window_scores(30.0, 4.0, times, diffs)
    loudest = max(scored, key=lambda row: row[1])
    assert loudest[2] is True
    src, rule = select_src_in(30.0, 4.0, "aerial", times, diffs)
    assert rule == "motion"
    assert src >= 19.0
    assert abs(src - loudest[0]) > 3.0
    assert abs(src - legacy_src_in(30.0, 4.0)) > 3.0


def test_image_hard_cut_is_skipped_for_the_later_motion():
    rng = np.random.default_rng(1)
    frames = []
    for t in range(30):
        if t == 10:
            frames.append((float(t), rng.integers(0, 256, size=(24, 40), dtype=np.uint8)))
        elif t >= 18:
            frames.append((float(t), _sine(0.1 * (t - 18), t)))
        else:
            frames.append((float(t), _sine(0.0, t)))
    src, rule = choose_src_in(30.0, 4.0, "push", frames)
    assert rule == "motion"
    assert src >= 17.0
    assert abs(src - legacy_src_in(30.0, 4.0)) > 3.0


def test_preview_frames_are_pictures_not_the_video(monkeypatch):
    monkeypatch.setattr(shot_window, "decode_image_rgb",
                        lambda blob: np.zeros((4, 4, 3), np.uint8))
    seen = []

    def getter(url):
        seen.append(url)
        return b"preview"

    video = {"duration": 12, "video_files": [{"link": "https://cdn.example/full.mp4"}],
             "video_pictures": [
                 {"nr": 2, "picture": "https://images.example/c.jpg"},
                 {"nr": 0, "picture": "https://images.example/a.jpg"},
                 {"nr": 1, "picture": "https://images.example/b.jpg"},
             ]}
    samples = load_preview_samples(video, getter=getter)
    assert seen == ["https://images.example/a.jpg",
                    "https://images.example/b.jpg",
                    "https://images.example/c.jpg"]
    assert [round(t, 3) for t, _ in samples] == [2.0, 6.0, 10.0]


def test_flat_clip_is_not_pushed_around_by_noise():
    samples = _clip(30, lambda t: 0.0)
    legacy = legacy_src_in(30.0, 4.0)
    for hint in ("aerial", "push", "static"):
        assert choose_src_in(30.0, 4.0, hint, samples) == (legacy, "legacy_third")


def test_unknown_hint_or_too_few_frames_stay_on_the_first_third():
    samples = _clip(30, lambda t: 0.0 if t < 16 else 0.2 * (t - 16))
    legacy = legacy_src_in(30.0, 4.0)
    assert choose_src_in(30.0, 4.0, "", samples) == (legacy, "legacy_third")
    assert choose_src_in(30.0, 4.0, "zoom", samples) == (legacy, "legacy_third")
    assert choose_src_in(30.0, 4.0, "aerial", samples[:1]) == (legacy, "legacy_third")


def test_people_rejected_before_download_then_next_candidate(iso, monkeypatch):
    calm = _clip(30, lambda t: 0.0)
    people = [(t, frame.copy()) for t, frame in calm]
    for _t, frame in people:
        frame[0, 0] = 255
    late = _clip(30, lambda t: 0.0 if t < 16 else 0.12 * (t - 16))
    videos = [_video(1, people), _video(2, late)]
    detector = _FakeDetector()
    downloaded, timeline = _assign(monkeypatch, videos, detector, [{
        "index": 0, "scene": "coast aerial", "render_length": 4.0,
        "motion_hint": "aerial", "exclude_assets": [],
    }])
    material_manager.assign_assets(
        "missing-project", timeline, CFG, {"pexels": "k"},
        people_detector=detector,
        preview_loader=lambda video: video["_frames"])
    assert downloaded == ["pexels-2"]
    asset = timeline["shots"][0]["asset"]
    assert asset["people_check"] == "clean"
    assert asset["src_in_rule"] == "motion"
    assert asset["src_in"] >= 15.0
    # 第一条全静止且有人：入点仍是前 1/3，抽的是那一帧，然后才换下一条。
    assert len(detector.seen) == 2
    assert abs(detector.seen[0] - legacy_src_in(30.0, 4.0)) <= 1.0
    assert abs(detector.seen[1] - asset["src_in"]) <= 1.0
    assert detector.seen[1] >= 15


def test_static_samples_the_calm_window_not_the_person_in_front(iso, monkeypatch):
    frames = []
    for t in range(30):
        # 人物只出现在前段运动里。静态窗从这段运动结束之后才开始。
        person = t < 12
        phase = 0.15 * t if t < 14 else 0.15 * 14
        frames.append((float(t), _sine(phase, t, person=person)))
    detector = _FakeDetector()
    downloaded, timeline = _assign(monkeypatch, [_video(7, frames)], detector, [{
        "index": 0, "scene": "calm lake", "render_length": 4.0,
        "motion_hint": "static",
    }])
    material_manager.assign_assets(
        "missing-project", timeline, CFG, {"pexels": "k"},
        people_detector=detector,
        preview_loader=lambda video: video["_frames"])
    assert downloaded == ["pexels-7"]
    asset = timeline["shots"][0]["asset"]
    assert asset["src_in"] >= 13.0
    assert detector.seen[0] >= 12
    assert asset["people_check"] == "clean"


def test_all_people_candidates_raise_without_downloading(iso, monkeypatch):
    people = _clip(30, lambda t: 0.0)
    for _t, frame in people:
        frame[0, 0] = 255
    downloaded, timeline = _assign(
        monkeypatch, [_video(1, people), _video(2, people)], _FakeDetector(), [{
            "index": 3, "scene": "crowded pier", "render_length": 4.0,
            "motion_hint": "static",
        }])
    with pytest.raises(material_manager.MaterialError):
        material_manager.assign_assets(
            "missing-project", timeline, CFG, {"pexels": "k"},
            people_detector=_FakeDetector(),
            preview_loader=lambda video: video["_frames"])
    assert downloaded == []


def test_neighbor_reuse_does_not_revive_a_person(iso, monkeypatch):
    calm = _clip(30, lambda t: 0.0)
    people = [(t, frame.copy()) for t, frame in calm]
    for _t, frame in people:
        frame[0, 0] = 255
    detector = _FakeDetector()
    downloaded, timeline = _assign(
        monkeypatch, [_video(1, calm), _video(2, people)], detector, [
            {"index": 0, "scene": "ridge", "render_length": 4.0,
             "motion_hint": "static"},
            {"index": 1, "scene": "ridge again", "render_length": 4.0,
             "motion_hint": "static",
             "exclude_assets": ["pexels-1"]},
        ])
    # 第二条检索只剩「有人」和「被排除」时不能下有人的那条。
    # 第一镜吃掉 pexels-1；第二镜排除 pexels-1，只剩有人的 pexels-2。
    videos_for_query = {
        "ridge": [_video(1, calm)],
        "ridge again": [_video(1, calm), _video(2, people)],
    }

    def search(_key, query, _per_page):
        return {"videos": videos_for_query[query]}

    monkeypatch.setattr(material_manager, "_search", search)
    with pytest.raises(material_manager.MaterialError):
        material_manager.assign_assets(
            "missing-project", timeline, CFG, {"pexels": "k"},
            people_detector=detector,
            preview_loader=lambda video: video["_frames"])
    assert downloaded == ["pexels-1"]


def test_reuse_fallback_still_checks_people(iso, monkeypatch):
    calm = _clip(30, lambda t: 0.02)
    people = [(t, frame.copy()) for t, frame in calm]
    for _t, frame in people:
        frame[0, 0] = 255
    other = _clip(30, lambda t: 0.0)
    detector = _FakeDetector()
    downloaded, timeline = _assign(monkeypatch, [], detector, [
        {"index": 0, "scene": "first", "render_length": 4.0,
         "motion_hint": "static"},
        {"index": 1, "scene": "second", "render_length": 4.0,
         "motion_hint": "static"},
    ])
    queries = {
        "first": [_video(1, calm)],
        # 近邻撞上 1，下一条有人，再下一条干净：应下到 3，而不是退回有人的 2。
        "second": [_video(2, people), _video(1, calm), _video(3, other)],
    }
    monkeypatch.setattr(
        material_manager, "_search",
        lambda _key, query, _per_page: {"videos": queries[query]})
    material_manager.assign_assets(
        "missing-project", timeline, CFG, {"pexels": "k"},
        people_detector=detector,
        preview_loader=lambda video: video["_frames"])
    assert downloaded == ["pexels-1", "pexels-3"]
    assert timeline["shots"][1]["asset"]["people_check"] == "clean"


def test_unavailable_detector_fails_open_and_still_uses_motion(iso, monkeypatch):
    people = _clip(30, lambda t: 0.0 if t < 16 else 0.12 * (t - 16))
    for _t, frame in people:
        frame[0, 0] = 255
    detector = _FakeDetector(available=False, detail=DETECTOR_UNAVAILABLE_NOTE)

    def check(_frame):
        raise AssertionError("检测器不可用时不应抽检画面")

    detector.check = check
    downloaded, timeline = _assign(monkeypatch, [_video(4, people)], detector, [{
        "index": 0, "scene": "ridge", "render_length": 4.0,
        "motion_hint": "push",
    }])
    material_manager.assign_assets(
        "missing-project", timeline, CFG, {"pexels": "k"},
        people_detector=detector,
        preview_loader=lambda video: video["_frames"])
    assert downloaded == ["pexels-4"]
    asset = timeline["shots"][0]["asset"]
    assert asset["people_check"] == "unavailable"
    assert "放行" in asset["people_check_note"]
    assert asset["src_in_rule"] == "motion"
    assert asset["src_in"] >= 15.0


def test_hint_falls_back_to_storyboard_when_timeline_has_none(iso, monkeypatch):
    pid_dir = iso / "projects" / "project-009"
    pid_dir.mkdir(parents=True)
    save_json(pid_dir / "storyboard.json", {"sections": [
        {"shots": [{"motion_hint": "static"}, {"motion_hint": "aerial"}]},
    ]})
    samples = _clip(30, lambda t: 0.0 if t < 16 else 0.12 * (t - 16))
    detector = _FakeDetector()
    downloaded, timeline = _assign(monkeypatch, [_video(8, samples)], detector, [{
        "index": 1, "scene": "sea", "render_length": 4.0,
    }])
    material_manager.assign_assets(
        "project-009", timeline, CFG, {"pexels": "k"},
        people_detector=detector,
        preview_loader=lambda video: video["_frames"])
    assert downloaded == ["pexels-8"]
    assert timeline["shots"][0]["asset"]["motion_hint"] == "aerial"
    assert timeline["shots"][0]["asset"]["src_in_rule"] == "motion"
    assert timeline["shots"][0]["asset"]["src_in"] >= 15.0


def test_no_preview_frame_skips_candidate_when_detector_works(iso, monkeypatch):
    downloaded, timeline = _assign(
        monkeypatch, [_video(1, []), _video(2, _clip(30, lambda t: 0.0))],
        _FakeDetector(), [{
            "index": 0, "scene": "lake", "render_length": 4.0,
            "motion_hint": "static",
        }])
    material_manager.assign_assets(
        "missing-project", timeline, CFG, {"pexels": "k"},
        people_detector=_FakeDetector(),
        preview_loader=lambda video: video["_frames"])
    assert downloaded == ["pexels-2"]


def test_frame_near_stays_by_the_in_point():
    samples = [(float(t), np.full((4, 4), t, np.uint8)) for t in range(30)]
    found = frame_near(samples, 18.2, 4.0)
    assert abs(found[0] - 18.2) <= 1.0


def test_parse_ppm_roundtrip():
    raw = b"P6\n# comment\n2 1\n255\n" + bytes([255, 0, 0, 0, 255, 0])
    image = parse_ppm(raw)
    assert image.shape == (1, 2, 3)
    assert image[0, 0].tolist() == [255, 0, 0]
    assert image[0, 1].tolist() == [0, 255, 0]


def test_missing_opencv_fails_open(monkeypatch):
    real_import = builtins.__import__

    def guarded(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "cv2":
            raise ImportError("no cv2 in this test")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded)
    detector = resolve_people_detector()
    assert isinstance(detector, UnavailablePeopleDetector)
    assert detector.available is False
    assert detector.check(np.zeros((8, 8), np.uint8)).status == "unavailable"
    assert "放行" in detector.detail
