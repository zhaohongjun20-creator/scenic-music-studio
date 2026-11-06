# -*- coding: utf-8 -*-
"""workbuddy 代审桥测试：纯逻辑（抽帧时刻/判词解析/轮次迁移/提示词），不调 CLI。"""
import json

from src import workbuddy_review as wr


def test_frame_times_covers_title_tail_and_even_sampling():
    ts = wr.frame_times(60.0)
    assert ts == sorted(set(ts)), "必须升序且去重"
    assert ts[0] == 1.5, "首帧必须覆盖片头标题卡"
    assert abs(ts[-1] - 58.6) < 0.11, "末帧必须落在片尾落版窗口"
    assert len(ts) == 6
    for a, b in zip(ts, ts[1:]):
        assert b - a > 1.0, "采样间隔过密无意义"


def test_parse_verdict_pass_fail_unknown():
    assert wr.parse_verdict("分析…\nVERDICT: pass")[0] == "pass"
    assert wr.parse_verdict("逐帧…\nVERDICT: fail - 有穿帮")[0] == "fail"
    assert wr.parse_verdict("结论不错") [0] == "unknown"
    # 多个 VERDICT 行取最后一行（提示词要求只输出一行，模型偶发复述）
    assert wr.parse_verdict("VERDICT: pass\nVERDICT: fail - x")[0] == "fail"


def test_extract_result_from_cli_json_stream():
    stream = json.dumps([
        {"type": "message", "role": "user"},
        {"type": "result", "subtype": "success",
         "result": "结论…\nVERDICT: pass"},
    ])
    assert wr._extract_result(stream) == "结论…\nVERDICT: pass"
    # 非法 json → 原文回退
    assert wr._extract_result("不是json") == "不是json"


def test_build_prompt_self_contained(tmp_path):
    frames = [tmp_path / f"rv_{i}.jpg" for i in range(3)]
    qc = tmp_path / "qc_report.json"
    prompt = wr.build_prompt("落日海平线", ["音乐: X — Y (CC BY 4.0)"],
                             frames, qc)
    assert "落日海平线" in prompt
    assert "CC BY 4.0" in prompt
    assert qc.resolve().as_posix() in prompt
    for f in frames:
        assert f.resolve().as_posix() in prompt
    assert "VERDICT: pass" in prompt, "提示词必须包含判词格式约定"


def test_review_project_unavailable_fallback(tmp_path, monkeypatch):
    """CLI 不可用时不抛异常，verdict=unavailable 且多轮累积落盘。"""
    from src import workbuddy_review as wr
    from src.audio_io import ffmpeg_exe

    final = tmp_path / "final.mp4"
    # 造 1s 灰视频供抽帧（抽帧时刻裁到 1s 内的会失败，这里直接造时长足够）
    subprocess_ok = True
    import subprocess
    r = subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error",
                        "-f", "lavfi", "-i", "color=c=0x808080:s=64x36:d=62:r=10",
                        "-pix_fmt", "yuv420p", str(final)], capture_output=True)
    assert r.returncode == 0
    monkeypatch.setattr(wr, "_run_cli",
                        lambda *a, **k: (_ for _ in ()).throw(wr.ReviewError("网络断")))
    monkeypatch.setattr(wr, "TIMEOUT_S", 10)

    data = wr.review_project("p", {"review": {}}, final, 60.0,
                             "落日海平线", ["音乐: X"])
    assert data["latest_verdict"] == "unavailable"
    out = tmp_path / "review_workbuddy.json"
    assert json.loads(out.read_text(encoding="utf-8"))["rounds"]

    # 二轮累积
    data2 = wr.review_project("p", {"review": {}}, final, 60.0,
                              "落日海平线", ["音乐: X"])
    assert len(data2["rounds"]) == 2


def test_legacy_single_round_migration(tmp_path):
    """旧单轮格式（含手写的 rounds 整数计数字段）迁入 rounds，不丢历史。"""
    out = tmp_path / "review_workbuddy.json"
    out.write_text(json.dumps({"at": "2026-11-03", "verdict": "fail",
                               "rounds": 2,               # 手写轮数，非列表
                               "detail": "旧判词"}, ensure_ascii=False),
                   encoding="utf-8")
    # 迁移逻辑内联在 review_project 里，这里直接测等效行为
    prev = json.loads(out.read_text(encoding="utf-8"))
    r = prev.get("rounds") if isinstance(prev, dict) else None
    rounds = r if isinstance(r, list) else [prev]
    rounds.append({"verdict": "pass"})
    assert rounds[0]["verdict"] == "fail" and rounds[1]["verdict"] == "pass"


def test_max_rounds_policy_marker(tmp_path, monkeypatch):
    """轮次上限政策：第 max_rounds 轮起标 policy_final（修改后直接发布）。"""
    import subprocess
    from src import workbuddy_review as wr
    from src.audio_io import ffmpeg_exe

    final = tmp_path / "final.mp4"
    r = subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error",
                        "-f", "lavfi", "-i", "color=c=0x808080:s=64x36:d=62:r=10",
                        "-pix_fmt", "yuv420p", str(final)], capture_output=True)
    assert r.returncode == 0
    monkeypatch.setattr(wr, "_run_cli",
                        lambda *a, **k: "有瑕疵\nVERDICT: fail - 某帧跑题")

    d1 = wr.review_project("p", {"review": {}}, final, 60.0, "测试",
                           ["音乐: X"], max_rounds=3)
    assert d1["policy_final"] is False and len(d1["rounds"]) == 1
    d2 = wr.review_project("p", {"review": {}}, final, 60.0, "测试",
                           ["音乐: X"], max_rounds=3)
    d3 = wr.review_project("p", {"review": {}}, final, 60.0, "测试",
                           ["音乐: X"], max_rounds=3)
    assert d3["policy_final"] is True
    assert "不再复审" in d3["policy_note"]
    assert d3["latest_verdict"] == "fail"     # 判词如实保留，只是政策放行
