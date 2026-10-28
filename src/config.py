# -*- coding: utf-8 -*-
"""配置加载：默认值 <- config.yaml <- .env（密钥）。

所有模块经 load_config() 取配置，保证单一来源；brief 可逐期覆盖
video.target_duration_s / music.fade_* 等字段，由调用方合并。
"""
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

DEFAULTS = {
    "video": {
        "width": 1920, "height": 1080, "fps": 30,
        "target_duration_s": 60.0, "duration_tolerance_s": 1.5,
        "contrast": 1.06, "brightness": 0.02, "saturation": 1.08,
    },
    "music": {
        "fade_in_s": 1.5, "fade_out_s": 3.0, "loudnorm": True,
        "catalogs": [],
    },
    "timeline": {
        "min_shot_s": 2.0, "snap_tolerance_s": 0.6, "snap_penalty": 0.5,
        "beats_per_bar": 4, "min_sections_boundary_novelty": 0.12,
    },
    "material": {
        "min_source_duration_s": 6, "max_height": 1080,
        "candidates_per_query": 8, "avoid_reuse_shots": 2,
    },
    "glm": {
        "model": "glm-4-flash", "temperature": 0.8,
        "timeout_s": 60, "max_tokens": 1500,
    },
    "style": {
        "profile": "",
    },
    "motion": {
        "title_s": 3.0,             # 片头标题卡时长（秒）
        "outro_s": 3.5,             # 片尾落版时长（秒）
        "accent": "#e8d9b0",        # 点缀线颜色（暖金）
    },
    "qc": {
        "max_black_seconds": 2.0, "max_volume_db": -0.5,
        "min_mean_volume_db": -45.0, "min_cut_snap_ratio": 0.8,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config() -> dict:
    load_dotenv(ROOT / ".env")
    cfg_path = ROOT / "config.yaml"
    user = {}
    if cfg_path.exists():
        with open(cfg_path, encoding="utf-8") as f:
            user = yaml.safe_load(f) or {}
    return _deep_merge(DEFAULTS, user)


def api_keys() -> dict:
    return {
        "pexels": os.getenv("PEXELS_API_KEY", "").strip(),
        "zhipu": os.getenv("ZHIPU_API_KEY", "").strip(),
    }
