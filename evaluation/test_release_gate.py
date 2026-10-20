"""release_gate.py 的单元测试：合成 summary 判定发布门槛。

分两类：
  · 合成 summary（快，用于精确定位单条门槛的判定逻辑）
  · **report.py 真实产出的 summary**（`_aggregate()` 的输出）—— 见本文件后半段
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import report as report_mod  # noqa: E402
from release_gate import compute_gates  # noqa: E402

FIXTURES = Path(__file__).resolve().parent.parent / "docs" / "evaluation-data-v040" / "fixtures"


def _dim(mean: float) -> dict:
    return {"n": 10, "mean": mean}


def _v040_gates(**overrides) -> dict:
    """全部 active 的 v0.4.0 效果门槛测量值（满足规范推导出的 n/R 要求）。"""
    gates = {
        "blind_hit_rate_translate": {"value": 0.75, "n": 27, "runs": 2},
        "blind_hit_rate_severe": {"value": 0.95, "n": 68, "runs": 2},
        "redundant_question_count": {"value": 0, "n": 61, "runs": 1},
        "delivery_drift_count": {"value": 0, "n": 360, "runs": 1},
    }
    gates.update(overrides)
    return gates


def _pass_summary() -> dict:
    return {
        # v0.4.0 代码审查 H3：compute_gates 现在把 report.py 算出的 summary["gate"]
        # 作为第一条门槛，不读它就会出现"report.md 说不可用于产品结论、
        # release_gate 说全部通过"的互相绕过。合成 summary 必须带上它。
        "gate": {"pass": True, "gate_reasons": []},
        "delivery_rate": 0.97,
        "control": {
            "by_ambiguity": {
                "severe": {"c_minus_b": {d: _dim(9.0) for d in ("accuracy", "completeness", "clarity")}},
                "medium": {"c_minus_b": {d: _dim(9.0) for d in ("accuracy", "completeness", "clarity")}},
            }
        },
        "by_ambiguity": {"clear": {"wins": 40, "ties": 10, "losses": 1}},
        "cost": {"pad_ratio": 1.8},
        "judge_agreement": {"winner_agreement": 0.8},
        # v0.4.0 P1-4：分母必须显式化（清单 A6）。缺这两个数 ⇒ 判 FAIL。
        "comparison": {
            "expected_total": 60, "attempted_total": 60, "judged_total": 60,
            "excluded": {"no_result": 0, "judge_failed": 0}, "coverage_rate": 1.0,
        },
        "v040_gates": _v040_gates(),
    }


def _fail_summary() -> dict:
    return {
        "gate": {"pass": False, "gate_reasons": ["人工校准样本 0 < 有效比较的 20%"]},
        "delivery_rate": 0.88,
        "control": {"by_ambiguity": {}},
        "by_ambiguity": {},
        "cost": {"pad_ratio": 2.6},
        "judge_agreement": {"winner_agreement": 0.6},
        "comparison": {
            "expected_total": 60, "attempted_total": 55, "judged_total": 55,
            "excluded": {"no_result": 0, "judge_failed": 5}, "coverage_rate": 0.9167,
        },
        "v040_gates": _v040_gates(),
    }


def test_pass_summary_all_gates_pass():
    gates = compute_gates(_pass_summary())
    assert all(g["pass"] for g in gates), [g for g in gates if not g["pass"]]
    # 7 条原有（6 条指标 + 1 条 report.py 前置门槛）+ 1 条分母完整性 + 4 条 active 规范门槛
    assert len(gates) == 12, [g["name"] for g in gates]


def test_missing_report_gate_blocks_release():
    """H3：summary 缺 report.py 的 gate 字段时必须 FAIL，不能当它不存在就放行。"""
    s = _pass_summary()
    s.pop("gate")
    gates = compute_gates(s)
    assert gates[0]["pass"] is False
    assert not all(g["pass"] for g in gates)


def test_report_gate_failure_blocks_even_when_metrics_pass():
    """H3 的核心场景：指标全过，但 report.py 判定"不可用于产品结论"。"""
    s = _pass_summary()
    s["gate"] = {"pass": False, "gate_reasons": ["人工校准样本 0 < 有效比较的 20%"]}
    gates = compute_gates(s)
    assert gates[0]["pass"] is False
    assert "人工校准" in gates[0]["value"]


def test_missing_cost_fails_bloat_gate():
    """H2：缺 cost 时膨胀比必须 FAIL。此前 cost.get("pad_ratio", 0.0)
    会让"没测到"变成"膨胀完美 x0.00 且 PASS"。"""
    s = _pass_summary()
    s.pop("cost")
    gates = compute_gates(s)
    bloat = [g for g in gates if "膨胀比" in g["name"]][0]
    assert bloat["pass"] is False
    assert bloat["value"] == "缺失"

    s2 = _pass_summary()
    s2["cost"] = {"other": 1}          # cost 存在但没有 pad_ratio
    gates2 = compute_gates(s2)
    bloat2 = [g for g in gates2 if "膨胀比" in g["name"]][0]
    assert bloat2["pass"] is False


def test_pad_ratio_none_fails_bloat_gate():
    """P1-6：report.py 现在在无比值样本时写 `pad_ratio: None`。
    这个形态必须判 FAIL —— 此前上游写 0.0，release_gate 收到 0.0 ⇒ PASS，
    "没测到膨胀比"被算成"膨胀完美"，H2 的 fail-open 只是被下移了一层。"""
    s = _pass_summary()
    s["cost"] = {"pad_ratio": None, "orig_chars_mean": 10.0}
    gates = compute_gates(s)
    bloat = [g for g in gates if "膨胀比" in g["name"]][0]
    assert bloat["pass"] is False, "缺膨胀比样本必须 FAIL"
    assert bloat["value"] != "×0.00", "缺数据不得被报成膨胀完美"
    assert "无样本" in bloat["value"]


def test_pad_ratio_none_is_not_silently_zero():
    """反向对照：把上游改回写 0.0，门槛就会放行 —— 说明这条断言不是恒真的。"""
    s = _pass_summary()
    s["cost"] = {"pad_ratio": 0.0}
    gates = compute_gates(s)
    bloat = [g for g in gates if "膨胀比" in g["name"]][0]
    assert bloat["pass"] is True, "0.0 确实会被放行（这正是必须在上游堵住的原因）"


def test_fail_summary_flags_multiple():
    gates = compute_gates(_fail_summary())
    failed = [g for g in gates if not g["pass"]]
    names = [g["name"] for g in failed]
    assert "总有效率 ≥ 95%" in names
    assert "严重模糊 C-B 质量提升 ≥ +0.8" in names
    assert "中等模糊 C-B 质量提升 ≥ +0.4" in names
    assert "清晰提示词明显变差率 ≤ 5%" in names
    assert "整体平均膨胀比 ≤ 2.0" in names
    assert "双裁判一致率 ≥ 75%" in names


def test_clear_worse_rate_boundary():
    s = _pass_summary()
    s["by_ambiguity"]["clear"] = {"wins": 19, "ties": 0, "losses": 1}
    gates = compute_gates(s)
    g = next(x for x in gates if "变差率" in x["name"])
    assert g["pass"] is True  # 1/20 = 5% 恰好等于阈值 → 允许

    s["by_ambiguity"]["clear"] = {"wins": 18, "ties": 0, "losses": 2}
    g = next(x for x in compute_gates(s) if "变差率" in x["name"])
    assert g["pass"] is False  # 2/20 = 10% > 5%


def test_missing_agreement_fails():
    s = _pass_summary()
    s.pop("judge_agreement", None)
    g = next(x for x in compute_gates(s) if "一致率" in x["name"])
    assert g["pass"] is False
    assert g["value"] == "缺失"


# =====================================================================
# 分母完整性门槛（清单 A6 / 缺失语义约定 R2、R4）
# =====================================================================

def test_missing_comparison_fails_denominator_gate():
    """缺"应有/实有"时必须 FAIL —— 没有分母的比率不可判定。"""
    s = _pass_summary()
    s.pop("comparison")
    g = next(x for x in compute_gates(s) if "比较样本完整性" in x["name"])
    assert g["pass"] is False
    assert g["value"] == "缺失"


def test_denominator_loss_fails():
    """实有/应有 低于 95% ⇒ FAIL（分母流失直接 FAIL，不允许"不可判定"混过去）。"""
    s = _pass_summary()
    s["comparison"] = {"expected_total": 60, "attempted_total": 57, "judged_total": 57,
                       "excluded": {"no_result": 0, "judge_failed": 3}, "coverage_rate": 0.95}
    g = next(x for x in compute_gates(s) if "比较样本完整性" in x["name"])
    assert g["pass"] is True, "57/60 = 95% 恰好达标"

    s["comparison"] = {"expected_total": 60, "attempted_total": 56, "judged_total": 56,
                       "excluded": {"no_result": 0, "judge_failed": 4}, "coverage_rate": 0.9333}
    g = next(x for x in compute_gates(s) if "比较样本完整性" in x["name"])
    assert g["pass"] is False
    assert "judge_failed=4" in g["value"]


# =====================================================================
# v0.4.0 规范门槛：样本量 / 轮次 / 区间
# =====================================================================

def test_v040_gate_missing_measurement_fails():
    """效果门槛没有测量数据 ⇒ FAIL（缺数据 fail-closed）。"""
    s = _pass_summary()
    s["v040_gates"] = {}
    gates = compute_gates(s)
    g4 = next(x for x in gates if "[G4]" in x["name"])
    assert g4["pass"] is False
    assert g4["value"] == "缺失"


def test_v040_gate_insufficient_sample_size_fails():
    """n 低于规范要求 ⇒ FAIL（样本量不足）。"""
    s = _pass_summary()
    s["v040_gates"] = _v040_gates(blind_hit_rate_severe={"value": 0.95, "n": 20, "runs": 2})
    g = next(x for x in compute_gates(s) if "[G5]" in x["name"])
    assert g["pass"] is False
    assert "需 n≥68" in g["threshold"]


def test_v040_gate_single_run_fails():
    """单次运行的结果不可进结论 ⇒ FAIL。"""
    s = _pass_summary()
    s["v040_gates"] = _v040_gates(blind_hit_rate_translate={"value": 0.75, "n": 27, "runs": 1})
    g = next(x for x in compute_gates(s) if "[G4]" in x["name"])
    assert g["pass"] is False
    assert "需 R≥2" in g["threshold"]


def test_v040_gate_straddling_interval_is_fail_not_pass():
    """区间跨越阈值 ⇒ FAIL（不可判定是 FAIL 的原因标签，不是第三种输出）。"""
    s = _pass_summary()
    # 90% 命中率但 n 只有 68 ⇒ 下界 84% < 85% ⇒ 区间跨越 ⇒ 必须 FAIL
    s["v040_gates"] = _v040_gates(blind_hit_rate_severe={"value": 0.90, "n": 68, "runs": 2})
    g = next(x for x in compute_gates(s) if "[G5]" in x["name"])
    assert g["pass"] is False
    assert "±" in g["value"]


def test_void_gates_are_not_part_of_the_gate_list():
    """已作废的门槛不参与放行判定，但也不能悄悄消失 —— 它们由 main() 显式列出。"""
    gates = compute_gates(_pass_summary())
    assert not any("[G1]" in g["name"] or "[G2]" in g["name"] or "[G3]" in g["name"]
                   for g in gates)


# =====================================================================
# 用 report.py **真实产出**的 summary 判定（不是手搓的假 summary）
# =====================================================================

def _real_summary(payload: dict) -> dict:
    """走 report._aggregate() 真实聚合出来的 summary。"""
    return report_mod._aggregate(payload)


def _real_payload(n_samples=60, judge_all=True, orig_len=100):
    scenarios = ("编程", "办公写作", "问答", "数据分析", "翻译", "创意")
    samples = []
    for i in range(n_samples):
        amb = ("clear", "medium", "severe")[i % 3]
        results = {"t1": {
            "original_output": "o" * orig_len,
            "enhanced_output": "e" * (orig_len * 2),
            "judge": {
                "original": {d: 0 for d in ("accuracy", "completeness", "relevance", "clarity")},
                "enhanced": {d: 2 for d in ("accuracy", "completeness", "relevance", "clarity")},
                "deltas": {d: 2 for d in ("accuracy", "completeness", "relevance", "clarity")},
                "winner": "enhanced", "reason": "x",
            },
            "agreement": {"winner_agreement": True,
                          "dim_corr": {d: 0.5 for d in ("accuracy", "completeness", "relevance", "clarity")}},
        }}
        if not (judge_all or (i % 4 != 0)):
            results["t1"].pop("judge")
        samples.append({
            "id": f"s{i+1:02d}", "scenario": scenarios[i % len(scenarios)],
            "ambiguity_level": amb,
            "original": "o" * orig_len, "enhanced_text": "e" * (orig_len * 2),
            "enhanced": {"primary_prompt": "e" * (orig_len * 2), "delivery_status": "complete"},
            "results": results,
        })
    return {
        "meta": {"generated_at": "t", "targets": ["t1"], "judge_model": "j",
                 "enhance_model": "e", "estimated_cost": 0.0, "human_review_count": n_samples},
        "samples": samples,
    }


def test_real_summary_missing_cost_makes_bloat_gate_fail():
    """真实 summary：原文为空 ⇒ 无比值样本 ⇒ pad_ratio=None ⇒ 膨胀比门槛必须 FAIL。

    这条取代"手搓假 summary"——它走的是 report.py 的真实聚合路径。
    """
    payload = _real_payload()
    for s in payload["samples"]:
        s["original"] = ""
    summary = _real_summary(payload)
    assert summary["cost"]["pad_ratio"] is None, "无比值样本时必须是 None，不是 0.0"
    gates = compute_gates(summary)
    bloat = next(g for g in gates if "膨胀比" in g["name"])
    assert bloat["pass"] is False
    assert "无样本" in bloat["value"]


def test_real_summary_cost_key_absent_makes_bloat_gate_fail():
    """真实 summary：连 cost 字典都没有时也必须 FAIL。"""
    payload = _real_payload()
    for s in payload["samples"]:
        s["results"]["t1"].pop("judge")
    summary = _real_summary(payload)
    assert summary["cost"] == {}
    bloat = next(g for g in compute_gates(summary) if "膨胀比" in g["name"])
    assert bloat["pass"] is False


def test_real_summary_denominator_loss_is_visible_and_fails():
    """真实 summary：1/4 样本没有 judge ⇒ 分母流失必须被记录并判 FAIL。"""
    summary = _real_summary(_real_payload(judge_all=False))
    comparison = summary["summary"]["comparison"]
    assert comparison["expected_total"] == 60
    assert comparison["judged_total"] == 45
    assert comparison["excluded"]["judge_failed"] == 15
    g = next(x for x in compute_gates(summary) if "比较样本完整性" in x["name"])
    assert g["pass"] is False
    assert "judge_failed=15" in g["value"]


def test_real_summary_full_coverage_passes_denominator_gate():
    summary = _real_summary(_real_payload())
    g = next(x for x in compute_gates(summary) if "比较样本完整性" in x["name"])
    assert g["pass"] is True
    assert "60/60" in g["value"]


# =====================================================================
# committed fixture 驱动的端到端判定
# =====================================================================

@pytest.mark.parametrize("name,expect_pass", [
    ("summary_pass.json", True),
    ("summary_missing_cost.json", False),
    ("summary_denominator_lost.json", False),
    ("summary_single_run.json", False),
    ("summary_v040_gates_missing.json", False),
])
def test_committed_fixtures_gate_outcome(name, expect_pass):
    """committed fixture ⇒ 唯一结论 + 与期望一致。"""
    path = FIXTURES / name
    assert path.exists(), f"缺少 committed fixture: {path}"
    summary = json.loads(path.read_text(encoding="utf-8"))
    gates = compute_gates(summary)
    all_pass = all(g["pass"] for g in gates)
    assert all_pass is expect_pass, [
        (g["name"], g["value"], g["threshold"]) for g in gates if not g["pass"]
    ]


def test_fixture_missing_cost_fails_only_bloat_gate():
    path = FIXTURES / "summary_missing_cost.json"
    summary = json.loads(path.read_text(encoding="utf-8"))
    failed = [g["name"] for g in compute_gates(summary) if not g["pass"]]
    assert "整体平均膨胀比 ≤ 2.0" in failed
