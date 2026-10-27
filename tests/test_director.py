# -*- coding: utf-8 -*-
"""AI 导演测试：规则骨架结构、GLM 创意层的应用与降级。"""
from src import director
from src.music_analyzer import window_sections

CFG = {"timeline": {"min_shot_s": 2.0},
       "glm": {"model": "test-model", "temperature": 0.8,
               "max_tokens": 100, "timeout_s": 5}}


def test_rules_mode_structure(synth_map):
    sb = director.plan_storyboard({"theme_zh": "治愈系山海"}, synth_map, CFG,
                                  api_key="")
    assert sb["generator"]["mode"] == "rules"
    assert [s["index"] for s in sb["sections"]] == \
        [s["index"] for s in window_sections(synth_map)]
    for sec in sb["sections"]:
        assert sec["shots"], f"段落 {sec['index']} 不应为空"
        for a, b in zip(sec["shots"], sec["shots"][1:]):
            assert a["scene"] != b["scene"], "相邻镜头检索词必须不同"
        for s in sec["shots"]:
            assert 2 <= len(s["scene"]) <= 60
            assert s["motion_hint"] in director.MOTION_HINTS
            assert s["duration_hint"] >= CFG["timeline"]["min_shot_s"] - 0.01


def test_glm_payload_applied(synth_map, monkeypatch):
    skeleton = director._rule_shots(window_sections(synth_map), ["mountain"],
                                    2.0)
    payload = {"sections": [
        {"index": sec["index"],
         "shots": [{"scene": f"custom query {sec['index']} {j}",
                    "mood_zh": "辽阔", "motion_hint": "pan"}
                   for j, _ in enumerate(sec["shots"])]}
        for sec in skeleton]}
    monkeypatch.setattr(director, "chat_json_ex", lambda *a, **k: (payload, ""))
    sb = director.plan_storyboard({"theme_zh": "测试"}, synth_map, CFG,
                                  api_key="fake")
    assert sb["generator"]["mode"] == "glm"
    assert sb["sections"][0]["shots"][0]["scene"].startswith("custom query")
    assert sb["sections"][0]["shots"][0]["mood_zh"] == "辽阔"


def test_glm_garbage_falls_back(synth_map, monkeypatch):
    monkeypatch.setattr(director, "chat_json_ex",
                        lambda *a, **k: ({"sections": []}, ""))
    sb = director.plan_storyboard({"theme_zh": "测试"}, synth_map, CFG,
                                  api_key="fake")
    assert sb["generator"]["mode"] == "rules"
    assert "不匹配" in sb["generator"]["note"]


def test_glm_none_falls_back(synth_map, monkeypatch):
    monkeypatch.setattr(director, "chat_json_ex",
                        lambda *a, **k: (None, "TimeoutError: boom"))
    sb = director.plan_storyboard({"theme_zh": "测试"}, synth_map, CFG,
                                  api_key="fake")
    assert sb["generator"]["mode"] == "rules"
    assert "boom" in sb["generator"]["note"]


def test_keywords_from_theme():
    assert "mountain" in director._keywords_for({"theme_zh": "治愈系山海"})
    assert director._keywords_for({"theme_zh": "未知主题"}) == \
        director.DEFAULT_KEYWORDS
    assert director._keywords_for({"keywords_en": ["lake", " mist"]}) == \
        ["lake", "mist"]
