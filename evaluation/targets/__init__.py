"""目标模型适配层包。"""
from .base import (
    GenerationTimeout,
    LoginRequired,
    NetworkError,
    RateLimited,
    SelectorError,
    TargetAdapter,
    TargetError,
    MockAdapter,
    create_adapter,
)

__all__ = [
    "TargetAdapter",
    "TargetError",
    "LoginRequired",
    "SelectorError",
    "GenerationTimeout",
    "RateLimited",
    "NetworkError",
    "MockAdapter",
    "create_adapter",
]
