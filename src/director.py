# -*- coding: utf-8 -*-
"""AI 导演：音乐段落 × 创作指令 → storyboard.json。

两层结构，各司其职：
- 规则骨架（确定性）：每段落的镜头数与时长分配由段落时长和角色参考
  区间推出——保证时间轴永远可行、可复现；
- GLM 创意层（可选）：在骨架内为每个镜头生成 Pexels 英文检索词与中文
  情绪标签，输出做结构校验，失败重试一次，再失败降级规则检索词
  （降级不阻塞，风格一致性优先于灵光一现）。

导演不做超出骨架的事：不改动段落边界（那是音乐结构的决定），不给出
少于最短镜头时长的方案（时间轴规划器会拒绝）。
"""
import time

from src.glm_client import chat_json_ex
from src.music_analyzer import window_sections

# 主题 → Pexels 英文检索词池（brief.keywords_en 可整体覆盖）
THEME_KEYWORDS = {
    "山海": ["mountain", "sea coast", "ocean waves", "mountain lake", "island"],
    "森林": ["forest", "foggy forest", "forest path", "forest river"],
    "星空": ["night sky stars", "milky way", "star trail", "aurora"],
    "雪境": ["snow mountain", "snowy forest", "winter landscape"],
    "城市": ["city skyline night", "city timelapse", "city lights"],
    "草原": ["grassland", "meadow", "steppe landscape"],
    "沙漠": ["desert dunes", "desert landscape"],
}
DEFAULT_KEYWORDS = ["mountain landscape", "ocean coast", "calm lake",
                    "clouds timelapse"]

# 角色 → 检索词修饰方向：开场给大景，高潮给运动感，收尾给余韵
ROLE_MODIFIERS = {
    "opening": ["aerial view", "wide shot", "sunrise"],
    "build": ["drone", "valley", "flowing water"],
    "climax": ["aerial drone", "epic", "waves crashing", "sweeping"],
    "settle": ["golden hour", "calm", "sunset"],
    "ending": ["dusk horizon", "silhouette", "twilight"],
}
FALLBACK_ROLE = "build"

MOTION_HINTS = {"aerial", "push", "pan", "static"}

# 各角色单镜头参考时长中值（与 timeline.ROLE_SHOT_LEN 一致的经验值）
_ROLE_MID = {"opening": 4.75, "build": 4.0, "climax": 3.3,
             "settle": 5.25, "ending": 5.75}


def _keywords_for(brief: dict) -> list[str]:
    kws = brief.get("keywords_en")
    if kws:
        return [str(k).strip() for k in kws if str(k).strip()] or DEFAULT_KEYWORDS
    theme = str(brief.get("theme_zh", ""))
    for zh, pool in THEME_KEYWORDS.items():
        if zh in theme:
            return pool
    return DEFAULT_KEYWORDS


def _shot_count(section_dur: float, role: str, min_shot_s: float) -> int:
    lo, hi = {"opening": (3.5, 6.0), "build": (3.0, 5.0), "climax": (2.4, 4.2),
              "settle": (4.0, 6.5), "ending": (4.5, 7.0)}.get(
        role, (3.0, 5.0))
    k = max(1, round(section_dur / ((lo + hi) / 2)))
    return min(k, max(1, int(section_dur // min_shot_s)))


def _rule_shots(sections: list[dict], keywords: list[str],
                min_shot_s: float) -> list[dict]:
    """规则检索词：关键词池 × 角色修饰方向轮转，相邻镜头天然不同。"""
    sb_sections, g = [], 0
    for sec in sections:
        k = _shot_count(sec["end"] - sec["start"], sec["role"], min_shot_s)
        mods = ROLE_MODIFIERS.get(sec["role"], ROLE_MODIFIERS[FALLBACK_ROLE])
        shots = []
        for j in range(k):
            kw = keywords[g % len(keywords)]
            mod = mods[j % len(mods)]
            shots.append({
                "shot_id": f"s{g + 1:02d}",
                "scene": f"{kw} {mod}",
                "motion_hint": "aerial" if sec["role"] in ("climax", "opening")
                else "static",
                "mood_zh": {"opening": "舒展", "build": "递进", "climax": "壮阔",
                            "settle": "舒缓", "ending": "余韵"}.get(sec["role"], "自然"),
                "duration_hint": round((sec["end"] - sec["start"]) / k, 3),
            })
            g += 1
        sb_sections.append({"index": sec["index"], "role": sec["role"],
                            "music_start": sec["start"], "music_end": sec["end"],
                            "energy": sec.get("energy"), "shot_count": k,
                            "shots": shots})
    return sb_sections


def _validate_glm_sections(data: dict, sb_sections: list[dict]) -> bool:
    """GLM 输出结构校验：段落数、每段镜头数、字段形态全部对上才算过。"""
    got = data.get("sections")
    if not isinstance(got, list) or len(got) != len(sb_sections):
        return False
    for want, got_sec in zip(sb_sections, got):
        shots = got_sec.get("shots") if isinstance(got_sec, dict) else None
        if not isinstance(shots, list) or len(shots) != want["shot_count"]:
            return False
        for s in shots:
            if not isinstance(s, dict):
                return False
            scene = str(s.get("scene", "")).strip()
            if not (2 <= len(scene) <= 60):
                return False
    return True


def _apply_glm_shots(data: dict, sb_sections: list[dict]) -> None:
    """把 GLM 的创意字段写进骨架（只动 scene/mood_zh/motion_hint）。"""
    for want, got_sec in zip(sb_sections, data["sections"]):
        for want_shot, got_shot in zip(want["shots"], got_sec["shots"]):
            want_shot["scene"] = str(got_shot.get("scene", want_shot["scene"])).strip()
            mood = str(got_shot.get("mood_zh", "")).strip()[:6]
            if mood:
                want_shot["mood_zh"] = mood
            hint = str(got_shot.get("motion_hint", "")).strip().lower()
            want_shot["motion_hint"] = hint if hint in MOTION_HINTS \
                else want_shot["motion_hint"]


def _glm_prompt(brief: dict, sections: list[dict], sb_sections: list[dict],
                music_map: dict) -> str:
    tempo = music_map.get("tempo", {})
    sec_lines = []
    for sec, sb in zip(sections, sb_sections):
        shots_desc = "、".join(
            f"镜头{j+1}约{s['duration_hint']}s" for j, s in enumerate(sb["shots"]))
        sec_lines.append(
            f"- 段落{sec['index']} {sec['role']}（{sec['start']:.1f}-{sec['end']:.1f}s，"
            f"能量{sec.get('energy', 0):.2f}，{sb['shot_count']}个镜头：{shots_desc}）")
    kws = "、".join(_keywords_for(brief))
    return f"""你是风景混剪片的导演。纯音乐风景短片，无旁白无字幕，画面全部来自 Pexels 实拍风景素材检索。只输出 JSON，不要任何其他内容。

创作指令：主题「{brief.get('theme_zh', '')}」，基调 {brief.get('mood', '治愈')}，平台 {brief.get('platform', 'bilibili')}。
可选关键词（可拓展相近词）：{kws}

音乐结构（画面必须贴合段落的情绪走向）：
{chr(10).join(sec_lines)}
节奏约 {tempo.get('bpm', '?')} BPM，高潮段在 {next((s['role'] + ' ' + str(s['start']) + '-' + str(s['end']) + 's' for s in sections if s['role'] == 'climax'), '未知')}。

为每个镜头给出：
- scene：Pexels 英文检索词，2-6 个英文单词，只检索真实自然风景（不要人物特写、不要文字画面），相邻镜头的检索词必须不同；高潮段优先有运动感的航拍/海浪/云海，首尾给舒展大景。
- mood_zh：该镜头的中文情绪词，2-4 字。
- motion_hint：aerial / push / pan / static 之一（素材本身运动的预期类型）。

输出格式：
{{"sections": [{{"index": 0, "shots": [{{"scene": "...", "mood_zh": "...", "motion_hint": "aerial"}}]}}]}}"""


def plan_storyboard(brief: dict, music_map: dict, cfg: dict,
                    api_key: str = "") -> dict:
    """生成 storyboard dict。GLM 不可用或输出不合法时整体走规则骨架。"""
    min_shot_s = float(cfg["timeline"]["min_shot_s"])
    sections = window_sections(music_map)
    keywords = _keywords_for(brief)
    sb_sections = _rule_shots(sections, keywords, min_shot_s)

    mode, model, note = "rules", None, ""
    if api_key:
        model = cfg["glm"]["model"]
        prompt = _glm_prompt(brief, sections, sb_sections, music_map)
        data, err = chat_json_ex(
            api_key, prompt, model=model,
            temperature=float(cfg["glm"]["temperature"]),
            max_tokens=int(cfg["glm"]["max_tokens"]),
            timeout=int(cfg["glm"]["timeout_s"]),
            required_keys=("sections",),
        )
        if data and _validate_glm_sections(data, sb_sections):
            _apply_glm_shots(data, sb_sections)
            mode = "glm"
        else:
            model = None            # 降级：创意层缺席，流程继续
            note = err or "输出结构与镜头数不匹配"

    return {
        "brief": brief,
        "sections": sb_sections,
        "generator": {"mode": mode, "model": model, "note": note,
                      "at": time.strftime("%Y-%m-%dT%H:%M:%S")},
    }
