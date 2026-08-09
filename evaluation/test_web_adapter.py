"""web_adapter 完成判定逻辑的单元测试（不启动真实浏览器）。

覆盖：文本稳定完成 / 停止按钮出现后消失完成 / 超时兜底。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from targets.web_adapter import WebTargetAdapter, _clean_text  # noqa: E402
from targets.api_adapter import ApiAdapter  # noqa: E402
from targets.anthropic_adapter import AnthropicAdapter  # noqa: E402
from targets.base import NetworkError  # noqa: E402

EVAL_ROOT = str(Path(__file__).resolve().parent)


def make_adapter(timeout: float = 2.0, stabilize: float = 0.5, stop_selector=None) -> WebTargetAdapter:
    target_cfg = {
        "url": "https://example.com",
        "timeout": timeout,
        "stabilize_seconds": stabilize,
        "wait_strategy": "stabilize",
        "selectors": {"stop": stop_selector, "input": "textarea", "output_selector": ["[class*='x']"]},
    }
    return WebTargetAdapter("fake", target_cfg, {"browser": {}, "_eval_root": EVAL_ROOT})


class FakeLocator:
    def __init__(self, visible: bool):
        self._visible = visible

    def is_visible(self, timeout: float = 0) -> bool:
        return self._visible

    def click(self, timeout: float = 0) -> None:
        return None

    def fill(self, text: str, timeout: float = 0) -> None:
        return None


class FakeKeyboard:
    def __init__(self):
        self.pressed: list[str] = []

    def press(self, key: str) -> None:
        self.pressed.append(key)


class FakePage:
    def __init__(self):
        self.keyboard = FakeKeyboard()


class FakeBrowserManager:
    def __init__(self, page: FakePage):
        self.page = page

    def page_for(self, target_id: str, url: str) -> FakePage:
        return self.page


def test_wait_done_stable_text():
    """输出文本出现后连续稳定 → 正常完成（不依赖超时）。"""
    a = make_adapter()
    a._locate = lambda sel, timeout=5_000: None
    a._safe_extract = lambda: "稳定回答内容"
    t0 = time.time()
    done, how = a._wait_done(time.time() + 2.0)
    assert done is True
    assert how == "stable"
    assert time.time() - t0 < 2.0  # 在超时前完成


def test_wait_done_stop_button_gone():
    """停止按钮出现后消失 → 正常完成（文本持续变化不触发稳定判定）。"""
    a = make_adapter(stop_selector="div[class*='stop']")
    seq = {"n": 0}

    def fake_locate(sel, timeout=5_000):
        seq["n"] += 1
        return FakeLocator(seq["n"] <= 2)  # 前两次可见（生成中），之后消失（完成）

    def fake_extract():
        return f"内容 {seq['n']}"  # 每次都不同 → 不触发稳定判定，只能靠 stop_gone 完成

    a._locate = fake_locate
    a._safe_extract = fake_extract
    t0 = time.time()
    done, how = a._wait_done(time.time() + 3.0)
    assert done is True
    assert how == "stop_gone"
    assert time.time() - t0 < 3.0


def test_wait_done_timeout():
    """始终取不到文本且无停止按钮 → 到达截止时间返回未完成（供超时兜底）。"""
    a = make_adapter(timeout=1.0)
    a._locate = lambda sel, timeout=5_000: None
    a._safe_extract = lambda: ""
    t0 = time.time()
    done, how = a._wait_done(time.time() + 1.0)
    assert done is False
    assert how == "timeout"
    assert 0.9 <= time.time() - t0 < 5.0


def test_wait_and_extract_timeout_returns_partial():
    """超时兜底：能提取到部分内容时返回部分内容而非抛异常。"""
    a = make_adapter(timeout=1.0)
    a._locate = lambda sel, timeout=5_000: None
    a._safe_extract = lambda: f"部分生成的内容 {time.time()}"  # 每次不同 → 永不稳定，直到超时

    class _NoExtract:
        def __call__(self, *args, **kwargs):
            raise AssertionError("超时兜底路径不应调用 _extract_output")

    a._extract_output = _NoExtract()
    text = a._wait_and_extract()
    assert text.startswith("部分生成的内容")


def test_clean_text_filters_citation_markers():
    """过滤 inner_text 混入的独立引用索引行，保留正文与段落空行。"""
    raw = "根据官方信息，\n-3\n上下文窗口为128K\n-1\n-11\n\n"
    cleaned = _clean_text(raw)
    assert "上下文窗口为128K" in cleaned
    assert "-3" not in cleaned
    assert "-11" not in cleaned


def test_clean_text_keeps_hyphenated_content():
    """负数或破折号开头的正文行不应被误删。"""
    cleaned = _clean_text("收益下降了 -20%\n结论不变")
    assert "收益下降了 -20%" in cleaned


def test_send_prompt_enter_then_fallback_click():
    """回车后输入框未清空（千问等站点）→ 自动点击发送图标兜底。"""
    a = make_adapter()
    page = FakePage()
    bm = FakeBrowserManager(page)
    a._get_browser_manager = lambda: bm
    a._locate = lambda sel, timeout=5_000: FakeLocator(True)
    calls = {"cleared": 0, "fallback": 0}

    def fake_cleared():
        calls["cleared"] += 1
        return calls["cleared"] >= 2  # 回车后第一次检查未清空 → 触发兜底；点击后再检查已清空

    def fake_fallback():
        calls["fallback"] += 1
        return True

    a._is_input_cleared = fake_cleared
    a._click_send_fallback = fake_fallback
    a._wait_generation_started = lambda: None
    a._send_prompt("你好")
    assert page.keyboard.pressed == ["Enter"]
    assert calls["fallback"] == 1
    assert calls["cleared"] == 2


def test_send_prompt_fallback_not_found_still_continues():
    """发送图标兜底找不到时，不抛错、按原流程继续。"""
    a = make_adapter()
    page = FakePage()
    bm = FakeBrowserManager(page)
    a._get_browser_manager = lambda: bm
    a._locate = lambda sel, timeout=5_000: FakeLocator(True)
    a._is_input_cleared = lambda: False  # 永远"未清空"
    a._click_send_fallback = lambda: False
    a._wait_generation_started = lambda: None
    a._send_prompt("你好")  # 不应抛异常
    assert page.keyboard.pressed == ["Enter"]


def make_api_adapter(**target_overrides) -> ApiAdapter:
    target_cfg = {
        "mode": "api",
        "base_url": "https://api.example.com/v1",
        "model": "test-model",
        **target_overrides,
    }
    return ApiAdapter("fake-api", target_cfg, {"browser": {}, "_eval_root": EVAL_ROOT})


def test_api_key_resolution_prefers_env_var(monkeypatch):
    """api_key_env 指向的环境变量已设置 → 使用环境变量值。"""
    monkeypatch.setenv("VOLCANO_API_KEY", "env-secret")
    a = make_api_adapter(api_key_env="VOLCANO_API_KEY", api_key="cfg-key")
    assert a._resolve_api_key() == "env-secret"
    a.health_check()  # 不应抛错


def test_api_key_resolution_env_missing_falls_back_to_cfg(monkeypatch):
    """api_key_env 设置但环境变量不存在 → 回退 target_cfg.api_key。"""
    monkeypatch.delenv("VOLCANO_API_KEY", raising=False)
    a = make_api_adapter(api_key_env="VOLCANO_API_KEY", api_key="cfg-key")
    assert a._resolve_api_key() == "cfg-key"


def test_api_key_resolution_falls_back_to_constructor_arg(monkeypatch):
    """没有 api_key_env / api_key → 使用构造参数 api_key。"""
    monkeypatch.delenv("VOLCANO_API_KEY", raising=False)
    a = make_api_adapter(api_key="ctor-key")
    assert a._resolve_api_key() == "ctor-key"


def test_api_key_resolution_none_raises(monkeypatch):
    """所有来源都为空 → health_check 抛 NetworkError 且包含 'API Key'。"""
    monkeypatch.delenv("VOLCANO_API_KEY", raising=False)
    a = make_api_adapter(api_key_env="VOLCANO_API_KEY")
    try:
        a.health_check()
        assert False, "应抛出 NetworkError"
    except NetworkError as exc:
        assert "API Key" in str(exc)


def make_anthropic_adapter(**target_overrides) -> AnthropicAdapter:
    target_cfg = {
        "mode": "anthropic",
        "base_url": "https://ark.cn-beijing.volces.com/api",
        "model": "test-plan-model",
        **target_overrides,
    }
    return AnthropicAdapter("fake-anthropic", target_cfg, {"browser": {}, "_eval_root": EVAL_ROOT})


def test_anthropic_key_resolution_none_raises(monkeypatch):
    """所有来源都为空（空 env + 无 cfg key + 空构造参数）→ health_check 抛 NetworkError。"""
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    a = make_anthropic_adapter(api_key_env="ARK_API_KEY")
    try:
        a.health_check()
        assert False, "应抛出 NetworkError"
    except NetworkError as exc:
        assert "API Key" in str(exc)


def test_anthropic_key_resolution_prefers_env_var(monkeypatch):
    """api_key_env 指向的环境变量已设置 → 使用环境变量值。"""
    monkeypatch.setenv("ARK_API_KEY", "env-secret")
    a = make_anthropic_adapter(api_key_env="ARK_API_KEY", api_key="cfg-key")
    assert a._resolve_api_key() == "env-secret"
    a.health_check()  # 不应抛错


def test_anthropic_key_resolution_env_missing_falls_back_to_cfg(monkeypatch):
    """api_key_env 设置但环境变量不存在 → 回退 target_cfg.api_key。"""
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    a = make_anthropic_adapter(api_key_env="ARK_API_KEY", api_key="cfg-key")
    assert a._resolve_api_key() == "cfg-key"


def test_anthropic_key_resolution_falls_back_to_constructor_arg(monkeypatch):
    """没有 api_key_env / api_key → 使用构造参数 api_key。"""
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    a = make_anthropic_adapter(api_key="ctor-key")
    assert a._resolve_api_key() == "ctor-key"


def test_anthropic_infer_builds_request(monkeypatch):
    """infer 走 POST {base_url}/v1/messages，x-api-key / anthropic-version 头正确，拼接所有 text 块。"""
    monkeypatch.setenv("ARK_API_KEY", "env-secret")
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def read(self):
            return json.dumps(
                {"content": [{"type": "text", "text": "回答A"}, {"type": "text", "text": "回答B"}]}
            ).encode("utf-8")

    def fake_urlopen(req, timeout=120):
        captured["req"] = req
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    a = make_anthropic_adapter(api_key_env="ARK_API_KEY", api_key="cfg-key")
    assert a.infer("你好") == "回答A回答B"
    req = captured["req"]
    headers = {k.lower(): v for k, v in req.headers.items()}
    assert req.full_url.endswith("/v1/messages")
    assert headers.get("x-api-key") == "env-secret"
    assert headers.get("anthropic-version") == "2023-06-01"
