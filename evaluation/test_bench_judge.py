"""bench_judge.py 测试：裁判 JSON 解析、围栏剥离、非法输出重试路径。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bench_api  # noqa: E402
import bench_judge  # noqa: E402

API_CFG = {"base_url": "http://fake", "protocol": "anthropic"}
JUDGE_CFG = {"model": "judge-model", "temperature": 0}


def _good_scores() -> dict:
    # 清单 C1：五个维度都必须真实给出。此前 novelty 缺失会回退取 relevance，
    # 于是"新颖性"实际测的是"相关性"。现在缺维度 = 该样本不可评。
    return {"accuracy": 9, "completeness": 8, "relevance": 7, "clarity": 6,
            "novelty": 5, "reason": "不错"}


def _call_chat_fake(monkeypatch, responses):
    """依次返回给定 ChatResult/异常；用 call_count 追踪调用次数。"""
    state = {"calls": 0}

    def fake(base_url, api_key, protocol, model, messages, temperature, max_tokens, timeout_s):
        state["calls"] += 1
        resp = responses[min(state["calls"] - 1, len(responses) - 1)]
        if isinstance(resp, BaseException):
            raise resp
        return resp

    monkeypatch.setattr(bench_judge.bench_api, "call_chat", fake)
    return state


def test_judge_answer_parses_ok(monkeypatch):
    payload = json.dumps(_good_scores(), ensure_ascii=False)
    state = _call_chat_fake(monkeypatch, [bench_api.ChatResult(text=payload, latency_s=0.5, usage={"input_tokens": 1})])

    result = bench_judge.judge_answer("任务", "提示词", "回答", API_CFG, JUDGE_CFG, "key", 30)

    assert state["calls"] == 1
    assert result["accuracy"] == 9
    assert result["completeness"] == 8
    assert result["relevance"] == 7
    assert result["clarity"] == 6
    assert result["reason"] == "不错"
    assert "error" not in result


def test_judge_answer_parses_fenced_json(monkeypatch):
    payload = "```json\n" + json.dumps(_good_scores(), ensure_ascii=False) + "\n```"
    _call_chat_fake(monkeypatch, [bench_api.ChatResult(text=payload)])

    result = bench_judge.judge_answer("任务", "提示词", "回答", API_CFG, JUDGE_CFG, "key", 30)
    assert result["accuracy"] == 9


def test_judge_answer_retries_bad_output_then_ok(monkeypatch):
    state = _call_chat_fake(monkeypatch, [
        bench_api.ChatResult(text="抱歉，我无法打分"),
        bench_api.ChatResult(text=json.dumps(_good_scores())),
    ])

    result = bench_judge.judge_answer("任务", "提示词", "回答", API_CFG, JUDGE_CFG, "key", 30)
    assert state["calls"] == 2
    assert result["accuracy"] == 9


def test_judge_answer_error_after_retry(monkeypatch):
    state = _call_chat_fake(monkeypatch, [
        bench_api.ChatResult(text="不是 JSON"),
        bench_api.ChatResult(text="还是不是 JSON"),
    ])

    result = bench_judge.judge_answer("任务", "提示词", "回答", API_CFG, JUDGE_CFG, "key", 30)
    assert state["calls"] == 2
    assert "error" in result
    assert "解析失败" in result["error"]


def test_judge_answer_rejects_out_of_range_score(monkeypatch):
    bad = {"accuracy": 11, "completeness": 5, "relevance": 5, "clarity": 5,
           "novelty": 5, "reason": ""}
    _call_chat_fake(monkeypatch, [
        bench_api.ChatResult(text=json.dumps(bad)),
        bench_api.ChatResult(text=json.dumps(_good_scores())),
    ])

    result = bench_judge.judge_answer("任务", "提示词", "回答", API_CFG, JUDGE_CFG, "key", 30)
    assert result["accuracy"] == 9  # 越界触发重试，第二次成功


def test_missing_novelty_is_not_substituted_by_relevance(monkeypatch):
    """清单 C1：novelty 缺失 ⇒ 该样本不可评，**不得**用 relevance 顶替。

    此前 novelty 缺失会回退取 relevance（"新颖性"实际测的是"相关性"），
    而且这个顶替被写成"保证旧数据兼容"，被当成既定设计。
    现在缺维度就是缺维度：报错并点名缺失的维度，失败原因可被审计。
    """
    payload = json.dumps({"accuracy": 9, "completeness": 8, "relevance": 7,
                          "clarity": 6, "reason": "不错"}, ensure_ascii=False)
    state = _call_chat_fake(monkeypatch, [bench_api.ChatResult(text=payload)])

    result = bench_judge.judge_answer("任务", "提示词", "回答", API_CFG, JUDGE_CFG, "key", 30)

    assert state["calls"] == 2, "缺维度必须重试，不得静默补一个维度"
    assert "error" in result
    assert "novelty" in result["error"], "失败原因必须点名缺失的维度"
    assert "novelty" not in result, "不得凭空造出 novelty 分"
    assert result.get("relevance") != 7 or "novelty" not in result


def test_missing_novelty_never_becomes_relevance_value(monkeypatch):
    """C1 的直接断言：解析结果里 novelty 永远不是 relevance 的拷贝。"""
    payload = json.dumps({"accuracy": 9, "completeness": 8, "relevance": 7,
                          "clarity": 6, "reason": "不错"}, ensure_ascii=False)
    _call_chat_fake(monkeypatch, [bench_api.ChatResult(text=payload)])

    with pytest.raises(ValueError):
        bench_judge._parse_scores(payload)


def test_judge_answer_propagates_api_errors(monkeypatch):
    _call_chat_fake(monkeypatch, [bench_api.RateLimited("HTTP 429: limit")])

    with pytest.raises(bench_api.RateLimited):
        bench_judge.judge_answer("任务", "提示词", "回答", API_CFG, JUDGE_CFG, "key", 30)
