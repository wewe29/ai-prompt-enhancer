"""配置加载、校验与 API Key 解析。

API Key 解析优先级（由 config.yaml 的 key_source 控制）：
1. keyring: 读取 PromptCraft 已存入 Windows 凭据管理器的 Key（SERVICE="PromptCraft", ACCOUNT="deepseek-api-key"）
2. env:     环境变量 DEEPSEEK_API_KEY
3. config:  config.yaml 中的 api_key 明文（不推荐提交到版本库）
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import yaml

try:
    import keyring
except Exception:  # keyring 在无桌面会话的环境可能导入失败
    keyring = None

# 与 Rust storage.rs 中 keyring 常量保持一致
KEYRING_SERVICE = "PromptCraft"
KEYRING_ACCOUNT = "deepseek-api-key"
ENV_API_KEY = "DEEPSEEK_API_KEY"

EVAL_ROOT = Path(__file__).resolve().parent

DEFAULTS: dict[str, Any] = {
    "enhancer": {
        "base_url": "https://api.deepseek.com",
        "protocol": "openai",
        "model": "deepseek-v4-flash",
        "verbosity": "standard",
        "custom_instructions": "",
        "temperature": 0.35,
    },
    "scenario_temperatures": {
        "编程": 0.2,
        "翻译": 0.2,
        "数据分析": 0.2,
        "问答": 0.4,
        "写作": 0.5,
        "模糊请求": 0.5,
        "创意": 0.7,
    },
    "key_source": "keyring",
    "api_key": None,
    "browser": {
        "mode": "playwright",  # playwright | cdp
        "cdp_url": "http://localhost:9222",
        "headless": False,
        "slow_input_chars": 200,
        "input_delay_ms": 30,
    },
    "judge": {
        "base_url": "https://ark.cn-beijing.volces.com/api/plan/v1",
        "api_key_env": "ARK_PLAN_API_KEY",
        "api_key_file": "key.local",
        "model": "deepseek-v4-flash",
        "temperature": 0,
        "randomize_order": True,
        "cross_check": False,  # 每条独立再评一次交叉验证（增加成本，默认关闭）
    },
    "run": {
        "delay_between": 2.0,  # 秒，请求间延迟
        "max_cost_usd": 2.0,   # 预算保护
        "retry_seconds": 60,   # RateLimited 后等待重试
        "control_group": True,  # A/B/C 三组对照默认开启
        "repeats": 1,           # 每个(样本×目标×变体)重复次数
    },
    "samples": "samples/samples.yaml",
}


class ConfigError(Exception):
    """配置错误"""


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """加载并合并默认配置。

    显式传入的字段覆盖默认值；返回的 dict 同时包含 path 等派生信息。
    """
    cfg_path = Path(path) if path else EVAL_ROOT / "config.yaml"
    cfg = _deep_copy(DEFAULTS)
    if cfg_path.exists():
        with open(cfg_path, "r", encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh) or {}
        if not isinstance(loaded, dict):
            raise ConfigError(f"配置文件 {cfg_path} 顶层必须是映射")
        cfg = _deep_merge(cfg, loaded)
    else:
        print(f"[config] 未找到 {cfg_path}，使用默认配置")
    cfg["_config_path"] = str(cfg_path)
    cfg["_eval_root"] = str(EVAL_ROOT)
    validate(cfg)
    return cfg


def validate(cfg: dict[str, Any]) -> None:
    """基础校验，尽早暴露配置错误。"""
    enh = cfg.get("enhancer", {})
    if not enh.get("base_url"):
        raise ConfigError("enhancer.base_url 不能为空")
    if not enh.get("model"):
        raise ConfigError("enhancer.model 不能为空")
    if cfg.get("key_source") not in ("keyring", "env", "config"):
        raise ConfigError("key_source 必须是 keyring | env | config")
    if cfg.get("browser", {}).get("mode") not in ("playwright", "cdp"):
        raise ConfigError("browser.mode 必须是 playwright | cdp")
    for tid, tcfg in cfg.get("targets", {}).items():
        if tcfg.get("enabled") and tcfg.get("mode") not in ("web", "api", "anthropic", "mock"):
            raise ConfigError(f"target {tid}: mode 必须是 web | api | anthropic | mock")


def resolve_api_key(cfg: dict[str, Any]) -> str:
    """按优先级解析 API Key。

    1. enhancer.api_key_env 指向的环境变量（增强器密钥，如 ARK_API_KEY）
    2. 由 key_source 控制的 DeepSeek Key（keyring → DEEPSEEK_API_KEY → config.api_key）
    """
    enh_env = str(cfg.get("enhancer", {}).get("api_key_env") or "").strip()
    if enh_env:
        key = os.environ.get(enh_env, "").strip()
        if key:
            return key
        print(f"[config] 环境变量 {enh_env} 未设置，回退 key_source 解析")
    source = cfg.get("key_source", "keyring")
    if source == "keyring":
        key = _read_from_keyring()
        if key:
            return key
        print("[config] keyring 中未找到 PromptCraft 的 API Key，尝试环境变量 DEEPSEEK_API_KEY")
        source = "env"
    if source == "env":
        key = os.environ.get(ENV_API_KEY, "").strip()
        if key:
            return key
        print(f"[config] 环境变量 {ENV_API_KEY} 未设置，尝试 config.yaml 的 api_key 字段")
        source = "config"
    if source == "config":
        key = str(cfg.get("api_key") or "").strip()
        if key:
            return key
    raise ConfigError(
        "未找到 API Key。请任选其一：\n"
        f"  1) 设置增强器密钥环境变量 {enh_env or 'ARK_API_KEY'}；\n"
        f"  2) 在 PromptCraft 应用中配置（脚本通过 keyring 读取，SERVICE={KEYRING_SERVICE}）；\n"
        f"  3) 在 evaluation/config.yaml 中填写 api_key（请勿提交到版本库）。"
    )


def resolve_judge_key(cfg: dict[str, Any], fallback: str = "") -> str:
    """解析裁判（judge）密钥：judge.api_key_env 环境变量 → judge.api_key_file → fallback。"""
    jcfg = cfg.get("judge", {}) or {}
    env_name = str(jcfg.get("api_key_env") or "").strip()
    if env_name:
        key = os.environ.get(env_name, "").strip()
        if key:
            return key
    key_file = str(jcfg.get("api_key_file") or "").strip()
    if key_file:
        path = Path(key_file)
        if not path.is_absolute():
            path = EVAL_ROOT / path
        if path.exists():
            key = path.read_text(encoding="utf-8").strip()
            if key:
                return key
    return fallback


def _read_from_keyring() -> str | None:
    if keyring is None:
        return None
    try:
        value = keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
        return value.strip() if value and value.strip() else None
    except Exception as exc:  # 某些环境（无桌面会话）会抛异常
        print(f"[config] 读取 keyring 失败（将回退其他来源）：{exc}")
        return None


def _deep_copy(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _deep_copy(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_deep_copy(v) for v in value]
    return value


def _deep_merge(base: dict, override: dict) -> dict:
    out = _deep_copy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = _deep_copy(value)
    return out


if __name__ == "__main__":
    # 快捷自检：python config.py
    cfg = load_config(sys.argv[1] if len(sys.argv) > 1 else None)
    print("配置加载成功：", cfg["_config_path"])
    try:
        print("API Key 来源：", "已解析（长度 %d）" % len(resolve_api_key(cfg)))
    except ConfigError as exc:
        print("API Key 缺失：", exc)
