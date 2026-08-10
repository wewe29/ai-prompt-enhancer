"""T10/T11 测试：pad_to_length 长度控制组与 token 成本聚合。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bench_report  # noqa: E402
import report as report_mod  # noqa: E402
import run_eval as run_eval_mod  # noqa: E402

DIMS = ("accuracy", "completeness", "relevance", "clarity")


# ---- pad_to_length ----
def test_pad_to_length_short_text_reaches_target():
    text = "简短请求"
    out = run_eval_mod.pad_to_length(text, 1000)
    assert out.startswith(text)
    assert 950 <= len(out) <= 1000


def test_pad_to_length_already_longer_unchanged():
    text = "长" * 500
    assert run_eval_mod.pad_to_length(text, 100) == text


def test_pad_to_length_near_target_unchanged():
    text = "长" * 990
    assert run_eval_mod.pad_to_length(text, 1000) == text


def test_pad_to_length_never_exceeds_target():
    out = run_eval_mod.pad_to_length("x", 40)
    assert len(out) == 40
    assert out.startswith("x")


# ---- estimate_tokens ----
def test_estimate_tokens():
    assert report_mod.estimate_tokens("") == 0
    assert report_mod.estimate_tokens("a" * 18) == 10
    assert report_mod.estimate_tokens("a" * 19) == 11


# ---- 成本聚合 ----
def _judge(deltas, winner="enhanced"):
    return {
        "original": {d: 0 for d in DIMS},
        "enhanced": {d: 0 for d in DIMS},
        "deltas": deltas,
        "winner": winner,
        "reason": "x",
    }


def _cost_payload():
    return {
        "meta": {"targets": ["t1"], "judge_model": "j", "enhance_model": "e"},
        "samples": [
            {
                "id": "s1", "scenario": "A",
                "original": "a" * 100, "enhanced_text": "b" * 200,
                "results": {"t1": {
                    "original_output": "o1", "enhanced_output": "e1",
                    "judge": _judge({d: 2 for d in DIMS}),
                }},
            },
            {
                "id": "s2", "scenario": "B",
                "original": "c" * 200, "enhanced_text": "d" * 400,
                "results": {"t1": {
                    "original_output": "o2", "enhanced_output": "e2",
                    "judge": _judge({d: 4 for d in DIMS}),
                }},
            },
        ],
    }


def test_cost_aggregation_synthetic_payload():
    cost = report_mod._aggregate(_cost_payload())["cost"]
    assert cost["orig_chars_mean"] == 150.0
    assert cost["enh_chars_mean"] == 300.0
    assert cost["orig_tokens_mean"] == 84.0  # ceil(100/1.8)=56, ceil(200/1.8)=112
    assert cost["enh_tokens_mean"] == 167.5  # ceil(200/1.8)=112, ceil(400/1.8)=223
    assert cost["pad_ratio"] == pytest.approx(2.0)  # 200/100 与 400/200
    expected_gain = round(3.0 / ((167.5 - 84.0) / 1000.0), 2)  # 质量均分差 3.0，每千 token
    assert cost["quality_gain_per_1k_tokens"] == pytest.approx(expected_gain)


def test_cost_aggregation_empty_without_judges():
    payload = _cost_payload()
    for s in payload["samples"]:
        s["results"]["t1"]["judge"] = None
    assert report_mod._aggregate(payload)["cost"] == {}


# ---- 长度控制组聚合 ----
def _control_payload():
    return {
        "meta": {"targets": ["t1"], "judge_model": "j", "enhance_model": "e"},
        "samples": [
            {
                "id": "s1", "scenario": "A",
                "original": "o" * 50, "enhanced_text": "e" * 100,
                "results": {"t1": {
                    "original_output": "o1", "enhanced_output": "e1", "padded_output": "p1",
                    "judge": _judge({d: 2 for d in DIMS}),
                    "judge_control": _judge({d: 0 for d in DIMS}, winner="tie"),
                    "judge_padded_vs_orig": _judge({d: 2 for d in DIMS}),
                }},
            },
        ],
    }


def test_control_aggregation():
    agg = report_mod._aggregate(_control_payload())
    bc = agg["by_control"]
    assert bc["enhanced_vs_padded"]["A"]["accuracy"]["mean"] == 0.0
    assert bc["padded_vs_original"]["A"]["accuracy"]["mean"] == 2.0
    assert bc["enhanced_vs_padded"]["A"]["clarity"]["mean"] == 0.0
    assert bc["padded_vs_original"]["A"]["clarity"]["mean"] == 2.0
    assert bc["pad_ratio"] == 2.0  # 100 / 50


def test_control_aggregation_empty_when_absent():
    bc = report_mod._aggregate(_cost_payload())["by_control"]
    assert bc["enhanced_vs_padded"] == {}
    assert bc["padded_vs_original"] == {}
    assert bc["pad_ratio"] == 0.0


# ---- Markdown 渲染 ----
def test_markdown_cost_and_control_sections(tmp_path):
    payload = _cost_payload()
    payload["samples"][0]["results"]["t1"].update({
        "padded_output": "p1",
        "judge_control": _judge({d: 0 for d in DIMS}, winner="tie"),
        "judge_padded_vs_orig": _judge({d: 2 for d in DIMS}),
    })
    report_mod.generate(tmp_path, payload)

    md = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "成本与 token" in md
    assert "每千 token 质量提升" in md
    assert "增强膨胀比" in md
    assert "长度控制组" in md
    assert "enhanced−padded 四维 delta 均分" in md
    assert "padded−original 四维 delta 均分" in md


def test_markdown_no_control_section_when_absent(tmp_path):
    report_mod.generate(tmp_path, _cost_payload())
    md = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "长度控制组" not in md
    assert "成本与 token" in md


# ---- bench_report 成本块 ----
def test_bench_cost_block():
    lines = bench_report._cost_block({
        "meta": {
            "prompts": [
                {"id": "p1", "len": 100, "enhanced_len": 200},
                {"id": "p2", "len": 200, "enhanced_len": 400},
            ],
        },
    })
    assert any("平均原始 prompt 长度：**150.0**" in line for line in lines)
    assert any("平均增强 prompt 长度：**300.0**" in line for line in lines)
    assert any("增强膨胀比：**×2.00**" in line for line in lines)


def test_bench_cost_block_empty_without_prompts():
    assert bench_report._cost_block({"meta": {}}) == []
    assert bench_report._cost_block({"meta": {"prompts": []}}) == []


def test_bench_markdown_contains_cost_block(tmp_path):
    payload = {
        "meta": {
            "generated_at": "t",
            "prompt_count": 2,
            "models": [{"id": "A", "label": "模型A"}],
            "prompts": [
                {"id": "p1", "len": 100, "enhanced_len": 200},
                {"id": "p2", "len": 200, "enhanced_len": 400},
            ],
        },
        "details": [],
    }
    path = bench_report.generate(tmp_path, payload)
    md = path.read_text(encoding="utf-8")
    assert "成本与 token" in md
    assert "平均原始 prompt 长度" in md
