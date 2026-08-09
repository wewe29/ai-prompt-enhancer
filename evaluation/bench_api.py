"""通用模型调用层：anthropic | openai 双协议，纯 stdlib（urllib）。

设计（docs/superpowers/specs/2026-08-09-model-benchmark-design.md）：
- call_chat 只负责一次请求：成功返回 ChatResult，失败抛出分类异常
  （RateLimited / TimeoutError / HttpError / NetworkError），由调用方重试。
- call_with_retry 负责重试策略：429 / 5xx / 超时 / 网络错误按退避表重试，
  重试耗尽后返回带 error 的 ChatResult（不抛异常）。
"""
from __future__ import annotations

import json
import os
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import urllib.error
import urllib.request

DEFAULT_TIMEOUT_S = 120


class RateLimited(Exception):
    """HTTP 429 限流"""


class HttpError(Exception):
    """HTTP 错误（status + body）"""

    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body[:200]}")
        self.status = status
        self.body = body


class NetworkError(Exception):
    """网络层错误（连接失败、DNS、被拒等）"""


@dataclass
class ChatResult:
    text: str
    latency_s: float = 0.0
    usage: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


def call_chat(
    base_url: str,
    api_key: str,
    protocol: str,
    model: str,
    messages: list[dict[str, Any]],
    temperature: float,
    max_tokens: int,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> ChatResult:
    """发起一次对话请求，成功返回 ChatResult，失败抛出分类异常。"""
    base_url = (base_url or "").rstrip("/")
    protocol = (protocol or "anthropic").lower()

    if protocol == "anthropic":
        url = f"{base_url}/v1/messages"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }
    elif protocol == "openai":
        url = f"{base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "content-type": "application/json",
        }
        body = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }
    else:
        raise ValueError(f"未知协议：{protocol}（支持 anthropic | openai）")

    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except Exception:
            detail = ""
        if exc.code == 429:
            raise RateLimited(f"HTTP 429: {detail[:200]}") from exc
        raise HttpError(exc.code, detail) from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, (socket.timeout, TimeoutError)):
            raise TimeoutError(f"请求超时（>{timeout_s}s）：{exc}") from exc
        raise NetworkError(f"网络错误：{exc}") from exc
    except (socket.timeout, TimeoutError) as exc:
        raise TimeoutError(f"请求超时（>{timeout_s}s）：{exc}") from exc
    latency_s = time.perf_counter() - start

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HttpError(0, f"响应不是有效 JSON：{raw[:200]}") from exc

    if protocol == "anthropic":
        blocks = data.get("content") or []
        text = "".join(
            block.get("text", "")
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        )
    else:
        choices = data.get("choices") or []
        text = ""
        if choices and isinstance(choices[0], dict):
            message = choices[0].get("message") or {}
            text = message.get("content") or ""
    return ChatResult(
        text=str(text),
        latency_s=latency_s,
        usage=data.get("usage") or {},
    )


def resolve_api_key(api_cfg: dict[str, Any], eval_root: str) -> str:
    """密钥解析优先级：api_key_env 环境变量 > api_key_file（相对 eval_root）> api_key 明文。"""
    if not isinstance(api_cfg, dict):
        raise ValueError("缺少 api 配置")
    env_name = str(api_cfg.get("api_key_env") or "").strip()
    if env_name:
        key = os.environ.get(env_name, "").strip()
        if key:
            return key
    key_file = str(api_cfg.get("api_key_file") or "").strip()
    if key_file:
        path = Path(key_file)
        if not path.is_absolute():
            path = Path(eval_root) / path
        if path.exists():
            key = path.read_text(encoding="utf-8").strip()
            if key:
                return key
    key = str(api_cfg.get("api_key") or "").strip()
    if key:
        return key
    raise ValueError(
        "未找到 API Key：请设置环境变量 %s，或在配置中填写 api_key_file / api_key"
        % (env_name or "api_key_env")
    )


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (RateLimited, TimeoutError, NetworkError)):
        return True
    if isinstance(exc, HttpError):
        return 500 <= exc.status < 600
    return False


def call_with_retry(fn, retries: int, backoff_s: list[float], label: str) -> ChatResult:
    """按退避表重试 fn；重试耗尽返回带 error 的 ChatResult（不抛异常）。

    fn 通常是一次 lambda: call_chat(...)，成功返回 ChatResult。
    """
    retries = max(0, int(retries))
    last_error: BaseException | None = None
    for attempt in range(retries + 1):
        try:
            result = fn()
            if isinstance(result, ChatResult) and result.error:
                raise RuntimeError(result.error)
            return result
        except BaseException as exc:  # noqa: BLE001 —— 单点失败必须隔离
            last_error = exc
            if not _is_retryable(exc) or attempt >= retries:
                break
            waits = backoff_s or [1.0]
            time.sleep(waits[min(attempt, len(waits) - 1)])
    return ChatResult(
        text="",
        latency_s=0.0,
        usage={},
        error=f"[{label}] 重试 {retries} 次后仍失败：{last_error}",
    )
