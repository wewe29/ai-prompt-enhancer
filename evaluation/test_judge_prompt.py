"""T4/T5 测试：prompt 级裁判解析/重试、改进幅度分类与聚合。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import judge as judge_mod  # noqa: E402
import report as report_mod  # noqa: E402

CFG = {"enhancer": {"base_url": "http://fake"}, "judge": {"model": "judge-model", "temperature": 0}}


class _FakeUsage:
    def __init__(self, pt: int = 10, ct: int = 5):
        self.prompt_tokens = pt
        self.completion_tokens = ct


class _FakeResponse:
    def __init__(self, content: str, usage=None):
        msg = type("_M", (), {"content": content})()
        self.choices = [type("_C", (), {"message": msg})()]
        self.usage = usage or _FakeUsage()


def _fake_openai(monkeypatch, responses):
    """用 Fake OpenAI client 依次返回给定响应/异常；state["calls"] 记录调用次数。"""
    state = {"calls": 0}

    class _Completions:
        def create(self, **kwargs):
            state["calls"] += 1
            resp = responses[min(state["calls"] - 1, len(responses) - 1)]
            if isinstance(resp, BaseException):
                raise resp
            return resp

    class _Chat:
        completions = _Completions()

    class _Client:
        def __init__(self, api_key, base_url):
            self.chat = _Chat()

    monkeypatch.setattr(judge_mod.openai, "OpenAI", _Client)
    return state


def _good() -> str:
    return json.dumps(
        {"structure": 9, "constraint_retention": 8, "info_gain": 7,
         "over_enhancement": 2, "reason": "结构清晰，约束完整"}
    )


def test_judge_prompt_parses_ok(monkeypatch):
    state = _fake_openai(monkeypatch, [_FakeResponse(_good())])

    result = judge_mod.judge_prompt_level("原始", "增强", CFG, "key")

    assert state["calls"] == 1
    assert "error" not in result
    assert result["structure"] == 9
    assert result["constraint_retention"] == 8
    assert result["info_gain"] == 7
    assert result["over_enhancement"] == 2
    assert result["reason"] == "结构清晰，约束完整"


def test_judge_prompt_parses_fenced_json(monkeypatch):
    payload = "```json\n" + _good() + "\n```"
    _fake_openai(monkeypatch, [_FakeResponse(payload)])

    result = judge_mod.judge_prompt_level("原始", "增强", CFG, "key")

    assert result["structure"] == 9
    assert "error" not in result


def test_judge_prompt_bad_output_retry_then_error(monkeypatch):
    state = _fake_openai(monkeypatch, [
        _FakeResponse("抱歉，我无法打分"),
        _FakeResponse("还是不是 JSON"),
    ])

    result = judge_mod.judge_prompt_level("原始", "增强", CFG, "key")

    assert state["calls"] == 2
    assert "error" in result
    assert "解析失败" in result["error"]


def test_judge_prompt_out_of_range_retries_then_ok(monkeypatch):
    bad = json.dumps(
        {"structure": 9, "constraint_retention": 8, "info_gain": 7,
         "over_enhancement": 11, "reason": "越界"}
    )
    state = _fake_openai(monkeypatch, [_FakeResponse(bad), _FakeResponse(_good())])

    result = judge_mod.judge_prompt_level("原始", "增强", CFG, "key")

    assert state["calls"] == 2
    assert "error" not in result
    assert result["over_enhancement"] == 2


def test_classify_magnitude_boundaries():
    assert report_mod.classify_magnitude(0.5) == "improve-minor"
    assert report_mod.classify_magnitude(0.99) == "improve-minor"
    assert report_mod.classify_magnitude(1.0) == "improve-moderate"
    assert report_mod.classify_magnitude(2.99) == "improve-moderate"
    assert report_mod.classify_magnitude(3.0) == "improve-major"
    assert report_mod.classify_magnitude(5.0) == "improve-major"
    assert report_mod.classify_magnitude(-2) == "regress-moderate"
    assert report_mod.classify_magnitude(-0.5) == "regress-minor"
    assert report_mod.classify_magnitude(-3.5) == "regress-major"
    assert report_mod.classify_magnitude(0.0) == "tie-minor"


def _judge(deltas: dict, winner: str) -> dict:
    dims = ("accuracy", "completeness", "relevance", "clarity")
    return {
        "original": {d: 0 for d in dims},
        "enhanced": {d: 0 for d in dims},
        "deltas": deltas,
        "winner": winner,
    }


def test_aggregate_by_magnitude_counts():
    samples = [
        {"id": "s1", "scenario": "A", "original": "",
         "results": {"t": {"judge": _judge({"accuracy": 4, "completeness": 4, "relevance": 4, "clarity": 4}, "enhanced")}}},
        {"id": "s2", "scenario": "B", "original": "",
         "results": {"t": {"judge": _judge({"accuracy": -1, "completeness": -1, "relevance": -1, "clarity": -1}, "original")}}},
        {"id": "s3", "scenario": "C", "original": "",
         "results": {"t": {"judge": _judge({"accuracy": 2, "completeness": 2, "relevance": 2, "clarity": 2}, "tie")}}},
        {"id": "s4", "scenario": "D", "original": "",
         "results": {"t": {"judge": _judge({"accuracy": 0, "completeness": 0, "relevance": 0, "clarity": 0}, "enhanced")}}},
    ]

    agg = report_mod._aggregate({"samples": samples})

    assert agg["by_magnitude"] == {
        "improve": {"minor": 1, "moderate": 0, "major": 1},
        "regress": {"minor": 0, "moderate": 1, "major": 0},
    }
    assert agg["summary"]["total"] == 4
    assert agg["summary"]["wins"] == 2
    assert agg["summary"]["losses"] == 1
    assert agg["summary"]["ties"] == 1  # tie 不计入 by_magnitude，但计入 summary


def test_aggregate_by_prompt_judge_means():
    samples = [
        {"id": "p1", "scenario": "写作", "original": "",
         "prompt_judge": {"structure": 8, "constraint_retention": 9, "info_gain": 7, "over_enhancement": 2, "reason": ""}},
        {"id": "p2", "scenario": "写作", "original": "",
         "prompt_judge": {"structure": 6, "constraint_retention": 7, "info_gain": 5, "over_enhancement": 1, "reason": ""}},
        {"id": "p3", "scenario": "编码", "original": "",
         "prompt_judge": {"error": "解析失败"}},
        {"id": "p4", "scenario": "写作", "original": "", "prompt_judge": None},
    ]

    agg = report_mod._aggregate({"samples": samples})

    by = agg["by_prompt_judge"]
    assert by["写作"]["structure"] == {"n": 2, "mean": 7.0}
    assert by["写作"]["over_enhancement"] == {"n": 2, "mean": 1.5}
    assert "编码" not in by  # 失败/缺失的 prompt_judge 不计入
