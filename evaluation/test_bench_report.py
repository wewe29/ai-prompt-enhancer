"""bench_report.py 测试：聚合数学（均分/胜率/综合分/排名）与 markdown 生成。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bench_report  # noqa: E402


def _payload():
    details = [
        {
            "prompt_id": "p1", "model_id": "A", "model_label": "模型A",
            "scores": {"accuracy": 10, "completeness": 10, "relevance": 10, "clarity": 10},
            "reason": "完美", "latency_s": 10.0, "output_len": 100, "error": None,
        },
        {
            "prompt_id": "p2", "model_id": "A", "model_label": "模型A",
            "scores": {"accuracy": 8, "completeness": 8, "relevance": 8, "clarity": 8},
            "reason": "很好", "latency_s": 60.0, "output_len": 200, "error": None,
        },
        {
            "prompt_id": "p1", "model_id": "B", "model_label": "模型B",
            "scores": {"accuracy": 5, "completeness": 5, "relevance": 5, "clarity": 5},
            "reason": "一般", "latency_s": 5.0, "output_len": 50, "error": None,
        },
        {
            "prompt_id": "p2", "model_id": "B", "model_label": "模型B",
            "scores": {"accuracy": 4, "completeness": 4, "relevance": 4, "clarity": 4},
            "reason": "较差", "latency_s": 5.0, "output_len": 60, "error": None,
        },
        {
            "prompt_id": "p1", "model_id": "C", "model_label": "模型C",
            "scores": None, "reason": "", "latency_s": None, "output_len": None,
            "error": "推理失败：HTTP 500",
        },
    ]
    return {
        "meta": {
            "generated_at": "2026-08-09 12:00:00",
            "enhancer_model": "enh",
            "judge_model": "judge",
            "models": [
                {"id": "A", "label": "模型A"},
                {"id": "B", "label": "模型B"},
                {"id": "C", "label": "模型C"},
            ],
            "prompt_count": 2,
        },
        "details": details,
    }


def test_aggregate_means_and_composite():
    agg = bench_report.aggregate(_payload())
    models = {m["id"]: m for m in agg["models"]}
    a, b, c = models["A"], models["B"], models["C"]

    assert a["dims"]["accuracy"] == 9.0
    assert a["dims"]["completeness"] == 9.0
    assert a["dims"]["relevance"] == 9.0
    assert a["dims"]["clarity"] == 9.0
    assert a["quality_mean"] == 9.0
    assert a["latency_mean"] == 35.0
    assert a["output_len_mean"] == 150.0

    # 综合分 = 0.6×质量 + 0.2×相关性 + 0.2×(1-min(latency,120)/120)
    assert a["composite"] == pytest.approx((8.183333 + 6.5) / 2, abs=0.001)
    assert b["composite"] == pytest.approx((4.191667 + 3.391667) / 2, abs=0.001)

    assert a["win_rate"] == 1.0
    assert b["win_rate"] == 0.0
    assert c["win_rate"] == 0.0
    assert c["composite"] == 0.0
    assert c["judged_prompts"] == 0


def test_aggregate_rank_desc():
    agg = bench_report.aggregate(_payload())
    ranks = [(m["id"], m["rank"]) for m in agg["models"]]
    assert ranks == [("A", 1), ("B", 2), ("C", 3)]
    composites = [m["composite"] for m in agg["models"]]
    assert composites == sorted(composites, reverse=True)


def test_aggregate_detail_enrichment():
    agg = bench_report.aggregate(_payload())
    by_key = {(d["prompt_id"], d["model_id"]): d for d in agg["details"]}
    assert by_key[("p1", "A")]["winner"] is True
    assert by_key[("p1", "B")]["winner"] is False
    assert by_key[("p1", "A")]["composite"] == pytest.approx(8.183333, abs=0.001)
    assert by_key[("p1", "C")]["composite"] is None
    assert by_key[("p1", "C")]["winner"] is False


def test_aggregate_tie_no_winner():
    details = []
    for mid in ("A", "B"):
        details.append({
            "prompt_id": "p1", "model_id": mid, "model_label": mid,
            "scores": {"accuracy": 8, "completeness": 8, "relevance": 8, "clarity": 8},
            "reason": "", "latency_s": 10.0, "output_len": 10, "error": None,
        })
    agg = bench_report.aggregate({"meta": {"models": [{"id": "A"}, {"id": "B"}]}, "details": details})
    for m in agg["models"]:
        assert m["win_rate"] == 0.0  # 并列最高：无人胜出


def test_generate_writes_markdown(tmp_path):
    payload = _payload()
    path = bench_report.generate(tmp_path, payload)

    assert path.name == "report_benchmark.md"
    assert path.exists()
    md = path.read_text(encoding="utf-8")
    assert "总览表" in md
    assert "| 模型 | 质量均分 | 相关性 | 延迟(s) | 综合分 |" in md
    assert "| 1 | 模型A |" in md
    assert "| 2 | 模型B |" in md
    assert "模型C" in md
    assert "按提示词详情" in md
    assert "逐模型小结" in md
    assert "失败清单" in md
    assert "推理失败：HTTP 500" in md


def test_generate_auto_aggregates_and_summary_json(tmp_path):
    payload = _payload()
    path = bench_report.generate(tmp_path, payload)

    md = path.read_text(encoding="utf-8")
    assert "| 1 | 模型A | 9.00 | 9.00 | 35.00 | 7.342 |" in md

    summary = {"meta": payload["meta"], **bench_report.aggregate(payload)}
    blob = json.dumps(summary, ensure_ascii=False)
    assert '"rank": 1' in blob
