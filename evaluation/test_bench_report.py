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
    assert "novelty" not in a["dims"]  # 旧数据缺 novelty → 维度统计中按缺失处理

    # 综合分 = 0.7×质量(准确/完整/清晰) + 0.2×相关性 + 0.1×速度分
    # A: p1 = 0.7×10 + 0.2×10 + 0.1×(1-10/120)=9.091667; p2 = 5.6+1.6+0.05=7.25
    assert a["composite"] == pytest.approx((9.091667 + 7.25) / 2, abs=0.001)
    # B: p1 = 3.5+1+0.095833=4.595833; p2 = 2.8+0.8+0.095833=3.695833
    assert b["composite"] == pytest.approx((4.595833 + 3.695833) / 2, abs=0.001)

    assert a["win_rate"] == 1.0
    assert b["win_rate"] == 0.0
    assert c["win_rate"] == 0.0
    assert c["composite"] == 0.0
    assert c["judged_prompts"] == 0


def test_quality_excludes_relevance():
    payload = _payload()
    for item in payload["details"]:
        if item["model_id"] == "A" and item["prompt_id"] == "p1":
            item["scores"] = {"accuracy": 10, "completeness": 10, "relevance": 5, "clarity": 10}
    agg = bench_report.aggregate(payload)
    a = {m["id"]: m for m in agg["models"]}["A"]

    # 质量均分 = 准确/完整/清晰 的均分，不含 relevance：(10+10+10)/3=10 与 8 的平均 → 9.0
    assert a["quality_mean"] == pytest.approx(9.0, abs=0.001)
    assert a["dims"]["relevance"] == pytest.approx(6.5, abs=0.001)
    # p1 综合分 = 0.7×10 + 0.2×5 + 0.1×(1-10/120) = 8.091667
    assert a["composite"] == pytest.approx((8.091667 + 7.25) / 2, abs=0.001)


def test_speed_weight_is_0_1():
    def detail(latency):
        return {
            "prompt_id": "p1", "model_id": "A", "model_label": "A",
            "scores": {"accuracy": 5, "completeness": 5, "relevance": 5, "clarity": 5},
            "reason": "", "latency_s": latency, "output_len": 10, "error": None,
        }

    agg = bench_report.aggregate({"meta": {"models": [{"id": "A"}]}, "details": [detail(1.0), detail(60.0)]})
    m = agg["models"][0]
    # 速度权重 0.1：latency 1 → 速度 0.991667；latency 60 → 速度 0.5
    # 0.7×5 + 0.2×5 = 4.5；综合分 = (4.5+0.1×0.991667 + 4.5+0.1×0.5)/2 ≈ 4.5746
    assert m["composite"] == pytest.approx((4.599167 + 4.55) / 2, abs=0.001)


def test_scenario_weighted_composite_programming():
    payload = {
        "meta": {
            "models": [{"id": "A", "label": "模型A"}],
            "prompts": [{"id": "p1", "scenario": "编程"}],
        },
        "details": [{
            "prompt_id": "p1", "model_id": "A", "model_label": "模型A",
            "scores": {"accuracy": 10, "completeness": 8, "relevance": 6, "clarity": 4},
            "reason": "", "latency_s": 120.0, "output_len": 100, "error": None,
        }],
    }
    sw = {"编程": {"accuracy": 0.4, "completeness": 0.3, "relevance": 0.2, "clarity": 0.1}}
    agg = bench_report.aggregate(payload, scenario_weights=sw)
    m = agg["models"][0]
    # 纯维度加权（速度/novelty 权重为 0）：0.4×10 + 0.3×8 + 0.2×6 + 0.1×4 = 8.0
    assert m["composite"] == pytest.approx(8.0, abs=0.001)
    assert agg["details"][0]["composite"] == pytest.approx(8.0, abs=0.001)


def test_scenario_weighted_composite_creative_with_novelty():
    details = [{
        "prompt_id": "p1", "model_id": "A", "model_label": "模型A", "scenario": "创意",
        "scores": {"accuracy": 10, "completeness": 8, "relevance": 6, "clarity": 4, "novelty": 2},
        "reason": "", "latency_s": 120.0, "output_len": 100, "error": None,
    }]
    sw = {"创意": {"accuracy": 0.15, "completeness": 0.15, "relevance": 0.25, "clarity": 0.2, "novelty": 0.25}}
    payload = {"meta": {"models": [{"id": "A", "label": "模型A"}]}, "details": details}
    agg = bench_report.aggregate(payload, scenario_weights=sw)
    m = agg["models"][0]
    # 0.15×10 + 0.15×8 + 0.25×6 + 0.2×4 + 0.25×2 = 5.5
    assert m["composite"] == pytest.approx(5.5, abs=0.001)
    assert m["dims"]["novelty"] == 2.0  # 新数据含 novelty 维度


def test_novelty_missing_falls_back_to_relevance():
    details = [{
        "prompt_id": "p1", "model_id": "A", "model_label": "模型A", "scenario": "创意",
        "scores": {"accuracy": 10, "completeness": 8, "relevance": 6, "clarity": 4},
        "reason": "", "latency_s": 120.0, "output_len": 100, "error": None,
    }]
    sw = {"创意": {"accuracy": 0.15, "completeness": 0.15, "relevance": 0.25, "clarity": 0.2, "novelty": 0.25}}
    payload = {"meta": {"models": [{"id": "A", "label": "模型A"}]}, "details": details}
    agg = bench_report.aggregate(payload, scenario_weights=sw)
    m = agg["models"][0]
    # 旧数据缺 novelty → 回退 relevance=6：0.15×10+0.15×8+0.25×6+0.2×4+0.25×6 = 6.5
    assert m["composite"] == pytest.approx(6.5, abs=0.001)
    assert "novelty" not in m["dims"]  # 维度统计仍按缺失处理，不虚造


def test_scenario_weights_from_payload_meta():
    payload = {
        "meta": {
            "models": [{"id": "A", "label": "模型A"}],
            "scenario_weights": {"编程": {"accuracy": 0.4, "completeness": 0.3, "relevance": 0.2, "clarity": 0.1}},
            "prompts": [{"id": "p1", "scenario": "编程"}],
        },
        "details": [{
            "prompt_id": "p1", "model_id": "A", "model_label": "模型A",
            "scores": {"accuracy": 10, "completeness": 8, "relevance": 6, "clarity": 4},
            "reason": "", "latency_s": 120.0, "output_len": 100, "error": None,
        }],
    }
    agg = bench_report.aggregate(payload)  # 不传参 → 从 meta.scenario_weights 读取
    assert agg["models"][0]["composite"] == pytest.approx(8.0, abs=0.001)


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
    assert by_key[("p1", "A")]["composite"] == pytest.approx(9.091667, abs=0.001)
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
    assert "| 1 | 模型A | 9.00 | 9.00 | 35.00 | 8.171 |" in md
    assert "综合分" in md

    summary = {"meta": payload["meta"], **bench_report.aggregate(payload)}
    blob = json.dumps(summary, ensure_ascii=False)
    assert '"rank": 1' in blob
