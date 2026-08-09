"""bench_api.py 测试：anthropic/openai 双协议请求与解析、异常分类、密钥解析、重试。"""
from __future__ import annotations

import io
import json
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import urllib.error  # noqa: E402

import bench_api  # noqa: E402


class FakeResponse:
    def __init__(self, body, status=200):
        self._body = body.encode("utf-8") if isinstance(body, str) else body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body

    def getcode(self):
        return self.status


class CapturingUrlopen:
    """替换 urllib.request.urlopen：记录请求，返回预设响应或抛出预设异常。"""

    def __init__(self, payload=None, exc=None):
        self.payload = payload
        self.exc = exc
        self.calls: list[tuple] = []

    def __call__(self, req, timeout=None):
        self.calls.append((req, timeout))
        if self.exc is not None:
            raise self.exc
        return FakeResponse(self.payload)


def _headers(req) -> dict:
    """urllib 会把头名首字母大写，get_header 又区分大小写，这里统一小写比对。"""
    return {k.lower(): v for k, v in req.headers.items()}


def _http_error(code: int, body: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "http://fake/v1/messages", code, "err", {}, io.BytesIO(body.encode("utf-8"))
    )


def _anthropic_response(*, with_thinking: bool = True) -> str:
    blocks = [{"type": "text", "text": "你好"}]
    if with_thinking:
        blocks.insert(0, {"type": "thinking", "thinking": "思考中……"})
    blocks.append({"type": "text", "text": "，世界"})
    return json.dumps({
        "content": blocks,
        "usage": {"input_tokens": 10, "output_tokens": 20},
    })


def test_anthropic_request_and_parse(monkeypatch):
    fake = CapturingUrlopen(_anthropic_response())
    monkeypatch.setattr(bench_api.urllib.request, "urlopen", fake)

    result = bench_api.call_chat(
        base_url="https://api.example.com",
        api_key="sk-123",
        protocol="anthropic",
        model="m-1",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.5,
        max_tokens=100,
        timeout_s=30,
    )

    assert len(fake.calls) == 1
    req, timeout = fake.calls[0]
    assert timeout == 30
    assert req.full_url == "https://api.example.com/v1/messages"
    headers = _headers(req)
    assert headers["x-api-key"] == "sk-123"
    assert headers["anthropic-version"] == "2023-06-01"
    assert headers["content-type"] == "application/json"
    body = json.loads(req.data)
    assert body["model"] == "m-1"
    assert body["max_tokens"] == 100
    assert body["temperature"] == 0.5
    assert body["messages"] == [{"role": "user", "content": "hi"}]

    assert result.error is None
    assert result.text == "你好，世界"  # thinking 块被跳过
    assert result.usage == {"input_tokens": 10, "output_tokens": 20}
    assert result.latency_s >= 0


def test_anthropic_skips_thinking_blocks(monkeypatch):
    fake = CapturingUrlopen(_anthropic_response(with_thinking=True))
    monkeypatch.setattr(bench_api.urllib.request, "urlopen", fake)
    result = bench_api.call_chat("http://h", "k", "anthropic", "m", [{"role": "user", "content": "x"}],
                                temperature=0, max_tokens=1, timeout_s=5)
    assert result.text == "你好，世界"
    assert "思考" not in result.text


def test_openai_protocol(monkeypatch):
    payload = json.dumps({
        "choices": [{"message": {"role": "assistant", "content": "答案"}}],
        "usage": {"prompt_tokens": 7, "completion_tokens": 9},
    })
    fake = CapturingUrlopen(payload)
    monkeypatch.setattr(bench_api.urllib.request, "urlopen", fake)

    result = bench_api.call_chat(
        base_url="https://api.openai.com/",
        api_key="sk-456",
        protocol="openai",
        model="gpt-x",
        messages=[{"role": "user", "content": "q"}],
        temperature=0,
        max_tokens=64,
        timeout_s=10,
    )

    req, _ = fake.calls[0]
    assert req.full_url == "https://api.openai.com/chat/completions"
    assert _headers(req)["authorization"] == "Bearer sk-456"
    body = json.loads(req.data)
    assert body["model"] == "gpt-x"
    assert body["max_tokens"] == 64
    assert result.text == "答案"
    assert result.usage == {"prompt_tokens": 7, "completion_tokens": 9}


def test_429_raises_rate_limited(monkeypatch):
    fake = CapturingUrlopen(exc=_http_error(429, '{"error":{"message":"limit"}}'))
    monkeypatch.setattr(bench_api.urllib.request, "urlopen", fake)
    with pytest.raises(bench_api.RateLimited):
        bench_api.call_chat("http://h", "k", "anthropic", "m", [], 0, 1, 5)


def test_http_5xx_raises_http_error(monkeypatch):
    fake = CapturingUrlopen(exc=_http_error(503, "busy"))
    monkeypatch.setattr(bench_api.urllib.request, "urlopen", fake)
    with pytest.raises(bench_api.HttpError) as exc_info:
        bench_api.call_chat("http://h", "k", "anthropic", "m", [], 0, 1, 5)
    assert exc_info.value.status == 503
    assert "busy" in exc_info.value.body


def test_timeout_raises(monkeypatch):
    fake = CapturingUrlopen(exc=socket.timeout("timed out"))
    monkeypatch.setattr(bench_api.urllib.request, "urlopen", fake)
    with pytest.raises(TimeoutError):
        bench_api.call_chat("http://h", "k", "anthropic", "m", [], 0, 1, 5)


def test_network_error_raises(monkeypatch):
    fake = CapturingUrlopen(exc=urllib.error.URLError(socket.gaierror(11001, "no such host")))
    monkeypatch.setattr(bench_api.urllib.request, "urlopen", fake)
    with pytest.raises(bench_api.NetworkError):
        bench_api.call_chat("http://h", "k", "anthropic", "m", [], 0, 1, 5)


# ---- 密钥解析优先级：env > file > literal ----

def test_resolve_api_key_env_priority(tmp_path, monkeypatch):
    (tmp_path / "key.local").write_text("file-key", encoding="utf-8")
    api_cfg = {"api_key_env": "BENCH_TEST_KEY", "api_key_file": "key.local", "api_key": "literal-key"}
    monkeypatch.setenv("BENCH_TEST_KEY", "env-key")
    assert bench_api.resolve_api_key(api_cfg, str(tmp_path)) == "env-key"


def test_resolve_api_key_file_fallback(tmp_path, monkeypatch):
    (tmp_path / "key.local").write_text("  file-key\n", encoding="utf-8")
    api_cfg = {"api_key_env": "BENCH_TEST_MISSING", "api_key_file": "key.local", "api_key": "literal-key"}
    monkeypatch.delenv("BENCH_TEST_MISSING", raising=False)
    assert bench_api.resolve_api_key(api_cfg, str(tmp_path)) == "file-key"


def test_resolve_api_key_literal_fallback(tmp_path, monkeypatch):
    api_cfg = {"api_key_env": "BENCH_TEST_MISSING", "api_key_file": "key.local", "api_key": "literal-key"}
    monkeypatch.delenv("BENCH_TEST_MISSING", raising=False)
    assert bench_api.resolve_api_key(api_cfg, str(tmp_path)) == "literal-key"


def test_resolve_api_key_missing_raises(tmp_path, monkeypatch):
    api_cfg = {"api_key_env": "BENCH_TEST_MISSING", "api_key_file": "key.local"}
    monkeypatch.delenv("BENCH_TEST_MISSING", raising=False)
    with pytest.raises(ValueError):
        bench_api.resolve_api_key(api_cfg, str(tmp_path))


# ---- 重试 ----

def test_call_with_retry_succeeds_after_retry(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(bench_api.time, "sleep", lambda s: None)

    def fn():
        calls["n"] += 1
        if calls["n"] == 1:
            raise bench_api.RateLimited("HTTP 429: slow down")
        return bench_api.ChatResult(text="ok", latency_s=1.0)

    result = bench_api.call_with_retry(fn, retries=2, backoff_s=[2, 5], label="测试")
    assert result.text == "ok"
    assert result.error is None
    assert calls["n"] == 2


def test_call_with_retry_exhausted_returns_error(monkeypatch):
    calls = {"n": 0}
    sleeps: list[float] = []
    monkeypatch.setattr(bench_api.time, "sleep", lambda s: sleeps.append(s))

    def fn():
        calls["n"] += 1
        raise bench_api.HttpError(500, "boom")

    result = bench_api.call_with_retry(fn, retries=2, backoff_s=[2, 5], label="测试")
    assert result.error is not None
    assert "测试" in result.error
    assert calls["n"] == 3
    assert sleeps == [2, 5]


def test_call_with_retry_no_retry_on_4xx(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(bench_api.time, "sleep", lambda s: None)

    def fn():
        calls["n"] += 1
        raise bench_api.HttpError(400, "bad request")

    result = bench_api.call_with_retry(fn, retries=2, backoff_s=[2, 5], label="测试")
    assert result.error is not None
    assert calls["n"] == 1
