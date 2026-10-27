# -*- coding: utf-8 -*-
"""doctor 启动体检：硬伤阻断制作，软项只降级（不耗 API 额度）。"""
import sys

from src.audio_io import ffmpeg_exe
from src.config import api_keys
from src.store import MUSIC_DIR, ROOT


def doctor(cfg: dict) -> list[tuple[str, str, str]]:
    """返回 (名称, 级别 ok/soft/hard, 说明) 列表。"""
    rows: list[tuple[str, str, str]] = []
    rows.append(("python", "ok" if sys.version_info >= (3, 12) else "hard",
                 sys.version.split()[0] + "（要求 3.12+）"))
    try:
        rows.append(("ffmpeg", "ok", ffmpeg_exe()))
    except Exception as e:                                   # pragma: no cover
        rows.append(("ffmpeg", "hard", f"不可用: {e}"))
    try:
        import numpy
        rows.append(("numpy", "ok", numpy.__version__))
    except ImportError:
        rows.append(("numpy", "hard", "未安装"))

    keys = api_keys()
    rows.append(("PEXELS_API_KEY",
                 "ok" if keys["pexels"] else "hard",
                 "已配置" if keys["pexels"]
                 else "缺失（在线素材必需；--offline 模式可绕过）"))
    rows.append(("ZHIPU_API_KEY",
                 "ok" if keys["zhipu"] else "soft",
                 "已配置" if keys["zhipu"] else
                 "未配置（导演走规则骨架，发布文案走模板）"))
    rows.append(("music 目录", "ok" if MUSIC_DIR.exists() else "soft",
                 str(MUSIC_DIR)))
    try:
        from src.material_manager import CACHE_DIR, cache_stats
        st = cache_stats()
        level = "ok" if st["bytes"] <= 2_000_000_000 else "soft"
        rows.append(("素材缓存", level,
                     f"{st['files']} 个 / {st['bytes'] / 1e6:.0f}MB"
                     + ("（偏大，可 python cli.py prune-cache 清理）"
                        if st["bytes"] > 2_000_000_000 else "")))
    except Exception:                                        # pragma: no cover
        rows.append(("素材缓存", "ok", "空"))
    for cat in cfg["music"].get("catalogs", []):
        p = (ROOT / cat).resolve()
        rows.append((f"曲库 {cat}", "ok" if p.exists() else "soft", str(p)))
    return rows
