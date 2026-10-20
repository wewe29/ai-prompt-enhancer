"""release_gate.py 的单元测试：合成 summary 判定发布门槛。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from release_gate import compute_gates  # noqa: E402


def _dim(mean: float) -> dict:
    return {"n": 10, "mean": mean}


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
    }


def _fail_summary() -> dict:
    return {
        "gate": {"pass": False, "gate_reasons": ["人工校准样本 0 < 有效比较的 20%"]},
        "delivery_rate": 0.88,
        "control": {"by_ambiguity": {}},
        "by_ambiguity": {},
        "cost": {"pad_ratio": 2.6},
        "judge_agreement": {"winner_agreement": 0.6},
    }


def test_pass_summary_all_gates_pass():
    gates = compute_gates(_pass_summary())
    assert all(g["pass"] for g in gates), gates
    assert len(gates) == 7          # 6 条原有 + 1 条 report.py 前置门槛


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
