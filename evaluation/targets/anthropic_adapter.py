"""Anthropic Messages 协议目标模型适配器（火山方舟 Agent Plan 等端点）。

POST {base_url}/v1/messages，鉴权头 x-api-key，body 与 Anthropic Messages API 兼容。
"""
from __future__ import annotations

import json
import os
from typing import Any

import urllib.request
import urllib.error

from .base import NetworkError, TargetAdapter


class AnthropicAdapter(TargetAdapter):
    def __init__(self, name: str, target_cfg: dict[str, Any], global_cfg: dict[str, Any], api_key: str = ""):
        super().__init__(name, target_cfg, global_cfg)
        self.base_url = (target_cfg.get("base_url") or "").rstrip("/")
        self.model = target_cfg.get("model") or ""
        self.temperature = float(target_cfg.get("temperature", 0.7))
        self.max_tokens = int(target_cfg.get("max_tokens", 2048))
        self._api_key = api_key

    def _resolve_api_key(self) -> str:
        env_name = self.target_cfg.get("api_key_env") or ""
        if env_name:
            value = os.environ.get(env_name, "")
            if value.strip():
                return value.strip()
        direct = (self.target_cfg.get("api_key") or "").strip()
        if direct:
            return direct
        return (self._api_key or "").strip()

    def health_check(self) -> None:
        if not self.base_url or not self.model:
            raise NetworkError(f"[{self.name}] anthropic target 缺少 base_url/model 配置")
        if not self._resolve_api_key():
            raise NetworkError(f"[{self.name}] anthropic target 需要提供 API Key")

    def infer(self, text: str, temperature: float | None = None) -> str:
        self.health_check()
        body = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature if temperature is None else temperature,
            "messages": [{"role": "user", "content": text}],
        }
        payload = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/v1/messages",
            data=payload,
            headers={
                "x-api-key": self._resolve_api_key(),
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
            raise NetworkError(f"[{self.name}] API 调用失败 HTTP {exc.code}: {detail}") from exc
        except Exception as exc:
            raise NetworkError(f"[{self.name}] API 调用失败：{exc}") from exc
        blocks = data.get("content") or []
        return "".join(b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text")
