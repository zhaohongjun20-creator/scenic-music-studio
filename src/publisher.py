# -*- coding: utf-8 -*-
"""发布物料：upload_info.txt 生成（标题/简介/署名块/标签/文件清单）。

制作与发布分离：本模块只准备物料，不做任何上传；只有
review.status == ready 的成片才给生成，上传永远由人执行。
署名块按素材逐条列出（Pexels 免署名但保留来源可追溯；音乐按其
许可条款必须署名的写在最前）。
"""
import re
import time
from pathlib import Path

from src.glm_client import chat_json
from src.jsonio import load_json
from src.review import validate_approval
from src.store import StoreError, project_dir


class PublishError(RuntimeError):
    pass


_MARKETING = re.compile(
    r"点赞|一键三连|三连|投币|关注我|求关注|记得关注|点点关注|转发|分享给")

_PROMPT = """你是B站风景频道的运营文案。这一期是 {duration:.0f} 秒纯音乐风景混剪，主题「{theme}」，基调 {mood}，高潮在约 {climax}s。只输出 JSON。

要求：
- title：中文标题，一句成立，40 字以内，有画面感，不用"绝美/震撼/必看"，不用感叹号。
- desc：分段长文案 3-5 段（单换行分隔），共 80-140 字：首段一句题词点意境；中段借景抒情由远及近；末段留白收束。不出现任何运营用语。
- tags：5 个中文标签，含 1 个情绪向标签。

输出格式：
{{"title": "...", "desc": "...\\n...", "tags": ["...", "..."]}}"""


def _clean_copy(copy: dict) -> dict:
    desc = str(copy.get("desc", "")).strip()
    lines = [ln.strip(" •-\t") for ln in re.split(r"\n+", desc)]
    copy["desc"] = "\n".join(ln for ln in lines if ln and not _MARKETING.search(ln))
    copy["title"] = re.sub(r"[!！]+$", "", str(copy.get("title", "")).strip())[:40]
    tags = copy.get("tags") or []
    copy["tags"] = [str(t).strip() for t in tags if str(t).strip()][:5]
    return copy


def _fallback_copy(brief: dict) -> dict:
    theme = brief.get("theme_zh") or "山海"
    return {
        "title": f"把{theme}的风，寄给你",
        "desc": "\n".join([
            "有些风景，看过就再也没忘。",
            f"{theme}在眼前铺开，像一句没有说出口的话。",
            "光落得很慢，像回忆里被反复折起的那一页。",
            "我把它们收在这里，等一个合适的人来看。",
        ]),
        "tags": ["风景", "治愈", "放松", "自然", "纯音乐"],
    }


def _credits_block(timeline: dict, brief: dict) -> str:
    lines = ["", "—— 素材与音乐来源 ——"]
    credit = brief.get("music_credit")
    if credit:
        if isinstance(credit, dict):
            lines.append(
                f"音乐：{credit.get('title', '?')} — {credit.get('artist', '?')}"
                f"（{credit.get('license', '?')}，{credit.get('source_url', '')}）")
        else:
            lines.append(f"音乐：{credit}")
    pexels = [s["asset"] for s in timeline["shots"]
              if (s.get("asset") or {}).get("provider") == "pexels"]
    if pexels:
        lines.append("视频素材：Pexels（Pexels License，免费商用）")
        seen = set()
        for a in pexels:
            if a["id"] in seen:
                continue
            seen.add(a["id"])
            lines.append(f"- {a['page_url'] or a['url']}"
                         + (f"（作者 {a['author']}）" if a.get("author") else ""))
    elif timeline["shots"] and (timeline["shots"][0].get("asset") or {}).get("provider") == "local_test":
        lines.append("视频素材：本地占位片段（仅测试，不可发布）")
    return "\n".join(lines)


def prepare(pid: str, cfg: dict, api_key: str) -> Path:
    pdir = project_dir(pid)
    out = pdir / "output" / "upload_info.txt"
    # 失败时不留下上一轮可被误当成有效的发布物料。
    out.unlink(missing_ok=True)
    try:
        review = validate_approval(pid)
    except StoreError as exc:
        raise PublishError(str(exc)) from exc
    approval = review["approval"]
    timeline = load_json(pdir / "timeline.json")
    brief = load_json(pdir / "brief.json") or {}
    if not timeline:
        raise PublishError("缺少 timeline.json")

    copy = None
    if api_key:
        climax = next((s for s in timeline["shots"] if s["role"] == "climax"), None)
        copy = chat_json(
            api_key, _PROMPT.format(
                duration=timeline["target_s"], theme=brief.get("theme_zh", ""),
                mood=brief.get("mood", "治愈"),
                climax=(climax or {}).get("start", 0)),
            model=cfg["glm"]["model"],
            temperature=float(cfg["glm"]["temperature"]),
            max_tokens=900, timeout=int(cfg["glm"]["timeout_s"]),
            required_keys=("title", "desc", "tags"))
    copy = _clean_copy(copy or _fallback_copy(brief))

    # 文案 API 可能耗时；落盘前再次检查，避免期间重新渲染/撤回审批。
    try:
        current = validate_approval(pid)
    except StoreError as exc:
        raise PublishError(str(exc)) from exc
    if current["approval"] != approval:
        raise PublishError("生成文案期间人工审批记录发生变化，请重新准备发布物料")
    out.write_text(
        "\n".join([
            f"【标题】{copy['title']}",
            "",
            f"【简介】\n{copy['desc']}",
            _credits_block(timeline, brief),
            "",
            f"【标签】{' '.join(copy['tags'])}",
            f"【建议分区】影视区-混剪/风景（按频道实际分区调整）",
            "",
            "【人工审片凭据（本地自声明）】",
            f"reviewer: {approval['reviewer']}",
            f"reviewed_at: {approval['at']}",
            f"render_id: {approval['render_id']}",
            f"final SHA256: {approval['binding']['final_sha256']}",
            f"timeline SHA256: {approval['binding']['timeline_sha256']}",
            f"QC SHA256: {approval['binding']['qc_sha256']}",
            approval['declaration'],
            "",
            f"【上传文件】",
            f"- {pdir / 'output' / 'final.mp4'}",
            f"- {pdir / 'output' / 'cover.jpg'}",
            "",
            f"生成于 {time.strftime('%Y-%m-%dT%H:%M:%S')}；"
            f"上传动作请人工执行，完成后可手动回写状态。",
        ]),
        encoding="utf-8")
    return out
