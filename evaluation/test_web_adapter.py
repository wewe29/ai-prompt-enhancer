"""web_adapter 完成判定逻辑的单元测试（不启动真实浏览器）。

覆盖：文本稳定完成 / 停止按钮出现后消失完成 / 超时兜底。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from targets.web_adapter import WebTargetAdapter, _clean_text  # noqa: E402

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
