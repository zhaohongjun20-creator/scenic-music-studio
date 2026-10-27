# -*- coding: utf-8 -*-
"""scenic-music-studio 命令行入口。

    python cli.py doctor                          # 环境体检
    python cli.py create --theme 治愈系山海 --music music/xx.mp3
    python cli.py make project-001 [--offline] [--redo]
    python cli.py show project-001                # 状态/时间轴/质检概览
    python cli.py redo-shot project-001 5 --note "高潮再壮阔一点"
    python cli.py approve project-001             # 人工放行
    python cli.py reject project-001 --note "..."
    python cli.py publish-prepare project-001     # 生成上传物料（不投稿）
"""
import argparse
import json
import sys
from pathlib import Path

from src import pipeline, preflight, publisher
from src import review as review_mod
from src import store
from src.config import api_keys, load_config
from src.jsonio import load_json
from src.store import StoreError


def _die(msg: str) -> None:
    print(f"错误: {msg}", file=sys.stderr)
    sys.exit(1)


def cmd_doctor(args) -> None:
    rows = preflight.doctor(load_config())
    for name, level, detail in rows:
        print(f"[{level:>4}] {name}: {detail}")
    if any(level == "hard" for _, level, _ in rows):
        sys.exit(1)


def _music_credit(music_path: str, cfg: dict) -> dict | None:
    """音乐文件若来自已知曲库，自动带上署名信息。"""
    target = Path(music_path).resolve()
    for cat in cfg["music"].get("catalogs", []):
        mf = (store.ROOT / cat).resolve()
        manifest = load_json(mf)
        if not manifest:
            continue
        for t in manifest.get("tracks", []):
            f = (mf.parent / t.get("file", "")).resolve()
            if f == target:
                return {"title": t.get("title"), "artist": t.get("artist"),
                        "license": t.get("license"),
                        "source_url": t.get("source_url")}
    return None


def cmd_create(args) -> None:
    cfg = load_config()
    if args.brief:
        brief = load_json(args.brief)
        if not brief:
            _die(f"brief 文件不存在或不是合法 JSON: {args.brief}")
        if not brief.get("music"):
            _die("brief 缺少 music 字段")
        brief["music"] = str(Path(brief["music"]).resolve())
    else:
        if not args.theme or not args.music:
            _die("create 需要 --theme 与 --music（或改用 --brief brief.json）")
        music = str(Path(args.music).resolve())
        if not Path(music).exists():
            _die(f"音乐文件不存在: {music}")
        brief = {
            "theme_zh": args.theme,
            "mood": args.mood,
            "duration_s": args.duration,
            "platform": args.platform,
            "music": music,
            "created_at": store.now(),
        }
        if args.keywords:
            brief["keywords_en"] = [k.strip() for k in args.keywords.split(",")
                                    if k.strip()]
        if args.style:
            sp = Path(args.style)
            if not sp.exists():
                _die(f"风格画像不存在: {args.style}")
            brief["style_profile"] = str(sp)
        credit = _music_credit(music, cfg)
        if credit:
            brief["music_credit"] = credit
            print(f"已从曲库匹配音乐署名：{credit['title']} — {credit['artist']}")
    if args.seed:
        brief["seed"] = args.seed
    manifest = store.new_project(brief)
    print(f"已创建 {manifest['id']}")
    print(f"下一步：python cli.py make {manifest['id']}"
          + ("（当前无 API key，建议加 --offline 先跑通全链）"
             if not api_keys()["pexels"] else ""))


def cmd_make(args) -> None:
    summary = pipeline.make(args.project, load_config(), api_keys(),
                            offline=args.offline, redo=args.redo)
    print(f"QC {'通过' if summary['qc_passed'] else '未通过'}：{summary['final']}")
    cr = summary.get("cut_report") or {}
    if cr:
        print(f"切点吸附：{cr.get('within_tolerance')}/{cr.get('cuts')} "
              f"（比率 {cr.get('ratio')}，容差 ±{cr.get('tolerance_s')}s）")
    for p in summary.get("problems", []):
        print(f"  QC 问题: {p}")
    if summary["qc_passed"]:
        print(f"下一步：python cli.py show {args.project} 后人工审片，"
              f"然后 approve / redo-shot")


def cmd_show(args) -> None:
    pdir = store.project_dir(args.project)
    manifest = store.load_manifest(args.project)
    review = review_mod.load_review(args.project)
    print(f"项目 {args.project}  状态: {manifest.get('status')}  "
          f"审片: {review.get('status')}")
    for stage, info in (manifest.get("stages") or {}).items():
        extra = f"  {json.dumps(info, ensure_ascii=False)}" if info else ""
        print(f"  [{info.get('status', '?'):>5}] {stage}{extra}")
    tline = load_json(pdir / "timeline.json")
    if tline:
        print(f"\n时间轴 {tline['target_s']}s，{len(tline['shots'])} 个镜头：")
        for s in tline["shots"]:
            lock = s.get("beat_locked")
            mark = f"♪{lock['kind']}@{lock['snapped_to']}s" if lock else "—"
            asset = (s.get("asset") or {}).get("id", "未分配")
            print(f"  #{s['index']:02d} {s['start']:6.2f}-{s['start'] + s['duration']:6.2f}s "
                  f"{s['role']:<7} {s['scene'][:36]:<38} 切点:{mark:<20} 素材:{asset}")
    report = load_json(pdir / "output" / "qc_report.json")
    if report:
        print(f"\nQC: {'通过' if report.get('passed') else '未通过'}")
        for p in report.get("problems", []):
            print(f"  - {p}")


def cmd_list(args) -> None:
    rows = store.list_projects()
    if not rows:
        print("还没有项目，先 create")
        return
    for m in rows:
        print(f"{m.get('id')}  {m.get('status'):>8}  {m.get('created_at')}")


def cmd_redo_shot(args) -> None:
    review_mod.redo_shot(args.project, args.index, note=args.note or "")
    print(f"镜头 {args.index} 已标记重做，重跑 make 生效"
          f"（其余镜头走缓存）")
    print(f"python cli.py make {args.project}")


def cmd_approve(args) -> None:
    review_mod.approve(args.project, note=args.note or "")
    print(f"{args.project} 已放行（ready）。"
          f"生成上传物料：python cli.py publish-prepare {args.project}")


def cmd_reject(args) -> None:
    if not args.note:
        _die("打回必须附意见 --note")
    review_mod.reject(args.project, note=args.note)
    print(f"{args.project} 已打回。改 brief/timeline 后重跑 make。")


def cmd_publish_prepare(args) -> None:
    out = publisher.prepare(args.project, load_config(), api_keys()["zhipu"])
    print(f"上传物料已生成（不自动投稿）：{out}")
    print("请人工上传后自行回写发布状态。")


def cmd_analyze_reference(args) -> None:
    from src import style_analyzer
    prof = style_analyzer.build_profile(args.samples, channel=args.channel,
                                        extra={"mid": args.mid} if args.mid else None)
    out = Path(args.out)
    style_analyzer.save_profile(prof, out)
    for s in prof["samples"]:
        print(f"{Path(s['file']).name}: {s['shots']}镜/{s['duration_s']}s "
              f"({s['shots_per_minute']}切/分) 中位{s['len_median']}s "
              f"硬切{s['hard_cut_frac']:.0%}")
    a = prof["aggregate"]
    print(f"聚合: {a['shots_per_minute_median']}切/分 中位 "
          f"{a['len_median_of_medians']}s 硬切 {a['hard_cut_frac_mean']:.0%}")
    print(f"已保存 {out}")
    print("接入: python cli.py create --style ... --theme ... --music ...")


def main() -> None:
    ap = argparse.ArgumentParser(prog="scenic-music-studio",
                                 description="音乐驱动的风景视频生产线")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor").set_defaults(func=cmd_doctor)

    p = sub.add_parser("analyze-reference", help="参考样片 → 风格画像 JSON")
    p.add_argument("samples", nargs="+", help="样片视频文件路径")
    p.add_argument("--channel", required=True, help="参考频道名（仅记录来源）")
    p.add_argument("--mid", help="参考频道 mid（可选，仅记录）")
    p.add_argument("--out", default="docs/reference/profile.json")
    p.set_defaults(func=cmd_analyze_reference)

    p = sub.add_parser("create", help="创建项目（创作指令 → brief.json）")
    p.add_argument("--brief", help="brief JSON 文件路径（给了则忽略以下参数）")
    p.add_argument("--theme", help="主题，如：治愈系山海")
    p.add_argument("--music", help="音乐文件路径（授权自备）")
    p.add_argument("--duration", type=float, default=60.0, help="目标时长秒")
    p.add_argument("--mood", default="治愈")
    p.add_argument("--platform", default="bilibili")
    p.add_argument("--keywords", help="英文检索词，逗号分隔（可选）")
    p.add_argument("--style", help="风格画像 JSON 路径（reference_profile）")
    p.add_argument("--seed", type=int, help="随机种子（转场选择可复现）")
    p.set_defaults(func=cmd_create)

    p = sub.add_parser("make", help="制作（断点续跑）")
    p.add_argument("project")
    p.add_argument("--offline", action="store_true",
                   help="离线模式：占位素材 + 规则导演，不耗 API")
    p.add_argument("--redo", action="store_true", help="全量重做")
    p.set_defaults(func=cmd_make)

    p = sub.add_parser("show", help="查看项目状态")
    p.add_argument("project")
    p.set_defaults(func=cmd_show)

    sub.add_parser("list").set_defaults(func=cmd_list)

    p = sub.add_parser("redo-shot", help="局部重做某个镜头")
    p.add_argument("project")
    p.add_argument("index", type=int, help="镜头序号（0 起）")
    p.add_argument("--note", help="重做原因，如：高潮再壮阔一点")
    p.set_defaults(func=cmd_redo_shot)

    p = sub.add_parser("approve", help="人工放行")
    p.add_argument("project")
    p.add_argument("--note")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("reject", help="打回")
    p.add_argument("project")
    p.add_argument("--note", required=True)
    p.set_defaults(func=cmd_reject)

    p = sub.add_parser("publish-prepare", help="生成上传物料（不投稿）")
    p.add_argument("project")
    p.set_defaults(func=cmd_publish_prepare)

    args = ap.parse_args()
    try:
        args.func(args)
    except (StoreError, publisher.PublishError) as e:
        _die(str(e))


if __name__ == "__main__":
    main()
