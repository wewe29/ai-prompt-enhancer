"""目标模型适配层：抽象基类、异常分类与工厂。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

# ---- 失败异常分类（run_eval 据此降级/重试）----


class TargetError(Exception):
    """目标模型调用失败的基类"""


class LoginRequired(TargetError):
    """未登录或登录态失效；跳过该 target 剩余样本"""


class SelectorError(TargetError):
    """页面结构变化，选择器定位失败；跳过单条"""


class GenerationTimeout(TargetError):
    """生成超时；截断当前结果"""


class RateLimited(TargetError):
    """触发限流；等待后重试"""


class NetworkError(TargetError):
    """网络/浏览器层故障；跳过单条"""


class TargetAdapter(ABC):
    """所有目标模型适配器的统一接口。"""

    name: str

    def __init__(self, name: str, target_cfg: dict[str, Any], global_cfg: dict[str, Any]):
        self.name = name
        self.target_cfg = target_cfg
        self.global_cfg = global_cfg

    @abstractmethod
    def health_check(self) -> None:
        """检查可用性（登录态/配置），失败抛 TargetError。"""

    @abstractmethod
    def infer(self, text: str) -> str:
        """发送提示词并返回模型回答文本。"""

    def new_chat(self) -> None:
        """开启新会话/清空上下文，避免串扰（默认无操作）。"""

    def close(self) -> None:
        """释放资源。"""

    def __enter__(self) -> "TargetAdapter":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


class MockAdapter(TargetAdapter):
    """离线模拟 target：不联网，返回确定性假回答，用于管线自测。"""

    def health_check(self) -> None:
        return None

    def infer(self, text: str) -> str:
        summary = text.strip().replace("\n", " ")[:80]
        return (
            f"【{self.name}·模拟回答】\n"
            f"针对你的要求「{summary}」，模拟回答要点如下：\n"
            "1. 需求已接收，正在按要点组织回答；\n"
            "2. 内容包含步骤与结论，格式结构清晰；\n"
            "3. 本回答为 MockAdapter 生成的占位输出，仅用于离线验证管线。"
        )


def create_adapter(name: str, target_cfg: dict[str, Any], global_cfg: dict[str, Any], browser_manager: Any = None, api_key: str = "") -> TargetAdapter:
    """按 target 的 mode 创建适配器：web | api | mock。

    browser_manager：web 模式下可传入共享的 BrowserManager（由 run_eval 统一管理生命周期）。
    """
    mode = target_cfg.get("mode", "web")
    if mode == "web":
        from .web_adapter import WebTargetAdapter

        return WebTargetAdapter(name, target_cfg, global_cfg, browser_manager=browser_manager)
    if mode == "api":
        from .api_adapter import ApiAdapter

        return ApiAdapter(name, target_cfg, global_cfg, api_key=api_key)
    if mode == "mock":
        return MockAdapter(name, target_cfg, global_cfg)
    raise ValueError(f"target {name}: 未知 mode={mode}")
