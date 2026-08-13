"""探测火山方舟 Agent Plan 端点可用的目标模型列表。

对每个候选模型 POST {base}/v1/messages（x-api-key + anthropic-version），
HTTP 200 视为可用，其余（4xx 模型不存在/5xx/超时）视为不可用。

用法：
  python probe_targets.py                # 探测内置候选列表
  python probe_targets.py --timeout 60   # 放宽超时
  python probe_targets.py --env ARK_PLAN_API_KEY   # 默认即此环境变量
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import urllib.error
import urllib.request

BASE_URL = "https://ark.cn-beijing.volces.com/api/plan"
ENV_NAME = "ARK_PLAN_API_KEY"
KEY_FILE = "key.local"

# 候选模型（逐个探测后取可用集合，剔除 deepseek-v4-flash 作为正式目标）
CANDIDATES = [
    "doubao-seed-2.0-mini",
    "glm-5.2",
    "deepseek-v4-flash",
    "doubao-seed-2.1-pro",
    "doubao-seed-1-6-251015",
    "doubao-seed-1-6-flash-250828",
    "doubao-seed-2-0-pro-260215",
    "kimi-k2-250905",
    "qwen3-32b-20250429",
    "glm-5-2-260617",
    "deepseek-v3-2-251201",
]


def resolve_key(env: str = ENV_NAME, key_file: str = KEY_FILE) -> str:
    """密钥：环境变量 ARK_PLAN_API_KEY 优先，其次 evaluation/key.local。"""
    key = os.environ.get(env, "").strip()
    if key:
        return key
    path = Path(key_file)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent / path
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return ""


def probe_model(base_url: str, api_key: str, model: str, timeout: float = 30.0) -> tuple[bool, str]:
    """探测单个模型：200 → (True, status)；否则 (False, 原因摘要)。"""
    body = json.dumps({
        "model": model,
        "max_tokens": 5,
        "messages": [{"role": "user", "content": "ping"}],
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/v1/messages",
        data=body,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
            return True, str(resp.status)
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001 —— 探测单点失败必须隔离
        return False, str(exc)[:120]


def probe_models(
    base_url: str, api_key: str, candidates: list[str], timeout: float = 30.0
) -> tuple[list[str], list[tuple[str, str]]]:
    """逐个探测，返回 (可用列表, 不可用[(模型, 原因)])。"""
    available: list[str] = []
    unavailable: list[tuple[str, str]] = []
    for model in candidates:
        ok, status = probe_model(base_url, api_key, model, timeout=timeout)
        if ok:
            available.append(model)
            print(f"  {model:35s} 可用")
        else:
            unavailable.append((model, status))
            print(f"  {model:35s} 不可用（{status}）")
    return available, unavailable


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="探测火山方舟 Agent Plan 端点可用模型")
    parser.add_argument("--base-url", default=BASE_URL, help="plan 端点根地址")
    parser.add_argument("--env", default=ENV_NAME, help="密钥环境变量名")
    parser.add_argument("--timeout", type=float, default=30.0, help="单次探测超时（秒）")
    args = parser.parse_args(argv)

    key = resolve_key(args.env)
    if not key:
        print(f"[错误] 未找到密钥：请设置环境变量 {args.env} 或在 evaluation/{KEY_FILE} 中填写")
        return 1

    print(f"[探测] {args.base_url} 候选模型 {len(CANDIDATES)} 个\n")
    available, unavailable = probe_models(args.base_url, key, CANDIDATES, timeout=args.timeout)

    print("\n可用模型：")
    for model in available:
        print(f"  - {model}")
    print(f"\n不可用：{len(unavailable)} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
