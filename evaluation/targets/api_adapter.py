"""API 直连目标模型适配器（备用/降级方案，默认关闭）。

当没有网页访问权限或网页版不稳定时，可将 target 的 mode 改为 api，
直接通过 OpenAI 兼容接口把提示词发给目标模型。
"""
from __future__ import annotations

import os
from typing import Any

import openai

from .base import NetworkError, TargetAdapter


class ApiAdapter(TargetAdapter):
    def __init__(self, name: str, target_cfg: dict[str, Any], global_cfg: dict[str, Any], api_key: str = ""):
        super().__init__(name, target_cfg, global_cfg)
        self.base_url = (target_cfg.get("base_url") or "").rstrip("/")
        self.model = target_cfg.get("model") or ""
        self.temperature = float(target_cfg.get("temperature", 0.7))
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
            raise NetworkError(f"[{self.name}] api target 缺少 base_url/model 配置")
        if not self._resolve_api_key():
            raise NetworkError(f"[{self.name}] api target 需要提供 API Key")

    def infer(self, text: str, temperature: float | None = None) -> str:
        self.health_check()
        client = openai.OpenAI(api_key=self._resolve_api_key(), base_url=self.base_url)
        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": text}],
                temperature=self.temperature if temperature is None else temperature,
            )
            return response.choices[0].message.content or ""
        except openai.APIError as exc:
            raise NetworkError(f"[{self.name}] API 调用失败：{exc}") from exc
