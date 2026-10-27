# -*- coding: utf-8 -*-
"""GLM API 封装：chat + 容错 JSON 解析（沿用 bili-autopost-sau copywriter 范式）。

无 key、网络失败或响应不合法一律返回 None，调用方走降级路径——
"产线可以断，烂片不能发"的反面：创意层可以缺，流程不能停。
"""
import json
import re

import requests

ZHIPU_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"


def _parse_llm_json(raw: str, required_keys: tuple[str, ...] = ()) -> dict | None:
    """容错解析：裸 JSON / markdown 围栏 / 前后杂质。"""
    if not raw:
        return None
    candidates = []
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.S)
    if m:
        candidates.append(m.group(1))
    brace = re.search(r"\{.*\}", raw, re.S)
    if brace:
        candidates.append(brace.group(0))
    for c in candidates:
        try:
            obj = json.loads(c)
            if isinstance(obj, dict) and all(k in obj for k in required_keys):
                return obj
        except (json.JSONDecodeError, TypeError):
            continue
    return None


def chat_json_ex(api_key: str, prompt: str, *, model: str = "glm-4-flash",
                 temperature: float = 0.8, max_tokens: int = 1500,
                 timeout: int = 60, required_keys: tuple[str, ...] = (),
                 retries: int = 3) -> tuple[dict | None, str]:
    """调用 GLM 取 JSON 对象。返回 (结果, 失败原因)；成功时原因为空串。"""
    if not api_key:
        return None, "未配置 API key"
    errors: list[str] = []
    for _ in range(max(1, retries)):
        try:
            r = requests.post(
                ZHIPU_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": model, "temperature": temperature,
                      "max_tokens": max_tokens,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=timeout,
            )
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            parsed = _parse_llm_json(content, required_keys)
            if parsed is not None:
                return parsed, ""
            errors.append("响应不是合法 JSON 或缺少必需键")
        except (requests.RequestException, KeyError, IndexError, ValueError) as e:
            errors.append(f"{type(e).__name__}: {e}")
    return None, "；".join(errors[-2:])


def chat_json(api_key: str, prompt: str, *, model: str = "glm-4-flash",
              temperature: float = 0.8, max_tokens: int = 1500,
              timeout: int = 60, required_keys: tuple[str, ...] = (),
              retries: int = 3) -> dict | None:
    """chat_json_ex 的简化封装：任何失败返回 None。"""
    obj, _ = chat_json_ex(api_key, prompt, model=model, temperature=temperature,
                          max_tokens=max_tokens, timeout=timeout,
                          required_keys=required_keys, retries=retries)
    return obj
